from typing import Any, Dict
import inspect

try:  # pragma: no cover - fastapi may be stubbed in tests
    from fastapi import APIRouter, Request  # type: ignore
except Exception:  # pragma: no cover - fallback
    from topstepx_backend.api.router_stub import APIRouter, Request  # type: ignore

from topstepx_backend.api.server import OrderRequest, StatusResponse

router = APIRouter()

@router.post("/orders", response_model=StatusResponse)
async def submit_order(order: OrderRequest, request: Request) -> StatusResponse:
    orchestrator = request.app.state.orchestrator
    await orchestrator.submit_order(order.dict())
    return StatusResponse(status="submitted")


@router.get("/orders")
async def list_orders(request: Request) -> Dict[str, Any]:
    orchestrator = request.app.state.orchestrator
    getter = getattr(orchestrator, "get_orders", None)
    if getter is not None:
        result = getter()
        if inspect.isawaitable(result):
            result = await result
        return result

    order_service = getattr(orchestrator, "order_service", None)
    if order_service and hasattr(order_service, "get_all_orders"):
        return order_service.get_all_orders()
    return {}


@router.get("/orders/{order_id}")
async def get_order(order_id: str, request: Request) -> Dict[str, Any]:
    orchestrator = request.app.state.orchestrator
    getter = getattr(orchestrator, "get_order", None)
    if getter is not None:
        result = getter(order_id)
        if inspect.isawaitable(result):
            result = await result
        return result

    order_service = getattr(orchestrator, "order_service", None)
    if order_service and hasattr(order_service, "get_all_orders"):
        orders = order_service.get_all_orders().values()
        for order in orders:
            if str(order.get("order_id")) == order_id or str(order.get("custom_tag")) == order_id:
                return order
    return {}
