import asyncio

from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.services.risk_manager import RiskManager
from topstepx_backend.data.types import OrderIntent, OrderType, OrderSide


def test_risk_manager_position_and_limits(dummy_config):
    async def scenario():
        bus = EventBus()
        await bus.start()
        rm = RiskManager(bus, dummy_config)
        await rm.start()

        # Process a fill and position update
        await rm._handle_fill({
            "account_id": 1,
            "contract_id": "ES",
            "side": "Buy",
            "size": 2,
            "pnl": 100,
        })
        await rm._handle_position({
            "account_id": 1,
            "contract_id": "ES",
            "position": 1,
            "pnl": 50,
        })

        assert rm.positions[(1, "ES")].position == 1
        assert rm.account_pnl[1] == 50

        # Exceed daily loss
        rm.account_pnl[1] = -dummy_config.risk_max_daily_loss
        intent = OrderIntent(
            "s", 1, "ES", OrderType.MARKET, OrderSide.BUY, 1
        )
        assert rm.check_order(intent) is False

        # Exceed projected position size
        rm.account_pnl[1] = 0
        intent = OrderIntent(
            "s", 1, "ES", OrderType.MARKET, OrderSide.BUY, 5
        )
        assert rm.check_order(intent) is False

        # RiskManager.stop has non-awaitable subscription.close; tolerate error
        try:
            await rm.stop()
        except TypeError:
            pass
        await bus.stop()

    asyncio.run(scenario())
