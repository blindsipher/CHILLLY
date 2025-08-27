"""Enumerations for TopstepX trading system data types."""

from enum import Enum


class OrderType(Enum):
    """Order types supported by TopstepX API."""

    UNKNOWN = "Unknown"  # Deprecated
    LIMIT = "Limit"  # 1 - Limit order
    MARKET = "Market"  # 2 - Market order
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
