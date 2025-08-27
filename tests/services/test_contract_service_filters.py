import pytest

from topstepx_backend.services.contract_service import ContractService, Contract
from topstepx_backend.config.settings import TopstepConfig
from topstepx_backend.networking.rate_limiter import RateLimiter


class DummyAuth:
    def get_token(self) -> str:
        return "token"


def create_service() -> ContractService:
    config = TopstepConfig(
        username="u",
        api_key="k" * 16,
        account_id=1,
        account_name="a",
        projectx_base_url="http://example.com",
        projectx_user_hub_url="http://example.com/user",
        projectx_market_hub_url="http://example.com/market",
        database_path=":memory:",
        log_level="INFO",
        environment="test",
    )
    return ContractService(config, DummyAuth(), RateLimiter())


@pytest.mark.asyncio
async def test_filter_contracts_by_symbol_and_sector():
    service = create_service()
    c1 = Contract(
        id="1",
        name="E-mini S&P",
        symbol="ESU24",
        contract_type="F",
        market="US",
        is_active=True,
        tick_size=0.25,
        point_value=12.5,
        currency="USD",
        sector="EP",
    )
    c2 = Contract(
        id="2",
        name="Euro FX",
        symbol="6EU24",
        contract_type="F",
        market="US",
        is_active=True,
        tick_size=0.0001,
        point_value=125000.0,
        currency="USD",
        sector="EU",
    )
    service._contracts = {c1.id: c1, c2.id: c2}

    result = await service.filter_contracts(symbol_prefix="ES")
    assert [c.id for c in result] == ["1"]

    result = await service.filter_contracts(sector="EU")
    assert [c.id for c in result] == ["2"]
