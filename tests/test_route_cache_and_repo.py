"""Composition repository adapter + route response cache middleware."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.server.domain.analytics.composition_repository import CompositionPresetRepository
from src.server.domain.analytics.repository_interfaces import NotFoundError, Repository
from src.server.domain.analytics import composition_service as svc
from src.server.infra.cache.route_cache import make_route_cache_middleware


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, px=None, **kw):
        self.kv[k] = v
        return True

    def incr(self, k):
        self.kv[k] = str(int(self.kv.get(k, 0)) + 1)
        return int(self.kv[k])


class FakeStore:
    def __init__(self):
        self.d = {}

    def list_presets(self, r):
        return list(self.d.values())

    def get_preset(self, r, i):
        return self.d.get(i)

    def create_preset(self, r, raw):
        self.d[raw["id"]] = dict(raw)
        return dict(raw)

    def update_preset(self, r, i, raw):
        if i not in self.d:
            raise ValueError("nf")
        self.d[i] = dict(raw)
        return dict(raw)

    def delete_preset(self, r, i):
        return self.d.pop(i, None) is not None


def test_repository_contract():
    repo = CompositionPresetRepository(None, store=FakeStore())
    assert isinstance(repo, Repository)
    repo.create({"id": "a"})
    assert repo.get("a") == {"id": "a"} and repo.get("zz") is None
    assert len(repo.list()) == 1
    with pytest.raises(NotFoundError):
        repo.update("zz", {"id": "zz"})
    assert repo.delete("a") is True and repo.delete("a") is False


def test_service_uses_repository(monkeypatch):
    fs = FakeStore()
    monkeypatch.setattr("src.server.domain.analytics.composition_repository.composition_store", fs)
    from src.server.domain.analytics import composition_repository as cr
    monkeypatch.setattr(cr, "get_preset_repository", lambda c: CompositionPresetRepository(c, store=fs))
    monkeypatch.setattr(svc, "get_preset_repository", cr.get_preset_repository)
    svc.create(None, {"id": "p1"})
    assert svc.get(None, "p1") == {"id": "p1"}
    with pytest.raises(svc.PresetNotFound):
        svc.update(None, "nope", {})
    with pytest.raises(svc.PresetNotFound):
        svc.delete(None, "nope")


def _app(client):
    app = FastAPI()
    calls = {"n": 0}
    app.middleware("http")(make_route_cache_middleware(lambda: client, cached_paths=("/api/composition/presets",)))

    @app.get("/api/composition/presets")
    def lst(q: str = ""):
        calls["n"] += 1
        return {"n": calls["n"], "q": q}

    @app.post("/api/composition/presets")
    def mk():
        return {"ok": True}

    @app.get("/other")
    def other():
        calls["n"] += 1
        return {"n": calls["n"]}
    return TestClient(app), calls


def test_hit_miss_and_invalidate():
    c, calls = _app(FakeRedis())
    a = c.get("/api/composition/presets?q=1")
    assert a.headers["x-route-cache"] == "MISS"
    b = c.get("/api/composition/presets?q=1")
    assert b.headers["x-route-cache"] == "HIT" and b.json() == a.json() and calls["n"] == 1
    assert c.get("/api/composition/presets?q=2").headers["x-route-cache"] == "MISS"
    c.post("/api/composition/presets")
    d = c.get("/api/composition/presets?q=1")
    assert d.headers["x-route-cache"] == "MISS" and d.json()["n"] == 3
    assert "x-route-cache" not in c.get("/other").headers


def test_fail_open():
    class Boom:
        def __getattr__(self, n):
            raise RuntimeError("down")
    c, calls = _app(Boom())
    assert c.get("/api/composition/presets").status_code == 200
    assert c.post("/api/composition/presets").status_code == 200


def test_disabled(monkeypatch):
    monkeypatch.setenv("ROUTE_CACHE_ENABLED", "0")
    c, calls = _app(FakeRedis())
    c.get("/api/composition/presets"); c.get("/api/composition/presets")
    assert calls["n"] == 2
