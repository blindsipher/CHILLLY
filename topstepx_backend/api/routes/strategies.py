from typing import Any, Dict, List
import inspect

try:  # pragma: no cover
    from fastapi import APIRouter, Request  # type: ignore
except Exception:  # pragma: no cover
    from topstepx_backend.api.router_stub import APIRouter, Request  # type: ignore

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


@router.get("/strategies")
async def list_strategies(request: Request) -> List[Dict[str, Any]]:
    orchestrator = request.app.state.orchestrator
    getter = getattr(orchestrator, "list_strategies", None)
    if getter is None:
        return []
    result = getter()
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/strategies/available")
async def available_strategies(request: Request) -> List[Any]:
    orchestrator = request.app.state.orchestrator
    getter = getattr(orchestrator, "list_available_strategies", None)
    if getter is None:
        return []
    result = getter()
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/strategies/{strategy_id}/performance")
async def strategy_performance(strategy_id: str, request: Request) -> Dict[str, Any]:
    orchestrator = request.app.state.orchestrator
    getter = getattr(orchestrator, "get_strategy_performance", None)
    if getter is None:
        return {}
    result = getter(strategy_id)
    if inspect.isawaitable(result):
        result = await result
    return result


@router.put("/strategies/{strategy_id}")
async def update_strategy(
    strategy_id: str, config: Dict[str, Any], request: Request
) -> StrategyResponse:
    orchestrator = request.app.state.orchestrator
    updater = getattr(orchestrator, "update_strategy", None)
    if updater is not None:
        result = updater(strategy_id, config)
        if inspect.isawaitable(result):
            await result
    return StrategyResponse(status="updated")
