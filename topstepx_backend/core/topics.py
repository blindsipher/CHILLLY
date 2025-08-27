"""Canonical topic definitions for unified EventBus.

This is the single source of truth for all event topics.
Use these helpers instead of string literals to enable easy refactoring.
"""


def market_bar(symbol: str, timeframe: str) -> str:
    """Topic for market bar events: market.bar.SYMBOL_TF"""
    return f"market.bar.{symbol}_{timeframe}"


def market_quote(contract_id: str) -> str:
    """Topic for real-time quote events: market.quote.CONTRACT_ID"""
    return f"market.quote.{contract_id}"


def market_trade(contract_id: str) -> str:
    """Topic for real-time trade events: market.trade.CONTRACT_ID"""
    return f"market.trade.{contract_id}"


def market_depth(contract_id: str) -> str:
    """Topic for market depth/DOM events: market.depth.CONTRACT_ID"""
    return f"market.depth.{contract_id}"


def boundary(timeframe: str) -> str:
    """Topic for clock boundary events: bar.boundary.TF"""
    return f"bar.boundary.{timeframe}"


def persist_bar_save() -> str:
    """Topic for bar persistence events"""
    return "persistence.bar.save"


def persist_tick_save() -> str:
    """Topic for tick persistence events"""
    return "persistence.tick.save"


# Signal topics (for future use)
def signal_entry(strategy_id: str, symbol: str) -> str:
    """Topic for strategy entry signals"""
    return f"signal.{strategy_id}.entry.{symbol}"


def signal_exit(strategy_id: str, symbol: str) -> str:
    """Topic for strategy exit signals"""
    return f"signal.{strategy_id}.exit.{symbol}"


# Order topics
def order_request_submit() -> str:
    """Topic for order submission requests"""
    return "order.request.submit"


def order_request_cancel() -> str:
    """Topic for order cancellation requests"""
    return "order.request.cancel"


def order_request_modify() -> str:
    """Topic for order modification requests"""
    return "order.request.modify"


def order_ack() -> str:
    """Topic for order acknowledgments from API"""
    return "order.ack"


def order_status_update() -> str:
    """Topic for order status updates from SignalR"""
    return "order.status.update"


def order_fill_update() -> str:
    """Topic for order fill updates from SignalR"""
    return "order.fill.update"


# Account topics
def account_position_update() -> str:
    """Topic for position updates"""
    return "account.position.update"


def account_balance_update() -> str:
    """Topic for balance updates"""
    return "account.balance.update"


def trade_update() -> str:
    """Topic for trade updates from SignalR"""
    return "trade.update"

# Strategy management topics
def strategy_add() -> str:
    """Topic for runtime strategy addition"""
    return "system.strategy.add"


def strategy_remove() -> str:
    """Topic for runtime strategy removal"""
    return "system.strategy.remove"
