"""System orchestrator for coordinating TopstepX backend services."""

import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone
from typing import Dict, Optional, Any
from contextlib import asynccontextmanager

from topstepx_backend.config.settings import get_config, TopstepConfig
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.data.polling_bar_service import PollingBarService
from topstepx_backend.data.historical_fetcher import HistoricalFetcher
from topstepx_backend.data.timeframe_aggregator import TimeframeAggregator
from topstepx_backend.networking.market_hub_agent import MarketHubAgent
from topstepx_backend.networking.user_hub_agent import UserHubAgent
from topstepx_backend.networking.rate_limiter import RateLimiter
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.core.service import Service
from topstepx_backend.core.topics import strategy_add, strategy_remove
from topstepx_backend.core.clock import SystemClock
from topstepx_backend.services.persistence import PersistenceService
from topstepx_backend.services.order_service import OrderService
from topstepx_backend.services.series_cache_service import SeriesCacheService
from topstepx_backend.services.market_subscription_service import MarketSubscriptionService
from topstepx_backend.strategy.runner import StrategyRunner
from topstepx_backend.strategy.registry import StrategyRegistry
from topstepx_backend.networking.subscription_manager import SubscriptionManager
from topstepx_backend.services.risk_manager import RiskManager


class SystemHealth:
    """System health monitoring and reporting."""

    def __init__(self):
        self.components: Dict[str, Dict[str, Any]] = {}
        self.last_health_check = None
        self.overall_status = "unknown"

    def update_component_health(
        self, name: str, status: str, details: Dict[str, Any] = None
    ):
        """Update health status for a component."""
        self.components[name] = {
            "status": status,
            "last_update": datetime.now(timezone.utc),
            "details": details or {},
        }
        self._calculate_overall_status()

    def _calculate_overall_status(self):
        """Calculate overall system health."""
        if not self.components:
            self.overall_status = "unknown"
            return

        statuses = [comp["status"] for comp in self.components.values()]

        if "critical" in statuses:
            self.overall_status = "critical"
        elif "warning" in statuses:
            self.overall_status = "warning"
        elif "error" in statuses:
            self.overall_status = "error"
        elif all(status == "healthy" for status in statuses):
            self.overall_status = "healthy"
        else:
            self.overall_status = "degraded"

    def get_health_report(self) -> Dict[str, Any]:
        """Get comprehensive health report."""
        return {
            "overall_status": self.overall_status,
            "last_check": self.last_health_check,
            "components": self.components,
            "component_count": len(self.components),
            "healthy_components": sum(
                1 for c in self.components.values() if c["status"] == "healthy"
            ),
            "timestamp": datetime.now(timezone.utc),
        }


class TopstepXOrchestrator:
    """Main orchestrator for TopstepX backend system."""

    def __init__(self, config: Optional[TopstepConfig] = None):
        self.config = config or get_config()
        self.logger = logging.getLogger(__name__)
        if self.config.use_uvloop:
            try:
                import uvloop

                asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
                self.logger.info("uvloop enabled")
            except ImportError:  # pragma: no cover - optional dependency
                self.logger.warning("uvloop requested but not installed")

        # Core services
        self.auth_manager: Optional[AuthManager] = None
        self.rate_limiter: Optional[RateLimiter] = None
        # Data persistence service

        # Event system components
        self.event_bus: Optional[EventBus] = None
        self.clock: Optional[SystemClock] = None
        self.persistence: Optional[PersistenceService] = None

        # Data services
        self.polling_bar_service: Optional[PollingBarService] = None
        self.historical_fetcher: Optional[HistoricalFetcher] = None
        self.timeframe_aggregator: Optional[TimeframeAggregator] = None
        # Coordinates dynamic market data subscriptions
        self.market_subscription_service: Optional[MarketSubscriptionService] = None
        self.series_cache_service: Optional[SeriesCacheService] = None

        # Network services
        self.market_hub: Optional[MarketHubAgent] = None
        self.user_hub: Optional[UserHubAgent] = None
        self.subscription_manager: Optional[SubscriptionManager] = None

        # Trading services
        self.order_service: Optional[OrderService] = None
        self.risk_manager: Optional[RiskManager] = None
        self.strategy_runner: Optional[StrategyRunner] = None

        # System management
        self.health_monitor = SystemHealth()
        self._running = False
        self._shutdown_event = asyncio.Event()
        self._health_check_task: Optional[asyncio.Task] = None

        # Track initialized services for startup/shutdown sequencing
        self._services: list[Service] = []

        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Setup signal handlers for graceful shutdown."""
        if sys.platform != "win32":
            for sig in [signal.SIGINT, signal.SIGTERM]:
                signal.signal(sig, self._signal_handler)

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals."""
        self.logger.info("Received signal %s, initiating graceful shutdown...", signum)
        asyncio.create_task(self.shutdown())

    async def initialize_services(self):
        """Initialize all services in dependency order."""
        self.logger.info("Initializing TopstepX backend services...")

        try:
            # Instantiate core components
            self.rate_limiter = RateLimiter()
            self.health_monitor.update_component_health("rate_limiter", "healthy")

            self.auth_manager = AuthManager(self.config)

            if self.config.event_backend == "redis":
                from topstepx_backend.core.redis_event_bus import RedisEventBus

                self.event_bus = RedisEventBus(self.config.redis_url)
            else:
                self.event_bus = EventBus(default_maxsize=5000)

            self.clock = SystemClock(self.event_bus)
            self.persistence = PersistenceService(
                self.event_bus, self.config.database_path
            )
            self.subscription_manager = SubscriptionManager(self.rate_limiter)
            self.timeframe_aggregator = TimeframeAggregator(
                self.config, self.event_bus
            )
            self.market_subscription_service = MarketSubscriptionService(
                self.config, self.event_bus
            )
            self.historical_fetcher = HistoricalFetcher(
                self.config, self.auth_manager, self.rate_limiter, self.event_bus
            )
            self.polling_bar_service = PollingBarService(
                self.config,
                self.auth_manager,
                self.rate_limiter,
                self.event_bus,
                self.market_subscription_service,
            )
            self.series_cache_service = SeriesCacheService(
                self.config, self.event_bus, max_bars_per_series=1000
            )
            self.market_hub = MarketHubAgent(self.config, self.auth_manager)
            self.user_hub = UserHubAgent(
                self.config, self.auth_manager, self.event_bus
            )
            self.risk_manager = RiskManager(self.event_bus, self.config)
            self.order_service = OrderService(
                self.event_bus, self.auth_manager, self.config, self.rate_limiter
            )
            registry = StrategyRegistry()
            self.strategy_runner = StrategyRunner(
                self.event_bus,
                registry,
                market_subscription_service=self.market_subscription_service,
                timeframe_aggregator=self.timeframe_aggregator,
                risk_manager=self.risk_manager,
                config=self.config,
            )

            # Services in startup order
            self._services = [
                self.auth_manager,
                self.event_bus,
                self.clock,
                self.persistence,
                self.timeframe_aggregator,
                self.market_subscription_service,
                self.historical_fetcher,
                self.polling_bar_service,
                self.series_cache_service,
                self.market_hub,
                self.user_hub,
                self.risk_manager,
                self.order_service,
                self.strategy_runner,
            ]

            for service in self._services:
                name = service.__class__.__name__.lower()
                if isinstance(service, SystemClock):
                    await service.start(
                        {"1m", "5m", "10m", "15m", "20m", "25m", "30m", "45m", "1h", "4h", "1d"}
                    )
                else:
                    await service.start()
                self.health_monitor.update_component_health(name, "healthy")

            # Initialize subscription manager health after services start
            self.health_monitor.update_component_health(
                "subscription_manager", "healthy"
            )

            if self.subscription_manager:
                self.subscription_manager.track_account(self.config.account_id)

            self.logger.info("All services initialized successfully")

        except Exception as e:
            self.logger.error("Failed to initialize services: %s", e)
            await self.shutdown()
            raise

    async def start(self):
        """Start the orchestrator and all services."""
        if self._running:
            self.logger.warning("Orchestrator is already running")
            return

        self.logger.info("Starting TopstepX backend orchestrator...")

        try:
            await self.initialize_services()
            self._running = True

            # Start health monitoring
            self._health_check_task = asyncio.create_task(self._health_check_loop())

            self.logger.info("TopstepX backend orchestrator started successfully")

            # Wait for shutdown signal
            await self._shutdown_event.wait()

        except Exception as e:
            self.logger.error("Error starting orchestrator: %s", e)
            await self.shutdown()
            raise

    async def shutdown(self):
        """Gracefully shutdown all services."""
        if not self._running:
            return

        self.logger.info("Shutting down TopstepX backend orchestrator...")
        self._running = False
        self._shutdown_event.set()

        # Cancel health check task
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # Shutdown services in reverse order
        for service in reversed(self._services):
            name = service.__class__.__name__.lower()
            try:
                self.logger.info("Stopping %s...", name)
                await service.stop()
                self.health_monitor.update_component_health(name, "stopped")
            except Exception as e:
                self.logger.error("Error stopping %s: %s", name, e)

        self.logger.info("TopstepX backend orchestrator shutdown complete")

    async def add_strategy(self, config: Dict[str, Any]) -> None:
        """Publish a request to add a strategy at runtime."""
        if not self.event_bus:
            return
        await self.event_bus.publish(strategy_add(), config)

    async def remove_strategy(self, strategy_id: str) -> None:
        """Publish a request to remove a strategy at runtime."""
        if not self.event_bus:
            return
        await self.event_bus.publish(
            strategy_remove(), {"strategy_id": strategy_id}
        )

    async def _health_check_loop(self):
        """Periodic health check loop."""
        while self._running:
            try:
                await self._perform_health_check()
                await asyncio.sleep(30)  # Check every 30 seconds
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Health check error: %s", e)
                await asyncio.sleep(10)  # Shorter retry on error

    async def _perform_health_check(self):
        """Perform comprehensive health check of all services."""
        self.health_monitor.last_health_check = datetime.now(timezone.utc)

        # Check authentication
        if self.auth_manager:
            try:
                token_valid = await self.auth_manager.is_token_valid()
                status = "healthy" if token_valid else "warning"
                self.health_monitor.update_component_health(
                    "auth_manager",
                    status,
                    {
                        "token_valid": token_valid,
                        "token_expires": getattr(
                            self.auth_manager, "_token_expires", None
                        ),
                    },
                )
            except Exception as e:
                self.health_monitor.update_component_health(
                    "auth_manager", "error", {"error": str(e)}
                )

        # Check event system
        if self.event_bus:
            metrics = self.event_bus.get_metrics()
            queue_utilization = (
                metrics.get("active_subscribers", 0)
                / max(metrics.get("max_subscribers", 1), 1)
            ) * 100
            status = "warning" if queue_utilization > 80 else "healthy"
            self.health_monitor.update_component_health(
                "event_bus",
                status,
                {
                    "queue_utilization_percent": queue_utilization,
                    "events_published": metrics.get("events_published", 0),
                    "active_subscribers": metrics.get("active_subscribers", 0),
                    "running": metrics.get("running", False),
                },
            )

        # Check system clock
        if self.clock:
            clock_metrics = self.clock.get_metrics()
            status = "healthy" if clock_metrics.get("running", False) else "error"
            self.health_monitor.update_component_health(
                "clock",
                status,
                {
                    "boundaries_emitted": clock_metrics.get("boundaries_emitted", 0),
                    "running": clock_metrics.get("running", False),
                },
            )

        # Check persistence service
        if self.persistence:
            persistence_metrics = self.persistence.get_metrics()
            status = "healthy" if persistence_metrics.get("running", False) else "error"
            self.health_monitor.update_component_health(
                "persistence",
                status,
                {
                    "bars_saved": persistence_metrics.get("bars_saved", 0),
                    "ticks_saved": persistence_metrics.get("ticks_saved", 0),
                    "running": persistence_metrics.get("running", False),
                },
            )

        # Check polling bar service
        if self.polling_bar_service:
            stats = self.polling_bar_service.get_metrics()
            health_status = self.polling_bar_service.get_health_status()
            status = "healthy" if health_status.get("status") == "healthy" else "error"
            self.health_monitor.update_component_health(
                "polling_bar_service",
                status,
                {
                    "polls_completed": stats.get("polls_completed", 0),
                    "bars_published": stats.get("bars_published", 0),
                    "partial_bars_published": stats.get("partial_bars_published", 0),
                    "subscribed_markets": stats.get("subscribed_markets", 0),
                    "active_markets": stats.get("active_markets", 0),
                },
            )

        # Check hub connections
        for hub_name, hub in [
            ("market_hub", self.market_hub),
            ("user_hub", self.user_hub),
        ]:
            if hub:
                connected = getattr(hub, "_connected", False)
                status = "healthy" if connected else "error"
                self.health_monitor.update_component_health(
                    hub_name,
                    status,
                    {
                        "connected": connected,
                        "reconnect_count": getattr(hub, "_reconnect_count", 0),
                    },
                )

    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        status = {
            "orchestrator": {
                "running": self._running,
                "startup_time": getattr(self, "_startup_time", None),
            },
            "health": self.health_monitor.get_health_report(),
        }

        # Add service-specific metrics

        if self.series_cache_service:
            status["series_cache_service"] = self.series_cache_service.get_stats()

        if self.polling_bar_service:
            status["polling_bar_service"] = self.polling_bar_service.get_metrics()

        if self.timeframe_aggregator:
            status["timeframe_aggregator"] = self.timeframe_aggregator.get_stats()

        # Add event system metrics
        if self.event_bus:
            status["event_bus"] = self.event_bus.get_metrics()

        # Add system clock metrics
        if self.clock:
            status["clock"] = self.clock.get_metrics()

        # Add persistence service metrics
        if self.persistence:
            status["persistence"] = self.persistence.get_metrics()

        return status

    @asynccontextmanager
    async def managed_lifecycle(self):
        """Context manager for managed service lifecycle."""
        try:
            await self.start()
            yield self
        finally:
            await self.shutdown()


async def main():
    """Main entry point for the orchestrator."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    orchestrator = TopstepXOrchestrator()

    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        logging.info("Received keyboard interrupt")
    except Exception as e:
        logging.error("Orchestrator error: %s", e)
    finally:
        await orchestrator.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
