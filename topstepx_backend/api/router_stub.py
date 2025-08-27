"""Fallback implementations for FastAPI routing utilities used in tests.

These classes implement just enough behaviour for the unit tests so that the
route modules can be imported without the real FastAPI dependency.  Each router
simply records registered handlers in a dictionary keyed by HTTP method and
path, allowing the lightweight test client to dispatch requests.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable, Dict, Tuple


class Request:  # pragma: no cover - trivial container
    def __init__(self) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace())


class APIRouter:
    def __init__(self) -> None:
        self.routes: Dict[Tuple[str, str], Callable[..., Any]] = {}

    def _add(self, method: str, path: str, func: Callable[..., Any]) -> Callable[..., Any]:
        self.routes[(method, path)] = func
        return func

    def post(self, path: str, response_model: Any | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return lambda func: self._add("POST", path, func)

    def get(self, path: str, response_model: Any | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return lambda func: self._add("GET", path, func)

    def delete(self, path: str, response_model: Any | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return lambda func: self._add("DELETE", path, func)

    def put(self, path: str, response_model: Any | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        return lambda func: self._add("PUT", path, func)

