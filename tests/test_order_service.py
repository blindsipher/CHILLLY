import asyncio

from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.services.order_service import OrderService
from topstepx_backend.config.settings import TopstepConfig


class DummyAuthManager:
    def get_token(self):
        return "token"


class DummyRateLimiter:
    async def acquire(self, endpoint: str = "", tokens: int = 1, raise_on_limit: bool = False):
        return True


class FakeResponse:
    def __init__(self):
        self.status = 200

    async def json(self):
        return {"accounts": [{"id": 1, "name": "acct", "canTrade": True, "isVisible": True}]}

    async def text(self):
        return ""


class FakeRequestContext:
    def __init__(self, response):
        self.response = response

    def __await__(self):
        async def _():
            return self.response
        return _().__await__()

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, exc_type, exc, tb):
        pass


class FakeSession:
    def __init__(self, *args, **kwargs):
        self.closed = False

    def post(self, *args, **kwargs):
        return FakeRequestContext(FakeResponse())

    async def close(self):
        self.closed = True


def test_order_service_start_stop():
    async def run():
        import aiohttp
        aiohttp.ClientSession = FakeSession
        bus = EventBus()
        await bus.start()
        cfg = TopstepConfig(
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
        auth = DummyAuthManager()
        rate = DummyRateLimiter()
        service = OrderService(bus, auth, cfg, rate)
        await service.start()
        assert service._running is True
        await service.stop()
        await bus.stop()
    asyncio.run(run())
