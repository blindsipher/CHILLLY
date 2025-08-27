import asyncio

from fastapi.testclient import TestClient

from topstepx_backend.api.server import APIServer, StatusResponse, StrategyResponse
from topstepx_backend.core.event_bus import EventBus


class DummyAuth:
    def get_token(self) -> str:
        return "token"


class DummyOrchestrator:
    def __init__(self) -> None:
        self.event_bus = EventBus()
        self.auth_manager = DummyAuth()
        self.status_called = False
        self.submit_payload = None
        self.add_payload = None
        self.remove_id = None

    def get_system_status(self):
        self.status_called = True
        return {"status": "ok"}

    async def submit_order(self, order):
        self.submit_payload = order

    async def add_strategy(self, cfg):
        self.add_payload = cfg

    async def remove_strategy(self, sid):
        self.remove_id = sid


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


def test_websocket_gateway():
    orch = DummyOrchestrator()
    server = APIServer(orch)
    loop = asyncio.get_event_loop()
    loop.run_until_complete(server.ws_gateway.start())

    client = TestClient(server.app)
    with client.websocket_connect("/ws/token") as ws:
        loop.run_until_complete(orch.event_bus.publish("topic", {"foo": 1}))
        msg = ws.receive_json()
        assert msg["topic"] == "topic"
        assert msg["payload"] == {"foo": 1}

    loop.run_until_complete(server.ws_gateway.stop())
