"""Strategy runner for managing multiple strategy instances."""

import asyncio
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass

from topstepx_backend.core.event_bus import EventBus, Subscription
from topstepx_backend.core.topics import market_bar, boundary
from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext, RiskLimits
from topstepx_backend.strategy.registry import StrategyRegistry
from topstepx_backend.data.types import Bar


@dataclass
class StrategyInstance:
    """Container for strategy and its execution context."""

    strategy: Strategy
    context: StrategyContext
    config: Dict[str, Any]
    subscriptions: List[Subscription]
    running: bool = False


class StrategyRunner:
    """
    Strategy runner managing multiple strategy instances.

    Responsibilities:
    - Load and instantiate strategies from configuration
    - Subscribe to market data for each strategy's contract/timeframe
    - Route events to appropriate strategies
    - Manage strategy lifecycle (start/stop)
    - Provide monitoring and health information
    """

    def __init__(
        self, event_bus: EventBus, registry: Optional[StrategyRegistry] = None
    ):
        """
        Initialize strategy runner.

        Args:
            event_bus: EventBus for publishing/subscribing to events
            registry: Strategy registry for loading configurations
        """
        self.event_bus = event_bus
        self.registry = registry or StrategyRegistry()
        self.logger = logging.getLogger(__name__)

        # Strategy instances
        self._strategies: Dict[str, StrategyInstance] = {}
        self._running = False

        # Event processing tasks
        self._consumer_tasks: List[asyncio.Task] = []

    async def start(self) -> None:
        """Start the strategy runner and all configured strategies."""
        if self._running:
            self.logger.warning("StrategyRunner already running")
            return

        self.logger.info("Starting StrategyRunner...")

        try:
            # Load strategy configurations and create instances
            await self._load_and_create_strategies()

            # Start all strategies
            await self._start_all_strategies()

            self._running = True
            self.logger.info(
                f"StrategyRunner started with {len(self._strategies)} strategies"
            )

        except Exception as e:
            self.logger.error(f"Failed to start StrategyRunner: {e}")
            await self.stop()
            raise

    async def stop(self) -> None:
        """Stop the strategy runner and all strategies."""
        if not self._running:
            return

        self.logger.info("Stopping StrategyRunner...")
        self._running = False

        # Cancel all consumer tasks
        for task in self._consumer_tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._consumer_tasks.clear()

        # Stop all strategies
        await self._stop_all_strategies()

        self.logger.info("StrategyRunner stopped")

    async def _load_and_create_strategies(self) -> None:
        """Load configurations and create strategy instances."""
        self.logger.info("Loading strategy configurations...")

        # Load configurations
        configs = self.registry.load_configurations()
        if not configs:
            self.logger.warning("No strategy configurations found")
            return

        # Discover strategy classes
        self.registry.discover_strategy_classes()

        # Create strategy instances
        for config in configs:
            await self._create_strategy_instance(config)

    async def _create_strategy_instance(self, config: Dict[str, Any]) -> None:
        """
        Create a single strategy instance from configuration.

        Args:
            config: Strategy configuration dictionary
        """
        try:
            strategy_id = config["strategy_id"]

            # Create strategy
            strategy = self.registry.create_strategy_instance(config)
            if not strategy:
                self.logger.error(f"Failed to create strategy: {strategy_id}")
                return

            # Create risk limits
            risk_config = config.get("risk", {})
            risk_limits = RiskLimits(
                max_position_size=risk_config.get("max_position_size", 10),
                max_daily_loss=risk_config.get("max_daily_loss", 1000.0),
                max_order_size=risk_config.get("max_order_size", 5),
                max_orders_per_minute=risk_config.get("max_orders_per_minute", 10),
            )

            # Create strategy context
            context = StrategyContext(
                strategy_id=strategy_id,
                event_bus=self.event_bus,
                logger=strategy.logger,
                account_id=config["account_id"],
                contract_id=config["contract_id"],
                timeframe=config["timeframe"],
                risk_limits=risk_limits,
            )

            # Create strategy instance container
            instance = StrategyInstance(
                strategy=strategy,
                context=context,
                config=config,
                subscriptions=[],
                running=False,
            )

            self._strategies[strategy_id] = instance
            self.logger.info(f"Created strategy instance: {strategy_id}")

        except Exception as e:
            self.logger.error(f"Failed to create strategy instance: {e}")

    async def _start_all_strategies(self) -> None:
        """Start all strategy instances."""
        for strategy_id, instance in self._strategies.items():
            await self._start_strategy_instance(strategy_id, instance)

    async def _start_strategy_instance(
        self, strategy_id: str, instance: StrategyInstance
    ) -> None:
        """
        Start a single strategy instance.

        Args:
            strategy_id: Strategy identifier
            instance: Strategy instance to start
        """
        try:
            self.logger.info(f"Starting strategy: {strategy_id}")

            # Call strategy's on_start method
            await instance.strategy.on_start(instance.context)

            # Set up subscriptions
            await self._setup_strategy_subscriptions(instance)

            instance.running = True
            instance.strategy._running = True

            self.logger.info(f"Strategy started: {strategy_id}")

        except Exception as e:
            self.logger.error(f"Failed to start strategy {strategy_id}: {e}")
            instance.running = False
            instance.strategy._running = False

    async def _setup_strategy_subscriptions(self, instance: StrategyInstance) -> None:
        """
        Set up EventBus subscriptions for a strategy instance.

        Args:
            instance: Strategy instance
        """
        config = instance.config
        contract_id = config["contract_id"]
        timeframe = config["timeframe"]

        # Subscribe to market bar events
        bar_topic = market_bar(contract_id, timeframe)
        bar_subscription = await self.event_bus.subscribe(
            bar_topic,
            critical=True,  # Strategies are critical consumers
            maxsize=1000,
        )
        instance.subscriptions.append(bar_subscription)

        # Subscribe to boundary events
        boundary_topic = boundary(timeframe)
        boundary_subscription = await self.event_bus.subscribe(
            boundary_topic, critical=True, maxsize=1000
        )
        instance.subscriptions.append(boundary_subscription)

        # Start consumer tasks
        bar_task = asyncio.create_task(
            self._consume_bar_events(instance, bar_subscription)
        )
        boundary_task = asyncio.create_task(
            self._consume_boundary_events(instance, boundary_subscription)
        )

        self._consumer_tasks.extend([bar_task, boundary_task])

        self.logger.debug(
            f"Set up subscriptions for {instance.strategy.strategy_id}: "
            f"bars={bar_topic}, boundaries={boundary_topic}"
        )

    async def _consume_bar_events(
        self, instance: StrategyInstance, subscription: Subscription
    ) -> None:
        """
        Consumer task for bar events.

        Args:
            instance: Strategy instance
            subscription: Event subscription
        """
        strategy_id = instance.strategy.strategy_id

        try:
            async for topic, payload in subscription:
                if not self._running or not instance.running:
                    break

                try:
                    # Convert payload to Bar object
                    bar = self._payload_to_bar(payload)
                    if bar:
                        await instance.strategy.on_bar(bar, instance.context)

                except Exception as e:
                    self.logger.error(f"Error in strategy {strategy_id} on_bar: {e}")

        except asyncio.CancelledError:
            self.logger.debug(f"Bar consumer cancelled for strategy: {strategy_id}")
        except Exception as e:
            self.logger.error(f"Bar consumer error for strategy {strategy_id}: {e}")

    async def _consume_boundary_events(
        self, instance: StrategyInstance, subscription: Subscription
    ) -> None:
        """
        Consumer task for boundary events.

        Args:
            instance: Strategy instance
            subscription: Event subscription
        """
        strategy_id = instance.strategy.strategy_id

        try:
            async for topic, payload in subscription:
                if not self._running or not instance.running:
                    break

                try:
                    timeframe = payload.get("timeframe", instance.config["timeframe"])
                    await instance.strategy.on_boundary(timeframe, instance.context)

                except Exception as e:
                    self.logger.error(
                        f"Error in strategy {strategy_id} on_boundary: {e}"
                    )

        except asyncio.CancelledError:
            self.logger.debug(
                f"Boundary consumer cancelled for strategy: {strategy_id}"
            )
        except Exception as e:
            self.logger.error(
                f"Boundary consumer error for strategy {strategy_id}: {e}"
            )

    def _payload_to_bar(self, payload: Any) -> Optional[Bar]:
        """
        Convert event payload to Bar object.

        Args:
            payload: Event payload

        Returns:
            Bar object or None if conversion failed
        """
        try:
            if isinstance(payload, dict):
                # Handle both dict and Bar objects
                from datetime import datetime

                # Parse timestamp
                timestamp = payload.get("timestamp")
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                elif not isinstance(timestamp, datetime):
                    self.logger.warning(
                        f"Invalid timestamp in bar payload: {timestamp}"
                    )
                    return None

                return Bar(
                    timestamp=timestamp,
                    contract_id=payload["contract_id"],
                    timeframe=payload["timeframe"],
                    open=float(payload["open"]),
                    high=float(payload["high"]),
                    low=float(payload["low"]),
                    close=float(payload["close"]),
                    volume=int(payload["volume"]),
                    source=payload.get("source", "unknown"),
                    revision=int(payload.get("revision", 1)),
                )

            elif isinstance(payload, Bar):
                return payload

            else:
                self.logger.warning(f"Unsupported bar payload type: {type(payload)}")
                return None

        except Exception as e:
            self.logger.error(f"Failed to convert payload to Bar: {e}")
            return None

    async def _stop_all_strategies(self) -> None:
        """Stop all strategy instances."""
        for strategy_id, instance in self._strategies.items():
            await self._stop_strategy_instance(strategy_id, instance)

    async def _stop_strategy_instance(
        self, strategy_id: str, instance: StrategyInstance
    ) -> None:
        """
        Stop a single strategy instance.

        Args:
            strategy_id: Strategy identifier
            instance: Strategy instance to stop
        """
        try:
            self.logger.info(f"Stopping strategy: {strategy_id}")

            instance.running = False
            instance.strategy._running = False

            # Call strategy's on_stop method
            await instance.strategy.on_stop(instance.context)

            # Close subscriptions
            for subscription in instance.subscriptions:
                await self.event_bus.unsubscribe(subscription)
            instance.subscriptions.clear()

            self.logger.info(f"Strategy stopped: {strategy_id}")

        except Exception as e:
            self.logger.error(f"Failed to stop strategy {strategy_id}: {e}")

    def get_strategy_stats(self) -> Dict[str, Any]:
        """
        Get statistics for all strategies.

        Returns:
            Dictionary with strategy statistics
        """
        stats = {
            "running": self._running,
            "total_strategies": len(self._strategies),
            "running_strategies": sum(
                1 for instance in self._strategies.values() if instance.running
            ),
            "strategies": {},
        }

        for strategy_id, instance in self._strategies.items():
            strategy_stats = instance.strategy.get_state()
            strategy_stats["context_metrics"] = instance.context.get_metrics()
            stats["strategies"][strategy_id] = strategy_stats

        return stats

    def get_strategy_instance(self, strategy_id: str) -> Optional[StrategyInstance]:
        """
        Get strategy instance by ID.

        Args:
            strategy_id: Strategy identifier

        Returns:
            Strategy instance or None if not found
        """
        return self._strategies.get(strategy_id)

    def list_strategies(self) -> List[Tuple[str, bool]]:
        """
        List all strategies with their running status.

        Returns:
            List of (strategy_id, running) tuples
        """
        return [
            (strategy_id, instance.running)
            for strategy_id, instance in self._strategies.items()
        ]
