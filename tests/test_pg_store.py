"""Contract tests for the SQLAlchemy/PostgreSQL composition store.

Runs on in-memory SQLite always; also on real PostgreSQL when TEST_DATABASE_URL is set
(CI provides one). Run with pytest.
"""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.analytics import composition_pg_store as pg
from src.server.domain.analytics.composition_models import (
    CompositionValidationError, KIND_USER, MODE_AUTO, MODE_PINNED,
)
from src.server.infra.db.config import get_settings, normalize_url
from src.server.infra.db.engine import dispose_engine, get_engine, make_engine
from src.server.infra.db.orm_models import Base
from src.server.infra.db.session import session_scope


def _slot(ref, target=None):
    return {"ref": ref, "target": target, "min": None, "max": None, "why": "", "pillar_id": None}


def _raw(name="Test", target=10):
    return {"name": name, "slots": [_slot("core:ramp", target)], "structure": {"lands": 38}}


def _make_engines():
    engines = [("sqlite", lambda: create_engine("sqlite://", poolclass=StaticPool,
                                                connect_args={"check_same_thread": False}))]
    url = os.environ.get("TEST_DATABASE_URL")
    if url:
        engines.append(("postgres", lambda: make_engine(get_settings(url))))
    return engines


@pytest.fixture(params=[n for n, _ in _make_engines()])
def factory(request):
    eng = dict(_make_engines())[request.param]()
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    Base.metadata.drop_all(eng)
    eng.dispose()


def test_preset_crud_roundtrip(factory):
    with session_scope(factory) as s:
        assert pg.list_presets(s) == []
        assert pg.get_preset(s, "nope") is None
        p = pg.create_preset(s, _raw())
    assert p["id"].startswith("p-") and p["kind"] == KIND_USER
    with session_scope(factory) as s:
        assert pg.get_preset(s, p["id"]) == p
        u = pg.update_preset(s, p["id"], _raw("Updated", 12))
    assert u["name"] == "Updated" and u["version"] == 2 and u["id"] == p["id"]
    with session_scope(factory) as s:
        assert pg.get_preset(s, p["id"])["name"] == "Updated"
        assert pg.delete_preset(s, p["id"]) is True
        assert pg.delete_preset(s, p["id"]) is False
        assert pg.list_presets(s) == []


def test_validation_and_missing(factory):
    with session_scope(factory) as s:
        with pytest.raises(CompositionValidationError):
            pg.create_preset(s, {"name": "", "slots": [], "structure": {"lands": 38}})
        with pytest.raises(ValueError):
            pg.update_preset(s, "nonexistent", {"name": "x"})


def test_list_newest_first(factory):
    with session_scope(factory) as s:
        a = pg.create_preset(s, _raw("A"))
        b = pg.create_preset(s, _raw("B"))
        assert [p["id"] for p in pg.list_presets(s)] == [b["id"], a["id"]]


def test_deck_composition(factory):
    with session_scope(factory) as s:
        assert pg.get_deck_composition(s, "archidekt:1")["mode"] == MODE_AUTO
        p = pg.create_preset(s, _raw())
        pg.set_deck_composition(s, "archidekt:1", {"mode": MODE_PINNED, "preset_id": p["id"],
                                                   "lands": None, "overrides": []})
        # upsert second time
        pg.set_deck_composition(s, "archidekt:1", {"mode": MODE_PINNED, "preset_id": p["id"],
                                                   "lands": 36, "overrides": []})
    with session_scope(factory) as s:
        rec = pg.get_deck_composition(s, "archidekt:1")
        assert rec["preset_id"] == p["id"] and rec["lands"] == 36
        with pytest.raises(CompositionValidationError):
            pg.set_deck_composition(s, "archidekt:2", {"mode": MODE_PINNED, "preset_id": "missing",
                                                       "lands": None, "overrides": []})


def test_rollback_discards_all_writes(factory):
    with pytest.raises(RuntimeError):
        with session_scope(factory) as s:
            pg.create_preset(s, _raw("A"))
            pg.create_preset(s, _raw("B"))
            raise RuntimeError("boom")
    with session_scope(factory) as s:
        assert pg.list_presets(s) == []


def test_matches_redis_store_shape(factory):
    """Same inputs produce the same dict keys as the Redis store."""
    from src.server.domain.analytics import composition_store as rs
    from tests.test_interactions import FakeRedis
    r = FakeRedis()
    expected = rs.create_preset(r, _raw())
    with session_scope(factory) as s:
        got = pg.create_preset(s, _raw())
    assert set(got) == set(expected)


# --- config / engine ---------------------------------------------------------

def test_normalize_url():
    assert normalize_url("postgres://u:p@h/d") == "postgresql+psycopg://u:p@h/d"
    assert normalize_url("postgresql://u@h/d") == "postgresql+psycopg://u@h/d"
    assert normalize_url("postgresql+psycopg://u@h/d") == "postgresql+psycopg://u@h/d"


def test_settings_env_and_masking(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@h/d")
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "1")
    st = get_settings()
    assert st.pool_size == 3 and st.max_overflow == 1
    assert "secret" not in st.safe_url()


def test_missing_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError):
        get_settings()


def test_pool_configured_and_singleton(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost:1/d")  # never connects
    monkeypatch.setenv("DB_POOL_SIZE", "4")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "2")
    dispose_engine()
    try:
        e = get_engine()
        assert e is get_engine()
        assert e.pool.size() == 4 and e.pool._max_overflow == 2
    finally:
        dispose_engine()


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL")
def test_postgres_pool_and_jsonb():
    eng = make_engine(get_settings(os.environ["TEST_DATABASE_URL"]))
    with eng.connect() as c:
        assert c.execute(text("select 1")).scalar() == 1
    assert eng.dialect.name == "postgresql"
    eng.dispose()
