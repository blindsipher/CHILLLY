from typing import Callable, List, Dict, Optional
from dataclasses import dataclass
import asyncio

from topstepx_backend.networking.hub_agent import HubAgent
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.utils.contract_validator import contract_validator
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.core.topics import market_quote, market_trade, market_depth


@dataclass
class MarketQuoteData:
    """Market quote data structure."""

    symbol: str
    symbol_name: str
    last_price: float
    best_bid: float
    best_ask: float
    change: float
    change_percent: float
    open: float
    high: float
    low: float
    volume: int
    last_updated: str
    timestamp: str


@dataclass
class MarketTradeData:
    """Market trade data structure."""

    symbol_id: str
    price: float
    timestamp: str
    type: int  # TradeLogType enum (0=Buy, 1=Sell)
    volume: int


@dataclass
class MarketDepthData:
    """Market depth (DOM) data structure."""

    symbol_id: str
    timestamp: str
    type: int  # DomType enum
    price: float
    volume: int
    current_volume: int


class MarketHubAgent(HubAgent):
    """SignalR agent for market hub - handles quotes, trades, and market depth."""

    def __init__(self, config: TopstepConfig, auth_manager: AuthManager, event_bus: EventBus, contract_service=None):
        super().__init__(config, auth_manager, "market")

        # Event bus integration
        self.event_bus = event_bus

        # Data handlers
        self.quote_handlers: List[Callable[[str, MarketQuoteData], None]] = []
        self.trade_handlers: List[Callable[[str, MarketTradeData], None]] = []
        self.depth_handlers: List[Callable[[str, MarketDepthData], None]] = []

        # Enhanced subscription management with state preservation
        self.subscription_state: Dict[str, Dict[str, bool]] = {}
        self._state_lock = asyncio.Lock()

        # Contract validation
        self.contract_service = contract_service
        self._contract_cache: Dict[str, str] = {}

        # Statistics
        self._validation_stats = {
            'valid_contracts': 0,
            'invalid_contracts': 0,
            'lookups_performed': 0,
            'cache_hits': 0
        }

        # Setup event handlers
        self.register_event_handler("GatewayQuote", self._handle_quote_update)
        self.register_event_handler("GatewayTrade", self._handle_trade_update)
        self.register_event_handler("GatewayDepth", self._handle_depth_update)

    @property
    def hub_url_template(self) -> str:
        """Market hub URL - No token in URL per security best practices."""
        return self.config.projectx_market_hub_url

    async def _validate_and_normalize_contract_id(self, identifier: str) -> Optional[str]:
        """Validate and normalize contract identifier to full contract ID."""
        validation = contract_validator.validate(identifier)
        
        if not validation.is_valid:
            self.logger.error(f"Invalid contract identifier: {validation.error_message}")
            self._validation_stats['invalid_contracts'] += 1
            return None
        
        # Already a valid contract ID
        if validation.normalized_id:
            self._validation_stats['valid_contracts'] += 1
            return validation.normalized_id
        
        # Check cache first
        if identifier in self._contract_cache:
            self._validation_stats['cache_hits'] += 1
            return self._contract_cache[identifier]
        
        # Perform API lookup if service available
        if validation.requires_lookup and self.contract_service:
            try:
                self._validation_stats['lookups_performed'] += 1
                success, contract_id, error = await contract_validator.normalize_identifier(
                    identifier, self.contract_service
                )
                
                if success and contract_id:
                    self._contract_cache[identifier] = contract_id
                    self._validation_stats['valid_contracts'] += 1
                    self.logger.info(f"Resolved '{identifier}' to '{contract_id}'")
                    return contract_id
                else:
                    self.logger.error(f"Failed to resolve '{identifier}': {error}")
                    self._validation_stats['invalid_contracts'] += 1
                    return None
                    
            except Exception as e:
                error_msg = f"Failed to lookup symbol '{identifier}': {e}"
                self.logger.error(error_msg)
                self._validation_stats['invalid_contracts'] += 1
                return None
        
        self.logger.error(f"Cannot resolve symbol '{identifier}' without contract service")
        self._validation_stats['invalid_contracts'] += 1
        return None

    async def _setup_subscriptions(self):
        """Restore all subscriptions with exact state after reconnection."""
        self.logger.info("Restoring market subscriptions after reconnection")
        
        async with self._state_lock:
            if not self.subscription_state:
                return
                
            self.logger.info(f"Restoring {len(self.subscription_state)} contract subscriptions")
            
            for contract_id, flags in self.subscription_state.items():
                try:
                    await self._subscribe_to_contract(
                        contract_id,
                        flags.get('quotes', True),
                        flags.get('trades', True),
                        flags.get('depth', False)
                    )
                    
                    self.logger.debug(f"Restored subscription for {contract_id}: {flags}")
                    
                    # Small delay to avoid overwhelming server
                    if len(self.subscription_state) > 1:
                        await asyncio.sleep(0.05)
                        
                except Exception as e:
                    self.logger.error(f"Failed to restore subscription for {contract_id}: {e}")

    async def subscribe_to_contract(
        self,
        contract_identifier: str,
        quotes: bool = True,
        trades: bool = True,
        depth: bool = False,
    ):
        """Subscribe to market data with contract validation and state preservation."""
        # Validate and normalize contract ID
        contract_id = await self._validate_and_normalize_contract_id(contract_identifier)
        if not contract_id:
            raise ValueError(f"Invalid contract identifier: {contract_identifier}")
        
        # Store subscription state with exact flags
        async with self._state_lock:
            self.subscription_state[contract_id] = {
                'quotes': quotes,
                'trades': trades,  
                'depth': depth
            }
        
        # Subscribe if connected
        if self.is_connected:
            await self._subscribe_to_contract(contract_id, quotes, trades, depth)
            
        self.logger.info(
            f"Subscribed to {contract_id} "
            f"(quotes={quotes}, trades={trades}, depth={depth})"
        )

    async def unsubscribe_from_contract(self, contract_identifier: str):
        """Unsubscribe from market data with contract validation."""
        # Validate contract ID
        contract_id = await self._validate_and_normalize_contract_id(contract_identifier)
        if not contract_id:
            raise ValueError(f"Invalid contract identifier: {contract_identifier}")
        
        # Remove from state
        async with self._state_lock:
            if contract_id not in self.subscription_state:
                self.logger.warning(f"Not subscribed to {contract_id}")
                return
                
            del self.subscription_state[contract_id]

        # Unsubscribe if connected
        if self.is_connected:
            await self._unsubscribe_from_contract(contract_id)
            
        self.logger.info(f"Unsubscribed from {contract_id}")

    async def _subscribe_to_contract(
        self,
        contract_id: str,
        quotes: bool = True,
        trades: bool = True,
        depth: bool = False,
    ):
        """Internal method to subscribe to contract data."""
        try:
            if quotes:
                await self.send_message("SubscribeContractQuotes", [contract_id])
                self.logger.debug(f"Subscribed to quotes for {contract_id}")

            if trades:
                await self.send_message("SubscribeContractTrades", [contract_id])
                self.logger.debug(f"Subscribed to trades for {contract_id}")

            if depth:
                await self.send_message("SubscribeContractMarketDepth", [contract_id])
                self.logger.debug(f"Subscribed to market depth for {contract_id}")

            self.logger.info(
                f"Subscribed to market data for {contract_id} (quotes={quotes}, trades={trades}, depth={depth})"
            )

        except Exception as e:
            self.logger.error(f"Failed to subscribe to {contract_id}: {e}")
            raise

    async def _unsubscribe_from_contract(self, contract_id: str):
        """Internal method to unsubscribe from contract data."""
        try:
            await self.send_message("UnsubscribeContractQuotes", [contract_id])
            await self.send_message("UnsubscribeContractTrades", [contract_id])
            await self.send_message("UnsubscribeContractMarketDepth", [contract_id])
            self.logger.info(f"Unsubscribed from market data for {contract_id}")

        except Exception as e:
            self.logger.error(f"Failed to unsubscribe from {contract_id}: {e}")

    async def _handle_quote_update(self, message):
        """Handle market quote update events."""
        contract_id = "unknown"
        try:
            # Input validation
            if not message:
                self.logger.warning("Received empty quote message")
                return

            self._update_message_stats()

            # Message format: ['CONTRACT_ID', {data}]
            if isinstance(message, list) and len(message) >= 2:
                contract_id = str(message[0]) if message[0] else "unknown"
                data = message[1]
                if not isinstance(data, dict):
                    self.logger.warning(
                        f"Invalid quote data format for {contract_id}: expected dict, got {type(data)}"
                    )
                    return
            else:
                # Fallback for unexpected format
                data = message[0] if isinstance(message, list) and message else message
                if not isinstance(data, dict):
                    self.logger.warning(
                        f"Invalid quote message format: expected dict, got {type(data)}"
                    )
                    return
                contract_id = (
                    str(data.get("symbol", "") or data.get("symbolId", "")) or "unknown"
                )

            # Validate required fields
            if not contract_id or contract_id == "unknown":
                self.logger.warning(f"Quote update missing contract ID: {data}")
                return

            # Safe numeric conversion with validation
            def safe_float(value, default=0.0):
                try:
                    return float(value) if value is not None else default
                except (ValueError, TypeError):
                    return default

            def safe_int(value, default=0):
                try:
                    return int(value) if value is not None else default
                except (ValueError, TypeError):
                    return default

            quote = MarketQuoteData(
                symbol=str(data.get("symbol", "")),
                symbol_name=str(data.get("symbolName", "")),
                last_price=safe_float(data.get("lastPrice")),
                best_bid=safe_float(data.get("bestBid")),
                best_ask=safe_float(data.get("bestAsk")),
                change=safe_float(data.get("change")),
                change_percent=safe_float(data.get("changePercent")),
                open=safe_float(data.get("open")),
                high=safe_float(data.get("high")),
                low=safe_float(data.get("low")),
                volume=safe_int(data.get("volume")),
                last_updated=str(data.get("lastUpdated", "")),
                timestamp=str(data.get("timestamp", "")),
            )

            self.logger.debug(
                f"Quote update {contract_id}: Last={quote.last_price}, Bid={quote.best_bid}, Ask={quote.best_ask}"
            )

            # Publish to event bus for real-time distribution
            try:
                quote_event = {
                    "contract_id": contract_id,
                    "symbol": quote.symbol,
                    "symbol_name": quote.symbol_name,
                    "last_price": quote.last_price,
                    "best_bid": quote.best_bid,
                    "best_ask": quote.best_ask,
                    "change": quote.change,
                    "change_percent": quote.change_percent,
                    "open": quote.open,
                    "high": quote.high,
                    "low": quote.low,
                    "volume": quote.volume,
                    "last_updated": quote.last_updated,
                    "timestamp": quote.timestamp,
                }
                await self.event_bus.publish(market_quote(contract_id), quote_event)
            except Exception as e:
                self.logger.error(f"Error publishing quote event for {contract_id}: {e}")

            # Call registered handlers with individual error isolation
            for handler in self.quote_handlers:
                try:
                    handler(contract_id, quote)
                except Exception as e:
                    self.logger.error(f"Error in quote handler: {e}")

        except Exception as e:
            self.logger.error(f"Error processing quote update for {contract_id}: {e}")
            # Don't re-raise - keep processing other messages

    async def _handle_trade_update(self, message):
        """Handle market trade update events."""
        contract_id = "unknown"
        try:
            # Input validation
            if not message:
                self.logger.warning("Received empty trade message")
                return

            self._update_message_stats()

            # Message format: [contract_id, trades[]]
            if isinstance(message, list) and len(message) >= 2:
                contract_id = str(message[0]) if message[0] else "unknown"
                trades = message[1]
                if not isinstance(trades, list):
                    self.logger.warning(
                        f"Invalid trade data format for {contract_id}: expected list, got {type(trades)}"
                    )
                    return
                
                # Process each trade in the list
                for trade_data in trades:
                    if not isinstance(trade_data, dict):
                        self.logger.warning(f"Invalid trade item format: {type(trade_data)}")
                        continue
                    
                    await self._process_single_trade(contract_id, trade_data)
            else:
                self.logger.warning(f"Invalid trade message format: expected [contract_id, trades[]], got {type(message)}")
                return

        except Exception as e:
            self.logger.error(f"Error processing trade update for {contract_id}: {e}")
            # Don't re-raise - keep processing other messages

    async def _process_single_trade(self, contract_id: str, data: dict):
        """Process a single trade data item."""
        try:
            # Validate required fields
            if not contract_id or contract_id == "unknown":
                self.logger.warning(f"Trade update missing contract ID: {data}")
                return

            # Safe numeric conversion with validation
            def safe_float(value, default=0.0):
                try:
                    return float(value) if value is not None else default
                except (ValueError, TypeError):
                    return default

            def safe_int(value, default=0):
                try:
                    return int(value) if value is not None else default
                except (ValueError, TypeError):
                    return default

            trade = MarketTradeData(
                symbol_id=str(data.get("symbolId", "")),
                price=safe_float(data.get("price")),
                timestamp=str(data.get("timestamp", "")),
                type=safe_int(data.get("type")),
                volume=safe_int(data.get("volume")),
            )

            # Additional validation for trade data
            if trade.price <= 0:
                self.logger.warning(
                    f"Invalid trade price for {contract_id}: {trade.price}"
                )
                return

            self.logger.debug(
                f"Trade update {contract_id}: Price={trade.price}, Volume={trade.volume}, Type={trade.type}"
            )

            # Publish to event bus for real-time distribution
            try:
                trade_event = {
                    "contract_id": contract_id,
                    "symbol_id": trade.symbol_id,
                    "price": trade.price,
                    "timestamp": trade.timestamp,
                    "type": trade.type,
                    "volume": trade.volume,
                }
                await self.event_bus.publish(market_trade(contract_id), trade_event)
            except Exception as e:
                self.logger.error(f"Error publishing trade event for {contract_id}: {e}")

            # Call registered handlers with individual error isolation
            for handler in self.trade_handlers:
                try:
                    handler(contract_id, trade)
                except Exception as e:
                    self.logger.error(f"Error in trade handler: {e}")

        except Exception as e:
            self.logger.error(f"Error processing single trade for {contract_id}: {e}")

    async def _handle_depth_update(self, message):
        """Handle market depth update events."""
        contract_id = "unknown"
        try:
            # Input validation
            if not message:
                self.logger.warning("Received empty depth message")
                return

            self._update_message_stats()

            # Message format: ['CONTRACT_ID', {data}]
            if isinstance(message, list) and len(message) >= 2:
                contract_id = str(message[0]) if message[0] else "unknown"
                data = message[1]
                if not isinstance(data, dict):
                    self.logger.warning(
                        f"Invalid depth data format for {contract_id}: expected dict, got {type(data)}"
                    )
                    return
            else:
                # Fallback for unexpected format
                data = message[0] if isinstance(message, list) and message else message
                if not isinstance(data, dict):
                    self.logger.warning(
                        f"Invalid depth message format: expected dict, got {type(data)}"
                    )
                    return
                contract_id = (
                    str(data.get("symbolId", "") or data.get("symbol", "")) or "unknown"
                )

            # Validate required fields
            if not contract_id or contract_id == "unknown":
                self.logger.warning(f"Depth update missing contract ID: {data}")
                return

            # Safe numeric conversion with validation
            def safe_float(value, default=0.0):
                try:
                    return float(value) if value is not None else default
                except (ValueError, TypeError):
                    return default

            def safe_int(value, default=0):
                try:
                    return int(value) if value is not None else default
                except (ValueError, TypeError):
                    return default

            depth = MarketDepthData(
                symbol_id=str(contract_id),
                timestamp=str(data.get("timestamp", "")),
                type=safe_int(data.get("type")),
                price=safe_float(data.get("price")),
                volume=safe_int(data.get("volume")),
                current_volume=safe_int(data.get("currentVolume")),
            )

            self.logger.debug(
                f"Depth update {contract_id}: Type={depth.type}, Price={depth.price}, Volume={depth.volume}"
            )

            # Publish to event bus for real-time distribution
            try:
                depth_event = {
                    "contract_id": contract_id,
                    "symbol_id": depth.symbol_id,
                    "timestamp": depth.timestamp,
                    "type": depth.type,
                    "price": depth.price,
                    "volume": depth.volume,
                    "current_volume": depth.current_volume,
                }
                await self.event_bus.publish(market_depth(contract_id), depth_event)
            except Exception as e:
                self.logger.error(f"Error publishing depth event for {contract_id}: {e}")

            # Call registered handlers with individual error isolation
            for handler in self.depth_handlers:
                try:
                    handler(contract_id, depth)
                except Exception as e:
                    self.logger.error(f"Error in depth handler: {e}")

        except Exception as e:
            self.logger.error(f"Error processing depth update for {contract_id}: {e}")
            # Don't re-raise - keep processing other messages

    def add_quote_handler(self, handler: Callable[[str, MarketQuoteData], None]):
        """Add market quote handler."""
        self.quote_handlers.append(handler)

    def add_trade_handler(self, handler: Callable[[str, MarketTradeData], None]):
        """Add market trade handler."""
        self.trade_handlers.append(handler)

    def add_depth_handler(self, handler: Callable[[str, MarketDepthData], None]):
        """Add market depth handler."""
        self.depth_handlers.append(handler)

    def remove_quote_handler(self, handler: Callable[[str, MarketQuoteData], None]):
        """Remove market quote handler."""
        if handler in self.quote_handlers:
            self.quote_handlers.remove(handler)

    def remove_trade_handler(self, handler: Callable[[str, MarketTradeData], None]):
        """Remove market trade handler."""
        if handler in self.trade_handlers:
            self.trade_handlers.remove(handler)

    def remove_depth_handler(self, handler: Callable[[str, MarketDepthData], None]):
        """Remove market depth handler."""
        if handler in self.depth_handlers:
            self.depth_handlers.remove(handler)

    async def get_subscribed_contracts(self) -> List[str]:
        """Get list of currently subscribed contracts."""
        async with self._state_lock:
            return list(self.subscription_state.keys())

    async def get_subscription_state(self) -> Dict[str, Dict[str, bool]]:
        """Get detailed subscription state for monitoring."""
        async with self._state_lock:
            return dict(self.subscription_state)

    async def clear_all_subscriptions(self):
        """Clear all contract subscriptions."""
        async with self._state_lock:
            self.subscription_state.clear()
        self._contract_cache.clear()
        self.logger.info("Cleared all contract subscriptions")

    def get_validation_stats(self) -> Dict[str, int]:
        """Get contract validation statistics."""
        return dict(self._validation_stats)
