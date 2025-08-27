import asyncio
import sys
import types
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

# Ensure repository root in path
sys.path.append(str(Path(__file__).resolve().parents[2]))

# Minimal aiohttp stub for import
class _DummyTimeout:
    def __init__(self, total=None, connect=None):
        self.total = total
        self.connect = connect

class _DummySession:
    def __init__(self, *args, **kwargs):
        pass

aiohttp_stub = types.SimpleNamespace(
    ClientSession=_DummySession,
    ClientTimeout=_DummyTimeout,
    ClientError=Exception,
)
sys.modules.setdefault("aiohttp", aiohttp_stub)
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda *a, **k: None))

from topstepx_backend.auth.auth_manager import AuthManager, AuthToken
from topstepx_backend.config.settings import TopstepConfig


class MockResponse:
    def __init__(self, status=200, json_data=None, text_data=""):
        self.status = status
        self._json = json_data or {}
        self._text = text_data

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        return self._json

    async def text(self):
        return self._text


class MockSession:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def close(self):
        pass

    def post(self, url, **kwargs):
        self.calls.append(url)
        resp_list = self.responses.get(url)
        if not resp_list:
            raise AssertionError(f"Unexpected URL {url}")
        return resp_list.pop(0)


@pytest.fixture
def dummy_config():
    return TopstepConfig(
        username="user",
        api_key="a" * 16,
        account_id=1,
        account_name="acc",
        projectx_base_url="https://example.com",
        projectx_user_hub_url="https://example.com/user",
        projectx_market_hub_url="https://example.com/market",
        database_path=":memory:",
        log_level="INFO",
        environment="development",
    )


def login_url(cfg: TopstepConfig) -> str:
    return f"{cfg.projectx_base_url}/api/Auth/loginKey"


def validate_url(cfg: TopstepConfig) -> str:
    return f"{cfg.projectx_base_url}/api/Auth/validate"


def test_initial_authentication(dummy_config):
    responses = {
        login_url(dummy_config): [MockResponse(200, {"success": True, "token": "init"})]
    }
    session = MockSession(responses)
    with patch("aiohttp.ClientSession", return_value=session):
        auth = AuthManager(dummy_config)
        asyncio.run(auth.start())
        assert auth.get_token() == "init"
        asyncio.run(auth.stop())


def test_validate_token_returns_new_token(dummy_config):
    responses = {
        login_url(dummy_config): [MockResponse(200, {"success": True, "token": "init"})],
        validate_url(dummy_config): [MockResponse(200, {"success": True, "newToken": "new"})],
    }
    session = MockSession(responses)
    with patch("aiohttp.ClientSession", return_value=session):
        auth = AuthManager(dummy_config)
        asyncio.run(auth.start())
        assert auth.get_token() == "init"
        assert asyncio.run(auth.validate_token()) is True
        assert auth.get_token() == "new"
        asyncio.run(auth.stop())


def test_refresh_loop_reauth_on_validation_failure(dummy_config):
    cfg = dummy_config
    login = login_url(cfg)
    validate = validate_url(cfg)
    responses = {
        login: [
            MockResponse(200, {"success": True, "token": "init"}),
            MockResponse(200, {"success": True, "token": "reauthed"}),
        ],
        validate: [MockResponse(401, text_data="bad token")],
    }
    session = MockSession(responses)
    auth = AuthManager(cfg)
    auth._session = session
    asyncio.run(auth._authenticate())
    with patch("topstepx_backend.auth.auth_manager.utc_now") as mock_now:
        fixed = datetime(2024, 1, 1, tzinfo=timezone.utc)
        mock_now.return_value = fixed
        auth._token.expires_at = fixed + timedelta(minutes=30)
        with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
            asyncio.run(auth._token_refresh_loop())
    assert auth.get_token() == "reauthed"
