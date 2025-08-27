from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
import uvicorn

from topstepx_backend.core.service import Service
from topstepx_backend.api.ws_gateway import WebSocketGateway
from topstepx_backend.auth.auth_manager import AuthenticationError

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


class StatusResponse(BaseModel):
    """Standard response schema with a status message."""

    status: str

    class Config:
        extra = "allow"


class StrategyResponse(BaseModel):
    """Response schema for strategy operations."""

    status: str


class TokenRequest(BaseModel):
    """Request schema for token issuance."""

    username: str
    api_key: str


class TokenResponse(BaseModel):
    """Response schema containing issued token."""

    token: str


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

        @self.app.get("/status", response_model=StatusResponse)
        async def status() -> StatusResponse:
            return StatusResponse(**self.orchestrator.get_system_status())

        @self.app.post("/orders", response_model=StatusResponse)
        async def submit_order(order: OrderRequest) -> StatusResponse:
            await self.orchestrator.submit_order(order.dict())
            return StatusResponse(status="submitted")

        @self.app.post("/strategies", response_model=StrategyResponse)
        async def add_strategy(config: Dict[str, Any]) -> StrategyResponse:
            await self.orchestrator.add_strategy(config)
            return StrategyResponse(status="added")

        @self.app.delete("/strategies/{strategy_id}", response_model=StrategyResponse)
        async def remove_strategy(strategy_id: str) -> StrategyResponse:
            await self.orchestrator.remove_strategy(strategy_id)
            return StrategyResponse(status="removed")

        @self.app.post("/auth/token", response_model=TokenResponse)
        async def auth_token(request: TokenRequest) -> TokenResponse:
            try:
                token = self.orchestrator.auth_manager.validate_credentials(
                    request.username, request.api_key
                )
            except AuthenticationError:
                raise HTTPException(status_code=401, detail='Invalid credentials')
            return TokenResponse(token=token)


        async def _ws_handler(
            websocket: WebSocket, token: str, path_patterns: str = ""
        ) -> None:
            await websocket.accept()
            try:
                patterns = []
                if path_patterns:
                    patterns.extend([p for p in path_patterns.split(",") if p])
                query = websocket.query_params.get("patterns")
                if query:
                    patterns.extend([p for p in query.split(",") if p])
                await self.ws_gateway.connect(websocket, token, patterns)
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                pass
            finally:
                await self.ws_gateway.disconnect(websocket)

        @self.app.websocket("/ws/{token}")
        async def websocket_endpoint(websocket: WebSocket, token: str) -> None:
            await _ws_handler(websocket, token)

        @self.app.websocket("/ws/{token}/{path_patterns:path}")
        async def websocket_endpoint_patterns(
            websocket: WebSocket, token: str, path_patterns: str
        ) -> None:
            await _ws_handler(websocket, token, path_patterns)

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
