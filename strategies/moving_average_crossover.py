"""Moving Average Crossover strategy for intraday trading.

This strategy goes long when a fast moving average crosses above a slow
moving average and goes short on the opposite cross. It can be applied to
any futures market by configuring the contract and timeframe in the
strategy configuration.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext
from topstepx_backend.data.models import Bar


@dataclass
class MACState:
    """Indicator and position state for the strategy."""

    prices: List[float]
    fast_ma: Optional[float] = None
    slow_ma: Optional[float] = None


class MovingAverageCrossoverStrategy(Strategy):
    """Dual moving average crossover strategy."""

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        super().__init__(strategy_id, params)

        self.fast_period = params.get("fast_period", 10)
        self.slow_period = params.get("slow_period", 30)
        self.position_size = params.get("position_size", 1)

        self.state = MACState(prices=[])
        self.current_position = 0
        self.last_signal: Optional[str] = None  # 'buy' or 'sell'

    def validate_params(self) -> bool:
        if self.fast_period < 1 or self.slow_period < 2:
            self.logger.error("Periods must be positive")
            return False
        if self.fast_period >= self.slow_period:
            self.logger.error("fast_period must be < slow_period")
            return False
        if self.position_size <= 0:
            self.logger.error("position_size must be > 0")
            return False
        return True

    async def on_start(self, ctx: StrategyContext) -> None:
        self.logger.info(
            "Starting MovingAverageCrossover strategy: fast=%s slow=%s size=%s",
            self.fast_period,
            self.slow_period,
            self.position_size,
        )
        self.state = MACState(prices=[])
        self.current_position = 0
        self.last_signal = None

    async def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        self.state.prices.append(bar.close)
        max_len = self.slow_period + 5
        if len(self.state.prices) > max_len:
            self.state.prices = self.state.prices[-max_len:]

        if len(self.state.prices) >= self.fast_period:
            self.state.fast_ma = sum(self.state.prices[-self.fast_period:]) / self.fast_period
        if len(self.state.prices) >= self.slow_period:
            self.state.slow_ma = sum(self.state.prices[-self.slow_period:]) / self.slow_period

        if self.state.fast_ma is None or self.state.slow_ma is None:
            return

        signal: Optional[str] = None
        if self.state.fast_ma > self.state.slow_ma:
            signal = "buy"
        elif self.state.fast_ma < self.state.slow_ma:
            signal = "sell"

        if signal and signal != self.last_signal:
            if signal == "buy":
                await self._go_long(ctx)
            elif signal == "sell":
                await self._go_short(ctx)
            self.last_signal = signal

        self.logger.debug(
            "close=%s fast_ma=%s slow_ma=%s position=%s",
            bar.close,
            self.state.fast_ma,
            self.state.slow_ma,
            self.current_position,
        )

    async def on_stop(self, ctx: StrategyContext) -> None:
        if self.current_position > 0:
            await ctx.sell_market(self.current_position, "strategy_stop")
        elif self.current_position < 0:
            await ctx.buy_market(abs(self.current_position), "strategy_stop")

    async def _go_long(self, ctx: StrategyContext) -> None:
        if self.current_position < 0:
            await ctx.buy_market(abs(self.current_position), "reverse")
            self.current_position = 0
        if self.current_position == 0:
            if await ctx.buy_market(self.position_size, "long"):
                self.current_position = self.position_size

    async def _go_short(self, ctx: StrategyContext) -> None:
        if self.current_position > 0:
            await ctx.sell_market(self.current_position, "reverse")
            self.current_position = 0
        if self.current_position == 0:
            if await ctx.sell_market(self.position_size, "short"):
                self.current_position = -self.position_size
