import asyncio
from types import SimpleNamespace

import aiohttp
import pytest

from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.services.order_service import OrderService
from topstepx_backend.data.types import OrderIntent, OrderType, OrderSide
from topstepx_backend.core.topics import order_ack, service_error


def test_order_service_payload_and_idempotency(dummy_config):
    async def scenario():
        bus = EventBus()
        await bus.start()

        async def acquire(*a, **k):
            return True

        auth = SimpleNamespace(get_token=lambda: "t")
        rate_limiter = SimpleNamespace(acquire=acquire)
        service = OrderService(bus, auth, dummy_config, rate_limiter)

        # Patch API submission
        async def mock_submit(payload):
            return {"success": True, "orderId": 1}

        service._session = object()
        service._submit_order_api = mock_submit

        intent = OrderIntent(
            "s",
            dummy_config.account_id,
            "123",
            OrderType.LIMIT,
            OrderSide.BUY,
            2,
            limit_price=10.0,
            custom_tag="tag1",
        )
        payload = service._build_api_payload(intent)
        assert payload["type"] == 1
        assert payload["side"] == 0
        assert payload["limitPrice"] == 10.0

        sub = await bus.subscribe(order_ack())
        await service._handle_submit_request(intent.to_dict())
        topic, ack = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert ack["order_id"] == 1

        await service._handle_submit_request(intent.to_dict())
        topic, ack2 = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert "Duplicate" in ack2.get("error_message", "")

        await bus.unsubscribe(sub)
        await bus.stop()

    asyncio.run(scenario())


def test_request_with_retry_success(dummy_config):
    class DummyResponse:
        def __init__(self, status=200, text="{}"):
            self.status = status
            self._text = text

        async def text(self):
            return self._text

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FlakySession:
        def __init__(self, failures):
            self.failures = failures
            self.calls = 0

        def request(self, *a, **k):
            if self.calls < self.failures:
                self.calls += 1
                raise aiohttp.ClientError("boom")
            self.calls += 1
            return DummyResponse()

    async def scenario():
        bus = EventBus()
        await bus.start()
        auth = SimpleNamespace(get_token=lambda: "t")
        rate_limiter = SimpleNamespace(acquire=lambda *a, **k: True)
        service = OrderService(bus, auth, dummy_config, rate_limiter)
        service._session = FlakySession(2)

        status, text = await service._request_with_retry("POST", "http://example.com")
        assert status == 200
        assert service._metrics["api_retries"] == 2

        await bus.stop()

    asyncio.run(scenario())


def test_request_with_retry_failure_event(dummy_config):
    class FailingSession:
        def request(self, *a, **k):
            raise aiohttp.ClientError("boom")

    async def scenario():
        bus = EventBus()
        await bus.start()
        auth = SimpleNamespace(get_token=lambda: "t")
        rate_limiter = SimpleNamespace(acquire=lambda *a, **k: True)
        service = OrderService(bus, auth, dummy_config, rate_limiter)
        service._session = FailingSession()

        sub = await bus.subscribe(service_error("order"))

        with pytest.raises(Exception):
            await service._request_with_retry(
                "POST", "http://example.com", max_retries=2
            )

        topic, payload = await asyncio.wait_for(sub.__anext__(), timeout=1)
        assert payload["retries"] == 2
        assert service._metrics["api_retries"] == 2

        await bus.unsubscribe(sub)
        await bus.stop()

    asyncio.run(scenario())
