"""Atomic Scryfall spacing limiter + rarity filter for cards without a printings row."""
from __future__ import annotations

import threading
import time

from src.server.infra.services import card_search_service as svc
from tests.test_cards_search import (  # noqa: F401  (fixtures + helpers)
    _card, client, factory, get, scry, seed, printing, FakeRedis,
)
import pytest


class _MgetRedis(FakeRedis):
    def mget(self, keys):
        return [self.get(k) for k in keys]


@pytest.fixture(autouse=True)
def _no_real_backfill(monkeypatch):
    """Cache misses queue a background Scryfall backfill; never let a test hit the network."""
    monkeypatch.setattr(svc, "backfill_rarities", lambda r, ids: svc._rarity_inflight.difference_update(ids))


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


def test_space_gaps_hold_with_late_wakeups(monkeypatch):
    """A late wake-up must not let two callers dispatch together: every sleep
    overshoots to at least LATE, so planned slots would all wake at once."""
    spacing, late = 0.05, 0.25
    real_sleep = time.sleep
    monkeypatch.setattr(svc, "SPACING_SECONDS", spacing)
    monkeypatch.setattr(svc, "_clock", time.monotonic)
    monkeypatch.setattr(svc, "_sleep", lambda d: real_sleep(max(d, late)))
    monkeypatch.setattr(svc, "_last_call", 0.0)
    stamps, stamp_lock = [], threading.Lock()
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        svc._space()
        with stamp_lock:
            stamps.append(time.monotonic())

    ts = [threading.Thread(target=worker) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    stamps.sort()
    assert len(stamps) == 4
    assert all(b - a >= spacing * 0.9 for a, b in zip(stamps, stamps[1:]))


def test_space_records_actual_dispatch_time(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(svc, "_clock", lambda: now[0])
    monkeypatch.setattr(svc, "_sleep", lambda d: now.__setitem__(0, now[0] + d + 1.0))  # oversleep
    monkeypatch.setattr(svc, "_last_call", 10.0)
    svc._space()
    assert svc._last_call == now[0] == pytest.approx(10.0 + svc.SPACING_SECONDS + 1.0)


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


def test_rare_chip_cache_miss_is_queued_for_backfill(client, factory, redis, monkeypatch):
    _seed(factory)
    queued = []
    monkeypatch.setattr(svc, "_schedule_rarity_backfill", lambda r, ids: queued.append(list(ids)))
    assert get(client, owned="owned", rarity="rare").json()["results"] == []
    assert queued == [["o-np"]]


def test_rejected_no_printing_rows_do_not_displace_later_matches(client, factory, redis, monkeypatch):
    from src.server.infra.db import card_search_repo as repo
    monkeypatch.setattr(repo, "MAX_LOCAL_ROWS", 3)
    cards = [_card(f"o-c{i}", f"A Common {i}") for i in range(5)] + [_card("o-z", "Zed Rare")]
    seed(factory, cards, instances=[(c["oracle_id"], "in_collection") for c in cards])
    for i in range(5):
        redis.set(f"cards:rarity:o-c{i}", "common")
    redis.set("cards:rarity:o-z", "rare")
    res = get(client, owned="owned", rarity="rare").json()["results"]
    assert [h["name"] for h in res] == ["Zed Rare"]
