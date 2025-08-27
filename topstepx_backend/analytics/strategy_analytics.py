import asyncio
import logging
from typing import Any, Dict, List

from topstepx_backend.core.event_bus import EventBus, Subscription
from topstepx_backend.core.service import Service
from topstepx_backend.core.topics import order_fill_update, account_position_update


class StrategyAnalytics(Service):
    """Aggregates per-strategy trading metrics."""

    def __init__(self, event_bus: EventBus):
        super().__init__()
        self.event_bus = event_bus
        self.logger = logging.getLogger(__name__)
        self._subscriptions: List[Subscription] = []
        self._tasks: List[asyncio.Task] = []
        # strategy_id -> metrics
        self._metrics: Dict[str, Dict[str, Any]] = {}

    async def start(self) -> None:
        """Start analytics consumers."""
        fill_sub = await self.event_bus.subscribe(
            order_fill_update(), critical=False, maxsize=2000
        )
        pos_sub = await self.event_bus.subscribe(
            account_position_update(), critical=False, maxsize=2000
        )
        self._subscriptions.extend([fill_sub, pos_sub])
        self._tasks.append(asyncio.create_task(self._consume_fills(fill_sub)))
        self._tasks.append(asyncio.create_task(self._consume_positions(pos_sub)))
        self._running = True
        self.logger.info("Strategy analytics service started")

    async def stop(self) -> None:
        """Stop analytics consumers and unsubscribe."""
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        for sub in self._subscriptions:
            sub.close()
        self._subscriptions.clear()
        self.logger.info("Strategy analytics service stopped")

    async def _consume_fills(self, subscription: Subscription) -> None:
        try:
            async for _, payload in subscription:
                if not self._running:
                    break
                self._handle_fill(payload)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Fill consumer error: {e}")

    async def _consume_positions(self, subscription: Subscription) -> None:
        try:
            async for _, payload in subscription:
                if not self._running:
                    break
                self._handle_position(payload)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Position consumer error: {e}")

    def _get_strategy_metrics(self, strategy_id: str) -> Dict[str, Any]:
        return self._metrics.setdefault(
            strategy_id, {"trades_executed": 0, "realized_pnl": 0.0, "position": 0}
        )

    def _handle_fill(self, payload: Dict[str, Any]) -> None:
        strategy_id = payload.get("strategy_id")
        custom_tag = payload.get("custom_tag")
        if not strategy_id and custom_tag:
            strategy_id = str(custom_tag).split("_")[0]
        if not strategy_id:
            return
        metrics = self._get_strategy_metrics(strategy_id)
        metrics["trades_executed"] += 1
        pnl = payload.get("pnl")
        if pnl is not None:
            metrics["realized_pnl"] += float(pnl)

    def _handle_position(self, payload: Dict[str, Any]) -> None:
        strategy_id = payload.get("strategy_id")
        if not strategy_id:
            return
        metrics = self._get_strategy_metrics(strategy_id)
        position = payload.get("position")
        if position is not None:
            metrics["position"] = int(position)
        pnl = payload.get("pnl")
        if pnl is not None:
            metrics["realized_pnl"] = float(pnl)

    def get_metrics(self) -> Dict[str, Dict[str, Any]]:
        """Return metrics for all strategies."""
        return {sid: data.copy() for sid, data in self._metrics.items()}
