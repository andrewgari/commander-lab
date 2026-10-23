from typing import Dict, Optional, Sequence

import pytest

from analytics.repository_interfaces import (
    Cache, ConflictError, NotFoundError, Repository,
)


class MemRepo(Repository[dict, str]):
    def __init__(self) -> None:
        self.rows: Dict[str, dict] = {}

    def create(self, entity: dict) -> dict:
        if entity["id"] in self.rows:
            raise ConflictError(entity["id"])
        self.rows[entity["id"]] = entity
        return entity

    def get(self, entity_id: str) -> Optional[dict]:
        return self.rows.get(entity_id)

    def list(self) -> Sequence[dict]:
        return list(self.rows.values())

    def update(self, entity_id: str, entity: dict) -> dict:
        if entity_id not in self.rows:
            raise NotFoundError(entity_id)
        self.rows[entity_id] = entity
        return entity

    def delete(self, entity_id: str) -> bool:
        return self.rows.pop(entity_id, None) is not None


class MemCache(Cache[str]):
    def __init__(self) -> None:
        self.data: Dict[str, str] = {}

    def get(self, key: str) -> Optional[str]:
        return self.data.get(key)

    def put(self, key: str, value: str, ttl_seconds: Optional[int] = None) -> None:
        self.data[key] = value

    def delete(self, key: str) -> bool:
        return self.data.pop(key, None) is not None


def test_interfaces_are_abstract():
    with pytest.raises(TypeError):
        Repository()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        Cache()  # type: ignore[abstract]


def test_partial_implementation_rejected():
    class Partial(Cache[str]):
        def get(self, key: str) -> Optional[str]:
            return None

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def test_repository_contract():
    r = MemRepo()
    r.create({"id": "a", "v": 1})
    with pytest.raises(ConflictError):
        r.create({"id": "a"})
    assert r.get("a") == {"id": "a", "v": 1}
    assert r.get("missing") is None
    assert r.update("a", {"id": "a", "v": 2})["v"] == 2
    with pytest.raises(NotFoundError):
        r.update("zzz", {})
    assert len(r.list()) == 1
    assert r.delete("a") is True
    assert r.delete("a") is False


def test_cache_contract():
    c = MemCache()
    assert c.get("k") is None
    c.put("k", "v", ttl_seconds=5)
    assert c.get("k") == "v"
    assert c.delete("k") is True
    assert c.delete("k") is False
