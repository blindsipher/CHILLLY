from typing import List

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel


router = APIRouter()


class SubscriptionRequest(BaseModel):
    """Request schema for adding a market data subscription."""

    contract_identifier: str


class SubscriptionResponse(BaseModel):
    """Standard response schema for subscription operations."""

    success: bool
    message: str
    active_contracts: List[str]


class ActiveSubscriptionsResponse(BaseModel):
    """Response schema listing active market subscriptions."""

    active_contracts: List[str]


@router.get("/subscriptions", response_model=ActiveSubscriptionsResponse)
async def list_subscriptions(request: Request) -> ActiveSubscriptionsResponse:
    """Return currently active market subscriptions."""

    orchestrator = request.app.state.orchestrator
    service = getattr(orchestrator, "market_subscription_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="MarketSubscriptionService not initialized")

    active = await service.get_active_subscriptions()
    return ActiveSubscriptionsResponse(active_contracts=list(active))


@router.post("/subscriptions", response_model=SubscriptionResponse)
async def add_subscription(
    payload: SubscriptionRequest, request: Request
) -> SubscriptionResponse:
    """Add a new contract to the subscription list."""

    orchestrator = request.app.state.orchestrator
    service = getattr(orchestrator, "market_subscription_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="MarketSubscriptionService not initialized")

    result = await service.add_subscription(payload.contract_identifier)
    return SubscriptionResponse(**result)


@router.delete("/subscriptions/{contract_id}", response_model=SubscriptionResponse)
async def remove_subscription(contract_id: str, request: Request) -> SubscriptionResponse:
    """Remove a contract from the subscription list."""

    orchestrator = request.app.state.orchestrator
    service = getattr(orchestrator, "market_subscription_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="MarketSubscriptionService not initialized")

    result = await service.remove_subscription(contract_id)
    return SubscriptionResponse(**result)
