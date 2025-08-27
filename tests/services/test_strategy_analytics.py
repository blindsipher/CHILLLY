import asyncio

from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.analytics.strategy_analytics import StrategyAnalytics
from topstepx_backend.core.topics import order_fill_update, account_position_update


def test_strategy_analytics_metrics():
    async def scenario():
        bus = EventBus()
        await bus.start()
        sa = StrategyAnalytics(bus)
        await sa.start()

        # simulate events
        await bus.publish(order_fill_update(), {"custom_tag": "s1_0_123", "pnl": 5})
        await bus.publish(account_position_update(), {"strategy_id": "s1", "position": 2})
        await asyncio.sleep(0.1)

        metrics = sa.get_metrics()
        assert metrics["s1"]["trades_executed"] == 1
        assert metrics["s1"]["realized_pnl"] == 5
        assert metrics["s1"]["position"] == 2

        await sa.stop()
        await bus.stop()

    asyncio.run(scenario())
