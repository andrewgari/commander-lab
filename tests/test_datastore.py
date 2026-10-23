"""DATA_ACCESS_MODE read/write switch."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datastore import WritesDisabledError, assert_readwrite, guard_redis, is_readonly


class Fake:
    def __init__(self):
        self.d = {}
        self.calls = []

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.calls.append(("set", k))
        self.d[k] = v

    def pipeline(self):
        outer = self

        class P:
            def set(self, k, v):
                outer.calls.append(("pset", k))

            def get(self, k):
                return None

            def execute(self):
                return []
        return P()


def test_default_readwrite(monkeypatch):
    monkeypatch.delenv("DATA_ACCESS_MODE", raising=False)
    assert not is_readonly()
    assert_readwrite()


@pytest.mark.parametrize("v", ["readonly", " ReadOnly "])
def test_readonly(monkeypatch, v):
    monkeypatch.setenv("DATA_ACCESS_MODE", v)
    assert is_readonly()
    with pytest.raises(WritesDisabledError):
        assert_readwrite()


def test_guarded_client(monkeypatch):
    f = Fake()
    g = guard_redis(f)
    monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
    g.set("a", 1)
    assert g.get("a") == 1
    monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
    assert g.get("a") == 1
    with pytest.raises(WritesDisabledError):
        g.set("b", 2)
    with pytest.raises(WritesDisabledError):
        g.pipeline().set("c", 3)
    assert f.calls == [("set", "a")]


def test_api_returns_503(monkeypatch):
    from fastapi.testclient import TestClient
    import app as app_module
    monkeypatch.setattr(app_module, "r", guard_redis(Fake()), raising=False)
    from datastore import _GuardedRedis

    @app_module.app.post("/__t_write")
    async def _w():
        app_module.r.set("x", 1)
        return {"ok": True}

    c = TestClient(app_module.app)
    monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
    res = c.post("/__t_write")
    assert res.status_code == 503 and res.json()["error"] == "writes_disabled"
    monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
    assert c.post("/__t_write").status_code == 200
