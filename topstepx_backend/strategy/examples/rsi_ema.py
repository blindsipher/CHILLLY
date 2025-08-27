"""RSI + EMA crossover strategy example."""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass

from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext
from topstepx_backend.data.models import Bar


@dataclass
class IndicatorState:
    """State for technical indicators."""

    prices: List[float]
    rsi_gains: List[float]
    rsi_losses: List[float]
    ema_value: Optional[float] = None
    rsi_value: Optional[float] = None


class RSIEMAStrategy(Strategy):
    """
    RSI + EMA crossover strategy.

    Entry Rules:
    - RSI < oversold (30) AND price above EMA -> BUY
    - RSI > overbought (70) AND price below EMA -> SELL

    Exit Rules:
    - RSI crosses back into neutral zone (30-70)
    """

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        super().__init__(strategy_id, params)

        # Strategy parameters
        self.rsi_period = params.get("rsi_period", 14)
        self.rsi_overbought = params.get("rsi_overbought", 70)
        self.rsi_oversold = params.get("rsi_oversold", 30)
        self.ema_period = params.get("ema_period", 50)
        self.position_size = params.get("position_size", 1)

        # Indicator state
        self.state = IndicatorState(prices=[], rsi_gains=[], rsi_losses=[])

        # Position tracking
        self.current_position = 0  # 0 = flat, positive = long, negative = short
        self.last_signal = None  # 'buy', 'sell', or None

        # EMA multiplier for efficiency
        self._ema_multiplier = 2.0 / (self.ema_period + 1)

    def validate_params(self) -> bool:
        """Validate strategy parameters."""
        if self.rsi_period < 2:
            self.logger.error("RSI period must be >= 2")
            return False

        if self.ema_period < 2:
            self.logger.error("EMA period must be >= 2")
            return False

        if self.rsi_overbought <= self.rsi_oversold:
            self.logger.error("RSI overbought must be > RSI oversold")
            return False

        if self.position_size <= 0:
            self.logger.error("Position size must be > 0")
            return False

        return True

    async def on_start(self, ctx: StrategyContext) -> None:
        """Initialize strategy on startup."""
        self.logger.info(
            f"Starting RSI+EMA strategy with params: "
            f"RSI={self.rsi_period}, EMA={self.ema_period}, "
            f"size={self.position_size}"
        )

        # Reset state
        self.state = IndicatorState(prices=[], rsi_gains=[], rsi_losses=[])
        self.current_position = 0
        self.last_signal = None

    async def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        """Process new bar data."""
        try:
            # Update indicators
            self._update_indicators(bar.close)

            # Skip if indicators not ready
            if not self._indicators_ready():
                self.logger.debug(
                    f"Indicators not ready - prices: {len(self.state.prices)}"
                )
                return

            # Generate trading signals
            signal = self._generate_signal(bar.close)

            if signal and signal != self.last_signal:
                await self._execute_signal(signal, ctx)
                self.last_signal = signal

            # Log current state
            self.logger.debug(
                f"Bar: {bar.close:.2f}, RSI: {self.state.rsi_value:.1f}, "
                f"EMA: {self.state.ema_value:.2f}, Position: {self.current_position}, "
                f"Signal: {signal}"
            )

        except Exception as e:
            self.logger.error(f"Error processing bar: {e}")

    async def on_stop(self, ctx: StrategyContext) -> None:
        """Clean up on strategy stop."""
        self.logger.info("Stopping RSI+EMA strategy")

        # Close any open positions
        if self.current_position != 0:
            self.logger.info("Closing position on strategy stop")
            if self.current_position > 0:
                await ctx.sell_market(abs(self.current_position), "strategy_stop")
            else:
                await ctx.buy_market(abs(self.current_position), "strategy_stop")

    def _update_indicators(self, price: float) -> None:
        """Update technical indicators with new price."""
        self.state.prices.append(price)

        # Keep only necessary history
        max_history = max(self.rsi_period, self.ema_period) + 10
        if len(self.state.prices) > max_history:
            self.state.prices = self.state.prices[-max_history:]

        # Update EMA
        self._update_ema(price)

        # Update RSI
        if len(self.state.prices) >= 2:
            self._update_rsi(price)

    def _update_ema(self, price: float) -> None:
        """Update Exponential Moving Average."""
        if self.state.ema_value is None:
            # Initialize with SMA for first value
            if len(self.state.prices) >= self.ema_period:
                self.state.ema_value = (
                    sum(self.state.prices[-self.ema_period :]) / self.ema_period
                )
        else:
            # Update with EMA formula
            self.state.ema_value = (
                price * self._ema_multiplier
                + self.state.ema_value * (1 - self._ema_multiplier)
            )

    def _update_rsi(self, price: float) -> None:
        """Update Relative Strength Index."""
        if len(self.state.prices) < 2:
            return

        # Calculate price change
        price_change = price - self.state.prices[-2]

        # Track gains and losses
        gain = max(0, price_change)
        loss = max(0, -price_change)

        self.state.rsi_gains.append(gain)
        self.state.rsi_losses.append(loss)

        # Keep only necessary history
        if len(self.state.rsi_gains) > self.rsi_period + 10:
            self.state.rsi_gains = self.state.rsi_gains[-(self.rsi_period + 10) :]
            self.state.rsi_losses = self.state.rsi_losses[-(self.rsi_period + 10) :]

        # Calculate RSI when we have enough data
        if len(self.state.rsi_gains) >= self.rsi_period:
            avg_gain = sum(self.state.rsi_gains[-self.rsi_period :]) / self.rsi_period
            avg_loss = sum(self.state.rsi_losses[-self.rsi_period :]) / self.rsi_period

            if avg_loss == 0:
                self.state.rsi_value = 100
            else:
                rs = avg_gain / avg_loss
                self.state.rsi_value = 100 - (100 / (1 + rs))

    def _indicators_ready(self) -> bool:
        """Check if indicators have enough data."""
        return (
            self.state.ema_value is not None
            and self.state.rsi_value is not None
            and len(self.state.prices) >= max(self.rsi_period, self.ema_period)
        )

    def _generate_signal(self, price: float) -> Optional[str]:
        """Generate trading signal based on RSI and EMA."""
        if not self._indicators_ready():
            return None

        rsi = self.state.rsi_value
        ema = self.state.ema_value

        # Entry signals
        if self.current_position == 0:  # Flat
            # Long signal: RSI oversold AND price above EMA
            if rsi <= self.rsi_oversold and price > ema:
                return "buy"

            # Short signal: RSI overbought AND price below EMA
            if rsi >= self.rsi_overbought and price < ema:
                return "sell"

        # Exit signals
        elif self.current_position > 0:  # Long position
            # Exit long when RSI moves back to neutral
            if rsi > self.rsi_oversold + 10:  # Add buffer to avoid whipsaws
                return "sell_exit"

        elif self.current_position < 0:  # Short position
            # Exit short when RSI moves back to neutral
            if rsi < self.rsi_overbought - 10:  # Add buffer to avoid whipsaws
                return "buy_exit"

        return None

    async def _execute_signal(self, signal: str, ctx: StrategyContext) -> None:
        """Execute trading signal."""
        try:
            if signal == "buy":
                # Enter long position
                success = await ctx.buy_market(
                    self.position_size, f"rsi_ema_long_{int(self.state.rsi_value)}"
                )
                if success:
                    self.current_position = self.position_size
                    self.logger.info(
                        f"LONG entry: RSI={self.state.rsi_value:.1f}, "
                        f"EMA={self.state.ema_value:.2f}"
                    )

            elif signal == "sell":
                # Enter short position
                success = await ctx.sell_market(
                    self.position_size, f"rsi_ema_short_{int(self.state.rsi_value)}"
                )
                if success:
                    self.current_position = -self.position_size
                    self.logger.info(
                        f"SHORT entry: RSI={self.state.rsi_value:.1f}, "
                        f"EMA={self.state.ema_value:.2f}"
                    )

            elif signal == "sell_exit":
                # Exit long position
                success = await ctx.sell_market(
                    abs(self.current_position),
                    f"rsi_ema_exit_long_{int(self.state.rsi_value)}",
                )
                if success:
                    self.logger.info(f"LONG exit: RSI={self.state.rsi_value:.1f}")
                    self.current_position = 0

            elif signal == "buy_exit":
                # Exit short position
                success = await ctx.buy_market(
                    abs(self.current_position),
                    f"rsi_ema_exit_short_{int(self.state.rsi_value)}",
                )
                if success:
                    self.logger.info(f"SHORT exit: RSI={self.state.rsi_value:.1f}")
                    self.current_position = 0

        except Exception as e:
            self.logger.error(f"Failed to execute signal {signal}: {e}")

    def get_state(self) -> Dict[str, Any]:
        """Get current strategy state."""
        base_state = super().get_state()
        base_state.update(
            {
                "rsi_value": self.state.rsi_value,
                "ema_value": self.state.ema_value,
                "current_position": self.current_position,
                "last_signal": self.last_signal,
                "prices_count": len(self.state.prices),
                "indicators_ready": self._indicators_ready(),
            }
        )
        return base_state
