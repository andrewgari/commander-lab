import json
import os
import sys
from unittest import mock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, *a, **kw):
        self.store[key] = value

    def keys(self, pattern="*"):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    def sinter(self, *keys):
        if not keys:
            return set()
        out = set(self.sets.get(keys[0], set()))
        for k in keys[1:]:
            out &= set(self.sets.get(k, set()))
        return out

    def mget(self, keys, *more):
        return [self.store.get(k) for k in (list(keys) + list(more))]

    def __getattr__(self, name):
        return lambda *a, **kw: None


def test_sync_inventory_follows_pagination_and_sets_timeouts(monkeypatch):
    monkeypatch.setenv("ARCHIDEKT_USERNAME", "testuser")
    import src.server.workers.sync as sync

    r = FakeRedis()
    calls = []

    def fake_get(url, *args, **kwargs):
        calls.append((url, kwargs.get("timeout")))
        resp = mock.Mock()
        resp.raise_for_status = lambda: None
        if "users" in url:
            resp.json = lambda: {"results": [{"id": 1}]}
        elif "v3" in url and "page=2" not in url:
            resp.json = lambda: {
                "results": [{"id": 101, "name": "Deck One", "colors": {"W": 1}, "parentFolderId": 588380}],
                "next": "https://archidekt.com/api/decks/v3/?ownerId=1&pageSize=100&page=2",
            }
        elif "v3" in url and "page=2" in url:
            resp.json = lambda: {
                "results": [{"id": 102, "name": "Deck Two", "colors": {"U": 1}, "parentFolderId": 656395}],
                "next": None,
            }
        else:
            resp.json = lambda: {"description": "", "cards": []}
        return resp

    with (
        mock.patch.object(sync, "r", r),
        mock.patch.object(sync.requests, "get", fake_get),
        mock.patch.object(sync.time, "sleep", lambda s: None),
    ):
        sync.sync_inventory()

    decks = json.loads(r.get("decks"))
    assert [d["id"] for d in decks] == [101, 102]
    assert all(timeout == (10, 30) for _, timeout in calls)


def test_sync_inventory_aborts_on_pagination_loop(monkeypatch):
    monkeypatch.setenv("ARCHIDEKT_USERNAME", "testuser")
    import src.server.workers.sync as sync

    r = FakeRedis()
    loop_url = "https://archidekt.com/api/decks/v3/?ownerId=1&pageSize=100"

    def fake_get(url, *args, **kwargs):
        resp = mock.Mock()
        resp.raise_for_status = lambda: None
        if "users" in url:
            resp.json = lambda: {"results": [{"id": 1}]}
        else:
            resp.json = lambda: {"results": [], "next": loop_url}
        return resp

    with (
        mock.patch.object(sync, "r", r),
        mock.patch.object(sync.requests, "get", fake_get),
    ):
        with pytest.raises(RuntimeError, match="Pagination loop"):
            sync.sync_inventory()
