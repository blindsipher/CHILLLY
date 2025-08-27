import sys, types
from pathlib import Path
from typing import Any

# Ensure repository root in path
sys.path.append(str(Path(__file__).resolve().parents[1]))

# Minimal aiohttp, dotenv, yaml, pydantic, and pysignalr stubs for tests
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
sys.modules.setdefault("yaml", types.SimpleNamespace(safe_load=lambda *a, **k: {}))
sys.modules.setdefault("pydantic", types.SimpleNamespace(BaseSettings=object, Field=lambda *a, **k: None))
pysignalr_client = types.SimpleNamespace(SignalRClient=object)
sys.modules.setdefault("pysignalr", types.SimpleNamespace(client=pysignalr_client))
sys.modules.setdefault("pysignalr.client", pysignalr_client)

# Minimal FastAPI stub
import re, inspect, asyncio
from urllib.parse import parse_qsl

class _Request:
    def __init__(self, app):
        self.app = app

class _Route:
    def __init__(self, method, path, func):
        self.method = method
        self.func = func
        raw_params = re.findall(r"{([^}]+)}", path)
        self.param_names = [p.split(":")[0] for p in raw_params]
        pattern = path
        for p in raw_params:
            name, *conv = p.split(":")
            if conv and conv[0] == "path":
                repl = "(.+)"
            else:
                repl = "([^/]+)"
            pattern = pattern.replace(f"{{{p}}}", repl)
        self.pattern = re.compile("^" + pattern + "$")

class _APIRouter:
    def __init__(self):
        self.routes = []

    def _add(self, method, path, **_):
        def decorator(func):
            self.routes.append(_Route(method, path, func))
            return func
        return decorator

    def get(self, path, **kwargs):
        return self._add("GET", path, **kwargs)

    def post(self, path, **kwargs):
        return self._add("POST", path, **kwargs)

    def put(self, path, **kwargs):
        return self._add("PUT", path, **kwargs)

    def delete(self, path, **kwargs):
        return self._add("DELETE", path, **kwargs)

    def websocket(self, path, **kwargs):
        return self._add("WS", path, **kwargs)

class _FastAPI(_APIRouter):
    def __init__(self):
        super().__init__()
        self.state = types.SimpleNamespace()

    def include_router(self, router):
        self.routes.extend(router.routes)

class _HTTPException(Exception):
    def __init__(self, status_code: int, detail: str = ""):
        self.status_code = status_code
        self.detail = detail

class _WebSocket:
    pass

class _WebSocketDisconnect(Exception):
    pass

class _Body(dict):
    def dict(self):
        return dict(self)

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError as e:
            raise AttributeError(item) from e

class _Response:
    def __init__(self, data, status_code: int = 200):
        self._data = data
        self.status_code = status_code

    def json(self):
        if hasattr(self._data, "dict"):
            return self._data.dict()
        return self._data

class _TestClient:
    def __init__(self, app):
        self.app = app

    def _call(self, method: str, path: str, json: Any | None = None):
        for route in self.app.routes:
            if route.method == method and route.pattern.match(path):
                match = route.pattern.match(path)
                kwargs = {
                    name: match.group(i + 1)
                    for i, name in enumerate(route.param_names)
                }
                sig = inspect.signature(route.func)
                body_assigned = False
                for name, param in sig.parameters.items():
                    if name in kwargs:
                        continue
                    if param.annotation is _Request:
                        kwargs[name] = _Request(self.app)
                    elif not body_assigned and json is not None:
                        kwargs[name] = _Body(json)
                        body_assigned = True
                try:
                    loop = asyncio.get_event_loop()
                except RuntimeError:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                try:
                    result = route.func(**kwargs)
                    if inspect.iscoroutine(result):
                        result = loop.run_until_complete(result)
                    return _Response(result)
                except _HTTPException as exc:
                    return _Response({"detail": exc.detail}, exc.status_code)
        raise _HTTPException(404, "Not found")

    def get(self, path: str):
        return self._call("GET", path)

    def post(self, path: str, json: Any | None = None):
        return self._call("POST", path, json)

    def put(self, path: str, json: Any | None = None):
        return self._call("PUT", path, json)

    def delete(self, path: str):
        return self._call("DELETE", path)

    # Minimal WebSocket support used in tests
    def websocket_connect(self, path: str):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        path_only, _, query = path.partition("?")
        for route in self.app.routes:
            if route.method == "WS" and route.pattern.match(path_only):
                match = route.pattern.match(path_only)
                kwargs = {
                    name: match.group(i + 1)
                    for i, name in enumerate(route.param_names)
                }
                ws = _WSConnection(self.app, dict(parse_qsl(query)))
                task = loop.create_task(route.func(ws, **kwargs))
                return _WSClient(ws, task, loop)
        raise _HTTPException(404, "Not found")


class _WSConnection:
    def __init__(self, app, query):
        self.app = app
        self.query_params = query
        self._incoming: asyncio.Queue[str] = asyncio.Queue()
        self._outgoing: asyncio.Queue[Any] = asyncio.Queue()

    async def accept(self):
        return None

    async def receive_text(self):
        return await self._incoming.get()

    async def send_json(self, data: Any):
        await self._outgoing.put(data)

    async def close(self):
        return None


class _WSClient:
    def __init__(self, ws: _WSConnection, task: asyncio.Task, loop: asyncio.AbstractEventLoop):
        self.ws = ws
        self.task = task
        self.loop = loop

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.task.cancel()
        self.loop.run_until_complete(self.ws.close())

    def receive_json(self, timeout: float | None = None):
        try:
            return self.loop.run_until_complete(
                asyncio.wait_for(self.ws._outgoing.get(), timeout)
            )
        except asyncio.TimeoutError as e:
            raise TimeoutError from e

fastapi_stub = types.SimpleNamespace(
    FastAPI=_FastAPI,
    APIRouter=_APIRouter,
    Request=_Request,
    WebSocket=_WebSocket,
    WebSocketDisconnect=_WebSocketDisconnect,
    HTTPException=_HTTPException,
    testclient=types.SimpleNamespace(TestClient=_TestClient),
)

sys.modules.setdefault("fastapi", fastapi_stub)
sys.modules.setdefault("fastapi.testclient", types.SimpleNamespace(TestClient=_TestClient))

# Ensure a default event loop exists for tests
asyncio.get_event_loop_policy().set_event_loop(asyncio.new_event_loop())

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
