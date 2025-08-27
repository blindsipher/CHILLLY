"""Async-friendly wrapper around pysignalr for proper asyncio integration."""

import asyncio
import logging
import sys
import time
from typing import Callable, Optional, Dict, Any, List
from dataclasses import dataclass
import threading

from pysignalr.client import SignalRClient
from topstepx_backend.auth.auth_manager import AuthManager


class ConnectionEvent:
    """Represents a SignalR connection event."""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTED = "reconnected"
    ERROR = "error"


@dataclass
class SignalRMessage:
    """Wrapper for SignalR messages with type information."""
    event_type: str
    data: Dict[str, Any]
    timestamp: float


class AsyncSignalRWrapper:
    """
    Async-friendly wrapper around pysignalr.SignalRClient.
    
    This wrapper solves the asyncio threading issues by:
    1. Using asyncio.Queue to bridge thread-safe communication
    2. Using loop.call_soon_threadsafe for callback handling
    3. Providing proper connection management and cleanup
    4. Supporting Windows event loop policy if needed
    """

    def __init__(
        self,
        hub_url: str,
        auth_manager: AuthManager,
        loop: Optional[asyncio.AbstractEventLoop] = None,
        enable_logging: bool = True,
    ):
        self.hub_url = hub_url
        self.auth_manager = auth_manager
        self._loop = loop or asyncio.get_running_loop()
        self.logger = logging.getLogger(f"{__name__}.{id(self)}")
        
        # Set Windows event loop policy if needed
        if sys.platform.startswith("win") and hasattr(asyncio, 'WindowsSelectorEventLoopPolicy'):
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        
        # Message queue for thread-safe communication
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._connection_event_queue: asyncio.Queue = asyncio.Queue()
        
        # SignalR client and management
        self._client: Optional[SignalRClient] = None
        self._client_thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        
        # Task tracking for cleanup
        self._message_task: Optional[asyncio.Task] = None
        self._connection_task: Optional[asyncio.Task] = None
        
        # Event handlers registry
        self._event_handlers: Dict[str, List[Callable]] = {}
        self._connection_handlers: Dict[str, List[Callable]] = {}
        
        # Reconnection tracking
        self._reconnect_guard = asyncio.Lock()
        
        if enable_logging:
            self.logger.info(f"AsyncSignalRWrapper initialized for {hub_url}")

    def on(self, event_name: str, handler: Callable):
        """Register an event handler for SignalR messages."""
        if event_name not in self._event_handlers:
            self._event_handlers[event_name] = []
        self._event_handlers[event_name].append(handler)
        self.logger.debug(f"Registered handler for event: {event_name}")

    def on_connection_event(self, event_type: str, handler: Callable):
        """Register a handler for connection events."""
        if event_type not in self._connection_handlers:
            self._connection_handlers[event_type] = []
        self._connection_handlers[event_type].append(handler)
        self.logger.debug(f"Registered connection handler for: {event_type}")

    def _push_message(self, event_type: str, data: Dict[str, Any]):
        """Thread-safe method to push messages from SignalR callbacks."""
        message = SignalRMessage(
            event_type=event_type,
            data=data,
            timestamp=asyncio.get_event_loop().time() if hasattr(asyncio, 'get_event_loop') else 0
        )
        # Use call_soon_threadsafe to safely put message in queue from worker thread
        self._loop.call_soon_threadsafe(self._message_queue.put_nowait, message)

    def _push_connection_event(self, event_type: str, data: Optional[Dict[str, Any]] = None):
        """Thread-safe method to push connection events."""
        event_data = data or {}
        self._loop.call_soon_threadsafe(
            self._connection_event_queue.put_nowait, 
            (event_type, event_data)
        )

    def _create_client(self) -> SignalRClient:
        """Create and configure the SignalR client."""
        # Create minimal SignalR client configuration for compatibility
        # Token is already in the URL, so no need for access_token_factory
        client = SignalRClient(self.hub_url)
        
        # Set up connection callbacks
        client.on_open(self._on_connected)
        client.on_close(self._on_disconnected)
        client.on_error(self._on_error)
        
        # Register all event handlers
        for event_name in self._event_handlers:
            client.on(event_name, lambda *args, e=event_name: self._on_message(e, *args))
        
        return client

    def _on_connected(self):
        """Called when SignalR connection is established (from worker thread)."""
        self._connected = True
        self.logger.info("SignalR connection established")
        self._push_connection_event(ConnectionEvent.CONNECTED)

    def _on_disconnected(self):
        """Called when SignalR connection is lost (from worker thread)."""
        self._connected = False
        self.logger.warning("SignalR connection lost")
        self._push_connection_event(ConnectionEvent.DISCONNECTED)

    def _on_error(self, error):
        """Called when SignalR connection error occurs (from worker thread)."""
        self._connected = False
        self.logger.error(f"SignalR connection error: {error}")
        self._push_connection_event(ConnectionEvent.ERROR, {"error": str(error)})

    def _on_message(self, event_type: str, *args):
        """Called when a SignalR message is received (from worker thread)."""
        # Convert positional args to a data dict
        if len(args) == 1 and isinstance(args[0], dict):
            data = args[0]
        elif len(args) == 2:
            # Common pattern: (contract_id, data)
            data = {"contract_id": args[0], "data": args[1]}
        else:
            data = {"args": list(args)}
        
        self._push_message(event_type, data)

    def _run_client_thread(self):
        """Run the SignalR client in a separate thread."""
        try:
            self.logger.info("Starting SignalR client thread")
            self._client = self._create_client()
            
            # Check if client has run method and call appropriately
            if hasattr(self._client, 'run'):
                if asyncio.iscoroutinefunction(self._client.run):
                    # Run the async client in a new event loop for this thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(self._client.run())
                    finally:
                        loop.close()
                else:
                    # Synchronous run method
                    self._client.run()
            elif hasattr(self._client, 'start'):
                # Some SignalR clients use start() method
                if asyncio.iscoroutinefunction(self._client.start):
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(self._client.start())
                        # Keep the thread alive while connected
                        while self._connected and self._running:
                            loop.run_until_complete(asyncio.sleep(1))
                    finally:
                        loop.close()
                else:
                    self._client.start()
                    # Keep the thread alive while connected
                    while self._connected and self._running:
                        time.sleep(1)
            else:
                # Fallback: just keep thread alive and rely on callbacks
                self.logger.info("No run/start method found, keeping thread alive")
                import time
                while self._connected and self._running:
                    time.sleep(1)
            
            self.logger.info("SignalR client thread completed")
        except Exception as e:
            self.logger.error(f"SignalR client thread error: {e}")
            self._push_connection_event(ConnectionEvent.ERROR, {"error": str(e)})

    async def start(self):
        """Start the SignalR connection."""
        if self._running:
            self.logger.warning("AsyncSignalRWrapper already running")
            return
        
        self._running = True
        self.logger.info("Starting AsyncSignalRWrapper")
        
        # Start SignalR client in background thread
        self._client_thread = threading.Thread(
            target=self._run_client_thread,
            daemon=True,
            name=f"SignalR-{id(self)}"
        )
        self._client_thread.start()
        
        # Start message processing tasks and track them for cleanup
        self._message_task = asyncio.create_task(self._process_messages())
        self._connection_task = asyncio.create_task(self._process_connection_events())

    async def stop(self):
        """Stop the SignalR connection and cleanup resources."""
        if not self._running:
            return
        
        self.logger.info("Stopping AsyncSignalRWrapper")
        self._running = False
        
        # Stop SignalR client - pysignalr doesn't have a stop method, we'll just let thread finish
        if self._client:
            try:
                # pysignalr client doesn't have explicit stop method - connection will close when thread ends
                self.logger.debug("Signaling SignalR client to stop")
            except Exception as e:
                self.logger.error(f"Error stopping SignalR client: {e}")
        
        # Cancel processing tasks
        for task in [self._message_task, self._connection_task]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Wait for thread to finish
        if self._client_thread and self._client_thread.is_alive():
            self._client_thread.join(timeout=5.0)
            if self._client_thread.is_alive():
                self.logger.warning("SignalR client thread did not stop gracefully")
        
        self._client = None
        self._client_thread = None
        self._message_task = None
        self._connection_task = None
        self._connected = False
        
        self.logger.info("AsyncSignalRWrapper stopped")

    async def send(self, method: str, args: List[Any] = None):
        """Send a message to the SignalR hub."""
        if not self._connected or not self._client:
            self.logger.warning(f"Cannot send message '{method}': not connected")
            return False
        
        try:
            # Run the send operation in a thread to avoid blocking
            # Handle different pysignalr API signatures
            def _send_message():
                if hasattr(self._client, 'send'):
                    try:
                        # Try with arguments parameter (some versions)
                        if args:
                            return self._client.send(method, args)
                        else:
                            return self._client.send(method, [])
                    except TypeError:
                        try:
                            # Try with unpacked args (other versions)
                            if args:
                                return self._client.send(method, *args)
                            else:
                                return self._client.send(method)
                        except TypeError:
                            # Try just method name
                            return self._client.send(method)
                else:
                    self.logger.error("SignalR client has no send method")
                    return False
                    
            await asyncio.get_running_loop().run_in_executor(None, _send_message)
            self.logger.debug(f"Sent message: {method} with args: {args}")
            return True
        except Exception as e:
            self.logger.error(f"Failed to send message '{method}': {e}")
            return False

    async def _process_messages(self):
        """Process incoming SignalR messages."""
        try:
            while self._running:
                try:
                    # Wait for messages with timeout to allow checking _running
                    message = await asyncio.wait_for(
                        self._message_queue.get(), 
                        timeout=1.0
                    )
                    
                    # Dispatch to registered handlers
                    handlers = self._event_handlers.get(message.event_type, [])
                    for handler in handlers:
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(message.event_type, message.data)
                            else:
                                handler(message.event_type, message.data)
                        except Exception as e:
                            self.logger.error(f"Error in message handler for {message.event_type}: {e}")
                    
                except asyncio.TimeoutError:
                    continue  # Check _running flag
                    
        except asyncio.CancelledError:
            self.logger.debug("Message processing task cancelled")
        except Exception as e:
            self.logger.error(f"Error in message processing: {e}")

    async def _process_connection_events(self):
        """Process connection state changes."""
        try:
            while self._running:
                try:
                    event_type, event_data = await asyncio.wait_for(
                        self._connection_event_queue.get(),
                        timeout=1.0
                    )
                    
                    # Dispatch to registered connection handlers
                    handlers = self._connection_handlers.get(event_type, [])
                    for handler in handlers:
                        try:
                            if asyncio.iscoroutinefunction(handler):
                                await handler(event_type, event_data)
                            else:
                                handler(event_type, event_data)
                        except Exception as e:
                            self.logger.error(f"Error in connection handler for {event_type}: {e}")
                    
                except asyncio.TimeoutError:
                    continue  # Check _running flag
                    
        except asyncio.CancelledError:
            self.logger.debug("Connection event processing task cancelled")
        except Exception as e:
            self.logger.error(f"Error in connection event processing: {e}")

    async def reconnect(self):
        """Force a reconnection."""
        async with self._reconnect_guard:
            if not self._running:
                return
            
            self.logger.info("Forcing reconnection...")
            
            # Stop current client
            if self._client:
                try:
                    # pysignalr client doesn't have explicit stop method
                    self.logger.debug("Stopping client during reconnect")
                except Exception as e:
                    self.logger.error(f"Error stopping client during reconnect: {e}")
            
            # Wait a bit for cleanup
            await asyncio.sleep(1.0)
            
            # Restart client thread
            if self._client_thread and self._client_thread.is_alive():
                self._client_thread.join(timeout=5.0)
            
            self._client_thread = threading.Thread(
                target=self._run_client_thread,
                daemon=True,
                name=f"SignalR-Reconnect-{id(self)}"
            )
            self._client_thread.start()

    @property
    def is_connected(self) -> bool:
        """Check if currently connected to SignalR hub."""
        return self._connected

    @property
    def is_running(self) -> bool:
        """Check if the wrapper is running."""
        return self._running