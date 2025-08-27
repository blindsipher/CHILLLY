import asyncio
import logging
from typing import List

from topstepx_backend.config.settings import get_config
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.networking.rate_limiter import RateLimiter
from topstepx_backend.services.contract_service import ContractService, Contract
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.strategy.registry import StrategyRegistry
from topstepx_backend.strategy.runner import StrategyRunner

async def discover_contracts(contract_service: ContractService) -> List[Contract]:
    """Fetch available contracts from ProjectX API and print a sample."""
    try:
        contracts = await contract_service.discover_contracts(force_refresh=True)
        logging.info("Discovered %d contracts", len(contracts))
        for contract in contracts[:5]:
            status = "active" if contract.is_active else "inactive"
            print(f"- {contract.id} ({contract.name}) [{status}]")
        return contracts
    except Exception as exc:
        logging.error("Failed to discover contracts: %s", exc)
        return []

async def main():
    logging.basicConfig(level=logging.INFO)
    config = get_config()

    rate_limiter = RateLimiter()
    auth = AuthManager(config)
    await auth.start()

    contract_service = ContractService(config, auth, rate_limiter)
    await contract_service.start()
    await discover_contracts(contract_service)

    event_bus = EventBus()
    await event_bus.start()

    registry = StrategyRegistry()
    runner = StrategyRunner(event_bus, registry)

    try:
        await runner.start()
        # Allow the runner to operate briefly
        await asyncio.sleep(2)
    finally:
        await runner.stop()
        await event_bus.stop()
        await contract_service.stop()
        await auth.stop()

if __name__ == "__main__":
    asyncio.run(main())
