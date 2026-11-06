"""Atomic Scryfall spacing limiter + rarity filter for cards without a printings row."""
from __future__ import annotations

import threading

from src.server.infra.services import card_search_service as svc
from tests.test_cards_search import (  # noqa: F401  (fixtures + helpers)
    _card, client, factory, get, scry, seed, printing, FakeRedis,
)
import pytest


class _MgetRedis(FakeRedis):
    def mget(self, keys):
        return [self.get(k) for k in keys]


@pytest.fixture()
def redis():
    return _MgetRedis()


def test_space_keeps_gap_under_concurrency(monkeypatch):
    now = [1000.0]
    clock_lock = threading.Lock()
    stamps = []

    def fake_sleep(d):
        with clock_lock:
            now[0] += d

    def fake_clock():
        with clock_lock:
            return now[0]

    monkeypatch.setattr(svc, "_clock", fake_clock)
    monkeypatch.setattr(svc, "_sleep", fake_sleep)
    monkeypatch.setattr(svc, "_last_call", 0.0)

    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        svc._space()
        with clock_lock:
            stamps.append(now[0])

    ts = [threading.Thread(target=worker) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    # Each call's slot is >= SPACING apart; the final clock covers all 8 slots.
    assert len(stamps) == 8
    assert now[0] - 1000.0 >= 7 * svc.SPACING_SECONDS - 1e-9


def test_space_slots_are_spaced(monkeypatch):
    slots = []
    monkeypatch.setattr(svc, "_clock", lambda: 5.0)       # frozen: every caller wants "now"
    monkeypatch.setattr(svc, "_sleep", lambda d: slots.append(d))
    monkeypatch.setattr(svc, "_last_call", 0.0)
    ts = [threading.Thread(target=svc._space) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    waits = sorted(slots)
    assert len(waits) == 7                                  # first caller needs no wait
    gaps = [b - a for a, b in zip(waits, waits[1:])]
    assert all(g >= svc.SPACING_SECONDS - 1e-9 for g in gaps)


def _seed(factory):
    seed(factory, [_card("o-np", "No Printing Elf"), _card("o-pr", "Printed Common")],
         instances=[("o-np", "in_collection"), ("o-pr", "in_collection")],
         printings=[printing("p1", "o-pr", "common")])


def test_rare_chip_includes_no_printing_card_with_cached_rarity(client, factory, redis):
    _seed(factory)
    redis.set("cards:rarity:o-np", "rare")
    res = get(client, owned="owned", rarity="rare").json()["results"]
    assert [(h["name"], h["rarity"]) for h in res] == [("No Printing Elf", "rare")]


def test_rare_chip_excludes_no_printing_card_when_rarity_differs_or_unknown(client, factory, redis):
    _seed(factory)
    redis.set("cards:rarity:o-np", "rare")
    names = [h["name"] for h in get(client, owned="owned", rarity="common").json()["results"]]
    assert names == ["Printed Common"]                      # cached rare is not common
    redis.delete("cards:rarity:o-np")
    assert get(client, owned="owned", rarity="rare").json()["results"] == []   # unknown
