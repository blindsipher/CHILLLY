from typing import Any, Dict, List
import inspect

from fastapi import APIRouter, Request

router = APIRouter()


def _call_orchestrator(orchestrator: Any, name: str, *args, default: Any = None) -> Any:
    func = getattr(orchestrator, name, None)
    if func is None:
        return default
    return func(*args)


@router.get("/positions")
async def get_positions(request: Request) -> List[Dict[str, Any]]:
    orchestrator = request.app.state.orchestrator
    result = _call_orchestrator(orchestrator, "get_positions", default=[])
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/account")
async def get_account(request: Request) -> Dict[str, Any]:
    orchestrator = request.app.state.orchestrator
    result = _call_orchestrator(orchestrator, "get_account", default={})
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/trades")
async def get_trades(request: Request) -> List[Dict[str, Any]]:
    orchestrator = request.app.state.orchestrator
    result = _call_orchestrator(orchestrator, "get_trades", default=[])
    if inspect.isawaitable(result):
        result = await result
    return result
