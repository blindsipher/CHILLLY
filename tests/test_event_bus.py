import asyncio

from topstepx_backend.core.event_bus import EventBus


def test_publish_and_receive():
    async def scenario():
        bus = EventBus(worker_concurrency=2)
        await bus.start()
        sub = await bus.subscribe("test.*")
        seq = await bus.publish("test.topic", {"v": 1})
        assert seq >= 0
        topic, payload = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert topic == "test.topic"
        assert payload == {"v": 1}
        await bus.stop()

    asyncio.run(scenario())


def test_metrics_queue_depth_and_worker_lag():
    async def scenario():
        bus = EventBus(worker_concurrency=1)
        await bus.start()
        sub = await bus.subscribe("foo")
        await bus.publish("foo", 1)
        topic, payload = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert payload == 1
        metrics = bus.get_metrics()
        assert metrics["events_published"] == 1
        assert metrics["events_processed"] == 1
        assert metrics["max_queue_depth"] >= 1
        assert "avg_worker_lag_ms" in metrics
        await bus.stop()

    asyncio.run(scenario())
