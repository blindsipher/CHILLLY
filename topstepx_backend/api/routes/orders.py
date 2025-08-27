from fastapi import APIRouter, Request

from topstepx_backend.api.server import OrderRequest, StatusResponse

router = APIRouter()

@router.post("/orders", response_model=StatusResponse)
async def submit_order(order: OrderRequest, request: Request) -> StatusResponse:
    orchestrator = request.app.state.orchestrator
    await orchestrator.submit_order(order.dict())
    return StatusResponse(status="submitted")
