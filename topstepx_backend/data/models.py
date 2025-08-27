from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from .types import OrderType, OrderSide, TimeInForce, OrderStatus


class Bar(BaseModel):
    """Standard bar data structure for messaging."""

    timestamp: datetime
    contract_id: str
    timeframe: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    source: str  # 'live', 'history', 'reconciled'
    revision: int = 1


class BarRequest(BaseModel):
    """Request configuration for bar data retrieval."""

    contract_id: str
    timeframe: str = "1m"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: Optional[int] = None
    ascending: bool = True


class OrderIntent(BaseModel):
    """Strategy order intent - what a strategy wants to do."""

    strategy_id: str
    account_id: int
    contract_id: str
    type: OrderType
    side: OrderSide
    size: int
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    trail_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    custom_tag: Optional[str] = None


class OrderAck(BaseModel):
    """Order acknowledgment from API."""

    custom_tag: str
    timestamp: datetime
    order_id: Optional[int] = None
    status: Optional[OrderStatus] = None
    success: Optional[bool] = None
    error_message: Optional[str] = None


class OrderFill(BaseModel):
    """Order fill notification from SignalR."""

    order_id: int
    custom_tag: Optional[str] = None
    price: float
    size: int
    fees: float
    timestamp: datetime
    pnl: Optional[float] = None


class PositionUpdate(BaseModel):
    """Position update from SignalR."""

    account_id: int
    contract_id: str
    size: int
    average_price: float
    unrealized_pnl: Optional[float]
    timestamp: datetime


class TradeUpdate(BaseModel):
    """Trade update from SignalR."""

    trade_id: int
    account_id: int
    contract_id: str
    price: float
    size: int
    side: OrderSide
    fees: float
    pnl: Optional[float]
    timestamp: datetime
    order_id: Optional[int] = None
