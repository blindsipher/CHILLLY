"""Subscription manager for auto-replay of SignalR subscriptions on reconnect."""

import logging
import asyncio
from typing import Set, Dict, Any, List, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from topstepx_backend.networking.user_hub_agent import UserHubAgent
    from topstepx_backend.networking.market_hub_agent import MarketHubAgent
    from topstepx_backend.networking.rate_limiter import RateLimiter


@dataclass
class SubscriptionState:
    """State tracking for subscriptions."""

    accounts: Set[int] = field(default_factory=set)
    contracts: Dict[str, Dict[str, bool]] = field(
        default_factory=dict
    )  # contract_id -> {quotes, trades, depth}


class SubscriptionManager:
    """
    Manager for tracking and replaying SignalR subscriptions on reconnection.

    Maintains state of all subscriptions and automatically re-establishes them
    when SignalR connections are restored after network interruptions.
    """

    def __init__(self, rate_limiter: Optional["RateLimiter"] = None):
        """Initialize subscription manager.
        
        Args:
            rate_limiter: Optional rate limiter for controlling subscription replay bursts
        """
        self.logger = logging.getLogger(__name__)
        self.state = SubscriptionState()
        self.rate_limiter = rate_limiter

    def track_account(self, account_id: int) -> None:
        """
        Track account subscription for replay.

        Args:
            account_id: Account ID to track
        """
        self.state.accounts.add(account_id)
        self.logger.debug(f"Tracking account subscription: {account_id}")

    def track_contract(
        self,
        contract_id: str,
        quotes: bool = True,
        trades: bool = True,
        depth: bool = False,
    ) -> None:
        """
        Track contract subscription for replay.

        Args:
            contract_id: Contract ID to track
            quotes: Whether to subscribe to quotes
            trades: Whether to subscribe to trades
            depth: Whether to subscribe to market depth
        """
        if contract_id not in self.state.contracts:
            self.state.contracts[contract_id] = {}

        self.state.contracts[contract_id].update(
            {"quotes": quotes, "trades": trades, "depth": depth}
        )

        self.logger.debug(
            f"Tracking contract subscription: {contract_id} "
            f"(quotes={quotes}, trades={trades}, depth={depth})"
        )

    def untrack_account(self, account_id: int) -> None:
        """
        Stop tracking account subscription.

        Args:
            account_id: Account ID to stop tracking
        """
        self.state.accounts.discard(account_id)
        self.logger.debug(f"Stopped tracking account: {account_id}")

    def untrack_contract(self, contract_id: str) -> None:
        """
        Stop tracking contract subscription.

        Args:
            contract_id: Contract ID to stop tracking
        """
        self.state.contracts.pop(contract_id, None)
        self.logger.debug(f"Stopped tracking contract: {contract_id}")

    async def replay(
        self,
        user_hub: Optional["UserHubAgent"] = None,
        market_hub: Optional["MarketHubAgent"] = None,
    ) -> None:
        """
        Replay all tracked subscriptions.

        Args:
            user_hub: User hub agent for account/order subscriptions
            market_hub: Market hub agent for contract subscriptions
        """
        self.logger.info("Replaying subscriptions after reconnection")

        try:
            # Replay user hub subscriptions
            if user_hub and user_hub.is_connected:
                await self._replay_user_subscriptions(user_hub)

            # Replay market hub subscriptions
            if market_hub and market_hub.is_connected:
                await self._replay_market_subscriptions(market_hub)

            self.logger.info("Subscription replay completed successfully")

        except Exception as e:
            self.logger.error(f"Error during subscription replay: {e}")

    async def _replay_user_subscriptions(self, user_hub: "UserHubAgent") -> None:
        """Replay user hub subscriptions."""
        try:
            # Subscribe to accounts
            if self.state.accounts:
                if self.rate_limiter:
                    await self.rate_limiter.acquire("signalr_subscribe")
                await user_hub.send_message("SubscribeAccounts", [])
                self.logger.info("Replayed SubscribeAccounts")

            # Subscribe to orders, positions, trades for each account
            for account_id in self.state.accounts:
                # Apply rate limiting between account subscriptions to avoid bursts
                if self.rate_limiter:
                    await self.rate_limiter.acquire("signalr_subscribe")
                    
                await user_hub.send_message("SubscribeOrders", [account_id])
                await user_hub.send_message("SubscribePositions", [account_id])
                await user_hub.send_message("SubscribeTrades", [account_id])

                self.logger.info(
                    f"Replayed user subscriptions for account {account_id}"
                )
                
                # Small delay between accounts to prevent overwhelming the server
                if len(self.state.accounts) > 1:
                    await asyncio.sleep(0.1)

        except Exception as e:
            self.logger.error(f"Failed to replay user subscriptions: {e}")

    async def _replay_market_subscriptions(self, market_hub: "MarketHubAgent") -> None:
        """Replay market hub subscriptions."""
        try:
            for i, (contract_id, subs) in enumerate(self.state.contracts.items()):
                # Apply rate limiting for each contract to avoid subscription bursts
                if self.rate_limiter:
                    await self.rate_limiter.acquire("signalr_subscribe")
                    
                # Subscribe to quotes
                if subs.get("quotes", False):
                    await market_hub.send_message(
                        "SubscribeContractQuotes", [contract_id]
                    )
                    self.logger.debug(f"Replayed quotes subscription for {contract_id}")

                # Subscribe to trades
                if subs.get("trades", False):
                    await market_hub.send_message(
                        "SubscribeContractTrades", [contract_id]
                    )
                    self.logger.debug(f"Replayed trades subscription for {contract_id}")

                # Subscribe to market depth
                if subs.get("depth", False):
                    await market_hub.send_message(
                        "SubscribeContractMarketDepth", [contract_id]
                    )
                    self.logger.debug(f"Replayed depth subscription for {contract_id}")
                
                # Small delay between contracts to prevent overwhelming the server
                if len(self.state.contracts) > 1 and i < len(self.state.contracts) - 1:
                    await asyncio.sleep(0.05)  # 50ms between contracts

            if self.state.contracts:
                self.logger.info(
                    f"Replayed market subscriptions for {len(self.state.contracts)} contracts"
                )

        except Exception as e:
            self.logger.error(f"Failed to replay market subscriptions: {e}")

    def get_tracked_accounts(self) -> List[int]:
        """Get list of tracked accounts."""
        return list(self.state.accounts)

    def get_tracked_contracts(self) -> List[str]:
        """Get list of tracked contracts."""
        return list(self.state.contracts.keys())

    def get_subscription_state(self) -> Dict[str, Any]:
        """Get current subscription state for monitoring."""
        return {
            "accounts": list(self.state.accounts),
            "contracts": dict(self.state.contracts),
            "total_accounts": len(self.state.accounts),
            "total_contracts": len(self.state.contracts),
        }

    def clear_all(self) -> None:
        """Clear all tracked subscriptions."""
        self.state.accounts.clear()
        self.state.contracts.clear()
        self.logger.info("Cleared all tracked subscriptions")
