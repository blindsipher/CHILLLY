"""Market subscription management service for dynamic contract control.

Provides runtime management of which contracts are being polled by PollingBarService.
Persists subscription state and integrates with EventBus for real-time updates.
Initial subscriptions are loaded from configuration.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Set, Dict, Any, Optional, TYPE_CHECKING
from dataclasses import dataclass
from datetime import datetime

from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.networking.api_helpers import utc_now
from topstepx_backend.utils.contract_validator import contract_validator

if TYPE_CHECKING:
    from topstepx_backend.services.contract_service import ContractService


@dataclass
class MarketSubscriptionState:
    """State of market subscriptions."""
    active_contracts: Set[str]
    last_updated: datetime
    update_count: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for persistence."""
        return {
            "active_contracts": list(self.active_contracts),
            "last_updated": self.last_updated.isoformat(),
            "update_count": self.update_count
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MarketSubscriptionState':
        """Create from dictionary."""
        return cls(
            active_contracts=set(data["active_contracts"]),
            last_updated=datetime.fromisoformat(data["last_updated"]),
            update_count=data.get("update_count", 0)
        )


class MarketSubscriptionService:
    """
    Manages market subscriptions for the polling service.
    
    Features:
    - Add/remove contract subscriptions at runtime
    - Persist subscription state to JSON
    - Publish subscription changes via EventBus
    - Integration with PollingBarService
    - Validation of contract identifiers
    """

    def __init__(
        self,
        config: TopstepConfig,
        event_bus: EventBus,
        contract_service: Optional['ContractService'] = None,
    ):
        self.config = config
        self.event_bus = event_bus
        self.contract_service = contract_service
        self.logger = logging.getLogger(self.__class__.__name__)
        
        # State management
        self._state: Optional[MarketSubscriptionState] = None
        self._state_lock = asyncio.Lock()
        self._running = False
        
        # Persistence - use same directory as database
        db_path = Path(config.database_path)
        data_dir = db_path.parent
        self._state_file = data_dir / "market_subscriptions.json"
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Stats
        self._stats = {
            "subscriptions_added": 0,
            "subscriptions_removed": 0,
            "invalid_requests": 0,
            "state_saves": 0,
            "last_operation": None
        }
    
    async def start(self):
        """Start the market subscription service."""
        self.logger.info("Starting MarketSubscriptionService...")
        
        # Load persisted state or create default
        await self._load_state()
        
        self._running = True
        self.logger.info(f"MarketSubscriptionService started with {len(self._state.active_contracts)} active contracts")
    
    async def stop(self):
        """Stop the market subscription service."""
        self.logger.info("Stopping MarketSubscriptionService...")
        self._running = False
        
        # Save final state
        await self._save_state()
        
        self.logger.info("MarketSubscriptionService stopped")
    
    async def _load_state(self):
        """Load subscription state from disk."""
        async with self._state_lock:
            if self._state_file.exists():
                try:
                    with open(self._state_file, 'r') as f:
                        data = json.load(f)
                    self._state = MarketSubscriptionState.from_dict(data)
                    self.logger.info(f"Loaded subscription state: {len(self._state.active_contracts)} contracts")
                except Exception as e:
                    self.logger.error(f"Failed to load subscription state: {e}")
                    self._state = self._create_default_state()
            else:
                self._state = self._create_default_state()
                await self._save_state()

    def _create_default_state(self) -> MarketSubscriptionState:
        """Create default subscription state from configuration."""
        return MarketSubscriptionState(
            active_contracts=set(self.config.default_contracts),
            last_updated=utc_now(),
        )
    
    async def _save_state(self):
        """Persist subscription state to disk."""
        async with self._state_lock:
            try:
                with open(self._state_file, 'w') as f:
                    json.dump(self._state.to_dict(), f, indent=2)
                self._stats["state_saves"] += 1
                self.logger.debug("Saved subscription state to disk")
            except Exception as e:
                self.logger.error(f"Failed to save subscription state: {e}")
    
    async def add_subscription(self, contract_identifier: str) -> Dict[str, Any]:
        """
        Add a contract subscription.

        Args:
            contract_identifier: Contract ID or symbol to subscribe to

        Returns:
            Dict with success status and message
        """
        validation = contract_validator.validate(contract_identifier)
        if not validation.is_valid:
            self._stats["invalid_requests"] += 1
            return {"success": False, "message": validation.error_message or "Invalid contract identifier"}

        contract_id = validation.normalized_id

        if not contract_id:
            if validation.requires_lookup and self.contract_service:
                success, normalized_id, error = await contract_validator.normalize_identifier(
                    contract_identifier, self.contract_service
                )
                if not success or not normalized_id:
                    self._stats["invalid_requests"] += 1
                    return {"success": False, "message": error or f"Failed to resolve symbol '{contract_identifier}'"}
                contract_id = normalized_id
            else:
                self._stats["invalid_requests"] += 1
                if validation.requires_lookup:
                    message = f"Symbol '{contract_identifier}' requires lookup but no contract service available"
                else:
                    message = validation.error_message or f"Unable to normalize contract identifier '{contract_identifier}'"
                return {"success": False, "message": message}

        async with self._state_lock:
            if contract_id in self._state.active_contracts:
                return {"success": False, "message": f"Contract {contract_id} already subscribed"}

            # Add subscription
            self._state.active_contracts.add(contract_id)
            self._state.last_updated = utc_now()
            self._state.update_count += 1

            # Update stats
            self._stats["subscriptions_added"] += 1
            self._stats["last_operation"] = f"Added {contract_id}"

        # Save state
        await self._save_state()

        # Publish event
        await self.event_bus.publish(
            "system.market_subscription.added",
            {"contract_id": contract_id, "timestamp": utc_now()}
        )

        self.logger.info(f"Added subscription for {contract_id}")
        return {
            "success": True,
            "message": f"Successfully subscribed to {contract_id}",
            "active_contracts": list(self._state.active_contracts)
        }
    
    async def remove_subscription(self, contract_id: str) -> Dict[str, Any]:
        """
        Remove a contract subscription.
        
        Returns:
            Dict with success status and message
        """
        async with self._state_lock:
            if contract_id not in self._state.active_contracts:
                return {
                    "success": False,
                    "message": f"Contract {contract_id} not currently subscribed"
                }
            
            # Remove subscription
            self._state.active_contracts.remove(contract_id)
            self._state.last_updated = utc_now()
            self._state.update_count += 1
            
            # Update stats
            self._stats["subscriptions_removed"] += 1
            self._stats["last_operation"] = f"Removed {contract_id}"
        
        # Save state
        await self._save_state()
        
        # Publish event
        await self.event_bus.publish(
            "system.market_subscription.removed",
            {"contract_id": contract_id, "timestamp": utc_now()}
        )
        
        self.logger.info(f"Removed subscription for {contract_id}")
        return {
            "success": True,
            "message": f"Successfully unsubscribed from {contract_id}",
            "active_contracts": list(self._state.active_contracts)
        }
    
    async def get_active_subscriptions(self) -> Set[str]:
        """Get current active contract subscriptions."""
        async with self._state_lock:
            return self._state.active_contracts.copy()
    
    async def get_subscription_state(self) -> Dict[str, Any]:
        """Get complete subscription state information."""
        async with self._state_lock:
            return {
                "active_contracts": list(self._state.active_contracts),
                "contract_count": len(self._state.active_contracts),
                "last_updated": self._state.last_updated.isoformat(),
                "update_count": self._state.update_count,
                "validation_info": "Accepts full contractIds (CON.F.US.EP.U25) and symbols (ES)"
            }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        return {
            "running": self._running,
            "subscriptions_added": self._stats["subscriptions_added"],
            "subscriptions_removed": self._stats["subscriptions_removed"],
            "invalid_requests": self._stats["invalid_requests"],
            "state_saves": self._stats["state_saves"],
            "last_operation": self._stats["last_operation"],
            "state_file": str(self._state_file)
        }
    
    async def validate_contracts(self, contracts: Set[str]) -> Dict[str, Any]:
        """
        Validate a set of contract identifiers.

        Returns:
            Dict with lists of valid and invalid contracts
        """
        from typing import List

        valid: List[str] = []
        invalid: List[str] = []

        for identifier in contracts:
            validation = contract_validator.validate(identifier)
            if not validation.is_valid:
                invalid.append(identifier)
                continue

            contract_id = validation.normalized_id

            if not contract_id and validation.requires_lookup and self.contract_service:
                success, normalized_id, _ = await contract_validator.normalize_identifier(
                    identifier, self.contract_service
                )
                if success and normalized_id:
                    contract_id = normalized_id

            if contract_id:
                valid.append(contract_id)
            else:
                invalid.append(identifier)

        return {
            "valid": valid,
            "invalid": invalid,
            "all_valid": len(invalid) == 0
        }