"""Very small synchronous test client for the FastAPI stubs."""

from __future__ import annotations

import asyncio
import json
import inspect
from typing import Any, Dict, Optional

from . import HTTPException, Request, WebSocket


class Response:
    def __init__(self, status_code: int, data: Any) -> None:
        self.status_code = status_code
        self._data = data

    def json(self) -> Any:
        if hasattr(self._data, "dict"):
            return self._data.dict()
        return self._data


class TestClient:
    """Minimal interface compatible with ``fastapi.testclient.TestClient``."""

    def __init__(self, app) -> None:
        self.app = app
        try:
            self.loop = asyncio.get_event_loop()
        except RuntimeError:  # pragma: no cover - no loop running
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)

    # --------------------------------------------------------------
    def _request(self, method: str, path: str, json_body: Dict[str, Any] | None = None) -> Response:
        endpoint, params = self.app._match_route(method, path)
        if endpoint is None:
            return Response(404, {"detail": "Not found"})

        kwargs = dict(params)
        sig = inspect.signature(endpoint)
        if "request" in sig.parameters:
            kwargs["request"] = Request(self.app, json_body)

        # populate body argument if present
        if json_body is not None:
            for name, param in sig.parameters.items():
                if name in kwargs:
                    continue
                anno = param.annotation
                mro = getattr(anno, "__mro__", ())
                if any(cls.__name__ == "BaseModel" for cls in mro):
                    kwargs[name] = anno(**json_body)  # type: ignore[call-arg]
                else:
                    kwargs[name] = json_body
                break

        result = endpoint(**kwargs)
        if inspect.iscoroutine(result):
            result = self.loop.run_until_complete(result)

        if isinstance(result, Response):
            return result
        return Response(200, result)

    # public HTTP verbs
    def get(self, path: str) -> Response:
        return self._request("GET", path)

    def post(self, path: str, json: Dict[str, Any] | None = None) -> Response:
        return self._request("POST", path, json)

    def delete(self, path: str) -> Response:
        return self._request("DELETE", path)

    def put(self, path: str, json: Dict[str, Any] | None = None) -> Response:
        return self._request("PUT", path, json)

    # --------------------------------------------------------------
    # WebSocket support

    class _WSContext:
        def __init__(self, loop: asyncio.AbstractEventLoop, ws: WebSocket, task: asyncio.Task[Any]) -> None:
            self.loop = loop
            self.ws = ws
            self.task = task

        def __enter__(self) -> "TestClient._WSContext":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            self.loop.run_until_complete(self.ws.close())
            self.loop.run_until_complete(self.task)

        def receive_json(self, timeout: Optional[float] = None) -> Any:
            try:
                data = self.loop.run_until_complete(self.ws._client_receive(timeout))
            except asyncio.TimeoutError as exc:  # pragma: no cover - used in tests
                raise TimeoutError() from exc
            return json.loads(data)

    def websocket_connect(self, path: str) -> "TestClient._WSContext":
        if "?" in path:
            path_only, qs = path.split("?", 1)
            query_params = dict(q.split("=") for q in qs.split("&") if q)
        else:
            path_only, query_params = path, {}

        endpoint, params = self.app._match_websocket(path_only)
        if endpoint is None:
            raise RuntimeError("WebSocket path not found")

        ws = WebSocket(query_params)
        kwargs = dict(params)
        kwargs["websocket"] = ws

        task = self.loop.create_task(endpoint(**kwargs))
        return TestClient._WSContext(self.loop, ws, task)


__all__ = ["TestClient", "Response"]

