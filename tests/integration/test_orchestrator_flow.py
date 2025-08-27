import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from topstepx_backend.orchestrator import TopstepXOrchestrator
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.services.persistence import PersistenceService
from topstepx_backend.services.order_service import OrderService
from topstepx_backend.core.topics import (
    persist_bar_save,
    order_request_submit,
    order_ack,
)
from topstepx_backend.data.types import Bar


def test_orchestrator_end_to_end(dummy_config, monkeypatch):
    async def fake_init(self):
        self.event_bus = EventBus()
        await self.event_bus.start()

        self.persistence = PersistenceService(self.event_bus, self.config.database_path)
        await self.persistence.start()

        async def acquire(*a, **k):
            return True

        auth = SimpleNamespace(get_token=lambda: "t")
        rate_limiter = SimpleNamespace(acquire=acquire)
        self.order_service = OrderService(
            self.event_bus, auth, self.config, rate_limiter
        )
        self.order_service._session = object()
        async def mock_submit(payload):
            return {"success": True, "orderId": 7}
        self.order_service._submit_order_api = mock_submit
        async def no_validate():
            return None
        self.order_service._validate_active_account = no_validate
        await self.order_service._setup_subscriptions()
        self.order_service._running = True

        self._services = [self.order_service, self.persistence]

    monkeypatch.setattr(TopstepXOrchestrator, "initialize_services", fake_init)

    async def scenario():
        orch = TopstepXOrchestrator(dummy_config)
        task = asyncio.create_task(orch.start())
        await asyncio.sleep(0.1)

        bar = Bar(
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            contract_id="ES",
            timeframe="1m",
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1,
            source="test",
        )
        await orch.event_bus.publish(persist_bar_save(), bar.to_dict())

        sub = await orch.event_bus.subscribe(order_ack())
        payload = {
            "strategy_id": "s",
            "account_id": dummy_config.account_id,
            "contract_id": "123",
            "type": "Limit",
            "side": "Buy",
            "size": 1,
            "limit_price": 10.0,
            "custom_tag": "t1",
        }
        await orch.event_bus.publish(order_request_submit(), payload)
        topic, ack = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert ack["order_id"] == 7

        await asyncio.sleep(0.1)
        row = await asyncio.to_thread(
            lambda: orch.persistence._conn.execute(
                "SELECT COUNT(*) FROM bars"
            ).fetchone()
        )
        assert row[0] == 1

        await orch.event_bus.unsubscribe(sub)
        await orch.shutdown()
        await task

    asyncio.run(scenario())
