"""Tiny subset of the ``uvicorn`` API used in tests.

The real project relies on uvicorn to serve the FastAPI application but for
testing purposes we only need the configuration container and a dummy ``Server``
class that exposes ``should_exit`` and ``serve``.  This stub keeps the code
importable without pulling in the heavy dependency.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass


@dataclass
class Config:
    app: object
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "info"


class Server:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.should_exit = False

    async def serve(self) -> None:  # pragma: no cover - not exercised in tests
        while not self.should_exit:
            await asyncio.sleep(0.1)


__all__ = ["Config", "Server"]

