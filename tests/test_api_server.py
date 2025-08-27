import asyncio

import pytest
from fastapi.testclient import TestClient

from topstepx_backend.api.server import APIServer, StatusResponse, StrategyResponse
from topstepx_backend.core.event_bus import EventBus


class DummyAuth:
    def get_token(self) -> str:
        return "token"


class DummyStrategyRunner:
    def __init__(self) -> None:
        self.stats_called = False

    def get_strategy_stats(self):
        self.stats_called = True
        return {"running": False}


class DummyOrchestrator:
    def __init__(self) -> None:
        self.event_bus = EventBus()
        self.auth_manager = DummyAuth()
        self.status_called = False
        self.submit_payload = None
        self.add_payload = None
        self.remove_id = None
        self.strategy_runner = DummyStrategyRunner()
        # Tracking for new endpoints
        self.contracts_called = False
        self.market_data_contract = None
        self.bars_contract = None
        self.quotes_contract = None
        self.positions_called = False
        self.account_called = False
        self.trades_called = False
        self.orders_called = False
        self.order_lookup = None
        self.list_strat_called = False
        self.list_avail_called = False
        self.strategy_perf_id = None
        self.update_strategy_id = None
        self.update_strategy_cfg = None
        self.health_called = False
        self.logs_called = False
        self.config_called = False

    def get_system_status(self):
        self.status_called = True
        return {"status": "ok"}

    async def submit_order(self, order):
        self.submit_payload = order

    async def add_strategy(self, cfg):
        self.add_payload = cfg

    async def remove_strategy(self, sid):
        self.remove_id = sid

    # Market data
    def get_contracts(self):
        self.contracts_called = True
        return ["ES"]

    def get_market_data(self, contract):
        self.market_data_contract = contract
        return {"contract": contract}

    def get_bars(self, contract):
        self.bars_contract = contract
        return [{"time": 1}]

    def get_quotes(self, contract):
        self.quotes_contract = contract
        return {"bid": 1}

    # Account & positions
    def get_positions(self):
        self.positions_called = True
        return [{"position": 1}]

    def get_account(self):
        self.account_called = True
        return {"id": 1}

    def get_trades(self):
        self.trades_called = True
        return [{"id": 1}]

    def get_orders(self):
        self.orders_called = True
        return {"1": {"order_id": "1"}}

    def get_order(self, order_id):
        self.order_lookup = order_id
        return {"order_id": order_id}

    # Strategies
    def list_strategies(self):
        self.list_strat_called = True
        return [{"id": "s1"}]

    def list_available_strategies(self):
        self.list_avail_called = True
        return [{"id": "s1"}, {"id": "s2"}]

    def get_strategy_performance(self, sid):
        self.strategy_perf_id = sid
        return {"id": sid, "pnl": 10}

    def update_strategy(self, sid, cfg):
        self.update_strategy_id = sid
        self.update_strategy_cfg = cfg
        return {"status": "updated"}

    # System
    def get_health(self):
        self.health_called = True
        return {"status": "good"}

    def get_logs(self):
        self.logs_called = True
        return ["log"]

    def get_config(self):
        self.config_called = True
        return {"debug": True}


def test_rest_endpoints():
    orch = DummyOrchestrator()
    server = APIServer(orch)
    client = TestClient(server.app)

    # system status
    resp = client.get("/status")
    assert resp.status_code == 200
    data = StatusResponse(**resp.json())
    assert data.status == "ok"
    assert orch.status_called

    # order submission
    order = {
        "strategy_id": "s1",
        "account_id": "a1",
        "contract_id": "c1",
        "type": "MARKET",
        "side": "BUY",
        "size": 1,
    }
    resp = client.post("/orders", json=order)
    assert resp.status_code == 200
    data = StatusResponse(**resp.json())
    assert data.status == "submitted"
    assert orch.submit_payload == order

    # strategy add/remove
    resp = client.post("/strategies", json={"name": "test"})
    assert resp.status_code == 200
    data = StrategyResponse(**resp.json())
    assert data.status == "added"
    assert orch.add_payload == {"name": "test"}

    resp = client.delete("/strategies/abc")
    assert resp.status_code == 200
    data = StrategyResponse(**resp.json())
    assert data.status == "removed"
    assert orch.remove_id == "abc"

    # metrics
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert resp.json() == {"running": False}
    assert orch.strategy_runner.stats_called

    # market data
    resp = client.get("/contracts")
    assert resp.status_code == 200
    assert resp.json() == ["ES"]
    assert orch.contracts_called

    resp = client.get("/market-data/ES")
    assert resp.json()["contract"] == "ES"
    assert orch.market_data_contract == "ES"

    resp = client.get("/bars/ES")
    assert resp.json() == [{"time": 1}]
    assert orch.bars_contract == "ES"

    resp = client.get("/quotes/ES")
    assert resp.json()["bid"] == 1
    assert orch.quotes_contract == "ES"

    # account & positions
    resp = client.get("/positions")
    assert resp.json() == [{"position": 1}]
    assert orch.positions_called

    resp = client.get("/account")
    assert resp.json() == {"id": 1}
    assert orch.account_called

    resp = client.get("/trades")
    assert resp.json() == [{"id": 1}]
    assert orch.trades_called

    resp = client.get("/orders")
    assert resp.json() == {"1": {"order_id": "1"}}
    assert orch.orders_called

    resp = client.get("/orders/1")
    assert resp.json() == {"order_id": "1"}
    assert orch.order_lookup == "1"

    # strategies
    resp = client.get("/strategies")
    assert resp.json() == [{"id": "s1"}]
    assert orch.list_strat_called

    resp = client.get("/strategies/available")
    assert resp.json() == [{"id": "s1"}, {"id": "s2"}]
    assert orch.list_avail_called

    resp = client.get("/strategies/s1/performance")
    assert resp.json() == {"id": "s1", "pnl": 10}
    assert orch.strategy_perf_id == "s1"

    resp = client.put("/strategies/s1", json={"p": 1})
    assert resp.json() == {"status": "updated"}
    assert orch.update_strategy_id == "s1"
    assert orch.update_strategy_cfg == {"p": 1}

    # system
    resp = client.get("/health")
    assert resp.json() == {"status": "good"}
    assert orch.health_called

    resp = client.get("/logs")
    assert resp.json() == ["log"]
    assert orch.logs_called

    resp = client.get("/config")
    assert resp.json() == {"debug": True}
    assert orch.config_called


def test_websocket_gateway():
    orch = DummyOrchestrator()
    server = APIServer(orch)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(server.ws_gateway.start())

    client = TestClient(server.app)
    with client.websocket_connect("/ws/token?patterns=topic1") as ws1, \
        client.websocket_connect("/ws/token/topic2") as ws2:
        loop.run_until_complete(orch.event_bus.publish("topic1", {"foo": 1}))
        msg1 = ws1.receive_json()
        assert msg1["topic"] == "topic1"
        assert msg1["payload"] == {"foo": 1}

        # ws2 should not receive topic1
        with pytest.raises(TimeoutError):
            ws2.receive_json(timeout=0.1)

        loop.run_until_complete(orch.event_bus.publish("topic2", {"bar": 2}))
        msg2 = ws2.receive_json()
        assert msg2["topic"] == "topic2"
        assert msg2["payload"] == {"bar": 2}

    loop.run_until_complete(server.ws_gateway.stop())
