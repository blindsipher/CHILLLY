import asyncio
import json
import logging
from contextlib import suppress
from typing import Any, Dict, Optional

from topstepx_backend.core.event_bus import Event, Subscription

try:
    import redis.asyncio as aioredis  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    aioredis = None


class RedisEventBus:
    """EventBus implementation backed by Redis pub/sub."""

    def __init__(self, redis_url: str, *, default_maxsize: int = 1024):
        if aioredis is None:  # pragma: no cover - optional dependency
            raise RuntimeError("redis package required for RedisEventBus")
        self.logger = logging.getLogger(__name__)
        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._default_maxsize = default_maxsize
        self._running = False
        self._subs: list[Subscription] = []
        self._tasks: Dict[Subscription, asyncio.Task] = {}
        self._pubsubs: Dict[Subscription, Any] = {}
        self._metrics = {
            "events_published": 0,
            "events_processed": 0,
            "events_dropped": 0,
            "subscriber_count": 0,
        }

    async def start(self):
        self._running = True
        self.logger.info("RedisEventBus started")

    async def stop(self):
        if not self._running:
            return
        self._running = False
        for sub, task in list(self._tasks.items()):
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
            ps = self._pubsubs.pop(sub, None)
            if ps is not None:
                await ps.close()
        await self._redis.close()
        self.logger.info("RedisEventBus stopped")

    async def publish(self, topic: str, payload: Any) -> int:
        if not self._running:
            self.logger.warning("Cannot publish - RedisEventBus not started")
            return -1
        evt = Event(topic, payload)
        data = json.dumps({"topic": topic, "payload": payload, "ts": evt.ts, "seq": evt.seq})
        await self._redis.publish(topic, data)
        self._metrics["events_published"] += 1
        return evt.seq

    async def subscribe(
        self, pattern: str, *, maxsize: Optional[int] = None, critical: bool = False
    ) -> Subscription:
        sub = Subscription(pattern, maxsize or self._default_maxsize, critical)
        ps = self._redis.pubsub()
        await ps.psubscribe(pattern)
        self._subs.append(sub)
        self._pubsubs[sub] = ps
        self._metrics["subscriber_count"] = len(self._subs)

        async def reader():
            try:
                while self._running and not sub._closed:
                    message = await ps.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message and message.get("type") == "pmessage":
                        try:
                            data = json.loads(message["data"])
                            evt = Event(data.get("topic", message["channel"]), data.get("payload"))
                            evt.ts = data.get("ts", evt.ts)
                            evt.seq = data.get("seq", evt.seq)
                            await sub.queue.put(evt)
                            self._metrics["events_processed"] += 1
                        except Exception:  # pragma: no cover - defensive
                            self._metrics["events_dropped"] += 1
            finally:
                await ps.close()

        task = asyncio.create_task(reader())
        self._tasks[sub] = task
        return sub

    async def unsubscribe(self, sub: Subscription):
        if sub in self._subs:
            self._subs.remove(sub)
            sub.close()
            task = self._tasks.pop(sub, None)
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            ps = self._pubsubs.pop(sub, None)
            if ps is not None:
                await ps.close()
            self._metrics["subscriber_count"] = len(self._subs)

    def get_metrics(self) -> Dict[str, Any]:
        base = dict(self._metrics)
        base.update({"running": self._running})
        return base
