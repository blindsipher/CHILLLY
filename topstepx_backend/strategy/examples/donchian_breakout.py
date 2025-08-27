"""Donchian Channel Breakout strategy example."""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext
from topstepx_backend.data.types import Bar


@dataclass
class DonchianState:
    """State for Donchian Channel indicators."""

    high_prices: List[float]
    low_prices: List[float]
    upper_channel: Optional[float] = None
    lower_channel: Optional[float] = None
    last_breakout: Optional[str] = None  # 'high' or 'low'


class DonchianStrategy(Strategy):
    """
    Donchian Channel Breakout strategy.

    Entry Rules:
    - Price breaks above upper channel (N-period high) -> BUY
    - Price breaks below lower channel (N-period low) -> SELL

    Exit Rules:
    - Stop loss after X ticks against position
    - Profit target after Y ticks in favor
    - Exit on opposite channel breakout
    """

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        super().__init__(strategy_id, params)

        # Strategy parameters
        self.high_period = params.get("high_period", 20)
        self.low_period = params.get("low_period", 20)
        self.position_size = params.get("position_size", 1)
        self.stop_loss_ticks = params.get("stop_loss_ticks", 10)
        self.profit_target_ticks = params.get("profit_target_ticks", 20)

        # Indicator state
        self.state = DonchianState(high_prices=[], low_prices=[])

        # Position tracking
        self.current_position = 0  # 0 = flat, positive = long, negative = short
        self.entry_price: Optional[float] = None
        self.stop_price: Optional[float] = None
        self.target_price: Optional[float] = None

        # Assume ES tick size for example (0.25 points)
        self.tick_size = 0.25

    def validate_params(self) -> bool:
        """Validate strategy parameters."""
        if self.high_period < 2:
            self.logger.error("High period must be >= 2")
            return False

        if self.low_period < 2:
            self.logger.error("Low period must be >= 2")
            return False

        if self.position_size <= 0:
            self.logger.error("Position size must be > 0")
            return False

        if self.stop_loss_ticks <= 0:
            self.logger.error("Stop loss ticks must be > 0")
            return False

        if self.profit_target_ticks <= 0:
            self.logger.error("Profit target ticks must be > 0")
            return False

        return True

    async def on_start(self, ctx: StrategyContext) -> None:
        """Initialize strategy on startup."""
        self.logger.info(
            f"Starting Donchian Breakout strategy with params: "
            f"High={self.high_period}, Low={self.low_period}, "
            f"Size={self.position_size}, Stop={self.stop_loss_ticks}, "
            f"Target={self.profit_target_ticks}"
        )

        # Reset state
        self.state = DonchianState(high_prices=[], low_prices=[])
        self.current_position = 0
        self.entry_price = None
        self.stop_price = None
        self.target_price = None

    async def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        """Process new bar data."""
        try:
            # Update indicators
            self._update_donchian_channels(bar.high, bar.low)

            # Skip if channels not ready
            if not self._channels_ready():
                self.logger.debug(
                    f"Channels not ready - highs: {len(self.state.high_prices)}, "
                    f"lows: {len(self.state.low_prices)}"
                )
                return

            # Check for exit conditions first (stop/target hit)
            if self.current_position != 0:
                exit_signal = self._check_exit_conditions(bar.high, bar.low, bar.close)
                if exit_signal:
                    await self._execute_exit(exit_signal, ctx)
                    return

            # Check for entry signals
            if self.current_position == 0:  # Only enter when flat
                entry_signal = self._check_entry_conditions(
                    bar.high, bar.low, bar.close
                )
                if entry_signal:
                    await self._execute_entry(entry_signal, bar.close, ctx)

            # Log current state
            self.logger.debug(
                f"Bar H/L/C: {bar.high:.2f}/{bar.low:.2f}/{bar.close:.2f}, "
                f"Upper: {self.state.upper_channel:.2f}, "
                f"Lower: {self.state.lower_channel:.2f}, "
                f"Position: {self.current_position}"
            )

        except Exception as e:
            self.logger.error(f"Error processing bar: {e}")

    async def on_stop(self, ctx: StrategyContext) -> None:
        """Clean up on strategy stop."""
        self.logger.info("Stopping Donchian Breakout strategy")

        # Close any open positions
        if self.current_position != 0:
            self.logger.info("Closing position on strategy stop")
            if self.current_position > 0:
                await ctx.sell_market(abs(self.current_position), "strategy_stop")
            else:
                await ctx.buy_market(abs(self.current_position), "strategy_stop")

    def _update_donchian_channels(self, high: float, low: float) -> None:
        """Update Donchian Channel values."""
        self.state.high_prices.append(high)
        self.state.low_prices.append(low)

        # Keep only necessary history
        max_history = max(self.high_period, self.low_period) + 10
        if len(self.state.high_prices) > max_history:
            self.state.high_prices = self.state.high_prices[-max_history:]
            self.state.low_prices = self.state.low_prices[-max_history:]

        # Calculate channels
        if len(self.state.high_prices) >= self.high_period:
            self.state.upper_channel = max(self.state.high_prices[-self.high_period :])

        if len(self.state.low_prices) >= self.low_period:
            self.state.lower_channel = min(self.state.low_prices[-self.low_period :])

    def _channels_ready(self) -> bool:
        """Check if channels have enough data."""
        return (
            self.state.upper_channel is not None
            and self.state.lower_channel is not None
        )

    def _check_entry_conditions(
        self, high: float, low: float, close: float
    ) -> Optional[str]:
        """Check for breakout entry conditions."""
        if not self._channels_ready():
            return None

        # Breakout above upper channel
        if high > self.state.upper_channel and self.state.last_breakout != "high":
            self.state.last_breakout = "high"
            return "buy_breakout"

        # Breakout below lower channel
        if low < self.state.lower_channel and self.state.last_breakout != "low":
            self.state.last_breakout = "low"
            return "sell_breakout"

        return None

    def _check_exit_conditions(
        self, high: float, low: float, close: float
    ) -> Optional[str]:
        """Check for exit conditions (stop loss, profit target)."""
        if self.current_position == 0:
            return None

        if self.current_position > 0:  # Long position
            # Check stop loss
            if self.stop_price and low <= self.stop_price:
                return "stop_loss"

            # Check profit target
            if self.target_price and high >= self.target_price:
                return "profit_target"

            # Check opposite breakout (exit long on lower channel break)
            if low < self.state.lower_channel:
                return "opposite_breakout"

        elif self.current_position < 0:  # Short position
            # Check stop loss
            if self.stop_price and high >= self.stop_price:
                return "stop_loss"

            # Check profit target
            if self.target_price and low <= self.target_price:
                return "profit_target"

            # Check opposite breakout (exit short on upper channel break)
            if high > self.state.upper_channel:
                return "opposite_breakout"

        return None

    async def _execute_entry(
        self, signal: str, price: float, ctx: StrategyContext
    ) -> None:
        """Execute entry signal."""
        try:
            if signal == "buy_breakout":
                # Enter long position
                success = await ctx.buy_market(
                    self.position_size, f"donchian_long_{int(price)}"
                )
                if success:
                    self.current_position = self.position_size
                    self.entry_price = price
                    self.stop_price = price - (self.stop_loss_ticks * self.tick_size)
                    self.target_price = price + (
                        self.profit_target_ticks * self.tick_size
                    )

                    self.logger.info(
                        f"LONG entry at {price:.2f}, Stop: {self.stop_price:.2f}, "
                        f"Target: {self.target_price:.2f}"
                    )

            elif signal == "sell_breakout":
                # Enter short position
                success = await ctx.sell_market(
                    self.position_size, f"donchian_short_{int(price)}"
                )
                if success:
                    self.current_position = -self.position_size
                    self.entry_price = price
                    self.stop_price = price + (self.stop_loss_ticks * self.tick_size)
                    self.target_price = price - (
                        self.profit_target_ticks * self.tick_size
                    )

                    self.logger.info(
                        f"SHORT entry at {price:.2f}, Stop: {self.stop_price:.2f}, "
                        f"Target: {self.target_price:.2f}"
                    )

        except Exception as e:
            self.logger.error(f"Failed to execute entry signal {signal}: {e}")

    async def _execute_exit(self, exit_reason: str, ctx: StrategyContext) -> None:
        """Execute exit signal."""
        try:
            position_size = abs(self.current_position)

            if self.current_position > 0:
                # Exit long position
                success = await ctx.sell_market(
                    position_size, f"donchian_exit_{exit_reason}"
                )
            else:
                # Exit short position
                success = await ctx.buy_market(
                    position_size, f"donchian_exit_{exit_reason}"
                )

            if success:
                self.logger.info(
                    f"Position CLOSED: {exit_reason}, Size: {self.current_position}"
                )

                # Reset position tracking
                self.current_position = 0
                self.entry_price = None
                self.stop_price = None
                self.target_price = None
                self.state.last_breakout = None

        except Exception as e:
            self.logger.error(f"Failed to execute exit {exit_reason}: {e}")

    def get_state(self) -> Dict[str, Any]:
        """Get current strategy state."""
        base_state = super().get_state()
        base_state.update(
            {
                "upper_channel": self.state.upper_channel,
                "lower_channel": self.state.lower_channel,
                "current_position": self.current_position,
                "entry_price": self.entry_price,
                "stop_price": self.stop_price,
                "target_price": self.target_price,
                "last_breakout": self.state.last_breakout,
                "high_prices_count": len(self.state.high_prices),
                "low_prices_count": len(self.state.low_prices),
                "channels_ready": self._channels_ready(),
            }
        )
        return base_state
