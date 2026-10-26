"""Slice 1: have/buy split math (domain/inventory/have_buy.py) + parity with
the legacy Redis read model (inventory_view.py status labels).

Parity claim (acceptance): for the same deck + collection, the per-card
have/pending counts the old UI shows (in_deck/in_collection -> "have",
in_mail -> "pending") equal the new Postgres-side split.
"""
from __future__ import annotations

import json

import pytest

import src.server.domain.inventory.inventory_view as inventory_view
from src.server.domain.inventory import have_buy


DECK_ID = 7


def _deck_cards():
    return [
        {"oracle_id": "oid-cmd", "name": "Commander", "board": "commander", "qty": 1, "category": None},
        {"oracle_id": "oid-a", "name": "Alpha", "board": "main", "qty": 1, "category": "Ramp"},
        {"oracle_id": "oid-b", "name": "Beta", "board": "main", "qty": 1, "category": "Removal"},
        {"oracle_id": "oid-c", "name": "Gamma", "board": "main", "qty": 1, "category": "Draw"},
        {"oracle_id": "oid-m", "name": "Maybe", "board": "maybe", "qty": 1, "category": None},
    ]


def _instances():
    return [
        # one Alpha physically in this deck
        {"id": "i1", "oracle_id": "oid-a", "status": "in_deck", "deck_id": DECK_ID},
        # one Beta free in the collection pool
        {"id": "i2", "oracle_id": "oid-b", "status": "in_collection", "deck_id": None},
        # a second Beta copy in the mail (pending, NOT have)
        {"id": "i3", "oracle_id": "oid-b", "status": "in_mail", "deck_id": None},
        # an Alpha bound to ANOTHER deck must not count
        {"id": "i4", "oracle_id": "oid-a", "status": "in_deck", "deck_id": 99},
        # wishlist placeholder: never counts
        {"id": "i5", "oracle_id": "oid-c", "status": "not_owned", "deck_id": None},
    ]


def test_split_counts():
    split = have_buy.compute_have_buy(_deck_cards(), _instances(), DECK_ID)
    cards = {c["name"]: c for c in split["cards"]}
    # maybe-board cards are pools, not requirements
    assert "Maybe" not in cards

    alpha = cards["Alpha"]
    assert (alpha["required"], alpha["have"], alpha["buy"]) == (1, 1, 0)
    assert alpha["have_in_deck"] == 1 and alpha["have_from_collection"] == 0

    beta = cards["Beta"]
    assert (beta["required"], beta["have"], beta["pending"], beta["buy"]) == (1, 1, 1, 0)
    assert beta["have_from_collection"] == 1

    gamma = cards["Gamma"]
    assert (gamma["required"], gamma["have"], gamma["buy"]) == (1, 0, 1)

    cmd = cards["Commander"]
    assert (cmd["required"], cmd["have"], cmd["buy"]) == (1, 0, 1)

    t = split["totals"]
    assert t["required"] == 4
    assert t["have"] == 2
    assert t["pending"] == 1
    assert t["buy"] == 2


def test_collection_pool_never_double_counts_a_copy():
    deck_cards = [{"oracle_id": "oid-a", "name": "Alpha", "board": "main", "qty": 2, "category": None}]
    instances = [{"id": "i1", "oracle_id": "oid-a", "status": "in_collection", "deck_id": None}]
    split = have_buy.compute_have_buy(deck_cards, instances, DECK_ID)
    row = split["cards"][0]
    assert (row["required"], row["have"], row["buy"]) == (2, 1, 1)


def test_status_vocabulary_matches_legacy_labels():
    # in_deck/in_collection -> have; in_mail -> pending (inventory_view STATUS_LABELS)
    assert inventory_view.STATUS_LABELS["in_deck"] == "have"
    assert inventory_view.STATUS_LABELS["in_collection"] == "have"
    assert inventory_view.STATUS_LABELS["in_mail"] == "pending"
    assert have_buy.STATUS_LABELS == inventory_view.STATUS_LABELS or (
        have_buy.STATUS_LABELS["in_deck"] == inventory_view.STATUS_LABELS["in_deck"]
        and have_buy.STATUS_LABELS["in_collection"] == inventory_view.STATUS_LABELS["in_collection"]
        and have_buy.STATUS_LABELS["in_mail"] == inventory_view.STATUS_LABELS["in_mail"]
    )


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls inventory_view makes."""

    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]


def _seed_legacy(r):
    """Seed the Redis-shaped world matching _deck_cards()/_instances()."""
    r.set("decks", json.dumps([{
        "id": DECK_ID, "registry_id": f"archidekt:{DECK_ID}",
        "name": "Parity Deck", "commanders": ["Commander"],
    }]))
    by_oid = {"oid-cmd": "Commander", "oid-a": "Alpha", "oid-b": "Beta",
              "oid-c": "Gamma", "oid-m": "Maybe"}
    for oid, name in by_oid.items():
        r.set(f"idx:card_name_to_oracle:{name}", oid)
    legacy_status = {"i1": ("Alpha", "in_deck", str(DECK_ID)),
                     "i2": ("Beta", "in_collection", None),
                     "i3": ("Beta", "in_mail", None),
                     "i4": ("Alpha", "in_deck", "99"),
                     "i5": ("Gamma", "not_owned", None)}
    for iid, (name, status, deck_ref) in legacy_status.items():
        record = {
            "id": iid, "card_name": name, "ownership_status": status,
            "deck_id": deck_ref, "deck_name": "Parity Deck" if deck_ref else "",
            "considered_for_deck": None, "finish": "nonfoil", "condition": "NM",
            "price_paid": 0.0, "archidekt_uid": "", "set": "", "set_name": "",
            "collector_number": "", "language": "EN", "source": "", "notes": "",
            "date_ordered": "", "date_acquired": "2026-01-01",
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
            "history": [],
        }
        r.set(f"instance:{iid}", json.dumps(record))
    return by_oid


def _legacy_have_pending(r, deck_name):
    """Old-UI numbers: per-card owned-copy labels from inventory_view rows.

    have = copies labeled 'have' physically in this deck, then free-pool
    copies (in_collection) up to the requirement; pending = 'pending' copies.
    """
    rows = inventory_view.build_inventory(r, decks=[deck_name])
    pool_rows = inventory_view.build_inventory(r)  # unfiltered: free pool too
    in_deck = {}
    for row in rows:
        if row["status"] == "have":
            in_deck[row["oracle_id"]] = in_deck.get(row["oracle_id"], 0) + 1
    free, pending = {}, {}
    for row in pool_rows:
        if row["ownership_status"] == "in_collection":
            free[row["oracle_id"]] = free.get(row["oracle_id"], 0) + 1
        elif row["ownership_status"] == "in_mail":
            pending[row["oracle_id"]] = pending.get(row["oracle_id"], 0) + 1
    return in_deck, free, pending


def test_parity_with_inventory_view():
    r = FakeRedis()
    by_oid = _seed_legacy(r)
    split = have_buy.compute_have_buy(_deck_cards(), _instances(), DECK_ID)
    in_deck, free, pending = _legacy_have_pending(r, "Parity Deck")

    for card in split["cards"]:
        oid = card["oracle_id"]
        legacy_have = in_deck.get(oid, 0) + max(
            0, min(card["required"] - in_deck.get(oid, 0), free.get(oid, 0)))
        assert card["have"] == legacy_have, f"{card['name']}: new={card['have']} legacy={legacy_have}"
        assert card["pending"] == pending.get(oid, 0), card["name"]
