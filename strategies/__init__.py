"""User-contributed strategy plugins.

This package collects standalone strategy implementations that can be
discovered by the :class:`StrategyRegistry`. Each module defines a
subclass of :class:`topstepx_backend.strategy.base.Strategy` and can be
referenced in ``strategies.yaml`` using its module path.
"""

__all__ = [
    "moving_average_crossover",
    "bollinger_reversion",
    "vwap_reversion",
    "opening_range_breakout",
    "macd_trend",
    "rsi_reversal",
]

