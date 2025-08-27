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
sys.modules.setdefault("uvicorn", types.SimpleNamespace(Config=object, Server=object))

# Minimal pydantic stub
class _BaseModel:
    def __init__(self, **data):
        for k, v in data.items():
            setattr(self, k, v)

    def dict(self):
        return self.__dict__


class _BaseSettings:
    pass


def _Field(default=None, default_factory=None):
    if default_factory is not None:
        return default_factory()
    return default


pydantic_stub = types.ModuleType("pydantic")
pydantic_stub.BaseModel = _BaseModel
pydantic_stub.BaseSettings = _BaseSettings
pydantic_stub.Field = _Field
sys.modules["pydantic"] = pydantic_stub

# Minimal FastAPI stub
class _FastAPI:
    def __init__(self):
        self.routes = {}

    def _register(self, method, path):
        def decorator(func):
            self.routes[(method, path)] = func
            return func
        return decorator

    def get(self, path, response_model=None):
        return self._register("GET", path)

    def post(self, path, response_model=None):
        return self._register("POST", path)

    def delete(self, path, response_model=None):
        return self._register("DELETE", path)

    def websocket(self, path):
        return self._register("WS", path)


class _HTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail


class _WebSocket:  # pragma: no cover - minimal stub
    pass


class _WebSocketDisconnect(Exception):  # pragma: no cover - minimal stub
    pass


class _Response:
    def __init__(self, status_code: int, json_data=None):
        self.status_code = status_code
        self._json = json_data or {}

    def json(self):
        return self._json


class _TestClient:
    __test__ = False
    def __init__(self, app):
        self.app = app

    def post(self, path, json=None):
        from topstepx_backend.api.server import TokenRequest, HTTPException as _HTTPExc

        func = self.app.routes.get(("POST", path))
        try:
            result = asyncio.run(func(TokenRequest(**(json or {}))))
            return _Response(200, result.dict())
        except _HTTPExc as exc:
            return _Response(exc.status_code, {"detail": exc.detail})


fastapi_stub = types.SimpleNamespace(
    FastAPI=_FastAPI,
    WebSocket=_WebSocket,
    WebSocketDisconnect=_WebSocketDisconnect,
    HTTPException=_HTTPException,
)
testclient_stub = types.SimpleNamespace(TestClient=_TestClient)
sys.modules.setdefault("fastapi", fastapi_stub)
sys.modules.setdefault("fastapi.testclient", testclient_stub)

from topstepx_backend.auth.auth_manager import (
    AuthManager,
    AuthToken,
    AuthenticationError,
)
from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.api.server import APIServer
from topstepx_backend.core.event_bus import EventBus
from fastapi.testclient import TestClient


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


def test_validate_credentials(dummy_config):
    responses = {
        login_url(dummy_config): [MockResponse(200, {"success": True, "token": "init"})]
    }
    session = MockSession(responses)
    with patch("aiohttp.ClientSession", return_value=session):
        auth = AuthManager(dummy_config)
        asyncio.run(auth.start())
        assert (
            auth.validate_credentials("user", "a" * 16) == "init"
        )
        with pytest.raises(AuthenticationError):
            auth.validate_credentials("bad", "wrong")
        asyncio.run(auth.stop())


def test_auth_token_endpoint(dummy_config):
    responses = {
        login_url(dummy_config): [MockResponse(200, {"success": True, "token": "init"})]
    }
    session = MockSession(responses)
    with patch("aiohttp.ClientSession", return_value=session):
        auth = AuthManager(dummy_config)
        asyncio.run(auth.start())

        class DummyOrch:
            def __init__(self, auth_manager):
                self.auth_manager = auth_manager
                self.event_bus = EventBus()

        server = APIServer(DummyOrch(auth))
        client = TestClient(server.app)

        resp = client.post(
            "/auth/token",
            json={"username": "user", "api_key": "a" * 16},
        )
        assert resp.status_code == 200
        assert resp.json()["token"] == "init"

        resp = client.post(
            "/auth/token",
            json={"username": "bad", "api_key": "wrong"},
        )
        assert resp.status_code == 401

        asyncio.run(auth.stop())
