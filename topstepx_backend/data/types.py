"""Data types for TopstepX trading system."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from enum import Enum


@dataclass
class Bar:
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

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for EventBus and persistence with explicit UTC timestamp."""
        return {
            "timestamp": self.timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "contract_id": self.contract_id,
            "timeframe": self.timeframe,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
            "source": self.source,
            "revision": self.revision,
        }


@dataclass
class BarRequest:
    """Request configuration for bar data retrieval."""

    contract_id: str
    timeframe: str = "1m"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: Optional[int] = None
    ascending: bool = True


# Order Management Types


class OrderType(Enum):
    """
    Order types supported by TopstepX API.
    Based on ProjectX Gateway API comprehensive specification.
    Note: ProjectX Gateway does not support type 0 or type 3 (Stop-Limit)
    """

    UNKNOWN = "Unknown"  # DEPRECATED - Not a valid ProjectX order type, will be rejected
    LIMIT = "Limit"  # 1 - Limit order
    MARKET = "Market"  # 2 - Market order
    # STOP_LIMIT removed - Not supported by ProjectX Gateway API
    STOP = "Stop"  # 4 - Stop order
    TRAILING_STOP = "TrailingStop"  # 5 - Trailing stop
    JOIN_BID = "JoinBid"  # 6 - Join bid (market making)
    JOIN_ASK = "JoinAsk"  # 7 - Join ask (market making)


class OrderSide(Enum):
    """Order side (buy/sell)."""

    BUY = "Buy"
    SELL = "Sell"


class TimeInForce(Enum):
    """Time in force options for orders."""

    DAY = "Day"
    GTC = "GTC"  # Good Till Canceled
    IOC = "IOC"  # Immediate or Cancel
    FOK = "FOK"  # Fill or Kill


class OrderStatus(Enum):
    """Order status from TopstepX API."""

    PENDING = "Pending"
    ACCEPTED = "Accepted"
    WORKING = "Working"
    FILLED = "Filled"
    PARTIAL_FILL = "PartialFill"
    CANCELED = "Canceled"
    REJECTED = "Rejected"
    EXPIRED = "Expired"


@dataclass
class OrderIntent:
    """
    Strategy order intent - what a strategy wants to do.
    Published by strategies, consumed by OrderService.
    """

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

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for API payload."""
        return {
            "strategy_id": self.strategy_id,
            "account_id": self.account_id,
            "contract_id": self.contract_id,
            "type": self.type.value,
            "side": self.side.value,
            "size": self.size,
            "limit_price": self.limit_price,
            "stop_price": self.stop_price,
            "trail_price": self.trail_price,
            "time_in_force": self.time_in_force.value,
            "custom_tag": self.custom_tag,
        }


@dataclass
class OrderAck:
    """
    Order acknowledgment from API.
    Published by OrderService after REST call.
    """

    custom_tag: str
    timestamp: datetime
    order_id: Optional[int] = None
    status: Optional[OrderStatus] = None
    success: Optional[bool] = None
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "order_id": self.order_id,
            "custom_tag": self.custom_tag,
            "status": self.status.value if self.status else None,
            "success": self.success,
            "timestamp": self.timestamp.isoformat(),
            "error_message": self.error_message,
        }


@dataclass
class OrderFill:
    """
    Order fill notification from SignalR.
    Published by UserHubAgent after receiving fill data.
    """

    order_id: int
    custom_tag: str
    price: float
    size: int
    fees: float
    timestamp: datetime
    pnl: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "order_id": self.order_id,
            "custom_tag": self.custom_tag,
            "price": self.price,
            "size": self.size,
            "fees": self.fees,
            "timestamp": self.timestamp.isoformat(),
            "pnl": self.pnl,
        }


@dataclass
class PositionUpdate:
    """
    Position update from SignalR.
    Published by UserHubAgent.
    """

    account_id: int
    contract_id: str
    size: int
    average_price: float
    unrealized_pnl: Optional[float]
    timestamp: datetime

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "account_id": self.account_id,
            "contract_id": self.contract_id,
            "size": self.size,
            "average_price": self.average_price,
            "unrealized_pnl": self.unrealized_pnl,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class TradeUpdate:
    """
    Trade update from SignalR.
    Published by UserHubAgent.
    """

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

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "trade_id": self.trade_id,
            "account_id": self.account_id,
            "contract_id": self.contract_id,
            "price": self.price,
            "size": self.size,
            "side": self.side.value,
            "fees": self.fees,
            "pnl": self.pnl,
            "timestamp": self.timestamp.isoformat(),
            "order_id": self.order_id,
        }
