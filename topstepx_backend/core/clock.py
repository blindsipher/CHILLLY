"""Deterministic system clock for UTC-aligned bar boundary events."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Set
from topstepx_backend.core.event_bus import EventBus


class SystemClock:
    """
    Deterministic clock that emits bar.boundary.* events at UTC-aligned intervals.

    This ensures consistent bar aggregation timing independent of data feed jitter.
    Events are emitted at the start of each new time period (e.g., 09:30:00 for 1m bars).
    """

    # Supported timeframes in seconds
    TIMEFRAMES = {
        "1s": 1,
        "5s": 5,
        "15s": 15,
        "30s": 30,
        "1m": 60,
        "5m": 300,
        "10m": 600,
        "15m": 900,
        "20m": 1200,
        "25m": 1500,
        "30m": 1800,
        "45m": 2700,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.logger = logging.getLogger(__name__)
        self._running = False
        self._tasks: Dict[str, asyncio.Task] = {}
        self._enabled_timeframes: Set[str] = set()

        # Metrics
        self._metrics = {
            "boundaries_emitted": 0,
            "missed_boundaries": 0,  # When clock is behind schedule
            "timeframes_active": 0,
        }

    async def start(self, timeframes: Optional[Set[str]] = None):
        """
        Start the clock for specified timeframes.

        Args:
            timeframes: Set of timeframes to enable (e.g., {"1m", "5m", "1h"})
                       If None, enables common trading timeframes
        """
        if self._running:
            self.logger.warning("Clock already running")
            return

        if timeframes is None:
            timeframes = {"1m", "5m", "15m", "1h"}  # Common trading timeframes

        # Validate timeframes
        invalid = timeframes - set(self.TIMEFRAMES.keys())
        if invalid:
            raise ValueError(f"Invalid timeframes: {invalid}")

        self._enabled_timeframes = timeframes
        self._running = True

        # Start tick tasks for each timeframe
        for tf in timeframes:
            task = asyncio.create_task(self._tick_loop(tf))
            self._tasks[tf] = task

        self._metrics["timeframes_active"] = len(timeframes)
        self.logger.info(f"SystemClock started with timeframes: {sorted(timeframes)}")

    async def stop(self):
        """Stop the clock and cancel all tick tasks."""
        if not self._running:
            return

        self._running = False

        # Cancel all tick tasks
        for tf, task in self._tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        self._tasks.clear()
        self._enabled_timeframes.clear()
        self._metrics["timeframes_active"] = 0

        self.logger.info("SystemClock stopped")

    async def _tick_loop(self, timeframe: str):
        """Main tick loop for a specific timeframe."""
        interval_seconds = self.TIMEFRAMES[timeframe]

        try:
            while self._running:
                # Calculate next boundary time
                now = datetime.now(timezone.utc)
                next_boundary = self._calculate_next_boundary(now, interval_seconds)

                # Sleep until next boundary
                sleep_duration = (next_boundary - now).total_seconds()
                if sleep_duration > 0:
                    await asyncio.sleep(sleep_duration)
                else:
                    # We're behind schedule
                    self._metrics["missed_boundaries"] += 1
                    self.logger.warning(
                        f"Clock behind schedule for {timeframe} by {-sleep_duration:.3f}s"
                    )

                # Emit boundary event
                if self._running:  # Check again in case stopped during sleep
                    await self._emit_boundary(timeframe, next_boundary)

        except asyncio.CancelledError:
            self.logger.debug(f"Tick loop cancelled for {timeframe}")
        except Exception as e:
            self.logger.error(f"Tick loop error for {timeframe}: {e}")

    def _calculate_next_boundary(
        self, current_time: datetime, interval_seconds: int
    ) -> datetime:
        """Calculate the next UTC-aligned boundary for the given interval."""
        # Get current timestamp in seconds since epoch
        current_timestamp = current_time.timestamp()

        # Round down to the current boundary
        current_boundary = int(current_timestamp // interval_seconds) * interval_seconds

        # Next boundary is one interval ahead
        next_boundary = current_boundary + interval_seconds

        return datetime.fromtimestamp(next_boundary, timezone.utc)

    async def _emit_boundary(self, timeframe: str, boundary_time: datetime):
        """Emit a bar boundary event."""
        topic = f"bar.boundary.{timeframe}"
        payload = {
            "timeframe": timeframe,
            "boundary_time": boundary_time,
            "timestamp_ms": int(boundary_time.timestamp() * 1000),
            "timezone": "UTC",
        }

        try:
            await self.event_bus.publish(topic, payload)
            self._metrics["boundaries_emitted"] += 1
            self.logger.debug(f"Emitted boundary: {timeframe} at {boundary_time}")

        except Exception as e:
            self.logger.error(f"Failed to emit boundary for {timeframe}: {e}")

    def get_metrics(self) -> Dict[str, any]:
        """Get clock metrics."""
        return {
            **self._metrics,
            "running": self._running,
            "enabled_timeframes": sorted(self._enabled_timeframes),
            "active_tasks": len(self._tasks),
        }

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
