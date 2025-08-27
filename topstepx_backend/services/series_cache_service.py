"""In-memory cache service for TradingView Lightweight Charts integration.

Maintains recent bar data for all timeframes to support fast chart rendering.
Subscribes to both finalized and partial bar events from EventBus.
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
from collections import deque, defaultdict

from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.data.types import Bar
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.networking.api_helpers import utc_now


@dataclass
class SeriesData:
    """Container for cached series data for a specific contract/timeframe."""
    contract_id: str
    timeframe: str
    bars: deque  # Recent bars (max_bars_per_series limit)
    partial_bar: Optional[Bar] = None  # Current partial bar
    last_update: Optional[datetime] = None
    
    def to_lightweight_charts_format(self, include_partial: bool = True) -> List[Dict[str, Any]]:
        """Convert to TradingView Lightweight Charts format."""
        result = []
        
        # Add finalized bars
        for bar in self.bars:
            result.append({
                "time": int(bar.timestamp.timestamp()),  # Unix timestamp
                "open": float(bar.open),
                "high": float(bar.high), 
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": int(bar.volume)
            })
        
        # Add partial bar if requested and available
        if include_partial and self.partial_bar:
            result.append({
                "time": int(self.partial_bar.timestamp.timestamp()),
                "open": float(self.partial_bar.open),
                "high": float(self.partial_bar.high),
                "low": float(self.partial_bar.low), 
                "close": float(self.partial_bar.close),
                "volume": int(self.partial_bar.volume)
            })
        
        return result


class SeriesCacheService:
    """
    In-memory cache for TradingView Lightweight Charts data.
    
    Features:
    - Caches recent bars for all supported timeframes (5m-1d)
    - Handles both finalized and partial bar updates
    - Provides TradingView Lightweight Charts compatible format
    - Automatic cache size management and cleanup
    - Performance metrics and health monitoring
    """
    
    # Supported timeframes matching TimeframeAggregator
    SUPPORTED_TIMEFRAMES = ["5m", "10m", "15m", "20m", "25m", "30m", "45m", "1h", "4h", "1d"]
    
    def __init__(
        self,
        config: TopstepConfig,
        event_bus: Optional[EventBus] = None,
        max_bars_per_series: int = 1000  # Configurable cache size
    ):
        self.config = config
        self.event_bus = event_bus
        self.max_bars_per_series = max_bars_per_series
        self.logger = logging.getLogger(__name__)
        
        # Cache storage: {(contract_id, timeframe): SeriesData}
        self._cache: Dict[tuple, SeriesData] = {}
        self._cache_lock = asyncio.Lock()
        
        # Per-series locks to prevent race conditions during bar updates
        self._series_locks: Dict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)
        
        # Service state
        self._running = False
        self._subscriptions = {}
        self._partial_subscriptions = {}
        self._consumer_tasks = {}
        self._partial_consumer_tasks = {}
        
        # Performance metrics
        self._stats = {
            "cache_hits": 0,
            "cache_misses": 0,
            "bars_cached": 0,
            "partial_updates": 0,
            "cache_size": 0,
            "last_update": None,
            "cleanup_runs": 0,
        }
        self._stats_lock = asyncio.Lock()
    
    async def start(self):
        """Start the series cache service."""
        if self._running:
            self.logger.warning("SeriesCacheService already running")
            return
        
        self._running = True
        
        if self.event_bus:
            await self._setup_subscriptions()
            await self._start_consumers()
        
        self.logger.info(
            f"SeriesCacheService started with max {self.max_bars_per_series} bars per series"
        )
    
    async def stop(self):
        """Stop the series cache service."""
        if not self._running:
            return
        
        self._running = False
        
        # Cancel all consumer tasks
        all_tasks = list(self._consumer_tasks.values()) + list(self._partial_consumer_tasks.values())
        for task in all_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        self._consumer_tasks.clear()
        self._partial_consumer_tasks.clear()
        
        self.logger.info("SeriesCacheService stopped")
    
    async def _setup_subscriptions(self):
        """Setup EventBus subscriptions for all timeframes."""
        # Subscribe to finalized bars for all timeframes
        for timeframe in self.SUPPORTED_TIMEFRAMES:
            topic = f"market.bar.*_{timeframe}"
            subscription = await self.event_bus.subscribe(topic, critical=False, maxsize=1000)
            self._subscriptions[timeframe] = subscription
            
            # Subscribe to partial bars for real-time updates
            partial_topic = f"market.bar.*_{timeframe}_partial"
            partial_subscription = await self.event_bus.subscribe(partial_topic, critical=False, maxsize=500)
            self._partial_subscriptions[timeframe] = partial_subscription
        
        self.logger.info(
            f"Subscribed to {len(self.SUPPORTED_TIMEFRAMES)} finalized and partial bar topics"
        )
    
    async def _start_consumers(self):
        """Start consumer tasks for all subscriptions."""
        # Start finalized bar consumers
        for timeframe, subscription in self._subscriptions.items():
            task = asyncio.create_task(self._consume_bars(timeframe, subscription, is_partial=False))
            self._consumer_tasks[timeframe] = task
        
        # Start partial bar consumers  
        for timeframe, subscription in self._partial_subscriptions.items():
            task = asyncio.create_task(self._consume_bars(timeframe, subscription, is_partial=True))
            self._partial_consumer_tasks[timeframe] = task
        
        self.logger.info(
            f"Started {len(self._consumer_tasks)} finalized and {len(self._partial_consumer_tasks)} partial consumers"
        )
    
    async def _consume_bars(self, timeframe: str, subscription, is_partial: bool):
        """Consumer task for bar events."""
        consumer_type = "partial" if is_partial else "finalized"
        self.logger.info(f"Starting {consumer_type} bar consumer for {timeframe}...")
        
        try:
            async for topic, payload in subscription:
                if not self._running:
                    break
                
                # Convert payload back to Bar object
                bar = Bar(**payload)
                
                # Update cache
                if is_partial:
                    await self._update_partial_cache(bar)
                else:
                    await self._update_finalized_cache(bar)
        
        except asyncio.CancelledError:
            self.logger.info(f"{consumer_type} bar consumer for {timeframe} stopping")
            raise
        except Exception as e:
            self.logger.error(f"Error in {consumer_type} bar consumer for {timeframe}: {e}")
    
    async def _update_finalized_cache(self, bar: Bar):
        """Update cache with finalized bar data."""
        cache_key = (bar.contract_id, bar.timeframe)
        
        # Use per-series lock to prevent race conditions
        async with self._series_locks[cache_key]:
            async with self._cache_lock:
                if cache_key not in self._cache:
                    self._cache[cache_key] = SeriesData(
                        contract_id=bar.contract_id,
                        timeframe=bar.timeframe,
                        bars=deque(maxlen=self.max_bars_per_series)
                    )
                
                series = self._cache[cache_key]
                
                # Deduplicate by timestamp - replace if same timestamp exists
                updated = False
                for i, existing_bar in enumerate(series.bars):
                    if existing_bar.timestamp == bar.timestamp:
                        # Replace existing bar with newer data
                        series.bars[i] = bar
                        updated = True
                        self.logger.debug(f"Replaced duplicate bar at {bar.timestamp} for {bar.contract_id} {bar.timeframe}")
                        break
                
                if not updated:
                    # New timestamp - add to deque
                    series.bars.append(bar)
                
                series.last_update = utc_now()
                
                # Clear partial bar if this finalized bar supersedes it
                if (series.partial_bar and 
                    series.partial_bar.timestamp <= bar.timestamp):
                    series.partial_bar = None
        
        async with self._stats_lock:
            self._stats["bars_cached"] += 1
            self._stats["cache_size"] = len(self._cache)
            self._stats["last_update"] = utc_now()
        
        self.logger.debug(f"Cached finalized {bar.timeframe} bar for {bar.contract_id}")
    
    async def _update_partial_cache(self, partial_bar: Bar):
        """Update cache with partial bar data."""
        cache_key = (partial_bar.contract_id, partial_bar.timeframe)
        
        # Use per-series lock to prevent race conditions
        async with self._series_locks[cache_key]:
            async with self._cache_lock:
                if cache_key not in self._cache:
                    self._cache[cache_key] = SeriesData(
                        contract_id=partial_bar.contract_id,
                        timeframe=partial_bar.timeframe,
                        bars=deque(maxlen=self.max_bars_per_series)
                    )
                
                series = self._cache[cache_key]
                series.partial_bar = partial_bar
                series.last_update = utc_now()
        
        async with self._stats_lock:
            self._stats["partial_updates"] += 1
            self._stats["last_update"] = utc_now()
        
        self.logger.debug(f"Updated partial {partial_bar.timeframe} bar for {partial_bar.contract_id}")
    
    async def get_series_data(
        self, 
        contract_id: str, 
        timeframe: str, 
        limit: Optional[int] = None,
        include_partial: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get cached series data in TradingView Lightweight Charts format.
        
        Args:
            contract_id: Contract identifier
            timeframe: Timeframe (5m, 10m, 15m, etc.)
            limit: Maximum number of bars to return
            include_partial: Whether to include current partial bar
            
        Returns:
            List of bars in TradingView format: [{time, open, high, low, close, volume}, ...]
        """
        if timeframe not in self.SUPPORTED_TIMEFRAMES:
            self.logger.warning(f"Unsupported timeframe: {timeframe}")
            return []
        
        cache_key = (contract_id, timeframe)
        
        async with self._cache_lock:
            if cache_key in self._cache:
                async with self._stats_lock:
                    self._stats["cache_hits"] += 1
                
                series = self._cache[cache_key]
                data = series.to_lightweight_charts_format(include_partial=include_partial)
                
                # Apply limit if specified
                if limit and len(data) > limit:
                    data = data[-limit:]  # Return most recent bars
                
                return data
            else:
                async with self._stats_lock:
                    self._stats["cache_misses"] += 1
                
                self.logger.debug(f"Cache miss for {contract_id} {timeframe}")
                return []
    
    async def get_available_series(self) -> List[Dict[str, str]]:
        """Get list of available series in cache."""
        async with self._cache_lock:
            return [
                {
                    "contract_id": contract_id,
                    "timeframe": timeframe,
                    "bar_count": len(series.bars),
                    "has_partial": series.partial_bar is not None,
                    "last_update": series.last_update.isoformat() if series.last_update else None
                }
                for (contract_id, timeframe), series in self._cache.items()
            ]
    
    async def has_partial(self, contract_id: str, timeframe: str) -> bool:
        """Check if series has a partial bar available."""
        cache_key = (contract_id, timeframe)
        async with self._cache_lock:
            if cache_key in self._cache:
                return self._cache[cache_key].partial_bar is not None
            return False
    
    async def clear_series(self, contract_id: str, timeframe: Optional[str] = None):
        """Clear specific series or all timeframes for a contract."""
        async with self._cache_lock:
            if timeframe:
                # Clear specific series
                cache_key = (contract_id, timeframe)
                if cache_key in self._cache:
                    del self._cache[cache_key]
                    self.logger.info(f"Cleared cache for {contract_id} {timeframe}")
            else:
                # Clear all timeframes for contract
                keys_to_remove = [
                    key for key in self._cache.keys()
                    if key[0] == contract_id
                ]
                for key in keys_to_remove:
                    del self._cache[key]
                self.logger.info(f"Cleared all cache entries for {contract_id}")
    
    async def cleanup_stale_entries(self, max_age_hours: int = 24):
        """Remove cache entries that haven't been updated recently."""
        cutoff_time = utc_now() - timedelta(hours=max_age_hours)
        stale_keys = []
        
        async with self._cache_lock:
            for key, series in self._cache.items():
                if series.last_update and series.last_update < cutoff_time:
                    stale_keys.append(key)
            
            for key in stale_keys:
                del self._cache[key]
        
        if stale_keys:
            self.logger.info(f"Cleaned up {len(stale_keys)} stale cache entries")
            async with self._stats_lock:
                self._stats["cleanup_runs"] += 1
                self._stats["cache_size"] = len(self._cache)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service performance statistics."""
        # Note: Using synchronous access for stats to match orchestrator pattern
        # Stats are read-mostly, so minimal risk of inconsistency
        stats = self._stats.copy()
        
        stats.update({
            "running": self._running,
            "cache_entries": len(self._cache),
            "supported_timeframes": self.SUPPORTED_TIMEFRAMES,
            "max_bars_per_series": self.max_bars_per_series,
            "active_consumers": len([t for t in self._consumer_tasks.values() if not t.done()]),
            "active_partial_consumers": len([t for t in self._partial_consumer_tasks.values() if not t.done()]),
        })
        
        return stats