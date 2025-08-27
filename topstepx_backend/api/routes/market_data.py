from typing import Any, Dict, List
import inspect

try:  # pragma: no cover
    from fastapi import APIRouter, Request  # type: ignore
except Exception:  # pragma: no cover
    from topstepx_backend.api.router_stub import APIRouter, Request  # type: ignore

router = APIRouter()


def _call(orchestrator: Any, name: str, *args, default: Any = None) -> Any:
    func = getattr(orchestrator, name, None)
    if func is None:
        return default
    return func(*args)


@router.get("/contracts")
async def get_contracts(request: Request) -> List[Any]:
    orchestrator = request.app.state.orchestrator
    result = _call(orchestrator, "get_contracts", default=[])
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/market-data/{contract}")
async def get_market_data(contract: str, request: Request) -> Dict[str, Any]:
    orchestrator = request.app.state.orchestrator
    result = _call(orchestrator, "get_market_data", contract, default={})
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/bars/{contract}")
async def get_bars(contract: str, request: Request) -> List[Any]:
    orchestrator = request.app.state.orchestrator
    result = _call(orchestrator, "get_bars", contract, default=[])
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/quotes/{contract}")
async def get_quotes(contract: str, request: Request) -> Dict[str, Any]:
    orchestrator = request.app.state.orchestrator
    result = _call(orchestrator, "get_quotes", contract, default={})
    if inspect.isawaitable(result):
        result = await result
    return result
