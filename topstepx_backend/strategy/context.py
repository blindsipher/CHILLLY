"""Strategy context providing access to services and utilities."""

import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional, TYPE_CHECKING

from topstepx_backend.data.types import OrderIntent, OrderType, OrderSide, TimeInForce
from topstepx_backend.core.topics import order_request_submit
from topstepx_backend.networking.api_helpers import utc_now

if TYPE_CHECKING:
    from topstepx_backend.core.event_bus import EventBus


@dataclass
class RiskLimits:
    """Risk management limits for strategy."""

    max_position_size: int
    max_daily_loss: float
    max_order_size: int
    max_orders_per_minute: int = 10


@dataclass
class StrategyContext:
    """
    Context object providing strategy access to system services.

    This encapsulates all external dependencies that strategies need,
    making strategies easier to test and more isolated.
    """

    strategy_id: str
    event_bus: "EventBus"
    logger: logging.Logger
    account_id: int
    contract_id: str
    timeframe: str
    risk_limits: RiskLimits

    # Runtime state
    _order_count: int = 0
    _last_order_time: Optional[float] = None

    async def emit_order(self, intent: OrderIntent) -> bool:
        """
        Submit an order intent to the OrderService.

        Args:
            intent: Order intent with strategy requirements

        Returns:
            True if order was submitted successfully, False otherwise
        """
        try:
            # Risk checks
            if not self._check_risk_limits(intent):
                self.logger.warning(f"Order blocked by risk limits: {intent}")
                return False

            # Ensure strategy_id and account_id match context
            intent.strategy_id = self.strategy_id
            intent.account_id = self.account_id

            # Generate unique custom tag for order correlation
            if not intent.custom_tag:
                intent.custom_tag = f"{self.strategy_id}_{self._order_count}_{int(utc_now().timestamp())}"

            # Publish order intent
            await self.event_bus.publish(order_request_submit(), intent.to_dict())

            self._order_count += 1
            self._last_order_time = utc_now().timestamp()

            self.logger.info(f"Order intent submitted: {intent.custom_tag}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to submit order intent: {e}")
            return False

    async def buy_market(self, size: int, custom_tag: Optional[str] = None) -> bool:
        """
        Convenience method for market buy orders.

        Args:
            size: Order size (positive integer)
            custom_tag: Optional custom tag for order tracking

        Returns:
            True if order was submitted successfully
        """
        intent = OrderIntent(
            strategy_id=self.strategy_id,
            account_id=self.account_id,
            contract_id=self.contract_id,
            type=OrderType.MARKET,
            side=OrderSide.BUY,
            size=size,
            custom_tag=custom_tag,
        )
        return await self.emit_order(intent)

    async def sell_market(self, size: int, custom_tag: Optional[str] = None) -> bool:
        """
        Convenience method for market sell orders.

        Args:
            size: Order size (positive integer)
            custom_tag: Optional custom tag for order tracking

        Returns:
            True if order was submitted successfully
        """
        intent = OrderIntent(
            strategy_id=self.strategy_id,
            account_id=self.account_id,
            contract_id=self.contract_id,
            type=OrderType.MARKET,
            side=OrderSide.SELL,
            size=size,
            custom_tag=custom_tag,
        )
        return await self.emit_order(intent)

    async def buy_limit(
        self,
        size: int,
        price: float,
        time_in_force: TimeInForce = TimeInForce.DAY,
        custom_tag: Optional[str] = None,
    ) -> bool:
        """
        Convenience method for limit buy orders.

        Args:
            size: Order size (positive integer)
            price: Limit price
            time_in_force: Order time in force
            custom_tag: Optional custom tag for order tracking

        Returns:
            True if order was submitted successfully
        """
        intent = OrderIntent(
            strategy_id=self.strategy_id,
            account_id=self.account_id,
            contract_id=self.contract_id,
            type=OrderType.LIMIT,
            side=OrderSide.BUY,
            size=size,
            limit_price=price,
            time_in_force=time_in_force,
            custom_tag=custom_tag,
        )
        return await self.emit_order(intent)

    async def sell_limit(
        self,
        size: int,
        price: float,
        time_in_force: TimeInForce = TimeInForce.DAY,
        custom_tag: Optional[str] = None,
    ) -> bool:
        """
        Convenience method for limit sell orders.

        Args:
            size: Order size (positive integer)
            price: Limit price
            time_in_force: Order time in force
            custom_tag: Optional custom tag for order tracking

        Returns:
            True if order was submitted successfully
        """
        intent = OrderIntent(
            strategy_id=self.strategy_id,
            account_id=self.account_id,
            contract_id=self.contract_id,
            type=OrderType.LIMIT,
            side=OrderSide.SELL,
            size=size,
            limit_price=price,
            time_in_force=time_in_force,
            custom_tag=custom_tag,
        )
        return await self.emit_order(intent)

    async def trailing_stop(
        self,
        side: OrderSide,
        size: int,
        trail_price: float,
        time_in_force: TimeInForce = TimeInForce.DAY,
        custom_tag: Optional[str] = None,
    ) -> bool:
        """
        Convenience method for trailing stop orders.

        Args:
            side: Order side (BUY or SELL)
            size: Order size (positive integer)
            trail_price: Trailing stop distance
            time_in_force: Order time in force
            custom_tag: Optional custom tag for order tracking

        Returns:
            True if order was submitted successfully
        """
        intent = OrderIntent(
            strategy_id=self.strategy_id,
            account_id=self.account_id,
            contract_id=self.contract_id,
            type=OrderType.TRAILING_STOP,
            side=side,
            size=size,
            trail_price=trail_price,
            time_in_force=time_in_force,
            custom_tag=custom_tag,
        )
        return await self.emit_order(intent)

    def _check_risk_limits(self, intent: OrderIntent) -> bool:
        """
        Check if order intent complies with risk limits.

        Args:
            intent: Order intent to check

        Returns:
            True if order passes risk checks
        """
        # Check order size limit
        if intent.size > self.risk_limits.max_order_size:
            self.logger.warning(
                f"Order size {intent.size} exceeds limit {self.risk_limits.max_order_size}"
            )
            return False

        # Check rate limiting
        current_time = utc_now().timestamp()
        if self._last_order_time:
            time_since_last = current_time - self._last_order_time
            if time_since_last < 60:  # Within last minute
                if self._order_count >= self.risk_limits.max_orders_per_minute:
                    self.logger.warning(
                        f"Rate limit exceeded: {self._order_count} orders in last minute"
                    )
                    return False

        return True

    def get_metrics(self) -> Dict[str, Any]:
        """
        Get strategy context metrics.

        Returns:
            Dictionary with context metrics
        """
        return {
            "strategy_id": self.strategy_id,
            "account_id": self.account_id,
            "contract_id": self.contract_id,
            "timeframe": self.timeframe,
            "order_count": self._order_count,
            "last_order_time": self._last_order_time,
            "risk_limits": {
                "max_position_size": self.risk_limits.max_position_size,
                "max_daily_loss": self.risk_limits.max_daily_loss,
                "max_order_size": self.risk_limits.max_order_size,
                "max_orders_per_minute": self.risk_limits.max_orders_per_minute,
            },
        }
