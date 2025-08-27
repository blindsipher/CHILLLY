"""Contract discovery and management service for TopstepX."""

import asyncio
import logging
import aiohttp
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Set
from dataclasses import dataclass
import json

from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.networking.rate_limiter import RateLimiter
from topstepx_backend.networking.api_helpers import auth_headers, utc_now


@dataclass
class Contract:
    """Represents a trading contract from TopstepX."""

    id: str
    name: str
    symbol: str
    contract_type: str
    market: str
    is_active: bool
    tick_size: float
    point_value: float
    currency: str
    expiry_date: Optional[datetime] = None
    description: Optional[str] = None
    sector: Optional[str] = None

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "Contract":
        """Create Contract from TopstepX Contract API response."""
        symbol_id = data.get("symbolId", "")
        parts = symbol_id.split(".")

        # Parse symbolId structure (e.g., F.US.EP -> Futures, US market, E-mini S&P)
        contract_type = parts[0] if len(parts) > 0 else symbol_id
        market = parts[1] if len(parts) > 1 else symbol_id
        # Base symbol is most useful for 'sector' classification
        sector = parts[2] if len(parts) > 2 else symbol_id

        return cls(
            id=str(data.get("id", "")),
            name=data.get("name", ""),
            symbol=data.get(
                "name", ""
            ),  # name is the specific contract symbol like 'ESU24'
            contract_type=contract_type,  # e.g., 'F' for Futures
            market=market,  # e.g., 'US'
            is_active=data.get("activeContract", False),  # activeContract field
            tick_size=float(data.get("tickSize", 0.0)),
            point_value=float(data.get("tickValue", 0.0)),  # tickValue in docs
            currency="USD",  # TopStep typically uses USD
            expiry_date=None,  # Not provided in Contract model
            description=data.get("description"),
            sector=sector,  # e.g., 'EP' for E-mini S&P
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert contract to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "symbol": self.symbol,
            "contract_type": self.contract_type,
            "market": self.market,
            "is_active": self.is_active,
            "tick_size": self.tick_size,
            "point_value": self.point_value,
            "currency": self.currency,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "description": self.description,
            "sector": self.sector,
        }


class ContractService:
    """Service for discovering and managing trading contracts."""

    def __init__(
        self,
        config: TopstepConfig,
        auth_manager: AuthManager,
        rate_limiter: RateLimiter,
    ):
        self.config = config
        self.auth_manager = auth_manager
        self.rate_limiter = rate_limiter
        self.logger = logging.getLogger(__name__)

        # HTTP session for reuse
        self._session: Optional[aiohttp.ClientSession] = None

        # Contract cache with TTL
        self._contracts: Dict[str, Contract] = {}
        self._cache_timestamp: Optional[datetime] = None
        self._cache_ttl = timedelta(hours=1)  # Cache for 1 hour

        # Subscription management
        self._subscribed_contracts: Set[str] = set()

        # Statistics
        self._stats = {
            "contracts_discovered": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "api_requests": 0,
            "last_refresh": None,
            "subscription_count": 0,
        }
        self._stats_lock = asyncio.Lock()

    async def start(self):
        """Initialize the contract service."""
        if not self._session:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30),
            )
            self.logger.debug("ContractService session initialized")

    async def stop(self):
        """Cleanup the contract service."""
        if self._session:
            await self._session.close()
            self._session = None
            self.logger.debug("ContractService session closed")

    async def _ensure_session(self):
        """Ensure session is available."""
        if not self._session:
            await self.start()

    async def discover_contracts(
        self, force_refresh: bool = False, live: bool = False
    ) -> List[Contract]:
        """Discover all available contracts from TopstepX API.

        Args:
            force_refresh: Force refresh even if cache is valid
            live: True for live contracts, False for demo contracts
        """
        # Check cache first
        if not force_refresh and self._is_cache_valid():
            async with self._stats_lock:
                self._stats["cache_hits"] += 1
            self.logger.debug(f"Returning {len(self._contracts)} contracts from cache")
            return list(self._contracts.values())

        async with self._stats_lock:
            self._stats["cache_misses"] += 1

        try:
            # Ensure session is available
            await self._ensure_session()

            # Wait for rate limit
            await self.rate_limiter.acquire("/api/Contract/available")

            # Get authentication headers
            headers = auth_headers(self.auth_manager.get_token())

            # Make API request with POST as per ProjectX Gateway API docs
            # Use the ProjectX Gateway API endpoint
            url = f"{self.config.projectx_base_url}/api/Contract/available"
            payload = {"live": live}

            self.logger.info(
                f"Requesting contracts from: {url} with payload: {payload}"
            )
            async with self._session.post(
                url, headers=headers, json=payload
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    contracts = self._parse_contracts_response(data)

                    # Update cache
                    self._contracts = {contract.id: contract for contract in contracts}
                    self._cache_timestamp = utc_now()

                    async with self._stats_lock:
                        self._stats["contracts_discovered"] = len(contracts)
                        self._stats["api_requests"] += 1
                        self._stats["last_refresh"] = utc_now()

                    self.logger.info(
                        f"Discovered {len(contracts)} contracts from TopstepX"
                    )
                    return contracts

                else:
                    error_text = await response.text()
                    raise Exception(
                        f"API request failed with status {response.status}: {error_text}"
                    )

        except Exception as e:
            self.logger.error(f"Error discovering contracts: {e}")
            raise

    def _parse_contracts_response(self, data: Any) -> List[Contract]:
        """Parse the contracts API response into Contract objects."""
        contracts = []

        # Handle different response formats
        contract_list = data
        if isinstance(data, dict):
            if "contracts" in data:
                contract_list = data["contracts"]
            elif "data" in data:
                contract_list = data["data"]
            elif "result" in data:
                contract_list = data["result"]

        if not isinstance(contract_list, list):
            self.logger.warning(
                f"Unexpected contracts response format: {type(contract_list)}"
            )
            return contracts

        for contract_data in contract_list:
            try:
                contract = Contract.from_api_response(contract_data)
                contracts.append(contract)
            except Exception as e:
                self.logger.warning(
                    f"Failed to parse contract data: {e}, data: {contract_data}"
                )

        return contracts

    def _is_cache_valid(self) -> bool:
        """Check if the contract cache is still valid."""
        if not self._cache_timestamp or not self._contracts:
            return False

        return utc_now() - self._cache_timestamp < self._cache_ttl

    async def get_contract_by_id(self, contract_id: str) -> Optional[Contract]:
        """Get a specific contract by ID."""
        # Ensure we have contracts loaded
        if not self._contracts:
            await self.discover_contracts()

        return self._contracts.get(contract_id)

    async def get_contract_by_symbol(self, symbol: str) -> Optional[Contract]:
        """Get a contract by its trading symbol."""
        # Ensure we have contracts loaded
        if not self._contracts:
            await self.discover_contracts()

        # Search by symbol (case-insensitive)
        symbol_lower = symbol.lower()
        for contract in self._contracts.values():
            if contract.symbol.lower() == symbol_lower:
                return contract

        return None

    async def filter_contracts(
        self,
        symbol_prefix: Optional[str] = None,
        sector: Optional[str] = None,
        active_only: bool = False,
    ) -> List[Contract]:
        """Filter cached contracts by symbol prefix and/or sector.

        Args:
            symbol_prefix: Return contracts whose symbol starts with this prefix.
            sector: Limit results to contracts belonging to this sector.
            active_only: Only include active contracts when True.
        """
        if not self._contracts:
            await self.discover_contracts()

        results: List[Contract] = list(self._contracts.values())

        if symbol_prefix:
            prefix = symbol_prefix.lower()
            results = [c for c in results if c.symbol.lower().startswith(prefix)]

        if sector:
            sector_lower = sector.lower()
            results = [
                c
                for c in results
                if c.sector and c.sector.lower() == sector_lower
            ]

        if active_only:
            results = [c for c in results if c.is_active]

        return results

    async def search_contracts_api(
        self, query: str, live: bool = False
    ) -> List[Contract]:
        """Search contracts using ProjectX Gateway API endpoint for faster lookups."""
        await self._ensure_session()

        try:
            # Use rate limiter for API call
            await self.rate_limiter.acquire("/api/Contract/search")

            # Prepare API request
            headers = auth_headers(self.auth_manager.get_token())
            url = f"{self.config.projectx_base_url}/api/Contract/search"
            payload = {"live": live, "searchText": query}

            self.logger.debug(f"Searching contracts via API: {query}")

            async with self._session.post(
                url, headers=headers, json=payload
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    # REUSE the robust parsing logic
                    contracts = self._parse_contracts_response(data)

                    async with self._stats_lock:
                        self._stats["api_requests"] += 1

                    self.logger.info(
                        f"Found {len(contracts)} contracts for query: {query}"
                    )
                    return contracts

                else:
                    error_text = await response.text()
                    self.logger.error(
                        f"Contract search API error {response.status}: {error_text}"
                    )

                    # Fall back to local search
                    return await self.search_contracts_local(query, active_only=True)

        except Exception as e:
            self.logger.error(f"Contract search API exception: {e}")
            # Fall back to local search
            return await self.search_contracts_local(query, active_only=True)

    async def search_contracts_local(
        self, query: str, active_only: bool = True
    ) -> List[Contract]:
        """Search contracts locally using cached data (fallback method)."""
        # Ensure we have contracts loaded
        if not self._contracts:
            await self.discover_contracts()

        query_lower = query.lower()
        matching_contracts = []

        for contract in self._contracts.values():
            # Skip inactive contracts if requested
            if active_only and not contract.is_active:
                continue

            # Search in name, symbol, and description
            searchable_text = f"{contract.name} {contract.symbol} {contract.description or ''}".lower()
            if query_lower in searchable_text:
                matching_contracts.append(contract)

        # Sort by relevance (exact symbol match first, then name match, then description match)
        query_lower = query.lower()

        def sort_key(contract):
            if contract.symbol.lower() == query_lower:
                return (0, contract.symbol)
            elif contract.name.lower() == query_lower:
                return (1, contract.name)
            else:
                return (2, contract.name)

        matching_contracts.sort(key=sort_key)
        return matching_contracts

    async def search_contracts(
        self, query: str, active_only: bool = True, use_api: bool = True
    ) -> List[Contract]:
        """Search contracts by name, symbol, or description.

        Args:
            query: Search text
            active_only: Only return active contracts (for local search)
            use_api: Try API endpoint first, fall back to local search
        """
        if use_api:
            return await self.search_contracts_api(query, live=False)
        else:
            return await self.search_contracts_local(query, active_only)

    async def get_active_contracts(self) -> List[Contract]:
        """Get all active contracts."""
        # Ensure we have contracts loaded
        if not self._contracts:
            await self.discover_contracts()

        return [contract for contract in self._contracts.values() if contract.is_active]

    async def get_contracts_by_market(self, market: str) -> List[Contract]:
        """Get all contracts for a specific market."""
        # Ensure we have contracts loaded
        if not self._contracts:
            await self.discover_contracts()

        market_lower = market.lower()
        return [
            contract
            for contract in self._contracts.values()
            if contract.market.lower() == market_lower
        ]

    async def get_contracts_by_type(self, contract_type: str) -> List[Contract]:
        """Get all contracts of a specific type."""
        # Ensure we have contracts loaded
        if not self._contracts:
            await self.discover_contracts()

        type_lower = contract_type.lower()
        return [
            contract
            for contract in self._contracts.values()
            if contract.contract_type.lower() == type_lower
        ]

    def get_available_markets(self) -> List[str]:
        """Get list of all available markets."""
        markets = set()
        for contract in self._contracts.values():
            if contract.market:
                markets.add(contract.market)
        return sorted(list(markets))

    def get_available_contract_types(self) -> List[str]:
        """Get list of all available contract types."""
        types = set()
        for contract in self._contracts.values():
            if contract.contract_type:
                types.add(contract.contract_type)
        return sorted(list(types))

    def subscribe_to_contract(self, contract_id: str):
        """Add a contract to the subscription list."""
        self._subscribed_contracts.add(contract_id)
        self._stats["subscription_count"] = len(self._subscribed_contracts)
        self.logger.info(f"Subscribed to contract: {contract_id}")

    def unsubscribe_from_contract(self, contract_id: str):
        """Remove a contract from the subscription list."""
        self._subscribed_contracts.discard(contract_id)
        self._stats["subscription_count"] = len(self._subscribed_contracts)
        self.logger.info(f"Unsubscribed from contract: {contract_id}")

    def get_subscribed_contracts(self) -> List[str]:
        """Get list of currently subscribed contract IDs."""
        return list(self._subscribed_contracts)

    async def export_contracts_to_file(self, filepath: str, format: str = "json"):
        """Export contracts to a file."""
        # Ensure we have contracts loaded
        if not self._contracts:
            await self.discover_contracts()

        contracts_data = [contract.to_dict() for contract in self._contracts.values()]

        if format.lower() == "json":
            with open(filepath, "w") as f:
                json.dump(contracts_data, f, indent=2, default=str)
        else:
            raise ValueError(f"Unsupported export format: {format}")

        self.logger.info(f"Exported {len(contracts_data)} contracts to {filepath}")

    async def __aenter__(self):
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.stop()

    def get_stats(self) -> Dict[str, Any]:
        """Get service statistics."""
        stats = self._stats.copy()
        stats.update(
            {
                "total_contracts": len(self._contracts),
                "active_contracts": sum(
                    1 for c in self._contracts.values() if c.is_active
                ),
                "cache_valid": self._is_cache_valid(),
                "cache_age_minutes": (
                    (utc_now() - self._cache_timestamp).total_seconds() / 60
                    if self._cache_timestamp
                    else None
                ),
                "subscribed_contracts": len(self._subscribed_contracts),
            }
        )
        return stats

    async def refresh_cache(self):
        """Force refresh the contract cache."""
        await self.discover_contracts(force_refresh=True)
