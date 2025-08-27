"""VWAP mean reversion strategy.

Trades when price deviates from the Volume Weighted Average Price (VWAP)
by a configurable number of ticks and exits when price reverts back to
the VWAP. Works on any futures market that provides volume data.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional

from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext
from topstepx_backend.data.types import Bar


@dataclass
class VWAPState:
    """Accumulated VWAP state."""

    total_volume: float = 0.0
    total_vp: float = 0.0  # volume * price
    vwap: Optional[float] = None


class VWAPReversionStrategy(Strategy):
    """VWAP mean reversion strategy."""

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        super().__init__(strategy_id, params)

        self.deviation_ticks = params.get("deviation_ticks", 4)
        self.tick_size = params.get("tick_size", 0.25)
        self.position_size = params.get("position_size", 1)

        self.state = VWAPState()
        self.current_position = 0

    def validate_params(self) -> bool:
        if self.deviation_ticks <= 0:
            self.logger.error("deviation_ticks must be > 0")
            return False
        if self.tick_size <= 0:
            self.logger.error("tick_size must be > 0")
            return False
        if self.position_size <= 0:
            self.logger.error("position_size must be > 0")
            return False
        return True

    async def on_start(self, ctx: StrategyContext) -> None:
        self.logger.info(
            "Starting VWAPReversion strategy: dev_ticks=%s size=%s",
            self.deviation_ticks,
            self.position_size,
        )
        self.state = VWAPState()
        self.current_position = 0

    async def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        self.state.total_volume += bar.volume
        self.state.total_vp += bar.close * bar.volume
        if self.state.total_volume > 0:
            self.state.vwap = self.state.total_vp / self.state.total_volume
        else:
            return

        deviation = self.deviation_ticks * self.tick_size
        upper = self.state.vwap + deviation
        lower = self.state.vwap - deviation

        if self.current_position == 0:
            if bar.close < lower:
                if await ctx.buy_market(self.position_size, "vwap_long"):
                    self.current_position = self.position_size
            elif bar.close > upper:
                if await ctx.sell_market(self.position_size, "vwap_short"):
                    self.current_position = -self.position_size
        elif self.current_position > 0 and bar.close >= self.state.vwap:
            await ctx.sell_market(self.current_position, "vwap_exit")
            self.current_position = 0
        elif self.current_position < 0 and bar.close <= self.state.vwap:
            await ctx.buy_market(abs(self.current_position), "vwap_exit")
            self.current_position = 0

        self.logger.debug(
            "close=%s vwap=%s upper=%s lower=%s position=%s",
            bar.close,
            self.state.vwap,
            upper,
            lower,
            self.current_position,
        )

    async def on_stop(self, ctx: StrategyContext) -> None:
        if self.current_position > 0:
            await ctx.sell_market(self.current_position, "strategy_stop")
        elif self.current_position < 0:
            await ctx.buy_market(abs(self.current_position), "strategy_stop")

