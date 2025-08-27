from typing import Any, Dict

from fastapi import APIRouter, Request

from topstepx_backend.api.server import StrategyResponse

router = APIRouter()

@router.post("/strategies", response_model=StrategyResponse)
async def add_strategy(config: Dict[str, Any], request: Request) -> StrategyResponse:
    orchestrator = request.app.state.orchestrator
    await orchestrator.add_strategy(config)
    return StrategyResponse(status="added")

@router.delete("/strategies/{strategy_id}", response_model=StrategyResponse)
async def remove_strategy(strategy_id: str, request: Request) -> StrategyResponse:
    orchestrator = request.app.state.orchestrator
    await orchestrator.remove_strategy(strategy_id)
    return StrategyResponse(status="removed")
