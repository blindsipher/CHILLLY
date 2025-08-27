"""Interactive demo script for contract selection and simple trading.

The script performs the following actions:

1. Authenticate with the TopstepX API
2. Discover available contracts and allow the user to choose one
3. Subscribe to market data for the selected contract and print live prices
4. Optionally place a single market order for one contract and close it

This runner is intended for quick manual testing of the backend components
without launching the full orchestrator.
"""

import asyncio
import logging
from typing import List, Optional

from topstepx_backend.config.settings import get_config
from topstepx_backend.auth.auth_manager import AuthManager
from topstepx_backend.networking.rate_limiter import RateLimiter
from topstepx_backend.services.contract_service import ContractService, Contract
from topstepx_backend.core.event_bus import EventBus
from topstepx_backend.data.polling_bar_service import PollingBarService
from topstepx_backend.core.topics import (
    market_bar,
    order_request_submit,
    order_ack,
    order_fill_update,
)
from topstepx_backend.services.order_service import OrderService
from topstepx_backend.data.types import OrderIntent, OrderType, OrderSide

async def discover_contracts(contract_service: ContractService) -> List[Contract]:
    """Fetch available contracts and print them with indexes."""
    try:
        contracts = await contract_service.discover_contracts(force_refresh=True)
        if not contracts:
            print("No contracts discovered.")
            return []

        print("Available contracts:")
        for idx, contract in enumerate(contracts):
            status = "active" if contract.is_active else "inactive"
            print(f"[{idx}] {contract.id} ({contract.name}) [{status}]")
        return contracts
    except Exception as exc:
        logging.error("Failed to discover contracts: %s", exc)
        return []


async def choose_contract(contracts: List[Contract]) -> Optional[Contract]:
    """Prompt the user to select a contract from the list."""
    if not contracts:
        return None

    while True:
        choice_str = await asyncio.to_thread(
            input, "Enter contract number to monitor (or blank to exit): "
        )
        if not choice_str.strip():
            return None
        try:
            idx = int(choice_str)
            return contracts[idx]
        except (ValueError, IndexError):
            print("Invalid selection. Try again.")


async def print_live_prices(event_bus: EventBus, contract_id: str) -> None:
    """Subscribe to partial bar updates and print the live price."""
    topic = f"{market_bar(contract_id, '1m')}_partial"
    subscription = await event_bus.subscribe(topic, critical=False)
    try:
        while True:
            _, payload = await subscription.recv()
            price = payload.get("close")
            timestamp = payload.get("timestamp")
            print(f"[{timestamp}] {contract_id} price: {price}")
    except asyncio.CancelledError:
        pass


async def trade_flow(event_bus: EventBus, order_service: OrderService, contract_id: str, account_id: int) -> None:
    """Handle optional order placement and closing."""
    place = await asyncio.to_thread(input, "Place market buy order for 1? (y/N): ")
    if place.lower() != "y":
        return

    ack_sub = await event_bus.subscribe(order_ack(), critical=False)
    fill_sub = await event_bus.subscribe(order_fill_update(), critical=False)

    buy_intent = OrderIntent(
        strategy_id="demo",
        account_id=account_id,
        contract_id=contract_id,
        type=OrderType.MARKET,
        side=OrderSide.BUY,
        size=1,
        custom_tag="demo_buy",
    )
    await event_bus.publish(order_request_submit(), buy_intent.to_dict())
    print("Buy order submitted")

    # Wait for acknowledgement and fill (best effort)
    try:
        _, ack_payload = await asyncio.wait_for(ack_sub.recv(), timeout=5)
        print("Ack:", ack_payload)
    except asyncio.TimeoutError:
        print("No acknowledgment received")
    try:
        _, fill_payload = await asyncio.wait_for(fill_sub.recv(), timeout=5)
        print("Fill:", fill_payload)
    except asyncio.TimeoutError:
        print("No fill received")

    await asyncio.to_thread(input, "Press Enter to send closing sell order...")

    sell_intent = OrderIntent(
        strategy_id="demo",
        account_id=account_id,
        contract_id=contract_id,
        type=OrderType.MARKET,
        side=OrderSide.SELL,
        size=1,
        custom_tag="demo_sell",
    )
    await event_bus.publish(order_request_submit(), sell_intent.to_dict())
    print("Sell order submitted")

    try:
        _, ack_payload = await asyncio.wait_for(ack_sub.recv(), timeout=5)
        print("Ack:", ack_payload)
    except asyncio.TimeoutError:
        print("No acknowledgment received for sell order")
    try:
        _, fill_payload = await asyncio.wait_for(fill_sub.recv(), timeout=5)
        print("Fill:", fill_payload)
    except asyncio.TimeoutError:
        print("No fill received for sell order")

async def main():
    """Entry point for interactive demo."""
    logging.basicConfig(level=logging.INFO)
    config = get_config()

    rate_limiter = RateLimiter()
    auth = AuthManager(config)
    await auth.start()

    contract_service = ContractService(config, auth, rate_limiter)
    await contract_service.start()
    contracts = await discover_contracts(contract_service)
    selected = await choose_contract(contracts)
    if not selected:
        print("No contract selected. Exiting.")
        await contract_service.stop()
        await auth.stop()
        return

    event_bus = EventBus()
    await event_bus.start()

    polling = PollingBarService(
        config,
        auth,
        rate_limiter,
        event_bus=event_bus,
        initial_subscriptions=[selected.id],
    )
    await polling.start()

    order_service = OrderService(event_bus, auth, config, rate_limiter)
    await order_service.start()

    price_task = asyncio.create_task(print_live_prices(event_bus, selected.id))

    try:
        await trade_flow(event_bus, order_service, selected.id, config.account_id)
        print("Press Ctrl+C to exit")
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")
    finally:
        price_task.cancel()
        await polling.stop()
        await order_service.stop()
        await event_bus.stop()
        await contract_service.stop()
        await auth.stop()

if __name__ == "__main__":
    asyncio.run(main())
