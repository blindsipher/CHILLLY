from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
import uvicorn

from topstepx_backend.core.service import Service
from topstepx_backend.api.ws_gateway import WebSocketGateway

if False:  # pragma: no cover - for type checking only
    from topstepx_backend.orchestrator import TopstepXOrchestrator


class OrderRequest(BaseModel):
    """Pydantic model for order submission requests."""

    strategy_id: str
    account_id: str
    contract_id: str
    type: str
    side: str
    size: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_price: Optional[float] = None
    time_in_force: Optional[str] = "Day"
    custom_tag: Optional[str] = None


class APIServer(Service):
    """FastAPI based HTTP server exposing orchestrator operations."""

    def __init__(
        self,
        orchestrator: "TopstepXOrchestrator",
        host: str = "0.0.0.0",
        port: int = 8000,
    ) -> None:
        super().__init__()
        self.orchestrator = orchestrator
        self.host = host
        self.port = port
        self.app = FastAPI()
        self._server: Optional[uvicorn.Server] = None
        self._server_task: Optional[asyncio.Task] = None
        self.ws_gateway = WebSocketGateway(
            orchestrator.event_bus, orchestrator.auth_manager
        )

        self._setup_routes()

    # ------------------------------------------------------------------
    def _setup_routes(self) -> None:
        """Configure REST and WebSocket endpoints."""

        @self.app.get("/status")
        async def status() -> Dict[str, Any]:
            return self.orchestrator.get_system_status()

        @self.app.post("/orders")
        async def submit_order(order: OrderRequest) -> Dict[str, str]:
            await self.orchestrator.submit_order(order.dict())
            return {"status": "submitted"}

        @self.app.post("/strategies")
        async def add_strategy(config: Dict[str, Any]) -> Dict[str, str]:
            await self.orchestrator.add_strategy(config)
            return {"status": "added"}

        @self.app.delete("/strategies/{strategy_id}")
        async def remove_strategy(strategy_id: str) -> Dict[str, str]:
            await self.orchestrator.remove_strategy(strategy_id)
            return {"status": "removed"}

        @self.app.websocket("/ws/{token}")
        async def websocket_endpoint(websocket: WebSocket, token: str) -> None:
            await websocket.accept()
            try:
                await self.ws_gateway.connect(websocket, token)
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                await self.ws_gateway.disconnect(websocket)

    # ------------------------------------------------------------------
    async def start(self) -> None:  # pragma: no cover - uvicorn handled in integration
        if self._running:
            return
        await self.ws_gateway.start()
        config = uvicorn.Config(self.app, host=self.host, port=self.port, log_level="info")
        self._server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(self._server.serve())
        self._running = True

    async def stop(self) -> None:  # pragma: no cover - uvicorn handled in integration
        if not self._running:
            return
        await self.ws_gateway.stop()
        if self._server:
            self._server.should_exit = True
        if self._server_task:
            await self._server_task
        self._running = False
        self._server = None
        self._server_task = None
