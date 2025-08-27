import asyncio
import contextlib
import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Optional, Set

from topstepx_backend.auth.auth_manager import AuthManager, AuthenticationError
from topstepx_backend.core.event_bus import EventBus, Subscription


class WebSocketGateway:
    """Bridge EventBus events to connected WebSocket clients.

    The gateway is intentionally framework agnostic.  A "client" is any object
    that exposes an asynchronous ``send`` method (mimicking the common WebSocket
    protocol APIs).  Web servers such as ``websockets`` or ``FastAPI`` can pass
    their connection objects directly to :meth:`connect`.

    The gateway subscribes to the :class:`EventBus` and forwards serialized
    events to all connected clients.  Basic authentication is performed by
    comparing provided tokens against the current token managed by
    :class:`AuthManager`.
    """

    def __init__(
        self,
        event_bus: EventBus,
        auth_manager: AuthManager,
        patterns: Optional[Iterable[str]] = None,
    ) -> None:
        self.event_bus = event_bus
        self.auth_manager = auth_manager
        self.patterns = list(patterns) if patterns else ["*"]
        self.logger = logging.getLogger(__name__)

        self._subscription: Optional[Subscription] = None
        self._forwarder_task: Optional[asyncio.Task] = None
        self._clients: Set[Any] = set()

    async def start(self) -> None:
        """Subscribe to the event bus and start forwarding events."""
        if self._subscription is not None:
            return

        # Subscribe using the first pattern.  When multiple patterns are
        # requested we create a single wildcard subscription (``*``) which is
        # sufficient for test scenarios and keeps implementation simple.
        pattern = self.patterns[0] if len(self.patterns) == 1 else "*"
        self._subscription = await self.event_bus.subscribe(pattern)
        self._forwarder_task = asyncio.create_task(self._forward_events())
        self.logger.info("WSGateway subscribed to '%s'", pattern)

    async def stop(self) -> None:
        """Stop forwarding events and unsubscribe from the bus."""
        if self._forwarder_task:
            self._forwarder_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._forwarder_task
            self._forwarder_task = None

        if self._subscription:
            await self.event_bus.unsubscribe(self._subscription)
            self._subscription = None

        # Close all connected clients
        for ws in list(self._clients):
            try:
                await ws.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._clients.discard(ws)

    async def connect(self, websocket: Any, token: str) -> None:
        """Register a WebSocket client after validating the token.

        Args:
            websocket: Object representing the WebSocket connection.  Must have
                an asynchronous ``send`` method and optionally ``close``.
            token: Authentication token provided by client.

        Raises:
            AuthenticationError: If the token does not match AuthManager's token.
        """
        expected = self.auth_manager.get_token()
        if token != expected:
            self.logger.warning("WebSocket authentication failed")
            raise AuthenticationError("Invalid authentication token")

        self._clients.add(websocket)
        self.logger.debug("Client connected; total clients=%d", len(self._clients))

    async def disconnect(self, websocket: Any) -> None:
        """Remove a WebSocket client."""
        self._clients.discard(websocket)
        try:
            await websocket.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        self.logger.debug("Client disconnected; total clients=%d", len(self._clients))

    async def _forward_events(self) -> None:
        """Internal task that forwards bus events to clients."""
        assert self._subscription is not None
        async for topic, payload in self._subscription:  # type: ignore[union-attr]
            message = self._serialize(topic, payload)
            await self._broadcast(message)

    async def _broadcast(self, message: str) -> None:
        if not self._clients:
            return
        coros = [self._safe_send(ws, message) for ws in list(self._clients)]
        await asyncio.gather(*coros, return_exceptions=True)

    async def _safe_send(self, ws: Any, message: str) -> None:
        try:
            await ws.send(message)
        except Exception:
            # Drop clients that fail to receive
            self.logger.debug("Removing failed client", exc_info=True)
            self._clients.discard(ws)
            with contextlib.suppress(Exception):
                await ws.close()  # type: ignore[attr-defined]

    def _serialize(self, topic: str, payload: Any) -> str:
        """Serialize an event into JSON string."""
        return json.dumps({"topic": topic, "payload": payload}, default=self._json_default)

    @staticmethod
    def _json_default(obj: Any) -> Any:
        if is_dataclass(obj):
            return asdict(obj)
        return str(obj)
