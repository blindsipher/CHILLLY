import asyncio
from topstepx_backend.core.event_bus import EventBus


def test_event_bus_publish_subscribe():
    async def run():
        bus = EventBus()
        await bus.start()
        sub = await bus.subscribe("test.topic")
        await bus.publish("test.topic", {"value": 1})
        topic, payload = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert topic == "test.topic"
        assert payload == {"value": 1}
        await bus.stop()
    asyncio.run(run())


def test_event_bus_wildcard_subscription():
    async def run():
        bus = EventBus()
        await bus.start()
        sub = await bus.subscribe("market.*")
        await bus.publish("market.update", 42)
        topic, payload = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert topic == "market.update"
        assert payload == 42
        await bus.stop()
    asyncio.run(run())
