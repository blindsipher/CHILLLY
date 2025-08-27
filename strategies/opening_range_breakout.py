"""Opening Range Breakout strategy.

Defines an opening range from the first N minutes of trading and takes a
position when price breaks out of that range. Works on any intraday
timeframe where bars are timestamped in UTC.
"""

from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Any, Optional

from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext
from topstepx_backend.data.models import Bar


@dataclass
class RangeState:
    """State for opening range tracking."""

    range_high: Optional[float] = None
    range_low: Optional[float] = None
    range_end: Optional[str] = None
    range_complete: bool = False


class OpeningRangeBreakoutStrategy(Strategy):
    """Opening range breakout strategy."""

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        super().__init__(strategy_id, params)

        self.range_minutes = params.get("range_minutes", 30)
        self.position_size = params.get("position_size", 1)

        self.state = RangeState()
        self.current_position = 0

    def validate_params(self) -> bool:
        if self.range_minutes <= 0:
            self.logger.error("range_minutes must be > 0")
            return False
        if self.position_size <= 0:
            self.logger.error("position_size must be > 0")
            return False
        return True

    async def on_start(self, ctx: StrategyContext) -> None:
        self.logger.info(
            "Starting OpeningRangeBreakout strategy: range_minutes=%s size=%s",
            self.range_minutes,
            self.position_size,
        )
        self.state = RangeState()
        self.current_position = 0

    async def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        if not self.state.range_complete:
            await self._update_range(bar)
            return

        if self.current_position == 0:
            if bar.high > self.state.range_high:
                if await ctx.buy_market(self.position_size, "or_long"):
                    self.current_position = self.position_size
            elif bar.low < self.state.range_low:
                if await ctx.sell_market(self.position_size, "or_short"):
                    self.current_position = -self.position_size
        else:
            if (
                self.current_position > 0 and bar.low <= self.state.range_low
            ) or (
                self.current_position < 0 and bar.high >= self.state.range_high
            ):
                if self.current_position > 0:
                    await ctx.sell_market(self.current_position, "or_exit")
                else:
                    await ctx.buy_market(abs(self.current_position), "or_exit")
                self.current_position = 0

        self.logger.debug(
            "close=%s range_high=%s range_low=%s position=%s",
            bar.close,
            self.state.range_high,
            self.state.range_low,
            self.current_position,
        )

    async def _update_range(self, bar: Bar) -> None:
        if self.state.range_high is None:
            self.state.range_high = bar.high
            self.state.range_low = bar.low
            self.state.range_end = (
                bar.timestamp + timedelta(minutes=self.range_minutes)
            ).isoformat()
        else:
            self.state.range_high = max(self.state.range_high, bar.high)
            self.state.range_low = min(self.state.range_low, bar.low)

        if bar.timestamp >= self._range_end_dt():
            self.state.range_complete = True
            self.logger.info(
                "Opening range complete: high=%s low=%s",
                self.state.range_high,
                self.state.range_low,
            )

    def _range_end_dt(self):
        if not self.state.range_end:
            return None
        from datetime import datetime

        return datetime.fromisoformat(self.state.range_end)

    async def on_stop(self, ctx: StrategyContext) -> None:
        if self.current_position > 0:
            await ctx.sell_market(self.current_position, "strategy_stop")
        elif self.current_position < 0:
            await ctx.buy_market(abs(self.current_position), "strategy_stop")

