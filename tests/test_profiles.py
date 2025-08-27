from topstepx_backend.config.profiles import DevelopmentProfile


def test_default_contracts_isolation():
    p1 = DevelopmentProfile()
    p2 = DevelopmentProfile()
    assert p1.default_contracts == []
    assert p2.default_contracts == []
    p1.default_contracts.append("ES")
    assert p2.default_contracts == []
    assert p1.environment == "development"
