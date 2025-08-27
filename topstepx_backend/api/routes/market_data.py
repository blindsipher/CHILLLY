from typing import Any, Dict, List
import inspect

from fastapi import APIRouter, Request

router = APIRouter()


def _call(
    orchestrator: Any, name: str, *args, default: Any = None, **kwargs
) -> Any:
    func = getattr(orchestrator, name, None)
    if func is None:
        return default
    return func(*args, **kwargs)


@router.get("/contracts")
async def get_contracts(
    request: Request, symbol: str | None = None, sector: str | None = None
) -> List[Any]:
    orchestrator = request.app.state.orchestrator
    kwargs = {}
    if symbol is not None:
        kwargs["symbol"] = symbol
    if sector is not None:
        kwargs["sector"] = sector
    result = _call(orchestrator, "get_contracts", default=[], **kwargs)
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
