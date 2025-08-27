import asyncio

from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.services.risk_manager import RiskManager
from topstepx_backend.core.topics import order_fill_update, account_position_update


def _config() -> TopstepConfig:
    return TopstepConfig(
        username="u",
        api_key="k" * 16,
        account_id=1,
        account_name="acc",
        projectx_base_url="",
        projectx_user_hub_url="",
        projectx_market_hub_url="",
        database_path="",
        log_level="INFO",
        environment="development",
    )


def test_risk_manager_start_stop_lifecycle():
    async def scenario():
        bus = EventBus()
        await bus.start()
        cfg = _config()
        rm = RiskManager(bus, cfg)
        await rm.start()
        assert rm._running
        assert len(rm._subscriptions) == 2
        assert len(rm._tasks) == 2
        await bus.publish(
            order_fill_update(),
            {"account_id": 1, "contract_id": "XYZ", "side": "BUY", "size": 1},
        )
        await bus.publish(
            account_position_update(),
            {
                "account_id": 1,
                "contract_id": "XYZ",
                "position": 1,
                "pnl": 10.0,
            },
        )
        await asyncio.sleep(0.1)
        assert rm.positions[(1, "XYZ")].position == 1
        assert rm.account_pnl[1] == 10.0
        subs = list(rm._subscriptions)
        await rm.stop()
        assert not rm._running
        assert rm._subscriptions == []
        assert rm._tasks == []
        for sub in subs:
            assert sub._closed
        await bus.stop()

    asyncio.run(scenario())
