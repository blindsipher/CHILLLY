from typing import Any, Dict, List
import inspect

try:  # pragma: no cover
    from fastapi import APIRouter, Request  # type: ignore
except Exception:  # pragma: no cover
    from topstepx_backend.api.router_stub import APIRouter, Request  # type: ignore

router = APIRouter()


def _call(orchestrator: Any, name: str, default: Any) -> Any:
    func = getattr(orchestrator, name, None)
    if func is None:
        return default
    return func()


@router.get("/health")
async def health(request: Request) -> Dict[str, Any]:
    orchestrator = request.app.state.orchestrator
    result = _call(orchestrator, "get_health", default={})
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/logs")
async def logs(request: Request) -> List[Any]:
    orchestrator = request.app.state.orchestrator
    result = _call(orchestrator, "get_logs", default=[])
    if inspect.isawaitable(result):
        result = await result
    return result


@router.get("/config")
async def config(request: Request) -> Dict[str, Any]:
    orchestrator = request.app.state.orchestrator
    result = _call(orchestrator, "get_config", default={})
    if inspect.isawaitable(result):
        result = await result
    return result
