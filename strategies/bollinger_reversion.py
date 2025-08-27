"""Bollinger Band mean reversion strategy.

The strategy buys when price closes below the lower band and sells when
price closes above the upper band. Positions are exited when price returns
to the moving average. Suitable for intraday mean reversion across futures
markets.
"""

from dataclasses import dataclass
from typing import Dict, Any, List, Optional

from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext
from topstepx_backend.data.models import Bar


@dataclass
class BollingerState:
    """Indicator and position state."""

    prices: List[float]
    sma: Optional[float] = None
    std: Optional[float] = None


class BollingerReversionStrategy(Strategy):
    """Bollinger Band mean reversion strategy."""

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        super().__init__(strategy_id, params)

        self.period = params.get("period", 20)
        self.num_std = params.get("num_std", 2.0)
        self.position_size = params.get("position_size", 1)

        self.state = BollingerState(prices=[])
        self.current_position = 0

    def validate_params(self) -> bool:
        if self.period < 2:
            self.logger.error("period must be >= 2")
            return False
        if self.num_std <= 0:
            self.logger.error("num_std must be > 0")
            return False
        if self.position_size <= 0:
            self.logger.error("position_size must be > 0")
            return False
        return True

    async def on_start(self, ctx: StrategyContext) -> None:
        self.logger.info(
            "Starting BollingerReversion strategy: period=%s num_std=%s size=%s",
            self.period,
            self.num_std,
            self.position_size,
        )
        self.state = BollingerState(prices=[])
        self.current_position = 0

    async def on_bar(self, bar: Bar, ctx: StrategyContext) -> None:
        self.state.prices.append(bar.close)
        if len(self.state.prices) > self.period + 5:
            self.state.prices = self.state.prices[-(self.period + 5) :]

        if len(self.state.prices) >= self.period:
            window = self.state.prices[-self.period :]
            sma = sum(window) / self.period
            variance = sum((p - sma) ** 2 for p in window) / self.period
            std = variance ** 0.5
            self.state.sma = sma
            self.state.std = std
        else:
            return

        upper = self.state.sma + self.num_std * self.state.std
        lower = self.state.sma - self.num_std * self.state.std

        if self.current_position == 0:
            if bar.close < lower:
                if await ctx.buy_market(self.position_size, "bb_long"):
                    self.current_position = self.position_size
            elif bar.close > upper:
                if await ctx.sell_market(self.position_size, "bb_short"):
                    self.current_position = -self.position_size
        elif self.current_position > 0 and bar.close >= self.state.sma:
            await ctx.sell_market(self.current_position, "bb_exit")
            self.current_position = 0
        elif self.current_position < 0 and bar.close <= self.state.sma:
            await ctx.buy_market(abs(self.current_position), "bb_exit")
            self.current_position = 0

        self.logger.debug(
            "close=%s sma=%s std=%s upper=%s lower=%s position=%s",
            bar.close,
            self.state.sma,
            self.state.std,
            upper,
            lower,
            self.current_position,
        )

    async def on_stop(self, ctx: StrategyContext) -> None:
        if self.current_position > 0:
            await ctx.sell_market(self.current_position, "strategy_stop")
        elif self.current_position < 0:
            await ctx.buy_market(abs(self.current_position), "strategy_stop")

