"""Lightweight stubs mimicking a tiny subset of FastAPI.

The real FastAPI framework is not available in this execution environment, but
the project relies on a few of its abstractions for routing and testing.  This
module provides minimal stand-ins that implement just enough behaviour for the
unit tests to exercise the surrounding application logic without requiring the
actual dependency.

Only the features used in the tests are implemented: ``FastAPI`` and
``APIRouter`` for registering HTTP and WebSocket routes, ``Request`` objects for
passing application state, a handful of exception classes, and WebSocket helper
types.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Exceptions


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


class WebSocketDisconnect(Exception):
    pass


# ---------------------------------------------------------------------------
# Request / WebSocket helpers


class Request:
    def __init__(self, app: "FastAPI", json: Any = None, query_params: Dict[str, str] | None = None) -> None:
        self.app = app
        self.state = app.state
        self.json = json
        self.query_params = query_params or {}


class WebSocket:
    """Minimal WebSocket used only for tests."""

    def __init__(self, query_params: Dict[str, str] | None = None) -> None:
        self.query_params = query_params or {}
        self._send_queue: asyncio.Queue[str] = asyncio.Queue()
        self._closed = asyncio.Event()

    async def accept(self) -> None:  # pragma: no cover - no behaviour required
        return

    async def receive_text(self) -> str:
        await self._closed.wait()
        raise WebSocketDisconnect()

    async def send(self, text: str) -> None:
        await self._send_queue.put(text)

    async def close(self) -> None:
        self._closed.set()

    async def _client_receive(self, timeout: Optional[float] = None) -> str:
        return await asyncio.wait_for(self._send_queue.get(), timeout)


# ---------------------------------------------------------------------------
# Routing helpers


def _compile_path(path: str) -> re.Pattern[str]:
    """Translate FastAPI style paths into regular expressions."""

    def repl(match: re.Match[str]) -> str:
        name, modifier = match.groups()
        if modifier == ":path":
            return f"(?P<{name}>.*)"
        return f"(?P<{name}>[^/]+)"

    pattern = re.sub(r"{([^}:]+)(:path)?}", repl, path)
    return re.compile("^" + pattern + "$")


class _RouterBase:
    def __init__(self) -> None:
        self.routes: List[Tuple[List[str], re.Pattern[str], Callable[..., Any]]] = []
        self.websockets: List[Tuple[re.Pattern[str], Callable[..., Any]]] = []

    # HTTP routes ---------------------------------------------------------
    def add_api_route(self, path: str, endpoint: Callable[..., Any], methods: Iterable[str]) -> None:
        self.routes.append((list(methods), _compile_path(path), endpoint))

    def get(self, path: str, response_model: Any | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(path, func, ["GET"])
            return func

        return decorator

    def post(self, path: str, response_model: Any | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(path, func, ["POST"])
            return func

        return decorator

    def delete(self, path: str, response_model: Any | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(path, func, ["DELETE"])
            return func

        return decorator

    def put(self, path: str, response_model: Any | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.add_api_route(path, func, ["PUT"])
            return func

        return decorator

    # WebSocket routes ----------------------------------------------------
    def websocket(self, path: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self.websockets.append((_compile_path(path), func))
            return func

        return decorator


class APIRouter(_RouterBase):
    pass


class FastAPI(_RouterBase):
    def __init__(self) -> None:
        super().__init__()
        self.state = SimpleNamespace()

    def include_router(self, router: APIRouter) -> None:
        self.routes.extend(router.routes)
        self.websockets.extend(router.websockets)

    # Internal dispatch helpers used by the test client ------------------
    def _match_route(self, method: str, path: str) -> Tuple[Optional[Callable[..., Any]], Dict[str, str]]:
        for methods, pattern, endpoint in self.routes:
            match = pattern.match(path)
            if match and method in methods:
                return endpoint, match.groupdict()
        return None, {}

    def _match_websocket(self, path: str) -> Tuple[Optional[Callable[..., Any]], Dict[str, str]]:
        for pattern, endpoint in self.websockets:
            match = pattern.match(path)
            if match:
                return endpoint, match.groupdict()
        return None, {}


__all__ = [
    "APIRouter",
    "FastAPI",
    "HTTPException",
    "Request",
    "WebSocket",
    "WebSocketDisconnect",
]

