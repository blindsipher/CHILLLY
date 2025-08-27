import asyncio
from datetime import datetime, timedelta, timezone

from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.data.timeframe_aggregator import TimeframeAggregator
from topstepx_backend.data.types import Bar
from topstepx_backend.config.settings import TopstepConfig


def make_config():
    return TopstepConfig(
        username="user",
        api_key="a" * 32,
        account_id=1,
        account_name="acct",
        projectx_base_url="https://api.example.com",
        projectx_user_hub_url="https://rtc.example.com/hubs/user",
        projectx_market_hub_url="https://rtc.example.com/hubs/market",
        base_url="https://api.example.com",
        user_hub_url="https://rtc.example.com/hubs/user",
        market_hub_url="https://rtc.example.com/hubs/market",
        database_path=":memory:",
        log_level="INFO",
        environment="development",
        live_mode=True,
    )


def test_timeframe_aggregator_handles_boundary():
    async def run():
        bus = EventBus()
        await bus.start()
        cfg = make_config()
        agg = TimeframeAggregator(cfg, event_bus=bus)
        sub = await bus.subscribe("market.bar.ES_5m")
        start = datetime(2024, 1, 1, tzinfo=timezone.utc)
        for i in range(5):
            bar = Bar(
                timestamp=start + timedelta(minutes=i),
                contract_id="ES",
                timeframe="1m",
                open=1.0,
                high=1.0 + i,
                low=1.0,
                close=1.0 + i,
                volume=1,
                source="live",
            )
            await agg._update_aggregation(bar, "5m")
        boundary_ts = start + timedelta(minutes=5)
        await agg._handle_boundary_close("5m", boundary_ts)
        topic, payload = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert topic == "market.bar.ES_5m"
        assert payload["timeframe"] == "5m"
        assert payload["contract_id"] == "ES"
        assert payload["open"] == 1.0
        assert payload["high"] == 1.0 + 4
        assert payload["close"] == 1.0 + 4
        assert payload["volume"] == 5
        await bus.stop()
    asyncio.run(run())
