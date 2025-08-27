"""RSI reversal strategy.

Enters long when RSI drops below an oversold threshold and short when RSI
exceeds an overbought threshold. Positions are exited when RSI returns to
the neutral zone.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext
from topstepx_backend.data.models import Bar


@dataclass
class RSIState:
    """State for RSI calculation."""

    prices: List[float]
    gains: List[float]
    losses: List[float]
    rsi: Optional[float] = None


class RSIReversalStrategy(Strategy):
    """RSI reversal strategy."""

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        super().__init__(strategy_id, params)

        self.period = params.get("period", 14)
        self.overbought = params.get("overbought", 70)
        self.oversold = params.get("oversold", 30)
        self.position_size = params.get("position_size", 1)

        self.state = RSIState(prices=[], gains=[], losses=[])
        self.current_position = 0

    def validate_params(self) -> bool:
        if self.period < 2:
            self.logger.error("period must be >= 2")
            return False
        if self.overbought <= self.oversold:
            self.logger.error("overbought must be > oversold")
            return False
        if self.position_size <= 0:
            self.logger.error("position_size must be > 0")
            return False
        return True

    async def on_start(self, ctx: StrategyContext) -> None:
        self.logger.info(
            "Starting RSIReversal strategy: period=%s size=%s",
            self.period,
            self.position_size,
        )
        self.state = RSIState(prices=[], gains=[], losses=[])
        self.current_position = 0

    async def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        price = bar.close
        self.state.prices.append(price)
        if len(self.state.prices) >= 2:
            change = price - self.state.prices[-2]
            self.state.gains.append(max(change, 0))
            self.state.losses.append(abs(min(change, 0)))
        if len(self.state.gains) > self.period:
            self.state.gains = self.state.gains[-self.period :]
            self.state.losses = self.state.losses[-self.period :]

        if len(self.state.gains) < self.period:
            return

        avg_gain = sum(self.state.gains) / self.period
        avg_loss = sum(self.state.losses) / self.period
        if avg_loss == 0:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        self.state.rsi = rsi

        if self.current_position == 0:
            if rsi < self.oversold:
                if await ctx.buy_market(self.position_size, "rsi_long"):
                    self.current_position = self.position_size
            elif rsi > self.overbought:
                if await ctx.sell_market(self.position_size, "rsi_short"):
                    self.current_position = -self.position_size
        elif self.current_position > 0 and rsi >= self.oversold:
            await ctx.sell_market(self.current_position, "rsi_exit")
            self.current_position = 0
        elif self.current_position < 0 and rsi <= self.overbought:
            await ctx.buy_market(abs(self.current_position), "rsi_exit")
            self.current_position = 0

        self.logger.debug(
            "close=%s rsi=%s position=%s",
            price,
            self.state.rsi,
            self.current_position,
        )

    async def on_stop(self, ctx: StrategyContext) -> None:
        if self.current_position > 0:
            await ctx.sell_market(self.current_position, "strategy_stop")
        elif self.current_position < 0:
            await ctx.buy_market(abs(self.current_position), "strategy_stop")

