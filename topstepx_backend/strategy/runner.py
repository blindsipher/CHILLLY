"""Strategy runner for managing multiple strategy instances."""

import asyncio
import importlib.util
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

try:  # pragma: no cover - optional dependency
    from watchdog.events import FileSystemEventHandler
    from watchdog.observers import Observer
except Exception:  # pragma: no cover - watchdog may not be installed
    FileSystemEventHandler = object  # type: ignore
    Observer = None

from topstepx_backend.core.event_bus import EventBus, Subscription
from topstepx_backend.core.topics import (
    market_bar,
    boundary,
    strategy_add,
    strategy_remove,
)
from topstepx_backend.strategy.base import Strategy
from topstepx_backend.strategy.context import StrategyContext, RiskLimits
from topstepx_backend.config.settings import TopstepConfig, get_config
from topstepx_backend.strategy.registry import StrategyRegistry
from topstepx_backend.data.models import Bar

if TYPE_CHECKING:
    from topstepx_backend.services.market_subscription_service import (
        MarketSubscriptionService,
    )
    from topstepx_backend.data.timeframe_aggregator import TimeframeAggregator
    from topstepx_backend.services.risk_manager import RiskManager


@dataclass
class StrategyInstance:
    """Container for strategy and its execution context."""

    strategy: Strategy
    context: StrategyContext
    config: Dict[str, Any]
    subscriptions: List[Subscription]
    running: bool = False


class _StrategyPluginHandler(FileSystemEventHandler):
    """Watchdog event handler that publishes strategy add/remove events."""

    def __init__(self, registry: StrategyRegistry, event_bus: EventBus, loop: asyncio.AbstractEventLoop):
        self.registry = registry
        self.event_bus = event_bus
        self.loop = loop
        self.logger = logging.getLogger(__name__)
        # Map file path -> strategy_id to handle removals
        self._file_map: Dict[str, str] = {}

    # Helper to load config from plugin module
    def _load_config(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            module_name = f"strategies.{path.stem}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if not spec or not spec.loader:  # pragma: no cover
                return None
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[attr-defined]

            # Register any new classes discovered in this module
            self.registry.discover_strategy_classes()

            config = getattr(module, "STRATEGY_CONFIG", None) or getattr(
                module, "CONFIG", None
            )
            if isinstance(config, dict):
                config = dict(config)
                if "class" not in config:
                    # Attempt to derive class path
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, Strategy)
                            and attr is not Strategy
                        ):
                            config["class"] = f"{module_name}.{attr_name}"
                            break
                return config
        except Exception as e:  # pragma: no cover - plugin errors
            self.logger.error(f"Failed to load strategy plugin {path}: {e}")
        return None

    def on_created(self, event):  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        if not str(event.src_path).endswith(".py"):
            return
        config = self._load_config(Path(event.src_path))
        if not config:
            return
        strategy_id = config.get("strategy_id")
        if not strategy_id:
            return
        self._file_map[event.src_path] = strategy_id
        asyncio.run_coroutine_threadsafe(
            self.event_bus.publish(strategy_add(), config), self.loop
        )

    # Treat modification as reload
    on_modified = on_created  # type: ignore

    def on_deleted(self, event):  # type: ignore[override]
        if getattr(event, "is_directory", False):
            return
        if not str(event.src_path).endswith(".py"):
            return
        strategy_id = self._file_map.pop(event.src_path, None)
        if not strategy_id:
            return
        asyncio.run_coroutine_threadsafe(
            self.event_bus.publish(strategy_remove(), {"strategy_id": strategy_id}),
            self.loop,
        )


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
        self,
        event_bus: EventBus,
        registry: Optional[StrategyRegistry] = None,
        market_subscription_service: Optional["MarketSubscriptionService"] = None,
        timeframe_aggregator: Optional["TimeframeAggregator"] = None,
        risk_manager: Optional["RiskManager"] = None,
        config: Optional[TopstepConfig] = None,
    ):
        """
        Initialize strategy runner.

        Args:
            event_bus: EventBus for publishing/subscribing to events
            registry: Strategy registry for loading configurations
        """
        self.event_bus = event_bus
        self.registry = registry or StrategyRegistry()
        self.market_subscription_service = market_subscription_service
        self.timeframe_aggregator = timeframe_aggregator
        self.risk_manager = risk_manager
        self.config = config or get_config()
        self.logger = logging.getLogger(__name__)

        # Strategy instances
        self._strategies: Dict[str, StrategyInstance] = {}
        self._running = False

        # Event processing tasks
        self._consumer_tasks: List[asyncio.Task] = []
        self._mgmt_subscriptions: List[Subscription] = []
        # Filesystem plugin watcher
        self._plugin_observer: Optional[Observer] = None
        self._plugin_handler: Optional[_StrategyPluginHandler] = None

    async def start(self) -> None:
        """Start the strategy runner and all configured strategies."""
        if self._running:
            self.logger.warning("StrategyRunner already running")
            return

        self.logger.info("Starting StrategyRunner...")

        try:
            # Load strategy configurations and create instances
            await self._load_and_create_strategies()

            # Register contracts with external services
            await self._register_active_contracts()

            # Start all strategies
            await self._start_all_strategies()

            # Subscribe to management events
            await self._setup_management_subscriptions()

            # Start filesystem watcher for strategy plugins
            await self._start_plugin_watcher()

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
        # Unsubscribe from management topics
        for sub in self._mgmt_subscriptions:
            await self.event_bus.unsubscribe(sub)
        self._mgmt_subscriptions.clear()

        # Stop filesystem watcher
        await self._stop_plugin_watcher()

        self.logger.info("StrategyRunner stopped")

    async def _start_plugin_watcher(self) -> None:
        """Start watchdog observer for the strategies plugin directory."""
        if Observer is None:
            self.logger.warning("watchdog not installed - plugin directory will not be monitored")
            return

        plugin_dir = self.registry.plugin_path
        if not plugin_dir.exists():
            return

        loop = asyncio.get_running_loop()
        handler = _StrategyPluginHandler(self.registry, self.event_bus, loop)
        observer = Observer()
        observer.schedule(handler, str(plugin_dir), recursive=False)
        observer.start()

        self._plugin_observer = observer
        self._plugin_handler = handler

    async def _stop_plugin_watcher(self) -> None:
        """Stop the plugin directory observer if running."""
        if self._plugin_observer:
            self._plugin_observer.stop()
            self._plugin_observer.join()
            self._plugin_observer = None
            self._plugin_handler = None

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
                max_position_size=risk_config.get(
                    "max_position_size", self.config.risk_max_position_size
                ),
                max_daily_loss=risk_config.get(
                    "max_daily_loss", self.config.risk_max_daily_loss
                ),
                max_order_size=risk_config.get(
                    "max_order_size", self.config.risk_max_order_size
                ),
                max_orders_per_minute=risk_config.get(
                    "max_orders_per_minute", self.config.risk_max_orders_per_minute
                ),
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
                risk_manager=self.risk_manager,
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
        """Convert event payload to Bar object."""
        try:
            if isinstance(payload, dict):
                return Bar(**payload)
            if isinstance(payload, Bar):
                return payload
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

    # ------------------------------------------------------------------
    # Management event handlers
    # ------------------------------------------------------------------

    async def _setup_management_subscriptions(self) -> None:
        """Subscribe to add/remove strategy events."""
        add_sub = await self.event_bus.subscribe(
            strategy_add(), critical=True, maxsize=100
        )
        remove_sub = await self.event_bus.subscribe(
            strategy_remove(), critical=True, maxsize=100
        )
        self._mgmt_subscriptions.extend([add_sub, remove_sub])
        self._consumer_tasks.append(
            asyncio.create_task(self._consume_strategy_add_events(add_sub))
        )
        self._consumer_tasks.append(
            asyncio.create_task(self._consume_strategy_remove_events(remove_sub))
        )

    async def _consume_strategy_add_events(self, subscription: Subscription) -> None:
        """Consume strategy addition events."""
        try:
            async for _, payload in subscription:
                if not self._running:
                    break
                if isinstance(payload, dict):
                    await self.add_strategy(payload)
        except asyncio.CancelledError:
            pass

    async def _consume_strategy_remove_events(
        self, subscription: Subscription
    ) -> None:
        """Consume strategy removal events."""
        try:
            async for _, payload in subscription:
                if not self._running:
                    break
                strategy_id = (
                    payload.get("strategy_id")
                    if isinstance(payload, dict)
                    else str(payload)
                )
                if strategy_id:
                    await self.remove_strategy(strategy_id)
        except asyncio.CancelledError:
            pass

    async def add_strategy(self, config: Dict[str, Any]) -> None:
        """Add and start a new strategy at runtime."""
        strategy_id = config.get("strategy_id")
        if not strategy_id or strategy_id in self._strategies:
            return

        self.registry.add_or_update_config(config)
        await self._create_strategy_instance(config)
        instance = self._strategies.get(strategy_id)
        if not instance:
            return

        # Register contract with services before starting
        await self._register_contract(config.get("contract_id"))

        await self._start_strategy_instance(strategy_id, instance)

    async def remove_strategy(self, strategy_id: str) -> None:
        """Stop and remove a strategy at runtime."""
        instance = self._strategies.get(strategy_id)
        if not instance:
            return

        await self._stop_strategy_instance(strategy_id, instance)
        del self._strategies[strategy_id]
        self.registry.remove_strategy_config(strategy_id)

        await self._unregister_contract(instance.config.get("contract_id"))

    async def _register_active_contracts(self) -> None:
        """Register all current strategy contracts with external services."""
        contracts = {inst.config.get("contract_id") for inst in self._strategies.values()}
        for contract in contracts:
            await self._register_contract(contract)

    async def _register_contract(self, contract_id: Optional[str]) -> None:
        if not contract_id:
            return
        if self.market_subscription_service:
            try:
                await self.market_subscription_service.add_subscription(contract_id)
            except Exception as e:
                self.logger.error(f"Failed to add subscription for {contract_id}: {e}")
        if self.timeframe_aggregator:
            watchlist = self.timeframe_aggregator.get_watchlist() or []
            if contract_id not in watchlist:
                watchlist.append(contract_id)
                self.timeframe_aggregator.update_watchlist(watchlist)

    async def _unregister_contract(self, contract_id: Optional[str]) -> None:
        if not contract_id:
            return
        # Only remove if no strategies still use the contract
        still_used = any(
            inst.config.get("contract_id") == contract_id
            for inst in self._strategies.values()
        )
        if still_used:
            return

        if self.market_subscription_service:
            try:
                await self.market_subscription_service.remove_subscription(contract_id)
            except Exception as e:
                self.logger.error(
                    f"Failed to remove subscription for {contract_id}: {e}"
                )
        if self.timeframe_aggregator:
            remaining = [inst.config.get("contract_id") for inst in self._strategies.values()]
            self.timeframe_aggregator.update_watchlist(remaining)
