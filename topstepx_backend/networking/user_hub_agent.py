from typing import Optional, Callable, List, TYPE_CHECKING
from dataclasses import dataclass

from topstepx_backend.networking.hub_agent import HubAgent
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.config.settings import TopstepConfig

if TYPE_CHECKING:
    from topstepx_backend.core.event_bus import EventBus


@dataclass
class UserAccountData:
    """Account data structure."""

    id: int
    name: str
    balance: float
    can_trade: bool
    is_visible: bool
    simulated: bool


@dataclass
class UserOrderData:
    """Order data structure."""

    id: int
    account_id: int
    contract_id: str
    symbol_id: str
    creation_timestamp: str
    update_timestamp: str
    status: int  # OrderStatus enum
    type: int  # OrderType enum
    side: int  # OrderSide enum
    size: int
    limit_price: Optional[float]
    stop_price: Optional[float]
    fill_volume: int
    filled_price: Optional[float]
    custom_tag: Optional[str]


@dataclass
class UserPositionData:
    """Position data structure."""

    id: int
    account_id: int
    contract_id: str
    creation_timestamp: str
    type: int  # PositionType enum
    size: int
    average_price: float


@dataclass
class UserTradeData:
    """Trade data structure."""

    id: int
    account_id: int
    contract_id: str
    creation_timestamp: str
    price: float
    profit_and_loss: Optional[float]
    fees: float
    side: int  # OrderSide enum
    size: int
    voided: bool
    order_id: int


class UserHubAgent(HubAgent):
    """SignalR agent for user hub - handles accounts, orders, positions, and trades."""

    def __init__(
        self,
        config: TopstepConfig,
        auth_manager: AuthManager,
        event_bus: Optional["EventBus"] = None,
    ):
        super().__init__(config, auth_manager, "user")
        self.event_bus = event_bus

        # Data handlers
        self.account_handlers: List[Callable[[UserAccountData], None]] = []
        self.order_handlers: List[Callable[[UserOrderData], None]] = []
        self.position_handlers: List[Callable[[UserPositionData], None]] = []
        self.trade_handlers: List[Callable[[UserTradeData], None]] = []

        # Subscription configuration
        self.subscribed_account_ids: List[int] = []
        self.subscribe_to_all_accounts = True

        # Setup event handlers
        self.register_event_handler("GatewayUserAccount", self._handle_account_update)
        self.register_event_handler("GatewayUserOrder", self._handle_order_update)
        self.register_event_handler("GatewayUserPosition", self._handle_position_update)
        self.register_event_handler("GatewayUserTrade", self._handle_trade_update)

    @property
    def hub_url_template(self) -> str:
        """User hub URL template - ProjectX Gateway API."""
        return self.config.projectx_user_hub_url

    async def _setup_subscriptions(self):
        """Setup user hub subscriptions."""
        try:
            # Subscribe to accounts
            await self.send_message("SubscribeAccounts", [])
            self.logger.info("Subscribed to account updates")

            # Subscribe to orders, positions, and trades
            if self.subscribe_to_all_accounts:
                # Subscribe using primary account ID from config (TopstepX requires specific account ID)
                account_id = self.config.account_id
                await self.send_message("SubscribeOrders", [account_id])
                await self.send_message("SubscribePositions", [account_id])
                await self.send_message("SubscribeTrades", [account_id])
                self.logger.info(
                    f"Subscribed to primary account {account_id} orders, positions, and trades"
                )
            else:
                # Subscribe to specific accounts
                for account_id in self.subscribed_account_ids:
                    await self.send_message("SubscribeOrders", [account_id])
                    await self.send_message("SubscribePositions", [account_id])
                    await self.send_message("SubscribeTrades", [account_id])
                    self.logger.info(
                        f"Subscribed to account {account_id} orders, positions, and trades"
                    )

        except Exception as e:
            self.logger.error(f"Failed to setup user hub subscriptions: {e}")
            raise

    async def subscribe_to_account(self, account_id: int):
        """Subscribe to a specific account's updates."""
        if account_id not in self.subscribed_account_ids:
            self.subscribed_account_ids.append(account_id)

        if self.is_connected:
            await self.send_message("SubscribeOrders", [account_id])
            await self.send_message("SubscribePositions", [account_id])
            await self.send_message("SubscribeTrades", [account_id])
            self.logger.info(f"Added subscription for account {account_id}")

    async def unsubscribe_from_account(self, account_id: int):
        """Unsubscribe from a specific account's updates."""
        if account_id in self.subscribed_account_ids:
            self.subscribed_account_ids.remove(account_id)

        if self.is_connected:
            await self.send_message("UnsubscribeOrders", [account_id])
            await self.send_message("UnsubscribePositions", [account_id])
            await self.send_message("UnsubscribeTrades", [account_id])
            self.logger.info(f"Removed subscription for account {account_id}")

    async def _handle_account_update(self, message):
        """Handle account update events."""
        try:
            self._update_message_stats()
            data = message[0] if isinstance(message, list) else message

            account = UserAccountData(
                id=data.get("id"),
                name=data.get("name", ""),
                balance=data.get("balance", 0.0),
                can_trade=data.get("canTrade", False),
                is_visible=data.get("isVisible", False),
                simulated=data.get("simulated", False),
            )

            self.logger.debug(
                f"Account update: {account.id} - Balance: {account.balance}"
            )

            # Call registered handlers
            for handler in self.account_handlers:
                try:
                    handler(account)
                except Exception as e:
                    self.logger.error(f"Error in account handler: {e}")

        except Exception as e:
            self.logger.error(f"Error processing account update: {e}")

    async def _handle_order_update(self, message):
        """Handle order update events."""
        try:
            self._update_message_stats()
            data = message[0] if isinstance(message, list) else message

            order = UserOrderData(
                id=data.get("id"),
                account_id=data.get("accountId"),
                contract_id=data.get("contractId", ""),
                symbol_id=data.get("symbolId", ""),
                creation_timestamp=data.get("creationTimestamp", ""),
                update_timestamp=data.get("updateTimestamp", ""),
                status=data.get("status", 0),
                type=data.get("type", 0),
                side=data.get("side", 0),
                size=data.get("size", 0),
                limit_price=data.get("limitPrice"),
                stop_price=data.get("stopPrice"),
                fill_volume=data.get("fillVolume", 0),
                filled_price=data.get("filledPrice"),
                custom_tag=data.get("customTag"),
            )

            self.logger.debug(
                f"Order update: {order.id} - Status: {order.status}, Size: {order.size}"
            )

            # Publish typed event to EventBus
            if self.event_bus:
                from topstepx_backend.core.topics import order_status_update
                from topstepx_backend.data.types import OrderStatus
                from topstepx_backend.networking.api_helpers import utc_now

                # Convert to typed event
                status_event = {
                    "order_id": order.id,
                    "custom_tag": order.custom_tag,
                    "status": OrderStatus.WORKING.value
                    if order.status == 1
                    else "Unknown",
                    "timestamp": utc_now().isoformat(),
                }

                await self.event_bus.publish(order_status_update(), status_event)

            # Call registered handlers
            for handler in self.order_handlers:
                try:
                    handler(order)
                except Exception as e:
                    self.logger.error(f"Error in order handler: {e}")

        except Exception as e:
            self.logger.error(f"Error processing order update: {e}")

    async def _handle_position_update(self, message):
        """Handle position update events."""
        try:
            self._update_message_stats()
            data = message[0] if isinstance(message, list) else message

            position = UserPositionData(
                id=data.get("id"),
                account_id=data.get("accountId"),
                contract_id=data.get("contractId", ""),
                creation_timestamp=data.get("creationTimestamp", ""),
                type=data.get("type", 0),
                size=data.get("size", 0),
                average_price=data.get("averagePrice", 0.0),
            )

            self.logger.debug(
                f"Position update: {position.id} - Size: {position.size}, Avg Price: {position.average_price}"
            )

            # Call registered handlers
            for handler in self.position_handlers:
                try:
                    handler(position)
                except Exception as e:
                    self.logger.error(f"Error in position handler: {e}")

        except Exception as e:
            self.logger.error(f"Error processing position update: {e}")

    async def _handle_trade_update(self, message):
        """Handle trade update events."""
        try:
            self._update_message_stats()
            data = message[0] if isinstance(message, list) else message

            trade = UserTradeData(
                id=data.get("id"),
                account_id=data.get("accountId"),
                contract_id=data.get("contractId", ""),
                creation_timestamp=data.get("creationTimestamp", ""),
                price=data.get("price", 0.0),
                profit_and_loss=data.get("profitAndLoss"),
                fees=data.get("fees", 0.0),
                side=data.get("side", 0),
                size=data.get("size", 0),
                voided=data.get("voided", False),
                order_id=data.get("orderId"),
            )

            self.logger.debug(
                f"Trade update: {trade.id} - Price: {trade.price}, Size: {trade.size}, P&L: {trade.profit_and_loss}"
            )

            # Publish typed event to EventBus for fills
            if self.event_bus:
                from topstepx_backend.core.topics import order_fill_update
                from topstepx_backend.networking.api_helpers import utc_now

                # Convert to order fill event
                fill_event = {
                    "order_id": trade.order_id,
                    "custom_tag": f"fill_{trade.id}",  # TODO: Get actual custom_tag from order correlation
                    "price": trade.price,
                    "size": trade.size,
                    "fees": trade.fees,
                    "timestamp": utc_now().isoformat(),
                    "pnl": trade.profit_and_loss,
                }

                await self.event_bus.publish(order_fill_update(), fill_event)

            # Call registered handlers
            for handler in self.trade_handlers:
                try:
                    handler(trade)
                except Exception as e:
                    self.logger.error(f"Error in trade handler: {e}")

        except Exception as e:
            self.logger.error(f"Error processing trade update: {e}")

    def add_account_handler(self, handler: Callable[[UserAccountData], None]):
        """Add account update handler."""
        self.account_handlers.append(handler)

    def add_order_handler(self, handler: Callable[[UserOrderData], None]):
        """Add order update handler."""
        self.order_handlers.append(handler)

    def add_position_handler(self, handler: Callable[[UserPositionData], None]):
        """Add position update handler."""
        self.position_handlers.append(handler)

    def add_trade_handler(self, handler: Callable[[UserTradeData], None]):
        """Add trade update handler."""
        self.trade_handlers.append(handler)

    def remove_account_handler(self, handler: Callable[[UserAccountData], None]):
        """Remove account update handler."""
        if handler in self.account_handlers:
            self.account_handlers.remove(handler)

    def remove_order_handler(self, handler: Callable[[UserOrderData], None]):
        """Remove order update handler."""
        if handler in self.order_handlers:
            self.order_handlers.remove(handler)

    def remove_position_handler(self, handler: Callable[[UserPositionData], None]):
        """Remove position update handler."""
        if handler in self.position_handlers:
            self.position_handlers.remove(handler)

    def remove_trade_handler(self, handler: Callable[[UserTradeData], None]):
        """Remove trade update handler."""
        if handler in self.trade_handlers:
            self.trade_handlers.remove(handler)
