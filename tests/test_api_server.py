import asyncio

import pytest
from fastapi.testclient import TestClient

from topstepx_backend.api.server import APIServer
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
    assert resp.json()["status"] == "ok"
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
    assert orch.submit_payload == order

    # strategy add/remove
    resp = client.post("/strategies", json={"name": "test"})
    assert resp.status_code == 200
    assert orch.add_payload == {"name": "test"}

    resp = client.delete("/strategies/abc")
    assert resp.status_code == 200
    assert orch.remove_id == "abc"


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
