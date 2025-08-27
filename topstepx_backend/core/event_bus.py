"""Unified EventBus with topic-based routing and explicit backpressure handling."""

import asyncio
import fnmatch
import time
import itertools
import logging
from contextlib import suppress
from typing import Any, Dict, List, Optional
from dataclasses import dataclass


@dataclass
class Event:
    """Event with global sequence ID for ordering guarantees."""

    __slots__ = ("ts", "topic", "payload", "seq")
    _seq_gen = itertools.count()

    def __init__(self, topic: str, payload: Any):
        self.ts = time.time()
        self.topic = topic
        self.payload = payload
        self.seq = next(Event._seq_gen)  # global monotonic id


class Subscription:
    """Individual subscription with pattern matching and backpressure handling."""

    __slots__ = ("queue", "pattern", "critical", "_closed")

    def __init__(self, pattern: str, maxsize: int, critical: bool):
        self.pattern = pattern
        self.critical = critical
        self.queue = asyncio.Queue(maxsize=maxsize)
        self._closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._closed and self.queue.empty():
            raise StopAsyncIteration
        event = await self.queue.get()
        return (event.topic, event.payload)

    def close(self):
        """Mark subscription as closed."""
        self._closed = True


class EventBus:
    """
    Unified EventBus with topic-based routing and explicit backpressure policies.

    Features:
    - Topic-based routing with wildcard support (market.*, signal.*)
    - Per-topic FIFO ordering guarantees
    - Explicit backpressure: critical subscribers block publishers, non-critical drop newest
    - Comprehensive metrics and observability
    """

    def __init__(self, *, default_maxsize: int = 1024, worker_concurrency: int = 4):
        self.logger = logging.getLogger(__name__)
        self._subs: List[Subscription] = []
        self._topic_fifo: Dict[str, asyncio.Queue] = {}  # topic -> asyncio.Queue
        self._default_maxsize = default_maxsize
        self._lock = asyncio.Lock()
        self._running = False

        # Worker management
        self._drain_queue: asyncio.Queue[str] = asyncio.Queue()
        self._scheduled_topics: set[str] = set()
        self._worker_concurrency = max(1, worker_concurrency)
        self._worker_tasks: List[asyncio.Task] = []

        # Metrics
        self._metrics = {
            "events_published": 0,
            "events_processed": 0,
            "events_dropped": 0,
            "backpressure_blocks": 0,
            "subscriber_count": 0,
            "avg_fanout_time_ms": 0.0,
            "avg_worker_lag_ms": 0.0,
            "max_queue_depth": 0,
        }
        self._metrics_lock = asyncio.Lock()

    async def subscribe(
        self, pattern: str, *, maxsize: Optional[int] = None, critical: bool = False
    ) -> Subscription:
        """
        Subscribe to events matching a topic pattern.

        Args:
            pattern: Topic pattern with wildcard support (e.g., 'market.*', 'signal.strategy.*')
            maxsize: Queue size for this subscriber (default: default_maxsize)
            critical: If True, blocks publishers when queue full. If False, drops newest events.

        Returns:
            Subscription object that can be used as async iterator
        """
        sub = Subscription(pattern, maxsize or self._default_maxsize, critical)

        async with self._lock:
            self._subs.append(sub)
            async with self._metrics_lock:
                self._metrics["subscriber_count"] = len(self._subs)

        self.logger.info(
            f"New subscription: pattern='{pattern}' critical={critical} maxsize={sub.queue.maxsize}"
        )
        return sub

    async def unsubscribe(self, sub: Subscription):
        """Remove a subscription from the bus."""
        async with self._lock:
            if sub in self._subs:
                self._subs.remove(sub)
                sub.close()
                async with self._metrics_lock:
                    self._metrics["subscriber_count"] = len(self._subs)
                self.logger.info(f"Unsubscribed: pattern='{sub.pattern}'")

    async def publish(self, topic: str, payload: Any) -> int:
        """
        Publish an event to all matching subscribers.

        Args:
            topic: Event topic (e.g., 'market.bar.ES_1m', 'signal.strategy1.entry')
            payload: Event payload

        Returns:
            Event sequence number for tracking
        """
        if not self._running:
            self.logger.warning("Cannot publish - EventBus not started")
            return -1

        evt = Event(topic, payload)

        async with self._lock:
            # Ensure per-topic FIFO ordering
            q = self._topic_fifo.setdefault(topic, asyncio.Queue())
            await q.put(evt)  # Never bounded - bus should not drop

            if topic not in self._scheduled_topics:
                self._scheduled_topics.add(topic)
                self._drain_queue.put_nowait(topic)

        async with self._metrics_lock:
            self._metrics["events_published"] += 1
            qsize = q.qsize()
            if qsize > self._metrics["max_queue_depth"]:
                self._metrics["max_queue_depth"] = qsize

        self.logger.debug(f"Published: {topic} seq={evt.seq}")
        return evt.seq

    async def _worker(self):
        """Background worker draining topic queues."""
        while self._running:
            topic = await self._drain_queue.get()
            await self._fanout(topic)
            async with self._lock:
                q = self._topic_fifo.get(topic)
                if q and not q.empty():
                    self._drain_queue.put_nowait(topic)
                else:
                    self._scheduled_topics.discard(topic)

    async def _fanout(self, topic: str):
        """Fan out events for a specific topic to all matching subscribers."""
        start_time = time.time()

        try:
            q = self._topic_fifo[topic]
            events_processed = 0
            total_lag_ms = 0.0

            while not q.empty():
                evt = await q.get()
                events_processed += 1
                total_lag_ms += (time.time() - evt.ts) * 1000

                # Snapshot subscribers to avoid iteration issues during unsubscribe
                async with self._lock:
                    current_subs = list(self._subs)

                # Deliver to all matching subscribers
                for sub in current_subs:
                    if fnmatch.fnmatch(evt.topic, sub.pattern):
                        await self._deliver(sub, evt)

            # Update metrics
            fanout_time_ms = (time.time() - start_time) * 1000
            async with self._metrics_lock:
                self._metrics["events_processed"] += events_processed
                # Running average
                count = max(1, self._metrics["events_processed"])
                current_avg = self._metrics["avg_fanout_time_ms"]
                self._metrics["avg_fanout_time_ms"] = (
                    current_avg * (count - events_processed) + fanout_time_ms
                ) / count
                current_lag = self._metrics["avg_worker_lag_ms"]
                self._metrics["avg_worker_lag_ms"] = (
                    current_lag * (count - events_processed) + total_lag_ms
                ) / count

        except Exception as e:
            self.logger.error(f"Fanout error for topic {topic}: {e}")

    async def _deliver(self, sub: Subscription, evt: Event):
        """Deliver event to a single subscriber with backpressure handling."""
        if sub._closed:
            return

        try:
            # Fast path: try immediate delivery
            sub.queue.put_nowait(evt)

        except asyncio.QueueFull:
            if sub.critical:
                # Critical subscriber: block until space available
                async with self._metrics_lock:
                    self._metrics["backpressure_blocks"] += 1
                await sub.queue.put(evt)
                self.logger.debug(
                    f"Critical subscriber blocked: pattern='{sub.pattern}'"
                )
            else:
                # Non-critical subscriber: drop oldest, add newest
                try:
                    _ = sub.queue.get_nowait()  # Remove oldest
                    sub.queue.put_nowait(evt)  # Add newest
                    async with self._metrics_lock:
                        self._metrics["events_dropped"] += 1
                    self.logger.debug(
                        f"Dropped event for non-critical subscriber: pattern='{sub.pattern}'"
                    )
                except asyncio.QueueEmpty:
                    # Queue became empty between full check and get_nowait - just add the event
                    sub.queue.put_nowait(evt)

    async def start(self):
        """Start the EventBus."""
        if self._running:
            return
        self._running = True
        for _ in range(self._worker_concurrency):
            self._worker_tasks.append(asyncio.create_task(self._worker()))
        self.logger.info("EventBus started")

    async def stop(self):
        """Stop the EventBus and close all subscriptions."""
        if not self._running:
            return

        self._running = False

        for task in self._worker_tasks:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._worker_tasks.clear()

        # Close all subscriptions
        async with self._lock:
            for sub in self._subs:
                sub.close()

        self.logger.info("EventBus stopped")

    def get_metrics(self) -> Dict[str, Any]:
        """Get current EventBus metrics."""
        base_metrics = self._metrics.copy()
        base_metrics.update(
            {
                "running": self._running,
                "active_topics": len(self._topic_fifo),
                "total_queue_size": sum(q.qsize() for q in self._topic_fifo.values()),
                "queue_depths": {topic: q.qsize() for topic, q in self._topic_fifo.items()},
            }
        )
        return base_metrics

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()
