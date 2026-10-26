"""Edge-case tests for the Repository adapter and PG transaction boundary.

Covers gaps not exercised by test_repository_interfaces.py, test_route_cache_and_repo.py
and test_pg_store.py:
  * CompositionPresetRepository maps store errors onto the interface's error types
    (missing id -> NotFoundError, validation errors pass through unchanged).
  * session_scope rolls back a multi-step unit of work when a later step fails,
    including when the failure is a BaseException (e.g. KeyboardInterrupt).
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.analytics import composition_pg_store as pg
from src.server.domain.analytics.composition_models import CompositionValidationError
from src.server.domain.analytics.repository_interfaces import NotFoundError, Repository
from src.server.domain.analytics.composition_repository import CompositionPresetRepository
from src.server.infra.db.orm_models import Base
from src.server.infra.db.session import session_scope


class FakeStore:
    """Stands in for analytics.composition_store; records calls, raises on demand."""

    def __init__(self):
        self.calls = []
        self.raise_on_update = None
        self.delete_result = True

    def create_preset(self, r, entity):
        self.calls.append(("create", entity))
        return {**entity, "id": "x"}

    def get_preset(self, r, pid):
        self.calls.append(("get", pid))
        return None

    def list_presets(self, r):
        self.calls.append(("list",))
        return []

    def update_preset(self, r, pid, entity):
        self.calls.append(("update", pid))
        if self.raise_on_update is not None:
            raise self.raise_on_update
        return {**entity, "id": pid}

    def delete_preset(self, r, pid):
        self.calls.append(("delete", pid))
        return self.delete_result


def test_adapter_is_a_repository():
    assert isinstance(CompositionPresetRepository(object(), FakeStore()), Repository)


def test_update_missing_id_maps_value_error_to_not_found():
    store = FakeStore()
    store.raise_on_update = ValueError("Preset 'nope' not found")
    repo = CompositionPresetRepository(object(), store)
    with pytest.raises(NotFoundError):
        repo.update("nope", {"name": "A"})


def test_update_validation_error_passes_through_unchanged():
    # CompositionValidationError is a ValueError subclass in some code paths; it must NOT
    # be reported as NotFound.
    store = FakeStore()
    err = CompositionValidationError([{"code": "E1", "level": "error", "message": "bad",
                                       "ref": None, "data": {}}])
    store.raise_on_update = err
    repo = CompositionPresetRepository(object(), store)
    with pytest.raises(CompositionValidationError) as exc_info:
        repo.update("p1", {"name": "A"})
    assert exc_info.value is err


def test_delete_coerces_to_bool():
    store = FakeStore()
    store.delete_result = None  # falsy non-bool from a store
    repo = CompositionPresetRepository(object(), store)
    assert repo.delete("p1") is False
    store.delete_result = 1
    assert repo.delete("p1") is True


def test_get_missing_returns_none_not_exception():
    repo = CompositionPresetRepository(object(), FakeStore())
    assert repo.get("absent") is None


@pytest.fixture
def factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    eng.dispose()


def _raw(name):
    return {"name": name, "slots": [], "structure": {"lands": 38}}


def test_session_scope_rolls_back_earlier_writes_on_later_failure(factory):
    with pytest.raises(RuntimeError):
        with session_scope(factory) as s:
            pg.create_preset(s, _raw("First"))
            raise RuntimeError("boom after first write")
    with session_scope(factory) as s:
        assert pg.list_presets(s) == []


def test_session_scope_rolls_back_on_base_exception(factory):
    with pytest.raises(KeyboardInterrupt):
        with session_scope(factory) as s:
            pg.create_preset(s, _raw("Interrupted"))
            raise KeyboardInterrupt
    with session_scope(factory) as s:
        assert pg.list_presets(s) == []


def test_failed_second_write_in_same_unit_discards_first(factory):
    with session_scope(factory) as s:
        pg.create_preset(s, _raw("Keep"))
    with pytest.raises(CompositionValidationError):
        with session_scope(factory) as s:
            pg.create_preset(s, _raw("Discarded"))
            pg.create_preset(s, {"name": "", "slots": [], "structure": {}})  # invalid name
    with session_scope(factory) as s:
        names = [p["name"] for p in pg.list_presets(s)]
    assert names == ["Keep"]
