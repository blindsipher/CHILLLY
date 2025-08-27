"""Multi-timeframe aggregator for converting 1m bars to higher timeframes."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
from dataclasses import dataclass

from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.data.types import Bar
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.core.topics import market_bar, boundary, persist_bar_save
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.networking.rate_limiter import RateLimiter
from topstepx_backend.data.historical_fetcher import HistoricalFetcher, HistoricalRequest


@dataclass
class AggregationState:
    """Tracks partial aggregation for a specific symbol/timeframe combination."""

    symbol: str
    timeframe: str
    bar_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    bar_count: int = 0
    last_update: datetime = None

    def update_with_bar(self, bar: Bar):
        """Update aggregation state with a new 1-minute bar."""
        if self.bar_count == 0:
            # First bar in the timeframe
            self.open = bar.open
            self.high = bar.high
            self.low = bar.low
        else:
            # Subsequent bars
            self.high = max(self.high, bar.high)
            self.low = min(self.low, bar.low)

        self.close = bar.close  # Always use the latest close
        self.volume += bar.volume
        self.bar_count += 1
        self.last_update = datetime.now(timezone.utc)

    def to_completed_bar(self) -> Bar:
        """Convert aggregation state to a completed bar."""
        if self.bar_count == 0:
            raise ValueError("Cannot create bar from empty aggregation state")

        return Bar(
            timestamp=self.bar_start,
            contract_id=self.symbol,
            timeframe=self.timeframe,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            source="aggregated",
            revision=1,
        )


class TimeframeAggregator:
    """Aggregates 1-minute bars into higher timeframes: 5m, 10m, 15m, 20m, 25m, 30m, 45m, 1h, 4h, 1d."""

    # Supported timeframes with their minute intervals. Must match SystemClock configuration
    # in the orchestrator to ensure boundary events are emitted for each timeframe.
    SUPPORTED_TIMEFRAMES = {
        "5m": 5,
        "10m": 10,
        "15m": 15,
        "20m": 20,
        "25m": 25,
        "30m": 30,
        "45m": 45,
        "1h": 60,
        "4h": 240,
        "1d": 1440,
    }

    def __init__(
        self,
        config: TopstepConfig,
        event_bus: Optional[EventBus] = None,
        watchlist: Optional[List[str]] = None,
    ):
        self.config = config
        self._event_bus = event_bus
        self.logger = logging.getLogger(__name__)
        self._running = False

        # Contract filtering for scalability - only process bars for specified contracts
        self._watchlist = set(watchlist) if watchlist else None

        # Historical warmup configuration
        self._warmup_bars = 100  # Number of historical bars to preload per timeframe
        self._warmup_enabled = False

        # Active aggregation states: {(symbol, timeframe): AggregationState}
        self._aggregation_states: Dict[tuple, AggregationState] = {}
        self._stats_lock = asyncio.Lock()

        # Event subscriptions and tasks
        self._market_subscription = None
        self._partial_subscription = None
        self._boundary_subscriptions = {}
        self._market_consumer_task = None
        self._partial_consumer_task = None
        self._boundary_consumer_tasks = {}

        # Performance metrics
        self._stats = {
            "bars_aggregated": 0,
            "timeframes_completed": 0,
            "aggregation_errors": 0,
            "active_states": 0,
            "last_aggregation": None,
        }

        # Subscriptions will be set up in start() method since they're async

    async def _setup_subscriptions(self):
        """Setup subscriptions to market.bar.*_1m, partial bars, and timeframe boundaries."""
        # Subscribe to all 1-minute market bar events (finalized)
        self._market_subscription = await self._event_bus.subscribe(
            "market.bar.*_1m", critical=True, maxsize=2000
        )

        # Subscribe to 1-minute partial bar events for live chart updates
        self._partial_subscription = await self._event_bus.subscribe(
            "market.bar.*_1m_partial", critical=False, maxsize=1000
        )

        # Subscribe to boundary events for each supported timeframe
        for timeframe in self.SUPPORTED_TIMEFRAMES:
            self._boundary_subscriptions[timeframe] = await self._event_bus.subscribe(
                boundary(timeframe), critical=True
            )

        self.logger.info(
            f"Subscribed to market.bar.*_1m, market.bar.*_1m_partial and {len(self.SUPPORTED_TIMEFRAMES)} boundary topics"
        )

    async def _consume_market_bars(self):
        """Background task to consume market bar events."""
        self.logger.info("Market bar consumer started...")
        try:
            async for topic, payload in self._market_subscription:
                if not self._running:
                    break

                # Convert payload back to Bar object
                bar = Bar(**payload)

                # Filter by watchlist if specified (for scalability)
                if self._watchlist and bar.contract_id not in self._watchlist:
                    continue

                # Process this bar for all supported timeframes
                for timeframe in self.SUPPORTED_TIMEFRAMES:
                    await self._update_aggregation(bar, timeframe)

                async with self._stats_lock:
                    self._stats["bars_aggregated"] += 1
                    self._stats["last_aggregation"] = datetime.now(timezone.utc)
                    self._stats["active_states"] = len(self._aggregation_states)

        except asyncio.CancelledError:
            self.logger.info("Market bar consumer stopping")
            raise
        except Exception as e:
            async with self._stats_lock:
                self._stats["aggregation_errors"] += 1
            self.logger.error(f"Error in market bar consumer: {e}")

    async def _consume_partial_bars(self):
        """Background task to consume partial bar events for live chart updates."""
        self.logger.info("Partial bar consumer started...")
        try:
            async for topic, payload in self._partial_subscription:
                if not self._running:
                    break

                # Convert payload back to Bar object
                partial_bar = Bar(**payload)

                # Filter by watchlist if specified
                if self._watchlist and partial_bar.contract_id not in self._watchlist:
                    continue

                # Process partial bar for all supported timeframes
                await self._update_partial_aggregations(partial_bar)

        except asyncio.CancelledError:
            self.logger.info("Partial bar consumer stopping")
            raise
        except Exception as e:
            async with self._stats_lock:
                self._stats["aggregation_errors"] += 1
            self.logger.error(f"Error in partial bar consumer: {e}")

    async def _update_partial_aggregations(self, partial_bar: Bar):
        """Update all timeframe aggregations with partial data and publish partial updates."""
        for timeframe in self.SUPPORTED_TIMEFRAMES:
            state_key = (partial_bar.contract_id, timeframe)
            
            # Get existing aggregation state
            if state_key not in self._aggregation_states:
                continue  # No active aggregation for this timeframe yet
            
            state = self._aggregation_states[state_key]
            
            # Create a partial aggregated bar by combining existing aggregation with partial data
            if state.bar_count > 0:
                # We have some completed bars in this timeframe period
                partial_aggregated_bar = Bar(
                    timestamp=state.bar_start,
                    contract_id=partial_bar.contract_id,
                    timeframe=timeframe,
                    open=state.open,
                    high=max(state.high, partial_bar.high),
                    low=min(state.low, partial_bar.low),
                    close=partial_bar.close,  # Use partial close
                    volume=state.volume,  # Don't include partial volume to avoid double counting
                    source="partial_aggregated",
                    revision=1,
                )
                
                # Publish partial update for charts
                partial_topic = f"{market_bar(partial_bar.contract_id, timeframe)}_partial"
                await self._event_bus.publish(partial_topic, partial_aggregated_bar.to_dict())
                
                self.logger.debug(
                    f"Published partial {timeframe} update for {partial_bar.contract_id}"
                )

    async def _consume_boundaries(self, timeframe: str):
        """Background task to consume boundary events for a specific timeframe."""
        self.logger.info(f"Boundary consumer for {timeframe} started...")
        try:
            subscription = self._boundary_subscriptions[timeframe]
            async for topic, payload in subscription:
                if not self._running:
                    break

                boundary_timestamp = payload.get("timestamp")
                await self._handle_boundary_close(timeframe, boundary_timestamp)

        except asyncio.CancelledError:
            self.logger.info(f"Boundary consumer for {timeframe} stopping")
            raise
        except Exception as e:
            self.logger.error(f"Error in boundary consumer for {timeframe}: {e}")

    async def _handle_boundary_close(self, timeframe: str, boundary_timestamp):
        """Handle boundary close for a specific timeframe - flush completed bars."""
        try:
            completed_bars = []

            # Find all aggregation states for this timeframe that are ready to complete
            keys_to_complete = []
            for key in list(self._aggregation_states.keys()):
                symbol, tf = key
                if tf == timeframe:
                    state = self._aggregation_states[key]
                    if state.bar_count > 0:
                        keys_to_complete.append(key)

            # Complete the bars using boundary timestamp
            for key in keys_to_complete:
                state = self._aggregation_states[key]
                completed_bar = state.to_completed_bar()

                # Use boundary timestamp
                if boundary_timestamp:
                    completed_bar.timestamp = boundary_timestamp

                completed_bars.append(completed_bar)

                # Reset state for next period
                state.bar_count = 0
                state.volume = 0
                state.open = 0
                state.high = 0
                state.low = 0
                state.close = 0

            # Publish completed bars with dual-publish pattern
            for bar in completed_bars:
                # 1. Publish domain event for strategies/other consumers
                market_topic = market_bar(bar.contract_id, bar.timeframe)
                await self._event_bus.publish(market_topic, bar.to_dict())

                # 2. Publish infrastructure command for PersistenceService
                await self._event_bus.publish(persist_bar_save(), bar.to_dict())

                self.logger.info(
                    f"Completed {timeframe} bar for {bar.contract_id} at {boundary_timestamp}"
                )

            async with self._stats_lock:
                self._stats["timeframes_completed"] += len(completed_bars)

        except Exception as e:
            async with self._stats_lock:
                self._stats["aggregation_errors"] += 1
            self.logger.error(f"Error handling {timeframe} boundary close: {e}")

    async def _update_aggregation(self, bar: Bar, timeframe: str):
        """Update aggregation state for a specific timeframe (driven by boundaries, not internal timing)."""
        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            self.logger.warning(f"Unsupported timeframe: {timeframe}")
            return

        state_key = (bar.contract_id, timeframe)

        # Get or create aggregation state
        if state_key not in self._aggregation_states:
            self._aggregation_states[state_key] = AggregationState(
                symbol=bar.contract_id,
                timeframe=timeframe,
                bar_start=bar.timestamp,  # Will be corrected by boundary events
                open=0,
                high=0,
                low=0,
                close=0,
                volume=0,
            )

        state = self._aggregation_states[state_key]

        # Always add to current aggregation - boundaries will trigger completion
        state.update_with_bar(bar)

        self.logger.debug(
            f"Updated {timeframe} aggregation for {bar.contract_id}: {state.bar_count} bars"
        )

    def get_active_timeframes(self, symbol: str) -> List[str]:
        """Get list of timeframes currently being aggregated for a symbol."""
        return [tf for (sym, tf) in self._aggregation_states.keys() if sym == symbol]

    def get_aggregation_progress(self, symbol: str, timeframe: str) -> Dict:
        """Get progress information for a specific symbol/timeframe aggregation."""
        state_key = (symbol, timeframe)
        if state_key not in self._aggregation_states:
            return {"status": "not_active"}

        state = self._aggregation_states[state_key]
        interval_minutes = self.SUPPORTED_TIMEFRAMES.get(timeframe, 0)

        return {
            "status": "active",
            "bars_collected": state.bar_count,
            "bars_needed": interval_minutes,
            "progress_percent": (state.bar_count / interval_minutes) * 100
            if interval_minutes > 0
            else 0,
            "period_start": state.bar_start,
            "last_update": state.last_update,
            "current_ohlcv": {
                "open": state.open,
                "high": state.high,
                "low": state.low,
                "close": state.close,
                "volume": state.volume,
            },
        }

    async def start(self):
        """Start the timeframe aggregator with event consumers."""
        if self._running:
            self.logger.warning("TimeframeAggregator already running")
            return

        self._running = True

        # Setup subscriptions if event bus available
        if self._event_bus:
            await self._setup_subscriptions()

        # Start market bar consumer
        if self._market_subscription:
            self._market_consumer_task = asyncio.create_task(
                self._consume_market_bars()
            )

        # Start partial bar consumer
        if self._partial_subscription:
            self._partial_consumer_task = asyncio.create_task(
                self._consume_partial_bars()
            )

        # Start boundary consumers for each timeframe
        for timeframe in self.SUPPORTED_TIMEFRAMES:
            if timeframe in self._boundary_subscriptions:
                task = asyncio.create_task(self._consume_boundaries(timeframe))
                self._boundary_consumer_tasks[timeframe] = task

        self.logger.info(
            f"TimeframeAggregator started with {len(self._boundary_consumer_tasks)} boundary consumers"
        )

    async def stop(self):
        """Stop the timeframe aggregator and cancel all tasks."""
        if not self._running:
            return

        self._running = False

        # Cancel market consumer task
        if self._market_consumer_task:
            self._market_consumer_task.cancel()
            try:
                await self._market_consumer_task
            except asyncio.CancelledError:
                pass
            self._market_consumer_task = None

        # Cancel partial consumer task
        if self._partial_consumer_task:
            self._partial_consumer_task.cancel()
            try:
                await self._partial_consumer_task
            except asyncio.CancelledError:
                pass
            self._partial_consumer_task = None

        # Cancel boundary consumer tasks
        for timeframe, task in self._boundary_consumer_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._boundary_consumer_tasks.clear()

        self.logger.info("TimeframeAggregator stopped")

    async def force_completion(self, symbol: str, timeframe: str) -> Optional[Bar]:
        """Force completion of a partial aggregation (useful for end-of-session)."""
        state_key = (symbol, timeframe)
        if state_key not in self._aggregation_states:
            return None

        state = self._aggregation_states[state_key]
        if state.bar_count == 0:
            return None

        completed_bar = state.to_completed_bar()

        # Publish with dual-publish pattern
        market_topic = market_bar(completed_bar.contract_id, completed_bar.timeframe)
        await self._event_bus.publish(market_topic, completed_bar.to_dict())
        await self._event_bus.publish(persist_bar_save(), completed_bar.to_dict())

        # Reset state
        state.bar_count = 0
        state.volume = 0

        async with self._stats_lock:
            self._stats["timeframes_completed"] += 1
            self._stats["active_states"] = len(self._aggregation_states)

        return completed_bar

    def get_stats(self) -> Dict:
        """Get aggregation statistics."""
        stats = self._stats.copy()
        stats.update(
            {
                "running": self._running,
                "supported_timeframes": list(self.SUPPORTED_TIMEFRAMES.keys()),
                "active_aggregations": len(self._aggregation_states),
                "market_consumer_running": self._market_consumer_task is not None
                and not self._market_consumer_task.done(),
                "boundary_consumers_running": len(
                    [t for t in self._boundary_consumer_tasks.values() if not t.done()]
                ),
                "watchlist_enabled": self._watchlist is not None,
                "watchlist_size": len(self._watchlist) if self._watchlist else 0,
                "warmup_enabled": self._warmup_enabled,
                "warmup_bars": self._warmup_bars,
            }
        )
        return stats

    def update_watchlist(self, contracts: List[str]):
        """Update the contract watchlist for filtering."""
        self._watchlist = set(contracts) if contracts else None
        self.logger.info(
            f"Updated watchlist: {len(contracts) if contracts else 0} contracts"
        )

    def get_watchlist(self) -> Optional[List[str]]:
        """Get current watchlist."""
        return list(self._watchlist) if self._watchlist else None

    async def warmup_historical_data(
        self, contracts: List[str], timeframes: List[str], bars_count: int = None
    ):
        """Warmup aggregations with historical data to provide context for indicators."""
        if not self._warmup_enabled:
            self.logger.info("Historical warmup disabled - skipping")
            return

        if not self._event_bus:
            self.logger.warning("Event bus not available - cannot warmup")
            return

        bars_count = bars_count or self._warmup_bars

        # Validate timeframes
        invalid_timeframes = [
            tf for tf in timeframes if tf not in self.SUPPORTED_TIMEFRAMES
        ]
        if invalid_timeframes:
            self.logger.warning(f"Invalid timeframes for warmup: {invalid_timeframes}")
            timeframes = [tf for tf in timeframes if tf in self.SUPPORTED_TIMEFRAMES]

        self.logger.info(
            f"Starting historical warmup for {len(contracts)} contracts, "
            f"{len(timeframes)} timeframes, {bars_count} bars each"
        )

        auth_manager = AuthManager(self.config)
        rate_limiter = RateLimiter()
        fetcher = HistoricalFetcher(self.config, auth_manager, rate_limiter, self._event_bus)

        subscription = None
        try:
            await auth_manager.start()
            await fetcher.start()

            subscription = await self._event_bus.subscribe(
                persist_bar_save(), critical=False, maxsize=bars_count * 10
            )

            for contract in contracts:
                try:
                    end_time = datetime.now(timezone.utc).replace(second=0, microsecond=0)
                    start_time = end_time - timedelta(minutes=bars_count)
                    request = HistoricalRequest(
                        contract_id=contract,
                        start_date=start_time,
                        end_date=end_time,
                        timeframe="1m",
                        priority=1,
                    )

                    success = await fetcher._fetch_historical_data(request)
                    if not success:
                        self.logger.error(
                            f"Failed to fetch historical bars for {contract}"
                        )
                        continue

                    processed = 0
                    while processed < bars_count:
                        try:
                            topic, payload = await asyncio.wait_for(
                                subscription.__anext__(), timeout=5
                            )
                        except asyncio.TimeoutError:
                            self.logger.warning(
                                f"Timeout waiting for historical bars for {contract}"
                            )
                            break

                        if (
                            payload.get("contract_id") != contract
                            or payload.get("timeframe") != "1m"
                        ):
                            continue

                        timestamp = payload["timestamp"]
                        if isinstance(timestamp, str):
                            timestamp_dt = datetime.fromisoformat(
                                timestamp.replace("Z", "+00:00")
                            )
                        else:
                            timestamp_dt = timestamp

                        bar = Bar(
                            timestamp=timestamp_dt,
                            contract_id=payload["contract_id"],
                            timeframe=payload["timeframe"],
                            open=float(payload["open"]),
                            high=float(payload["high"]),
                            low=float(payload["low"]),
                            close=float(payload["close"]),
                            volume=int(payload["volume"]),
                            source=payload.get("source", "historical"),
                            revision=int(payload.get("revision", 1)),
                        )

                        for timeframe in timeframes:
                            await self._update_aggregation(bar, timeframe)

                        await self._event_bus.publish(
                            market_bar(contract, "1m"), bar.to_dict()
                        )

                        async with self._stats_lock:
                            self._stats["bars_aggregated"] += 1
                            self._stats["last_aggregation"] = datetime.now(timezone.utc)
                            self._stats["active_states"] = len(self._aggregation_states)

                        processed += 1
                        if processed % 50 == 0 or processed == bars_count:
                            self.logger.info(
                                f"{contract} warmup: processed {processed} bars"
                            )

                    self.logger.info(
                        f"Completed warmup for {contract}: {processed} bars"
                    )
                except Exception as e:
                    self.logger.error(
                        f"Warmup error for {contract}: {e}", exc_info=True
                    )

        except Exception as e:
            self.logger.error(f"Historical warmup failed: {e}")
        finally:
            if subscription:
                await self._event_bus.unsubscribe(subscription)
            await fetcher.stop()
            await auth_manager.stop()

        self.logger.info("Historical warmup completed")

    def enable_warmup(self, enabled: bool = True, bars_count: int = 100):
        """Enable or disable historical warmup."""
        self._warmup_enabled = enabled
        self._warmup_bars = bars_count
        self.logger.info(
            f"Historical warmup {'enabled' if enabled else 'disabled'} "
            f"({bars_count} bars per timeframe)"
        )

    async def cleanup_stale_states(self, max_age_hours: int = 24):
        """Remove aggregation states that haven't been updated recently."""
        if not self._aggregation_states:
            return

        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        stale_keys = []

        for key, state in self._aggregation_states.items():
            if state.last_update and state.last_update < cutoff_time:
                stale_keys.append(key)

        for key in stale_keys:
            del self._aggregation_states[key]
            self.logger.info(f"Cleaned up stale aggregation state: {key}")

        if stale_keys:
            async with self._stats_lock:
                self._stats["active_states"] = len(self._aggregation_states)
