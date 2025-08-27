import asyncio
import contextlib
import json
import logging
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Iterable, Optional, Set

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

    def __init__(self, event_bus: EventBus, auth_manager: AuthManager) -> None:
        self.event_bus = event_bus
        self.auth_manager = auth_manager
        self.logger = logging.getLogger(__name__)

        # Mapping of pattern -> subscription/task/client set
        self._subscriptions: Dict[str, Subscription] = {}
        self._forwarder_tasks: Dict[str, asyncio.Task] = {}
        self._pattern_clients: Dict[str, Set[Any]] = {}
        self._client_patterns: Dict[Any, Set[str]] = {}

    async def start(self) -> None:
        """Placeholder to align with Service interface."""
        # Subscriptions are created on-demand when clients connect.
        return

    async def stop(self) -> None:
        """Stop forwarding events and unsubscribe from the bus."""
        # Cancel all forwarder tasks and unsubscribe
        for pattern, task in list(self._forwarder_tasks.items()):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            self._forwarder_tasks.pop(pattern, None)
        for pattern, sub in list(self._subscriptions.items()):
            await self.event_bus.unsubscribe(sub)
            self._subscriptions.pop(pattern, None)

        # Close all connected clients
        for ws in list(self._client_patterns.keys()):
            try:
                await ws.close()  # type: ignore[attr-defined]
            except Exception:
                pass
            self._client_patterns.pop(ws, None)

    async def connect(self, websocket: Any, token: str, patterns: Iterable[str]) -> None:
        """Register a WebSocket client after validating the token.

        Args:
            websocket: Object representing the WebSocket connection.  Must have
                an asynchronous ``send`` method and optionally ``close``.
            token: Authentication token provided by client.
            patterns: Iterable of topic patterns the client is interested in.

        Raises:
            AuthenticationError: If the token does not match AuthManager's token.
        """
        expected = self.auth_manager.get_token()
        if token != expected:
            self.logger.warning("WebSocket authentication failed")
            raise AuthenticationError("Invalid authentication token")

        pattern_set = set(patterns) or {"*"}
        self._client_patterns[websocket] = pattern_set
        for pattern in pattern_set:
            if pattern not in self._subscriptions:
                sub = await self.event_bus.subscribe(pattern)
                self._subscriptions[pattern] = sub
                self._pattern_clients[pattern] = set()
                self._forwarder_tasks[pattern] = asyncio.create_task(
                    self._forward_events(pattern)
                )
                self.logger.info("WSGateway subscribed to '%s'", pattern)
            self._pattern_clients[pattern].add(websocket)

        self.logger.debug(
            "Client connected with patterns=%s; total clients=%d",
            pattern_set,
            len(self._client_patterns),
        )

    async def disconnect(self, websocket: Any) -> None:
        """Remove a WebSocket client."""
        patterns = self._client_patterns.pop(websocket, set())
        for pattern in patterns:
            clients = self._pattern_clients.get(pattern)
            if clients is not None:
                clients.discard(websocket)
                if not clients:
                    self._pattern_clients.pop(pattern, None)
                    task = self._forwarder_tasks.pop(pattern, None)
                    if task:
                        task.cancel()
                        if task is not asyncio.current_task():
                            with contextlib.suppress(asyncio.CancelledError):
                                await task
                    sub = self._subscriptions.pop(pattern, None)
                    if sub:
                        await self.event_bus.unsubscribe(sub)
        try:
            await websocket.close()  # type: ignore[attr-defined]
        except Exception:
            pass
        self.logger.debug(
            "Client disconnected; total clients=%d", len(self._client_patterns)
        )

    async def _forward_events(self, pattern: str) -> None:
        """Forward events for a specific subscription pattern."""
        subscription = self._subscriptions[pattern]
        async for topic, payload in subscription:
            message = self._serialize(topic, payload)
            await self._broadcast(pattern, message)

    async def _broadcast(self, pattern: str, message: str) -> None:
        clients = self._pattern_clients.get(pattern)
        if not clients:
            return
        coros = [self._safe_send(ws, message) for ws in list(clients)]
        await asyncio.gather(*coros, return_exceptions=True)

    async def _safe_send(self, ws: Any, message: str) -> None:
        try:
            await ws.send(message)
        except Exception:
            # Drop clients that fail to receive
            self.logger.debug("Removing failed client", exc_info=True)
            await self.disconnect(ws)

    def _serialize(self, topic: str, payload: Any) -> str:
        """Serialize an event into JSON string."""
        return json.dumps({"topic": topic, "payload": payload}, default=self._json_default)

    @staticmethod
    def _json_default(obj: Any) -> Any:
        if is_dataclass(obj):
            return asdict(obj)
        return str(obj)
