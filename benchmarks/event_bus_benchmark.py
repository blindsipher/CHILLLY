"""Simple benchmarking script for the in-memory EventBus.

Run with: ``python benchmarks/event_bus_benchmark.py``.
"""

import asyncio
import statistics
import time
from contextlib import suppress

from topstepx_backend.core.event_bus import EventBus


async def run_benchmark(event_count: int = 20000) -> None:
    bus = EventBus(worker_concurrency=4)
    await bus.start()
    latencies: list[float] = []

    async def subscriber() -> None:
        sub = await bus.subscribe("bench.*", maxsize=10, critical=False)
        try:
            async for _topic, payload in sub:
                latencies.append((time.time() - payload["ts"]) * 1000)
                await asyncio.sleep(0.01)  # slow consumer to trigger drops
        except asyncio.CancelledError:
            pass

    task = asyncio.create_task(subscriber())

    await asyncio.gather(
        *(bus.publish("bench.topic", {"ts": time.time()}) for _ in range(event_count))
    )

    # Allow bus to drain
    await asyncio.sleep(2)
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task

    metrics = bus.get_metrics()
    await bus.stop()

    avg_latency = statistics.mean(latencies) if latencies else 0.0
    print("Events published:", metrics["events_published"])
    print("Events processed:", metrics["events_processed"])
    print("Events dropped:", metrics["events_dropped"])
    print("Average latency (ms):", round(avg_latency, 3))


if __name__ == "__main__":
    asyncio.run(run_benchmark())
