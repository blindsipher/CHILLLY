import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Callable, List
from dataclasses import dataclass
from enum import Enum
import time

from topstepx_backend.networking.async_signalr_wrapper import AsyncSignalRWrapper, ConnectionEvent
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.config.settings import TopstepConfig


class ConnectionState(Enum):
    """SignalR connection states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    FAILED = "failed"


@dataclass
class ConnectionStats:
    """Connection statistics and health metrics."""

    connected_at: Optional[float] = None
    disconnected_at: Optional[float] = None
    reconnect_attempts: int = 0
    messages_received: int = 0
    messages_sent: int = 0
    last_message_at: Optional[float] = None
    total_downtime: float = 0.0


class HubAgent(ABC):
    """Base class for SignalR hub agents with automatic reconnection and error handling."""

    def __init__(self, config: TopstepConfig, auth_manager: AuthManager, hub_name: str):
        self.config = config
        self.auth_manager = auth_manager
        self.hub_name = hub_name
        self.logger = logging.getLogger(f"{__name__}.{hub_name}")

        # Connection management
        self._client: Optional[AsyncSignalRWrapper] = None
        self._state = ConnectionState.DISCONNECTED
        self._stats = ConnectionStats()
        self._reconnect_task: Optional[asyncio.Task] = None
        self._is_running = False

        # Event handlers
        self._event_handlers: Dict[str, Callable] = {}

        # Reconnection configuration
        self.max_reconnect_attempts = 10
        self.base_reconnect_delay = 1.0  # seconds
        self.max_reconnect_delay = 60.0  # seconds
        self.exponential_backoff_factor = 2.0

    @property
    @abstractmethod
    def hub_url_template(self) -> str:
        """Template for hub URL. Should include {token} placeholder."""
        pass

    @property
    def state(self) -> ConnectionState:
        """Current connection state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self._state == ConnectionState.CONNECTED

    @property
    def stats(self) -> ConnectionStats:
        """Connection statistics."""
        return self._stats

    def _set_state(self, new_state: ConnectionState):
        """Update connection state with logging."""
        if new_state != self._state:
            old_state = self._state
            self._state = new_state
            self.logger.info(
                f"Connection state changed: {old_state.value} -> {new_state.value}"
            )

            # Update statistics
            now = time.time()
            if new_state == ConnectionState.CONNECTED:
                self._stats.connected_at = now
                self._stats.reconnect_attempts = 0
            elif (
                new_state == ConnectionState.DISCONNECTED
                and old_state == ConnectionState.CONNECTED
            ):
                self._stats.disconnected_at = now
                if self._stats.connected_at:
                    uptime = now - self._stats.connected_at
                    self.logger.debug(f"Connection uptime: {uptime:.2f}s")

    async def start(self):
        """Start the hub agent and establish connection."""
        if self._is_running:
            self.logger.warning("Hub agent already running")
            return

        self._is_running = True
        self.logger.info(f"Starting {self.hub_name} hub agent")

        try:
            await self._connect()
        except Exception as e:
            self.logger.error(f"Failed to start hub agent: {e}")
            self._is_running = False
            raise

    async def stop(self):
        """Stop the hub agent and cleanup resources."""
        if not self._is_running:
            return

        self.logger.info(f"Stopping {self.hub_name} hub agent")
        self._is_running = False

        # Cancel reconnection task
        if self._reconnect_task:
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass

        # Disconnect client
        if self._client:
            try:
                await self._client.stop()
            except Exception as e:
                self.logger.error(f"Error stopping SignalR client: {e}")
            self._client = None

        self._set_state(ConnectionState.DISCONNECTED)

    async def _connect(self):
        """Establish SignalR connection using AsyncSignalRWrapper."""
        self._set_state(ConnectionState.CONNECTING)

        try:
            # Get hub URL with token in URL (required for pysignalr compatibility)
            base_url = self.hub_url_template
            token = self.auth_manager.get_token()
            if token:
                # Handle both string tokens and token objects
                token_str = token.token if hasattr(token, 'token') else str(token)
                hub_url = f"{base_url}?access_token={token_str}"
            else:
                raise Exception("No authentication token available")

            self.logger.info(f"Connecting to SignalR hub: {base_url}")
            self.logger.debug(
                f"Token factory available: {callable(self.auth_manager.access_token_factory)}"
            )

            # Create async SignalR wrapper
            self._client = AsyncSignalRWrapper(
                hub_url=hub_url,
                auth_manager=self.auth_manager,
                loop=asyncio.get_running_loop(),
                enable_logging=True
            )

            # Setup connection event handlers
            self._client.on_connection_event(ConnectionEvent.CONNECTED, self._on_connected_async)
            self._client.on_connection_event(ConnectionEvent.DISCONNECTED, self._on_disconnected_async)
            self._client.on_connection_event(ConnectionEvent.RECONNECTED, self._on_reconnected_async)
            self._client.on_connection_event(ConnectionEvent.ERROR, self._on_error_async)

            self.logger.debug("Connection event handlers set up")

            # Register custom event handlers
            for event_name, handler in self._event_handlers.items():
                self._client.on(event_name, handler)

            self.logger.debug(
                f"Registered {len(self._event_handlers)} custom event handlers"
            )

            # Start the async wrapper
            await self._client.start()
            self.logger.debug("AsyncSignalRWrapper started")

        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self.logger.exception("Full connection error details:")
            self._set_state(ConnectionState.FAILED)
            if self._is_running:
                await self._schedule_reconnect()
            raise

    async def _on_connected_async(self, event_type: str, event_data: Dict[str, Any]):
        """Handle successful connection (async-safe version)."""
        self.logger.info(f"Connected to {self.hub_name} hub")
        self._set_state(ConnectionState.CONNECTED)

        # Setup subscriptions
        try:
            await self._setup_subscriptions()
            self.logger.info(f"Subscriptions established for {self.hub_name} hub")
        except Exception as e:
            self.logger.error(f"Failed to setup subscriptions: {e}")

    async def _on_disconnected_async(self, event_type: str, event_data: Dict[str, Any]):
        """Handle disconnection (async-safe version)."""
        self.logger.warning(f"Disconnected from {self.hub_name} hub")
        self._set_state(ConnectionState.DISCONNECTED)

        if self._is_running:
            await self._schedule_reconnect()

    async def _on_reconnected_async(self, event_type: str, event_data: Dict[str, Any]):
        """Handle reconnection (async-safe version)."""
        self.logger.info(f"Reconnected to {self.hub_name} hub")
        self._set_state(ConnectionState.CONNECTED)

        # Re-setup subscriptions after reconnection
        try:
            await self._setup_subscriptions()
            self.logger.info(f"Subscriptions re-established for {self.hub_name} hub")
        except Exception as e:
            self.logger.error(f"Failed to re-setup subscriptions: {e}")

    async def _on_error_async(self, event_type: str, event_data: Dict[str, Any]):
        """Handle connection errors (async-safe version)."""
        error = event_data.get("error", "Unknown error")
        self.logger.error(f"SignalR error: {error}")
        self._set_state(ConnectionState.FAILED)

        if self._is_running:
            await self._schedule_reconnect()

    async def _schedule_reconnect(self):
        """Schedule reconnection with exponential backoff."""
        if self._reconnect_task and not self._reconnect_task.done():
            return  # Reconnection already scheduled

        self._stats.reconnect_attempts += 1

        if self._stats.reconnect_attempts > self.max_reconnect_attempts:
            self.logger.error(
                f"Max reconnection attempts ({self.max_reconnect_attempts}) exceeded"
            )
            self._set_state(ConnectionState.FAILED)
            return

        # Calculate delay with exponential backoff
        delay = min(
            self.base_reconnect_delay
            * (self.exponential_backoff_factor ** (self._stats.reconnect_attempts - 1)),
            self.max_reconnect_delay,
        )

        self.logger.info(
            f"Scheduling reconnection attempt {self._stats.reconnect_attempts} in {delay:.1f}s"
        )
        self._reconnect_task = asyncio.create_task(self._reconnect_after_delay(delay))

    async def _reconnect_after_delay(self, delay: float):
        """Wait and then attempt reconnection."""
        try:
            await asyncio.sleep(delay)
            if self._is_running:
                self._set_state(ConnectionState.RECONNECTING)
                # Use the wrapper's reconnect method if available
                if self._client and hasattr(self._client, 'reconnect'):
                    await self._client.reconnect()
                else:
                    # Fall back to full reconnection
                    await self._connect()
        except asyncio.CancelledError:
            self.logger.debug("Reconnection cancelled")
        except Exception as e:
            self.logger.error(f"Reconnection failed: {e}")

    async def force_reconnect(self):
        """Force an immediate reconnection."""
        if not self._is_running:
            self.logger.warning("Cannot force reconnect: hub agent not running")
            return

        self.logger.info(f"Forcing reconnection for {self.hub_name} hub")
        self._set_state(ConnectionState.RECONNECTING)
        
        if self._client:
            try:
                await self._client.reconnect()
            except Exception as e:
                self.logger.error(f"Force reconnect failed: {e}")
                # Try full reconnection as fallback
                await self._connect()

    def register_event_handler(self, event_name: str, handler: Callable):
        """Register an event handler for SignalR events."""
        self._event_handlers[event_name] = handler
        if self._client:
            self._client.on(event_name, handler)

    async def send_message(self, method: str, args: List[Any] = None):
        """Send a message to the hub."""
        if not self.is_connected or not self._client:
            self.logger.warning(f"Cannot send message '{method}': not connected")
            return False

        try:
            success = await self._client.send(method, args or [])
            if success:
                self._stats.messages_sent += 1
                self.logger.debug(f"Sent message: {method} with args: {args}")
            return success
        except Exception as e:
            self.logger.error(f"Failed to send message '{method}': {e}")
            return False

    def _update_message_stats(self):
        """Update message reception statistics."""
        self._stats.messages_received += 1
        self._stats.last_message_at = time.time()

    @abstractmethod
    async def _setup_subscriptions(self):
        """Setup SignalR subscriptions. Implemented by subclasses."""
        pass

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
