"""Polling-based bar service for TopstepX trading system.

Replaces SignalR real-time data with 30-second History API polling.
Supports subscribable markets with finalized and partial bar publishing.
"""

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, TYPE_CHECKING
from dataclasses import dataclass

import aiohttp

from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.core.service import Service
from topstepx_backend.core.topics import market_bar, persist_bar_save
from topstepx_backend.data.types import Bar
from topstepx_backend.networking.api_helpers import (
    auth_headers,
    format_iso_utc,
    utc_now,
)
from topstepx_backend.networking.rate_limiter import RateLimiter

if TYPE_CHECKING:
    from topstepx_backend.services.market_subscription_service import MarketSubscriptionService


@dataclass
class MarketSubscription:
    """Configuration for a subscribed market."""
    contract_id: str
    enabled: bool = True
    last_poll_time: Optional[datetime] = None
    poll_failures: int = 0


class PollingBarService(Service):
    """
    30-second polling service for finalized and partial 1-minute bars.
    
    Features:
    - Subscribable markets with dynamic management
    - 30-second polling with includePartialBar: true
    - Finalized 1m bars → TimeframeAggregator
    - Partial 1m bars → TradingView charts
    - Error handling with exponential backoff
    - Health monitoring integration
    """

    def __init__(
        self,
        config: TopstepConfig,
        auth_manager: AuthManager,
        rate_limiter: RateLimiter,
        event_bus: Optional[EventBus] = None,
        market_subscription_service: Optional['MarketSubscriptionService'] = None,
        initial_subscriptions: Optional[List[str]] = None,
    ):
        super().__init__()
        self.config = config
        self.auth_manager = auth_manager
        self.rate_limiter = rate_limiter
        self.event_bus = event_bus
        self.market_subscription_service = market_subscription_service
        self.logger = logging.getLogger(__name__)

        # Market subscriptions: {contract_id: MarketSubscription}
        self._subscriptions: Dict[str, MarketSubscription] = {}
        
        # Initialize with default subscriptions if provided
        if initial_subscriptions:
            for contract_id in initial_subscriptions:
                self._subscriptions[contract_id] = MarketSubscription(contract_id=contract_id)
        
        # EventBus subscription tasks for dynamic updates
        self._event_tasks = []

        # Service state
        self._session: Optional[aiohttp.ClientSession] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._poll_interval = 30.0  # 30 seconds

        # Performance metrics
        self._metrics = {
            "polls_completed": 0,
            "bars_published": 0,
            "partial_bars_published": 0,
            "poll_errors": 0,
            "last_poll_time": None,
            "subscribed_markets": 0,
            "active_markets": 0,
        }
        self._metrics_lock = asyncio.Lock()

        # Error handling
        self._max_consecutive_failures = 3
        self._backoff_multiplier = 2.0
        self._max_backoff_delay = 300.0  # 5 minutes max

    async def start(self):
        """Start the polling bar service."""
        if self._running:
            self.logger.warning("PollingBarService already running")
            return

        self._running = True
        
        # Create HTTP session
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "TopstepX-PollingBarService/1.0"}
        )
        
        # Sync with MarketSubscriptionService if available
        if self.market_subscription_service:
            await self._sync_with_subscription_service()
            await self._setup_event_handlers()
        
        # Start polling task
        self._polling_task = asyncio.create_task(self._polling_loop())

        async with self._metrics_lock:
            self._metrics["subscribed_markets"] = len(self._subscriptions)
            self._metrics["active_markets"] = len([s for s in self._subscriptions.values() if s.enabled])

        self.logger.info(
            f"PollingBarService started with {len(self._subscriptions)} subscribed markets, "
            f"polling every {self._poll_interval}s"
        )

    async def stop(self):
        """Stop the polling bar service."""
        if not self._running:
            return

        self._running = False

        # Cancel polling task
        if self._polling_task:
            self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None

        # Close HTTP session
        if self._session:
            await self._session.close()
            self._session = None
        
        # Cancel event consumer tasks
        if self._event_tasks:
            for task in self._event_tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._event_tasks.clear()

        self.logger.info("PollingBarService stopped")

    async def _polling_loop(self):
        """Main polling loop with error handling and backoff."""
        self.logger.info("Starting polling loop...")
        consecutive_failures = 0
        base_delay = self._poll_interval

        while self._running:
            try:
                start_time = utc_now()
                
                # Poll all active subscriptions
                await self._poll_all_markets()
                
                # Update success metrics
                async with self._metrics_lock:
                    self._metrics["polls_completed"] += 1
                    self._metrics["last_poll_time"] = start_time

                # Reset failure tracking on success
                consecutive_failures = 0
                current_delay = base_delay

                # Calculate actual delay accounting for processing time
                processing_time = (utc_now() - start_time).total_seconds()
                sleep_time = max(0, current_delay - processing_time)

                self.logger.debug(
                    f"Poll completed in {processing_time:.2f}s, "
                    f"sleeping {sleep_time:.2f}s until next poll"
                )

                if sleep_time > 0:
                    await asyncio.sleep(sleep_time)

            except asyncio.CancelledError:
                self.logger.info("Polling loop cancelled")
                break
            except Exception as e:
                consecutive_failures += 1
                async with self._metrics_lock:
                    self._metrics["poll_errors"] += 1

                self.logger.error(
                    f"Polling error (attempt {consecutive_failures}): {e}"
                )

                # Apply exponential backoff
                if consecutive_failures >= self._max_consecutive_failures:
                    backoff_delay = min(
                        base_delay * (self._backoff_multiplier ** (consecutive_failures - self._max_consecutive_failures)),
                        self._max_backoff_delay
                    )
                    self.logger.warning(
                        f"Multiple failures, backing off for {backoff_delay:.1f}s"
                    )
                    await asyncio.sleep(backoff_delay)
                else:
                    await asyncio.sleep(base_delay)

        self.logger.info("Polling loop stopped")

    async def _poll_all_markets(self):
        """Poll all active market subscriptions."""
        active_subscriptions = [
            sub for sub in self._subscriptions.values() 
            if sub.enabled
        ]

        if not active_subscriptions:
            self.logger.debug("No active market subscriptions")
            return

        # Poll markets concurrently with rate limiting
        tasks = []
        for subscription in active_subscriptions:
            task = asyncio.create_task(self._poll_market(subscription))
            tasks.append(task)

        # Wait for all polls to complete
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any individual market failures
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                contract_id = active_subscriptions[i].contract_id
                self.logger.warning(f"Failed to poll {contract_id}: {result}")

    async def _poll_market(self, subscription: MarketSubscription):
        """Poll a single market for bar data."""
        contract_id = subscription.contract_id
        
        try:
            # Apply rate limiting with correct endpoint
            await self.rate_limiter.acquire("/api/History/retrieveBars")

            # Get authentication token
            token = self.auth_manager.get_token()

            # Calculate polling window - last 2 minutes to ensure we catch everything
            end_time = utc_now()
            start_time = end_time - timedelta(minutes=2)

            # Build History API request using correct POST endpoint
            url = f"{self.config.projectx_base_url}/api/History/retrieveBars"
            headers = auth_headers(token)
            
            # Use JSON body with correct parameter types per ProjectX Gateway API
            payload = {
                "contractId": contract_id,
                "live": self.config.live_mode,  # Use config setting
                "startTime": format_iso_utc(start_time),
                "endTime": format_iso_utc(end_time),
                "unit": 2,  # Minute
                "unitNumber": 1,  # 1 minute
                "limit": 1000,
                "includePartialBar": True  # Boolean, not string
            }

            # Make API request using POST with JSON body
            async with self._session.post(url, headers=headers, json=payload) as response:
                if response.status == 401:
                    # Token expired - refresh and retry once
                    await self.auth_manager.validate_token()  # This refreshes if needed
                    headers = auth_headers(self.auth_manager.get_token())
                    async with self._session.post(url, headers=headers, json=payload) as retry_response:
                        if retry_response.status != 200:
                            error_text = await retry_response.text()
                            raise Exception(f"HTTP {retry_response.status}: {error_text}")
                        data = await retry_response.json()
                elif response.status != 200:
                    error_text = await response.text()
                    raise Exception(f"HTTP {response.status}: {error_text}")
                else:
                    data = await response.json()
                
            # Process bars from response
            await self._process_bar_data(contract_id, data)
            
            # Update subscription state
            subscription.last_poll_time = utc_now()
            subscription.poll_failures = 0

            self.logger.debug(f"Successfully polled {contract_id}")

        except Exception as e:
            subscription.poll_failures += 1
            self.logger.error(f"Error polling {contract_id}: {e}")
            
            # Disable subscription after too many failures
            if subscription.poll_failures >= 5:
                subscription.enabled = False
                self.logger.warning(
                    f"Disabled subscription for {contract_id} after {subscription.poll_failures} failures"
                )

    async def _process_bar_data(self, contract_id: str, data: Dict[str, Any]):
        """Process bar data from History API response."""
        bars = data.get("bars", [])
        partial_bar = data.get("partialBar")

        # Process completed bars
        for bar_data in bars:
            bar = self._create_bar_from_api_data(contract_id, bar_data, is_partial=False)
            await self._publish_bar(bar, is_partial=False)

        # Process partial bar if present
        if partial_bar:
            bar = self._create_bar_from_api_data(contract_id, partial_bar, is_partial=True)
            await self._publish_bar(bar, is_partial=True)

    def _create_bar_from_api_data(self, contract_id: str, bar_data: Dict[str, Any], is_partial: bool) -> Bar:
        """Create a Bar object from History API data."""
        # Parse timestamp - API returns ISO format
        timestamp_str = bar_data["timestamp"]
        if timestamp_str.endswith("Z"):
            timestamp = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        else:
            timestamp = datetime.fromisoformat(timestamp_str)
            # Ensure timezone-aware datetime using existing core utility
            if not timestamp.tzinfo:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

        return Bar(
            timestamp=timestamp,
            contract_id=contract_id,
            timeframe="1m",
            open=float(bar_data["open"]),
            high=float(bar_data["high"]),
            low=float(bar_data["low"]),
            close=float(bar_data["close"]),
            volume=int(bar_data.get("volume", 0)),
            source="polling",
            revision=1
        )

    async def _publish_bar(self, bar: Bar, is_partial: bool):
        """Publish bar using EventBus with dual-publish pattern."""
        if not self.event_bus:
            return

        try:
            if is_partial:
                # Publish partial bar for charts
                topic = f"{market_bar(bar.contract_id, bar.timeframe)}_partial"
                await self.event_bus.publish(topic, bar.to_dict())
                
                async with self._metrics_lock:
                    self._metrics["partial_bars_published"] += 1

                self.logger.debug(f"Published partial {bar.timeframe} bar for {bar.contract_id}")
            else:
                # Publish finalized bar with dual-publish pattern
                # 1. Domain event for TimeframeAggregator and strategies
                market_topic = market_bar(bar.contract_id, bar.timeframe)
                await self.event_bus.publish(market_topic, bar.to_dict())

                # 2. Infrastructure command for PersistenceService
                await self.event_bus.publish(persist_bar_save(), bar.to_dict())

                async with self._metrics_lock:
                    self._metrics["bars_published"] += 1

                self.logger.debug(f"Published finalized {bar.timeframe} bar for {bar.contract_id}")

        except Exception as e:
            self.logger.error(f"Failed to publish bar for {bar.contract_id}: {e}")

    # Market subscription management methods

    async def subscribe_market(self, contract_id: str) -> bool:
        """Add a market to the subscription list."""
        if contract_id in self._subscriptions:
            # Re-enable if disabled
            self._subscriptions[contract_id].enabled = True
            self._subscriptions[contract_id].poll_failures = 0
            self.logger.info(f"Re-enabled subscription for {contract_id}")
        else:
            # Add new subscription
            self._subscriptions[contract_id] = MarketSubscription(contract_id=contract_id)
            self.logger.info(f"Added new subscription for {contract_id}")

        async with self._metrics_lock:
            self._metrics["subscribed_markets"] = len(self._subscriptions)
            self._metrics["active_markets"] = len([s for s in self._subscriptions.values() if s.enabled])

        return True

    async def unsubscribe_market(self, contract_id: str) -> bool:
        """Remove a market from the subscription list."""
        if contract_id in self._subscriptions:
            del self._subscriptions[contract_id]
            
            async with self._metrics_lock:
                self._metrics["subscribed_markets"] = len(self._subscriptions)
                self._metrics["active_markets"] = len([s for s in self._subscriptions.values() if s.enabled])
                
            self.logger.info(f"Unsubscribed from {contract_id}")
            return True
        return False

    async def get_subscribed_markets(self) -> List[str]:
        """Get list of all subscribed markets."""
        return list(self._subscriptions.keys())

    async def get_active_markets(self) -> List[str]:
        """Get list of currently active (enabled) markets."""
        return [
            contract_id for contract_id, sub in self._subscriptions.items()
            if sub.enabled
        ]

    def get_metrics(self) -> Dict[str, Any]:
        """Get service metrics."""
        metrics = self._metrics.copy()
        metrics.update(
            {
                "running": self._running,
                "poll_interval_seconds": self._poll_interval,
                "total_subscriptions": len(self._subscriptions),
                "disabled_subscriptions": len(
                    [s for s in self._subscriptions.values() if not s.enabled]
                ),
                "avg_bars_per_poll": (
                    metrics["bars_published"] / max(metrics["polls_completed"], 1)
                ),
            }
        )
        return metrics

    def get_health_status(self) -> Dict[str, Any]:
        """Get health status for system monitoring."""
        recent_poll = (
            self._metrics["last_poll_time"]
            and (utc_now() - self._metrics["last_poll_time"]).total_seconds()
            < (self._poll_interval * 2)
        )
        
        return {
            "status": "healthy" if (self._running and recent_poll) else "unhealthy",
            "running": self._running,
            "recent_poll_success": recent_poll,
            "subscribed_markets": len(self._subscriptions),
            "active_markets": len([s for s in self._subscriptions.values() if s.enabled]),
            "consecutive_failures": max([s.poll_failures for s in self._subscriptions.values()] + [0]),
            "session_active": self._session is not None and not self._session.closed,
        }
    
    async def _sync_with_subscription_service(self):
        """Sync subscriptions with MarketSubscriptionService."""
        if not self.market_subscription_service:
            return
            
        # Get current subscriptions from the service
        active_contracts = await self.market_subscription_service.get_active_subscriptions()
        
        # Clear existing subscriptions and sync with service
        self._subscriptions.clear()
        for contract_id in active_contracts:
            self._subscriptions[contract_id] = MarketSubscription(contract_id=contract_id)
        
        self.logger.info(f"Synced with MarketSubscriptionService: {len(active_contracts)} contracts")
    
    async def _setup_event_handlers(self):
        """Setup EventBus consumers for subscription changes using async iterator pattern."""
        if not self.event_bus or not self.market_subscription_service:
            return
        
        # Subscribe to events and start consumer tasks
        sub_added = await self.event_bus.subscribe("system.market_subscription.added", maxsize=100, critical=True)
        sub_removed = await self.event_bus.subscribe("system.market_subscription.removed", maxsize=100, critical=True)
        
        # Start consumer tasks
        self._event_tasks.append(
            asyncio.create_task(self._consume_subscription_added(sub_added))
        )
        self._event_tasks.append(
            asyncio.create_task(self._consume_subscription_removed(sub_removed))
        )
        
        self.logger.info("Setup EventBus consumers for market subscription changes")
    
    async def _consume_subscription_added(self, subscription):
        """Consumer task for subscription added events."""
        async for topic, payload in subscription:
            try:
                contract_id = payload.get("contract_id")
                if contract_id:
                    await self.subscribe_market(contract_id)
                    self.logger.info(f"Added subscription for {contract_id} via event")
            except Exception as e:
                self.logger.error(f"Error processing subscription added event: {e}")
    
    async def _consume_subscription_removed(self, subscription):
        """Consumer task for subscription removed events."""
        async for topic, payload in subscription:
            try:
                contract_id = payload.get("contract_id")
                if contract_id:
                    await self.unsubscribe_market(contract_id)
                    self.logger.info(f"Removed subscription for {contract_id} via event")
            except Exception as e:
                self.logger.error(f"Error processing subscription removed event: {e}")