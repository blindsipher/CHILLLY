import asyncio
from datetime import datetime, timezone

from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.services.persistence import (
    PersistenceService,
    BarSaveEvent,
    TickSaveEvent,
)
from topstepx_backend.data.types import Bar


def test_persistence_saves_bar_and_tick(dummy_config):
    async def scenario():
        bus = EventBus()
        await bus.start()
        ps = PersistenceService(bus, dummy_config.database_path)
        await ps.start()

        bar = Bar(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            contract_id="ES",
            timeframe="1m",
            open=1.0,
            high=2.0,
            low=0.5,
            close=1.5,
            volume=100,
            source="live",
        )
        await ps._handle_bar_save(BarSaveEvent([bar]))

        row = await asyncio.to_thread(
            lambda: ps._conn.execute(
                "SELECT open_scaled, volume FROM bars"
            ).fetchone()
        )
        assert row[0] == int(bar.open * ps.DEFAULT_PRICE_SCALE)
        assert row[1] == bar.volume

        tick = TickSaveEvent(
            symbol="ES",
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            price=99.5,
            volume=1,
        )
        await ps._handle_tick_save(tick)
        row = await asyncio.to_thread(
            lambda: ps._conn.execute(
                "SELECT price_scaled, volume FROM ticks"
            ).fetchone()
        )
        assert row[0] == int(tick.price * ps.DEFAULT_PRICE_SCALE)
        assert row[1] == tick.volume

        await ps.stop()
        await bus.stop()

    asyncio.run(scenario())
