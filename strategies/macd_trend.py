"""MACD trend-following strategy.

Uses the Moving Average Convergence Divergence indicator. A long position
is taken when the MACD line crosses above the signal line and a short
position on the opposite cross.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional

from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext
from topstepx_backend.data.types import Bar


@dataclass
class MACDState:
    """State for MACD calculations."""

    fast_ema: Optional[float] = None
    slow_ema: Optional[float] = None
    macd: Optional[float] = None
    signal: Optional[float] = None


class MACDTrendStrategy(Strategy):
    """MACD trend-following strategy."""

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        super().__init__(strategy_id, params)

        self.fast_period = params.get("fast_period", 12)
        self.slow_period = params.get("slow_period", 26)
        self.signal_period = params.get("signal_period", 9)
        self.position_size = params.get("position_size", 1)

        self.fast_mult = 2 / (self.fast_period + 1)
        self.slow_mult = 2 / (self.slow_period + 1)
        self.signal_mult = 2 / (self.signal_period + 1)

        self.state = MACDState()
        self.current_position = 0
        self.last_signal: Optional[str] = None

    def validate_params(self) -> bool:
        if self.fast_period <= 0 or self.slow_period <= 0 or self.signal_period <= 0:
            self.logger.error("periods must be positive")
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
            "Starting MACDTrend strategy: fast=%s slow=%s signal=%s size=%s",
            self.fast_period,
            self.slow_period,
            self.signal_period,
            self.position_size,
        )
        self.state = MACDState()
        self.current_position = 0
        self.last_signal = None

    async def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        price = bar.close
        if self.state.fast_ema is None:
            self.state.fast_ema = price
            self.state.slow_ema = price
            return

        self.state.fast_ema = (
            price * self.fast_mult + self.state.fast_ema * (1 - self.fast_mult)
        )
        self.state.slow_ema = (
            price * self.slow_mult + self.state.slow_ema * (1 - self.slow_mult)
        )
        self.state.macd = self.state.fast_ema - self.state.slow_ema

        if self.state.signal is None:
            self.state.signal = self.state.macd
            return

        self.state.signal = (
            self.state.macd * self.signal_mult
            + self.state.signal * (1 - self.signal_mult)
        )

        signal: Optional[str] = None
        if self.state.macd > self.state.signal:
            signal = "buy"
        elif self.state.macd < self.state.signal:
            signal = "sell"

        if signal and signal != self.last_signal:
            if signal == "buy":
                await self._go_long(ctx)
            else:
                await self._go_short(ctx)
            self.last_signal = signal

        self.logger.debug(
            "close=%s macd=%s signal=%s position=%s",
            price,
            self.state.macd,
            self.state.signal,
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

