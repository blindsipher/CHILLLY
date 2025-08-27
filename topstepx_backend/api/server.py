"""FastAPI server for exposing orchestrator services."""

from __future__ import annotations

import asyncio
from typing import Optional, List

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import uvicorn

from topstepx_backend.core.topics import order_request_submit


class OrderRequest(BaseModel):
    """Pydantic model representing an order submission request."""

    strategy_id: str
    account_id: int
    contract_id: str
    type: str
    side: str
    size: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_price: Optional[float] = None
    time_in_force: str = "Day"
    custom_tag: Optional[str] = None


class APIServer:
    """Lightweight API server exposing orchestrator services."""

    def __init__(self, orchestrator: "TopstepXOrchestrator", host: str = "0.0.0.0", port: int = 8000):
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.app = FastAPI()
        self._server: Optional[uvicorn.Server] = None
        self._task: Optional[asyncio.Task] = None

        self._setup_routes()

    # ------------------------------------------------------------------
    def _setup_routes(self) -> None:
        """Configure API routes."""

        @self.app.get("/health")
        async def health() -> dict:
            """Return system health and component status."""
            return self.orchestrator.get_system_status()

        @self.app.post("/orders")
        async def submit_order(order: OrderRequest) -> dict:
            """Submit an order via the event bus."""
            if not self.orchestrator.event_bus:
                raise HTTPException(status_code=503, detail="Event bus unavailable")
            payload = (
                order.model_dump() if hasattr(order, "model_dump") else order.dict()
            )
            await self.orchestrator.event_bus.publish(
                order_request_submit(), payload
            )
            return {"status": "submitted"}

        @self.app.get("/chart/{contract_id}/{timeframe}")
        async def get_chart(
            contract_id: str,
            timeframe: str,
            limit: Optional[int] = None,
            include_partial: bool = True,
        ) -> List[dict]:
            """Return cached chart data from SeriesCacheService."""
            service = self.orchestrator.series_cache_service
            if not service:
                raise HTTPException(status_code=503, detail="Chart service unavailable")
            return await service.get_series_data(
                contract_id, timeframe, limit=limit, include_partial=include_partial
            )

    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Start the Uvicorn server in the background."""
        config = uvicorn.Config(
            self.app, host=self.host, port=self.port, log_level="info", loop="asyncio"
        )
        self._server = uvicorn.Server(config)
        self._task = asyncio.create_task(self._server.serve())
        # Wait for the server to start or fail
        while not self._server.started and not self._task.done():
            await asyncio.sleep(0.05)
        if self._task.done() and self._task.exception():
            raise self._task.exception()

    async def stop(self) -> None:
        """Stop the Uvicorn server."""
        if self._server:
            self._server.should_exit = True
        if self._task:
            await self._task
            self._task = None
        self._server = None

