"""Order service for managing TopstepX order lifecycle via REST API."""

import asyncio
import logging
import json
import aiohttp
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

from topstepx_backend.core.event_bus import EventBus, Subscription
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.networking.api_helpers import auth_headers, utc_now
from topstepx_backend.networking.rate_limiter import RateLimiter
from topstepx_backend.data.types import (
    OrderIntent,
    OrderAck,
    OrderStatus,
    OrderType,
    OrderSide,
    TimeInForce,
)
from topstepx_backend.core.topics import (
    order_request_submit,
    order_request_cancel,
    order_request_modify,
    order_ack,
    order_status_update,
    order_fill_update,
    service_error,
)


class OrderService:
    """
    Order management service handling REST API calls and correlation.

    Responsibilities:
    - Subscribe to order.request.* events from strategies
    - Submit orders to TopstepX REST API
    - Correlate orders using customTag for strategy attribution
    - Publish order.ack events with API responses
    - Maintain order state for monitoring and correlation
    """

    def __init__(
        self, event_bus: EventBus, auth_manager: AuthManager, config: TopstepConfig, rate_limiter: RateLimiter
    ):
        """
        Initialize order service.

        Args:
            event_bus: EventBus for subscribing/publishing events
            auth_manager: Authentication manager for API tokens
            config: TopstepX configuration
            rate_limiter: Rate limiter for API calls
        """
        self.event_bus = event_bus
        self.auth_manager = auth_manager
        self.config = config
        self.rate_limiter = rate_limiter
        self.logger = logging.getLogger(__name__)

        # HTTP session
        self._session: Optional[aiohttp.ClientSession] = None

        # Order correlation mapping: customTag -> (strategy_id, order_id, status)
        self._order_map: Dict[str, Dict[str, Any]] = {}
        
        # Idempotency cache: custom_tag -> (order_id, timestamp)
        self._idempotency_cache: Dict[str, tuple[str, datetime]] = {}
        self._idempotency_ttl = timedelta(minutes=5)  # 5 minute TTL

        # Event subscriptions
        self._subscriptions: List[Subscription] = []
        self._consumer_tasks: List[asyncio.Task] = []

        # Service state
        self._running = False

        # Metrics
        self._metrics = {
            "orders_submitted": 0,
            "orders_acknowledged": 0,
            "orders_filled": 0,
            "orders_cancelled": 0,
            "orders_rejected": 0,
            "api_errors": 0,
            "api_retries": 0,
            "duplicate_orders_prevented": 0,
            "last_activity": None,
        }

    async def start(self) -> None:
        """Start the order service."""
        if self._running:
            self.logger.warning("OrderService already running")
            return

        self.logger.info("Starting OrderService...")

        try:
            # Initialize HTTP session
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
                headers={"Content-Type": "application/json"},
            )

            # Validate account is active and tradeable before starting
            await self._validate_active_account()

            # Subscribe to order request events
            await self._setup_subscriptions()

            self._running = True
            self.logger.info("OrderService started successfully")

        except Exception as e:
            self.logger.error(f"Failed to start OrderService: {e}")
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop the order service."""
        if not self._running:
            return

        self.logger.info("Stopping OrderService...")
        self._running = False

        # Cancel consumer tasks
        for task in self._consumer_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._consumer_tasks.clear()

        # Unsubscribe from events
        for subscription in self._subscriptions:
            await self.event_bus.unsubscribe(subscription)
        self._subscriptions.clear()

        # Close HTTP session
        if self._session:
            await self._session.close()
            self._session = None

        self.logger.info("OrderService stopped")

    async def _setup_subscriptions(self) -> None:
        """Set up EventBus subscriptions for order requests."""
        # Subscribe to order submission requests
        submit_subscription = await self.event_bus.subscribe(
            order_request_submit(),
            critical=True,  # Critical subscriber - block if overwhelmed
            maxsize=1000,
        )
        self._subscriptions.append(submit_subscription)

        # Subscribe to order cancellation requests
        cancel_subscription = await self.event_bus.subscribe(
            order_request_cancel(), critical=True, maxsize=500
        )
        self._subscriptions.append(cancel_subscription)

        # Subscribe to order modification requests
        modify_subscription = await self.event_bus.subscribe(
            order_request_modify(), critical=True, maxsize=500
        )
        self._subscriptions.append(modify_subscription)

        # Subscribe to order status updates for correlation
        status_subscription = await self.event_bus.subscribe(
            order_status_update(),
            critical=False,  # Non-critical - we can drop status updates if overwhelmed
            maxsize=2000,
        )
        self._subscriptions.append(status_subscription)

        # Subscribe to order fill updates for correlation
        fill_subscription = await self.event_bus.subscribe(
            order_fill_update(),
            critical=False,  # Non-critical
            maxsize=2000,
        )
        self._subscriptions.append(fill_subscription)

        # Start consumer tasks
        submit_task = asyncio.create_task(
            self._consume_submit_requests(submit_subscription)
        )
        cancel_task = asyncio.create_task(
            self._consume_cancel_requests(cancel_subscription)
        )
        modify_task = asyncio.create_task(
            self._consume_modify_requests(modify_subscription)
        )
        status_task = asyncio.create_task(
            self._consume_status_updates(status_subscription)
        )
        fill_task = asyncio.create_task(self._consume_fill_updates(fill_subscription))

        self._consumer_tasks.extend(
            [submit_task, cancel_task, modify_task, status_task, fill_task]
        )

        self.logger.info("Order service subscriptions established")

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        *,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        **kwargs,
    ) -> tuple[int, str]:
        """Execute HTTP request with retries and exponential backoff."""
        if not self._session:
            raise RuntimeError("HTTP session not initialized")

        for attempt in range(max_retries + 1):
            try:
                async with self._session.request(method, url, **kwargs) as response:
                    text = await response.text()
                    if response.status >= 500:
                        raise RuntimeError(
                            f"HTTP {response.status}: {text}")
                    return response.status, text
            except Exception as e:
                if attempt == max_retries:
                    self.logger.error(
                        f"{method} {url} failed after {attempt + 1} attempts: {e}"
                    )
                    self._metrics["api_errors"] += 1
                    error_event = {
                        "url": url,
                        "method": method,
                        "error": str(e),
                        "retries": attempt,
                    }
                    try:
                        await self.event_bus.publish(
                            service_error("order"), error_event
                        )
                    except Exception:
                        self.logger.exception("Failed to publish error event")
                    raise
                wait = backoff_base * (2**attempt)
                self.logger.warning(
                    f"{method} {url} attempt {attempt + 1} failed: {e}. Retrying in {wait:.2f}s"
                )
                self._metrics["api_retries"] += 1
                await asyncio.sleep(wait)

    async def _consume_submit_requests(self, subscription: Subscription) -> None:
        """Consumer for order submission requests."""
        self.logger.info("Order submit consumer started")

        try:
            async for topic, payload in subscription:
                if not self._running:
                    break

                try:
                    await self._handle_submit_request(payload)
                except Exception as e:
                    self.logger.error(f"Error handling submit request: {e}")
                    self._metrics["api_errors"] += 1

        except asyncio.CancelledError:
            self.logger.debug("Submit consumer cancelled")
        except Exception as e:
            self.logger.error(f"Submit consumer error: {e}")

    async def _consume_cancel_requests(self, subscription: Subscription) -> None:
        """Consumer for order cancellation requests."""
        self.logger.info("Order cancel consumer started")

        try:
            async for topic, payload in subscription:
                if not self._running:
                    break

                try:
                    await self._handle_cancel_request(payload)
                except Exception as e:
                    self.logger.error(f"Error handling cancel request: {e}")
                    self._metrics["api_errors"] += 1

        except asyncio.CancelledError:
            self.logger.debug("Cancel consumer cancelled")
        except Exception as e:
            self.logger.error(f"Cancel consumer error: {e}")

    async def _consume_modify_requests(self, subscription: Subscription) -> None:
        """Consumer for order modification requests."""
        self.logger.info("Order modify consumer started")

        try:
            async for topic, payload in subscription:
                if not self._running:
                    break

                try:
                    await self._handle_modify_request(payload)
                except Exception as e:
                    self.logger.error(f"Error handling modify request: {e}")
                    self._metrics["api_errors"] += 1

        except asyncio.CancelledError:
            self.logger.debug("Modify consumer cancelled")
        except Exception as e:
            self.logger.error(f"Modify consumer error: {e}")

    async def _consume_status_updates(self, subscription: Subscription) -> None:
        """Consumer for order status updates from SignalR."""
        self.logger.info("Order status consumer started")

        try:
            async for topic, payload in subscription:
                if not self._running:
                    break

                try:
                    await self._handle_status_update(payload)
                except Exception as e:
                    self.logger.error(f"Error handling status update: {e}")

        except asyncio.CancelledError:
            self.logger.debug("Status consumer cancelled")
        except Exception as e:
            self.logger.error(f"Status consumer error: {e}")

    async def _consume_fill_updates(self, subscription: Subscription) -> None:
        """Consumer for order fill updates from SignalR."""
        self.logger.info("Order fill consumer started")

        try:
            async for topic, payload in subscription:
                if not self._running:
                    break

                try:
                    await self._handle_fill_update(payload)
                except Exception as e:
                    self.logger.error(f"Error handling fill update: {e}")

        except asyncio.CancelledError:
            self.logger.debug("Fill consumer cancelled")
        except Exception as e:
            self.logger.error(f"Fill consumer error: {e}")

    async def _handle_submit_request(self, payload: Dict[str, Any]) -> None:
        """Handle order submission request."""
        try:
            # Parse order intent
            intent = self._parse_order_intent(payload)
            if not intent:
                return

            # Check idempotency cache if custom_tag is provided
            if intent.custom_tag:
                # Clean expired entries
                now = utc_now()
                expired_tags = [
                    tag for tag, (_, timestamp) in self._idempotency_cache.items()
                    if now - timestamp > self._idempotency_ttl
                ]
                for tag in expired_tags:
                    del self._idempotency_cache[tag]
                
                # Check if this order was recently submitted
                if intent.custom_tag in self._idempotency_cache:
                    cached_order_id, cached_timestamp = self._idempotency_cache[intent.custom_tag]
                    self.logger.warning(
                        f"Duplicate order prevented: {intent.custom_tag} already submitted as order {cached_order_id}"
                    )
                    self._metrics["duplicate_orders_prevented"] += 1
                    
                    # Return cached acknowledgment
                    ack = OrderAck(
                        order_id=cached_order_id,
                        custom_tag=intent.custom_tag,
                        status=OrderStatus.ACCEPTED,
                        timestamp=utc_now(),
                        error_message="Duplicate order (idempotency check)",
                    )
                    await self.event_bus.publish(order_ack(), ack.to_dict())
                    return

            # Build API payload
            api_payload = self._build_api_payload(intent)

            # Apply rate limiting for order placement
            await self.rate_limiter.acquire("/api/Order/place")

            # Submit to TopstepX API
            response = await self._submit_order_api(api_payload)

            # Process API response
            await self._process_submit_response(intent, response)
            
            # Store in idempotency cache if successful and has custom_tag
            if intent.custom_tag and response.get("success", False):
                order_id = response.get("orderId", -1)
                self._idempotency_cache[intent.custom_tag] = (order_id, utc_now())

            self._metrics["orders_submitted"] += 1
            self._metrics["last_activity"] = utc_now()

        except Exception as e:
            self.logger.error(f"Failed to handle submit request: {e}")
            # Publish error acknowledgment
            if "custom_tag" in payload:
                error_ack = OrderAck(
                    order_id=-1,
                    custom_tag=payload["custom_tag"],
                    status=OrderStatus.REJECTED,
                    timestamp=utc_now(),
                    error_message=str(e),
                )
                await self.event_bus.publish(order_ack(), error_ack.to_dict())

    async def _handle_cancel_request(self, payload: Dict[str, Any]) -> None:
        """Handle order cancellation request."""
        try:
            self.logger.info(f"Processing cancel request: {payload}")

            # Extract required fields
            order_id = payload.get("order_id")
            custom_tag = payload.get("custom_tag", "")

            if not order_id:
                self.logger.error("Cancel request missing order_id")
                await self._publish_cancel_ack(
                    custom_tag, success=False, error_message="Missing order_id"
                )
                return

            # Prepare API request - ProjectX Gateway API cancel expects flat JSON
            headers = auth_headers(self.auth_manager.get_token())
            url = f"{self.config.projectx_base_url}/api/Order/cancel"
            cancel_payload = {
                "orderId": int(order_id),  # Ensure integer
                "accountId": int(self.config.account_id),  # Required field
            }

            self.logger.debug(f"Sending cancel request to {url}: {cancel_payload}")

            # Make API call
            status, response_text = await self._request_with_retry(
                "POST", url, json=cancel_payload, headers=headers
            )

            if status in [200, 204]:
                self.logger.info(f"Order {order_id} cancelled successfully")
                self._metrics["orders_cancelled"] += 1

                # Publish success ack
                await self._publish_cancel_ack(
                    custom_tag, success=True, order_id=order_id
                )

                # Publish status update
                status_update = {
                    "order_id": order_id,
                    "custom_tag": custom_tag,
                    "status": "CANCELLED",
                    "timestamp": utc_now().isoformat(),
                }
                await self.event_bus.publish(order_status_update(), status_update)

            else:
                error_msg = f"Cancel failed: {status} - {response_text}"
                self.logger.error(error_msg)
                self._metrics["api_errors"] += 1

                await self._publish_cancel_ack(
                    custom_tag,
                    success=False,
                    error_message=error_msg,
                    order_id=order_id,
                )

        except Exception as e:
            error_msg = f"Exception during cancel request: {e}"
            self.logger.error(error_msg)
            self._metrics["api_errors"] += 1

            await self._publish_cancel_ack(
                payload.get("custom_tag", ""),
                success=False,
                error_message=error_msg,
                order_id=payload.get("order_id"),
            )

    async def _publish_cancel_ack(
        self,
        custom_tag: str,
        success: bool,
        error_message: str = None,
        order_id: str = None,
    ) -> None:
        """Publish order cancel acknowledgment."""
        try:
            ack = OrderAck(
                custom_tag=custom_tag,
                success=success,
                order_id=order_id,
                timestamp=utc_now(),
                error_message=error_message,
            )
            await self.event_bus.publish(order_ack(), ack.to_dict())
            self._metrics["orders_acknowledged"] += 1
            self._metrics["last_activity"] = utc_now()

        except Exception as e:
            self.logger.error(f"Failed to publish cancel ack: {e}")

    async def _validate_active_account(self) -> None:
        """Validate that the configured account ID is active and can trade.

        Raises:
            RuntimeError: If account is not found, inactive, or cannot trade
        """
        try:
            self.logger.info(f"Validating account ID: {self.config.account_id}")

            # Get authentication headers
            headers = auth_headers(self.auth_manager.get_token())
            url = f"{self.config.projectx_base_url}/api/Account/search"
            payload = {"onlyActiveAccounts": True}

            status, response_text = await self._request_with_retry(
                "POST", url, json=payload, headers=headers
            )
            if status != 200:
                raise RuntimeError(
                    f"Account search failed: {status} - {response_text}"
                )

            data = json.loads(response_text) if response_text else {}
            accounts = data.get("accounts", [])

            if not accounts:
                raise RuntimeError("No active accounts found for this user")

            # Check if configured account exists and is valid
            target_account_id = int(self.config.account_id)
            account_found = False

            for account in accounts:
                account_id = account.get("id")
                account_name = account.get("name", "Unknown")
                can_trade = account.get("canTrade", False)
                is_visible = account.get("isVisible", False)

                if account_id == target_account_id:
                    account_found = True

                    if not can_trade:
                        raise RuntimeError(
                            f"Account {target_account_id} ({account_name}) exists but canTrade=False. "
                            "Trading is disabled for this account."
                        )

                    if not is_visible:
                        raise RuntimeError(
                            f"Account {target_account_id} ({account_name}) exists but isVisible=False. "
                            "Account is not visible/accessible."
                        )

                    self.logger.info(
                        f"✓ Account {target_account_id} ({account_name}) validated: "
                        f"canTrade={can_trade}, isVisible={is_visible}"
                    )
                    return

                if not account_found:
                    available_accounts = [
                        f"{acc.get('id')} ({acc.get('name', 'Unknown')})"
                        for acc in accounts
                    ]
                    raise RuntimeError(
                        f"Account {target_account_id} not found in active accounts. "
                        f"Available accounts: {', '.join(available_accounts)}"
                    )

        except Exception as e:
            self.logger.error(f"Account validation failed: {e}")
            raise RuntimeError(f"Account validation failed: {e}")

    async def _handle_modify_request(self, payload: Dict[str, Any]) -> None:
        """Handle order modification request."""
        try:
            self.logger.info(f"Processing modify request: {payload}")

            # Extract required fields
            order_id = payload.get("order_id")
            custom_tag = payload.get("custom_tag", "")

            if not order_id:
                self.logger.error("Modify request missing order_id")
                await self._publish_modify_ack(
                    custom_tag, success=False, error_message="Missing order_id"
                )
                return

            # Build modification payload
            headers = auth_headers(self.auth_manager.get_token())
            url = f"{self.config.projectx_base_url}/api/Order/modify"
            modify_payload = {
                "accountId": int(self.config.account_id),
                "orderId": int(order_id),
            }

            # Add modifiable fields (only include fields that are provided)
            if "size" in payload and payload["size"] is not None:
                modify_payload["size"] = int(payload["size"])

            if "limit_price" in payload and payload["limit_price"] is not None:
                modify_payload["limitPrice"] = float(payload["limit_price"])
            elif payload.get("limit_price") is None and "limit_price" in payload:
                # Explicitly set to null to remove limit price
                modify_payload["limitPrice"] = None

            if "stop_price" in payload and payload["stop_price"] is not None:
                modify_payload["stopPrice"] = float(payload["stop_price"])
            elif payload.get("stop_price") is None and "stop_price" in payload:
                modify_payload["stopPrice"] = None

            if "trail_price" in payload and payload["trail_price"] is not None:
                modify_payload["trailPrice"] = float(payload["trail_price"])
            elif payload.get("trail_price") is None and "trail_price" in payload:
                modify_payload["trailPrice"] = None

            # Ensure at least one field is being modified
            modifiable_fields = ["size", "limitPrice", "stopPrice", "trailPrice"]
            if not any(field in modify_payload for field in modifiable_fields):
                error_msg = "No modifiable fields provided (size, limit_price, stop_price, trail_price)"
                self.logger.error(error_msg)
                await self._publish_modify_ack(
                    custom_tag,
                    success=False,
                    error_message=error_msg,
                    order_id=order_id,
                )
                return

            self.logger.debug(f"Sending modify request to {url}: {modify_payload}")

            # Make API call
            status, response_text = await self._request_with_retry(
                "POST", url, json=modify_payload, headers=headers
            )

            if status == 200:
                # Parse response to check for success
                try:
                    response_data = json.loads(response_text) if response_text else {}
                    success = response_data.get(
                        "success", True
                    )  # Default to True for 200
                    error_message = response_data.get("errorMessage")

                    if success:
                        self.logger.info(f"Order {order_id} modified successfully")
                        await self._publish_modify_ack(
                            custom_tag, success=True, order_id=order_id
                        )

                        # Update internal order tracking if we have it
                        if custom_tag and custom_tag in self._order_map:
                            self._order_map[custom_tag]["last_update"] = utc_now()
                            self._order_map[custom_tag]["modified"] = True
                            # Update size if it was modified
                            if "size" in modify_payload:
                                self._order_map[custom_tag]["size"] = (
                                    modify_payload["size"]
                                )
                    else:
                        error_msg = f"Modify rejected by API: {error_message}"
                        self.logger.warning(error_msg)
                        await self._publish_modify_ack(
                            custom_tag,
                            success=False,
                            error_message=error_msg,
                            order_id=order_id,
                        )

                except Exception:
                    # Handle case where response is 200 but not JSON (some APIs return plain text success)
                    self.logger.info(
                        f"Order {order_id} modified successfully (non-JSON response)"
                    )
                    await self._publish_modify_ack(
                        custom_tag, success=True, order_id=order_id
                    )

            else:
                error_msg = f"Modify failed: {status} - {response_text}"
                self.logger.error(error_msg)
                self._metrics["api_errors"] += 1
                await self._publish_modify_ack(
                    custom_tag,
                    success=False,
                    error_message=error_msg,
                    order_id=order_id,
                )

        except Exception as e:
            error_msg = f"Exception during modify request: {e}"
            self.logger.error(error_msg)
            self._metrics["api_errors"] += 1

            await self._publish_modify_ack(
                payload.get("custom_tag", ""),
                success=False,
                error_message=error_msg,
                order_id=payload.get("order_id"),
            )

    async def _publish_modify_ack(
        self,
        custom_tag: str,
        success: bool,
        error_message: str = None,
        order_id: str = None,
    ) -> None:
        """Publish order modification acknowledgment."""
        try:
            ack = OrderAck(
                custom_tag=custom_tag,
                success=success,
                order_id=order_id,
                timestamp=utc_now(),
                error_message=error_message,
            )
            await self.event_bus.publish(order_ack(), ack.to_dict())
            self._metrics["orders_acknowledged"] += 1
            self._metrics["last_activity"] = utc_now()

        except Exception as e:
            self.logger.error(f"Failed to publish modify ack: {e}")

    async def _handle_status_update(self, payload: Dict[str, Any]) -> None:
        """Handle order status update from SignalR."""
        try:
            custom_tag = payload.get("custom_tag")
            status = payload.get("status")

            if custom_tag and custom_tag in self._order_map:
                # Update order state
                self._order_map[custom_tag]["status"] = status
                self._order_map[custom_tag]["last_update"] = utc_now()

                self.logger.debug(f"Updated order status: {custom_tag} -> {status}")

        except Exception as e:
            self.logger.error(f"Error processing status update: {e}")

    async def _handle_fill_update(self, payload: Dict[str, Any]) -> None:
        """Handle order fill update from SignalR."""
        try:
            custom_tag = payload.get("custom_tag")

            if custom_tag and custom_tag in self._order_map:
                # Update metrics
                self._metrics["orders_filled"] += 1
                self._metrics["last_activity"] = utc_now()

                # Update order state
                self._order_map[custom_tag]["filled"] = True
                self._order_map[custom_tag]["fill_price"] = payload.get("price")
                self._order_map[custom_tag]["fill_size"] = payload.get("size")
                self._order_map[custom_tag]["last_update"] = utc_now()

                self.logger.info(f"Order filled: {custom_tag} @ {payload.get('price')}")

        except Exception as e:
            self.logger.error(f"Error processing fill update: {e}")

    def _parse_order_intent(self, payload: Dict[str, Any]) -> Optional[OrderIntent]:
        """Parse order intent from payload."""
        try:
            return OrderIntent(
                strategy_id=payload["strategy_id"],
                account_id=payload["account_id"],
                contract_id=payload["contract_id"],
                type=OrderType(payload["type"]),
                side=OrderSide(payload["side"]),
                size=payload["size"],
                limit_price=payload.get("limit_price"),
                stop_price=payload.get("stop_price"),
                trail_price=payload.get("trail_price"),
                time_in_force=TimeInForce(payload.get("time_in_force", "Day")),
                custom_tag=payload.get("custom_tag"),
            )
        except Exception as e:
            self.logger.error(f"Failed to parse order intent: {e}")
            return None

    def _validate_order_intent(self, intent: OrderIntent) -> None:
        """
        Validate order intent before building payload.
        Raises ValueError for invalid orders.
        """
        
        # Validate required fields per order type
        if intent.type == OrderType.LIMIT:
            if intent.limit_price is None:
                raise ValueError("LIMIT order requires limit_price")
        
        elif intent.type == OrderType.STOP:
            if intent.stop_price is None:
                raise ValueError("STOP order requires stop_price")
        
        elif intent.type == OrderType.TRAILING_STOP:
            if intent.trail_price is None:
                raise ValueError("TRAILING_STOP order requires trail_price")
        
        elif intent.type in [OrderType.JOIN_BID, OrderType.JOIN_ASK]:
            # These order types should not have price fields
            if intent.limit_price is not None or intent.stop_price is not None:
                raise ValueError(f"{intent.type.value} orders cannot have price fields")

    def _build_api_payload(self, intent: OrderIntent) -> Dict[str, Any]:
        """
        Build ProjectX Gateway API payload from order intent.
        Uses ProjectX format: 0=BUY/1=SELL, integer types, 'size' parameter.
        """
        # Validate order intent first
        self._validate_order_intent(intent)
        
        # Map order type to ProjectX integer values
        # No STOP_LIMIT mapping - will fail fast
        type_mapping = {
            OrderType.LIMIT: 1,
            OrderType.MARKET: 2,
            OrderType.STOP: 4,
            OrderType.TRAILING_STOP: 5,
            OrderType.JOIN_BID: 6,
            OrderType.JOIN_ASK: 7,
        }

        # Map order side to ProjectX integer values (0=BUY, 1=SELL)
        side_mapping = {
            OrderSide.BUY: 0,  # ProjectX format: 0=Bid/Buy
            OrderSide.SELL: 1,  # ProjectX format: 1=Ask/Sell
        }

        # Get mapped values - fail fast if not found
        if intent.type not in type_mapping:
            raise ValueError(f"Unsupported order type: {intent.type}")
        
        if intent.side not in side_mapping:
            raise ValueError(f"Unsupported order side: {intent.side}")

        payload = {
            "accountId": int(intent.account_id),  # ProjectX requires integer
            "contractId": int(intent.contract_id) if intent.contract_id.isdigit() else intent.contract_id,
            "type": type_mapping[intent.type],  # No fallback - fail fast
            "side": side_mapping[intent.side],  # No fallback - fail fast
            "size": intent.size,  # ProjectX uses 'size' not 'quantity'
        }

        # Add custom tag for correlation (ProjectX format)
        if intent.custom_tag:
            payload["customTag"] = intent.custom_tag

        # Add price fields based on order type
        if intent.type == OrderType.LIMIT:
            payload["limitPrice"] = float(intent.limit_price)

        elif intent.type == OrderType.STOP:
            payload["stopPrice"] = float(intent.stop_price)

        elif intent.type == OrderType.TRAILING_STOP:
            payload["trailPrice"] = float(intent.trail_price)  # ProjectX format

        # Note: TimeInForce not supported in ProjectX Gateway API
        # JOIN_BID and JOIN_ASK don't need price fields

        return payload

    async def _submit_order_api(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Submit order to TopstepX REST API."""
        if not self._session:
            raise RuntimeError("HTTP session not initialized")

        # Get authentication headers
        token = self.auth_manager.get_token()
        headers = auth_headers(token)

        # Submit order to ProjectX Gateway API
        url = f"{self.config.projectx_base_url}/api/Order/place"

        status, text = await self._request_with_retry(
            "POST",
            url,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10, connect=3),
        )
        if status != 200:
            raise RuntimeError(f"Order API error {status}: {text}")

        return json.loads(text) if text else {}

    async def _process_submit_response(
        self, intent: OrderIntent, response: Dict[str, Any]
    ) -> None:
        """Process API response and publish acknowledgment."""
        try:
            success = response.get("success", False)
            order_id = response.get("orderId", -1)
            error_message = response.get("errorMessage")

            # Determine status
            if success:
                status = OrderStatus.ACCEPTED
                self._metrics["orders_acknowledged"] += 1
            else:
                status = OrderStatus.REJECTED
                self._metrics["orders_rejected"] += 1

            # Store order correlation
            if intent.custom_tag:
                self._order_map[intent.custom_tag] = {
                    "strategy_id": intent.strategy_id,
                    "order_id": order_id,
                    "status": status.value,
                    "account_id": intent.account_id,
                    "contract_id": intent.contract_id,
                    "size": intent.size,
                    "side": intent.side.value,
                    "type": intent.type.value,
                    "submitted_at": utc_now(),
                    "last_update": utc_now(),
                    "filled": False,
                }

            # Publish acknowledgment
            ack = OrderAck(
                order_id=order_id,
                custom_tag=intent.custom_tag or "",
                status=status,
                timestamp=utc_now(),
                error_message=error_message,
            )

            await self.event_bus.publish(order_ack(), ack.to_dict())

            if success:
                self.logger.info(
                    f"Order acknowledged: {intent.custom_tag} -> ID {order_id}"
                )
            else:
                self.logger.warning(
                    f"Order rejected: {intent.custom_tag} -> {error_message}"
                )

        except Exception as e:
            self.logger.error(f"Failed to process submit response: {e}")

    def get_order_state(self, custom_tag: str) -> Optional[Dict[str, Any]]:
        """Get order state by custom tag."""
        return self._order_map.get(custom_tag)

    def get_strategy_orders(self, strategy_id: str) -> List[Dict[str, Any]]:
        """Get all orders for a specific strategy."""
        return [
            order
            for order in self._order_map.values()
            if order.get("strategy_id") == strategy_id
        ]

    def get_metrics(self) -> Dict[str, Any]:
        """Get order service metrics."""
        return {
            **self._metrics,
            "running": self._running,
            "active_orders": len(self._order_map),
            "session_active": self._session is not None,
        }

    def get_all_orders(self) -> Dict[str, Dict[str, Any]]:
        """Get all tracked orders."""
        return self._order_map.copy()

    async def place_order(
        self,
        accountId: int,
        contractId: str, 
        type: int,
        side: int,
        size: int,
        limitPrice: Optional[float] = None,
        stopPrice: Optional[float] = None,
        trailPrice: Optional[float] = None,
        customTag: Optional[str] = None,
        linkedOrderId: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Direct order placement method for testing/external use.
        
        This method bypasses the event bus and places orders directly via API.
        Parameters match TopstepX API specification exactly.
        
        Args:
            accountId: Account ID
            contractId: Contract ID
            type: Order type (1=Limit, 2=Market, 4=Stop, 5=TrailingStop, 6=JoinBid, 7=JoinAsk)
            side: Order side (0=Bid/Buy, 1=Ask/Sell)  
            size: Order size
            limitPrice: Limit price (required for Limit orders)
            stopPrice: Stop price (required for Stop orders)
            trailPrice: Trail price (required for Trailing Stop orders)
            customTag: Custom tag for correlation
            linkedOrderId: Linked order ID
            
        Returns:
            Dict with success, orderId, errorCode, errorMessage
        """
        if not self._running:
            return {
                "success": False,
                "orderId": -1,
                "errorCode": 500,
                "errorMessage": "OrderService not running"
            }
            
        try:
            # Apply rate limiting
            await self.rate_limiter.acquire("/api/Order/place")
            
            # Build API payload according to TopstepX specification
            payload = {
                "accountId": accountId,
                "contractId": contractId,
                "type": type,
                "side": side,
                "size": size
            }
            
            # Add optional parameters
            if limitPrice is not None:
                payload["limitPrice"] = limitPrice
            if stopPrice is not None:
                payload["stopPrice"] = stopPrice
            if trailPrice is not None:
                payload["trailPrice"] = trailPrice
            if customTag is not None:
                payload["customTag"] = customTag
            if linkedOrderId is not None:
                payload["linkedOrderId"] = linkedOrderId
                
            # Submit to API
            response = await self._submit_order_api(payload)
            
            # Update metrics
            self._metrics["orders_submitted"] += 1
            self._metrics["last_activity"] = utc_now()
            
            if response.get("success", False):
                self._metrics["orders_acknowledged"] += 1
            else:
                self._metrics["orders_rejected"] += 1
                
            return response
            
        except Exception as e:
            self.logger.error(f"Direct order placement failed: {e}")
            self._metrics["api_errors"] += 1
            return {
                "success": False,
                "orderId": -1,
                "errorCode": 500,
                "errorMessage": str(e)
            }
