"""Base strategy class and interfaces for TopstepX trading strategies."""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, TYPE_CHECKING

from topstepx_backend.data.models import Bar

if TYPE_CHECKING:
    from topstepx_backend.strategy.context import StrategyContext


class Strategy(ABC):
    """
    Base class for all trading strategies.

    Strategies consume market.bar.* events and publish order.request.* events.
    They should not perform direct HTTP calls - all order management goes
    through the OrderService via the EventBus.
    """

    def __init__(self, strategy_id: str, params: Dict[str, Any]):
        """
        Initialize strategy with ID and parameters.

        Args:
            strategy_id: Unique identifier for this strategy instance
            params: Strategy parameters from configuration
        """
        self.strategy_id = strategy_id
        self.params = params
        self.logger = logging.getLogger(f"{self.__class__.__module__}.{strategy_id}")

        # Strategy state
        self._running = False
        self._initialized = False

    @abstractmethod
    async def on_bar(self, bar: Bar, ctx: "StrategyContext") -> None:
        """
        Handle new bar data.

        This is the main strategy logic entry point. Called for each new
        bar matching the strategy's contract_id and timeframe.

        Args:
            bar: New bar data
            ctx: Strategy context with access to EventBus, risk controls, etc.
        """
        pass

    async def on_boundary(self, timeframe: str, ctx: "StrategyContext") -> None:
        """
        Handle timeframe boundary events.

        Called at regular intervals (e.g., minute boundaries) regardless
        of whether bar data was received. Useful for time-based logic.

        Args:
            timeframe: Timeframe that just completed (e.g., "1m", "5m")
            ctx: Strategy context
        """
        pass

    async def on_start(self, ctx: "StrategyContext") -> None:
        """
        Called when strategy starts.

        Override this method to perform initialization logic like:
        - Loading historical data
        - Initializing indicators
        - Setting up initial state

        Args:
            ctx: Strategy context
        """
        pass

    async def on_stop(self, ctx: "StrategyContext") -> None:
        """
        Called when strategy stops.

        Override this method to perform cleanup logic like:
        - Closing open positions
        - Saving state
        - Resource cleanup

        Args:
            ctx: Strategy context
        """
        pass

    def get_state(self) -> Dict[str, Any]:
        """
        Get current strategy state for debugging/monitoring.

        Returns:
            Dictionary with strategy state information
        """
        return {
            "strategy_id": self.strategy_id,
            "running": self._running,
            "initialized": self._initialized,
            "params": self.params,
        }

    def validate_params(self) -> bool:
        """
        Validate strategy parameters.

        Override this method to add custom parameter validation.
        Called during strategy initialization.

        Returns:
            True if parameters are valid, False otherwise
        """
        return True

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(strategy_id='{self.strategy_id}')"
