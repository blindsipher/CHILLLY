import asyncio
import logging
from dataclasses import dataclass
from typing import Dict, Tuple, Any

from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.core.event_bus import EventBus, Subscription
from topstepx_backend.core.service import Service
from topstepx_backend.core.topics import order_fill_update, account_position_update
from topstepx_backend.data.types import OrderIntent, OrderSide


@dataclass
class PositionState:
    """Holds position and PnL state."""

    position: int = 0
    pnl: float = 0.0


class RiskManager(Service):
    """Manages trading risk limits and account state."""

    def __init__(self, event_bus: EventBus, config: TopstepConfig):
        super().__init__()
        self.event_bus = event_bus
        self.config = config
        self.logger = logging.getLogger(__name__)
        self.positions: Dict[Tuple[int, str], PositionState] = {}
        self.account_pnl: Dict[int, float] = {}
        self._subscriptions: list[Subscription] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Subscribe to necessary topics."""
        fill_sub = await self.event_bus.subscribe(
            order_fill_update(), critical=False, maxsize=2000
        )
        pos_sub = await self.event_bus.subscribe(
            account_position_update(), critical=False, maxsize=2000
        )
        self._subscriptions.extend([fill_sub, pos_sub])
        self._running = True
        self._tasks.append(asyncio.create_task(self._consume_fills(fill_sub)))
        self._tasks.append(asyncio.create_task(self._consume_positions(pos_sub)))
        self.logger.info("Risk manager started")

    async def stop(self) -> None:
        """Stop consumers and unsubscribe."""
        self._running = False
        for task in self._tasks:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        for sub in self._subscriptions:
            await sub.close()
        self._subscriptions.clear()
        self.logger.info("Risk manager stopped")

    async def _consume_fills(self, subscription: Subscription) -> None:
        try:
            async for _, payload in subscription:
                if not self._running:
                    break
                await self._handle_fill(payload)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # pragma: no cover - logging only
            self.logger.error(f"Fill consumer error: {e}")

    async def _consume_positions(self, subscription: Subscription) -> None:
        try:
            async for _, payload in subscription:
                if not self._running:
                    break
                await self._handle_position(payload)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # pragma: no cover - logging only
            self.logger.error(f"Position consumer error: {e}")

    async def _handle_fill(self, payload: Dict[str, Any]) -> None:
        account_id = payload.get("account_id")
        contract_id = payload.get("contract_id")
        side = payload.get("side")
        size = int(payload.get("size", 0))
        pnl = payload.get("pnl")
        if account_id is None or contract_id is None or side is None:
            return
        key = (int(account_id), str(contract_id))
        state = self.positions.setdefault(key, PositionState())
        # Normalize side
        if isinstance(side, OrderSide):
            side_val = side
        else:
            side_val = OrderSide(side)
        if side_val == OrderSide.BUY:
            state.position += size
        else:
            state.position -= size
        if pnl is not None:
            acc_id = int(account_id)
            self.account_pnl[acc_id] = self.account_pnl.get(acc_id, 0.0) + float(pnl)

    async def _handle_position(self, payload: Dict[str, Any]) -> None:
        account_id = payload.get("account_id")
        contract_id = payload.get("contract_id")
        position = payload.get("position")
        pnl = payload.get("pnl")
        if account_id is None or contract_id is None:
            return
        key = (int(account_id), str(contract_id))
        state = self.positions.setdefault(key, PositionState())
        if position is not None:
            state.position = int(position)
        if pnl is not None:
            state.pnl = float(pnl)
            self.account_pnl[int(account_id)] = float(pnl)

    def check_order(self, intent: OrderIntent) -> bool:
        """Validate order against global risk thresholds."""
        # Max order size
        if intent.size > self.config.risk_max_order_size:
            self.logger.warning(
                "Order size %s exceeds limit %s",
                intent.size,
                self.config.risk_max_order_size,
            )
            return False

        key = (intent.account_id, intent.contract_id)
        current = self.positions.get(key, PositionState()).position
        delta = intent.size if intent.side == OrderSide.BUY else -intent.size
        projected = current + delta
        if abs(projected) > self.config.risk_max_position_size:
            self.logger.warning(
                "Projected position %s exceeds limit %s",
                projected,
                self.config.risk_max_position_size,
            )
            return False

        pnl = self.account_pnl.get(intent.account_id, 0.0)
        if pnl <= -self.config.risk_max_daily_loss:
            self.logger.warning(
                "Account %s daily loss limit reached: %s",
                intent.account_id,
                pnl,
            )
            return False

        return True
