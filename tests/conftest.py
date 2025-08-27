import sys, types
from pathlib import Path

# Ensure repository root in path
sys.path.append(str(Path(__file__).resolve().parents[1]))

# Minimal aiohttp, dotenv, pydantic, and pysignalr stubs for tests
class _DummyTimeout:
    def __init__(self, total=None, connect=None):
        self.total = total
        self.connect = connect

class _DummySession:
    def __init__(self, *args, **kwargs):
        pass
    async def close(self):
        pass

aiohttp_stub = types.SimpleNamespace(
    ClientSession=_DummySession,
    ClientTimeout=_DummyTimeout,
    ClientError=Exception,
)

sys.modules.setdefault("aiohttp", aiohttp_stub)
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))
sys.modules.setdefault("pydantic", types.SimpleNamespace(BaseSettings=object, Field=lambda *a, **k: None))
pysignalr_client = types.SimpleNamespace(SignalRClient=object)
sys.modules.setdefault("pysignalr", types.SimpleNamespace(client=pysignalr_client))
sys.modules.setdefault("pysignalr.client", pysignalr_client)

import pytest
from topstepx_backend.config.settings import TopstepConfig


@pytest.fixture
def dummy_config(tmp_path):
    return TopstepConfig(
        username="user",
        api_key="a" * 16,
        account_id=1,
        account_name="acc",
        projectx_base_url="https://example.com",
        projectx_user_hub_url="https://example.com/user",
        projectx_market_hub_url="https://example.com/market",
        database_path=str(tmp_path / "test.db"),
        log_level="INFO",
        environment="test",
    )
