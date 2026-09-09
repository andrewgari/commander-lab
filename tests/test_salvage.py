"""
Unit tests for Deck Salvager matching logic (salvage.py).

Uses the same in-memory FakeRedis pattern as tests/test_instances.py.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import instances as instance_store
import salvage


class FakeRedis:
    """Minimal in-memory stand-in for the redis calls instances.py makes."""

    def __init__(self):
        self.store = {}
        self.sets = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    def srem(self, key, member):
        self.sets.get(key, set()).discard(member)

    def smembers(self, key):
        return set(self.sets.get(key, set()))

    def sinter(self, *keys):
        sets = [self.sets.get(k, set()) for k in keys]
        if not sets:
            return set()
        result = sets[0]
        for s in sets[1:]:
            result = result & s
        return result

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, parent):
        self.parent = parent
        self.ops = []

    def __getattr__(self, name):
        def call(*args, **kwargs):
            self.ops.append((name, args, kwargs))
            return self
        return call

    def execute(self):
        for name, args, kwargs in self.ops:
            getattr(self.parent, name)(*args, **kwargs)
        self.ops = []


class TestMatchDecklistToInventory(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def _seed_deck(self, deck_id="deck-1", deck_name="Najeela Hatebears"):
        # Ensure a deck exists in-deck for committed instances to reference.
        return deck_id, deck_name

    def test_have_status_when_free_covers_quantity(self):
        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_collection"
        )
        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_collection"
        )

        result = salvage.match_decklist_to_inventory(
            self.r, [{"name": "Sol Ring", "quantity": 1}]
        )

        match = result["matches"][0]
        self.assertEqual(match["status"], "have")
        self.assertEqual(match["free"], 2)
        self.assertEqual(match["committed"], 0)
        self.assertEqual(match["in_mail"], 0)
        self.assertEqual(match["committed_decks"], [])
        self.assertEqual(result["summary"], {"have": 1, "steal": 0, "incoming": 0, "missing": 0})

    def test_steal_status_partial_free_own_one_need_two(self):
        instance_store.create_instance(
            self.r, card_name="Command Tower", ownership_status="in_collection"
        )

        result = salvage.match_decklist_to_inventory(
            self.r, [{"name": "Command Tower", "quantity": 2}]
        )

        match = result["matches"][0]
        self.assertEqual(match["status"], "steal")
        self.assertEqual(match["free"], 1)
        self.assertEqual(match["quantity"], 2)
        self.assertEqual(result["summary"]["steal"], 1)

    def test_steal_status_zero_free_but_committed_elsewhere(self):
        deck_id, deck_name = self._seed_deck()
        instance_store.create_instance(
            self.r,
            card_name="Mana Crypt",
            ownership_status="in_deck",
            deck_id=deck_id,
            deck_name=deck_name,
        )

        result = salvage.match_decklist_to_inventory(
            self.r, [{"name": "Mana Crypt", "quantity": 1}]
        )

        match = result["matches"][0]
        self.assertEqual(match["status"], "steal")
        self.assertEqual(match["free"], 0)
        self.assertEqual(match["committed"], 1)
        self.assertEqual(match["committed_decks"], ["Najeela Hatebears"])
        self.assertEqual(result["summary"]["steal"], 1)

    def test_steal_status_reports_multiple_distinct_committed_decks(self):
        instance_store.create_instance(
            self.r,
            card_name="Cyclonic Rift",
            ownership_status="in_deck",
            deck_id="deck-1",
            deck_name="Najeela Hatebears",
        )
        instance_store.create_instance(
            self.r,
            card_name="Cyclonic Rift",
            ownership_status="in_deck",
            deck_id="deck-2",
            deck_name="Atraxa Superfriends",
        )

        result = salvage.match_decklist_to_inventory(
            self.r, [{"name": "Cyclonic Rift", "quantity": 1}]
        )

        match = result["matches"][0]
        self.assertEqual(match["status"], "steal")
        self.assertEqual(match["committed"], 2)
        self.assertEqual(
            match["committed_decks"], ["Atraxa Superfriends", "Najeela Hatebears"]
        )

    def test_incoming_status_when_only_in_mail(self):
        instance_store.create_instance(
            self.r, card_name="Rhystic Study", ownership_status="in_mail"
        )

        result = salvage.match_decklist_to_inventory(
            self.r, [{"name": "Rhystic Study", "quantity": 1}]
        )

        match = result["matches"][0]
        self.assertEqual(match["status"], "incoming")
        self.assertEqual(match["free"], 0)
        self.assertEqual(match["committed"], 0)
        self.assertEqual(match["in_mail"], 1)
        self.assertEqual(result["summary"]["incoming"], 1)

    def test_missing_status_when_no_instances_at_all(self):
        result = salvage.match_decklist_to_inventory(
            self.r, [{"name": "Nowhere Card", "quantity": 1}]
        )

        match = result["matches"][0]
        self.assertEqual(match["status"], "missing")
        self.assertEqual(match["free"], 0)
        self.assertEqual(match["committed"], 0)
        self.assertEqual(match["in_mail"], 0)
        self.assertEqual(result["summary"]["missing"], 1)

    def test_missing_status_when_only_not_owned_instances(self):
        instance_store.create_instance(
            self.r, card_name="Never Bought", ownership_status="not_owned"
        )

        result = salvage.match_decklist_to_inventory(
            self.r, [{"name": "Never Bought", "quantity": 1}]
        )

        match = result["matches"][0]
        self.assertEqual(match["status"], "missing")
        self.assertEqual(result["summary"]["missing"], 1)

    def test_summary_counts_across_full_decklist(self):
        # have
        instance_store.create_instance(
            self.r, card_name="Sol Ring", ownership_status="in_collection"
        )
        # steal (partial free)
        instance_store.create_instance(
            self.r, card_name="Command Tower", ownership_status="in_collection"
        )
        # steal (committed elsewhere)
        instance_store.create_instance(
            self.r,
            card_name="Mana Crypt",
            ownership_status="in_deck",
            deck_id="deck-1",
            deck_name="Najeela Hatebears",
        )
        # incoming
        instance_store.create_instance(
            self.r, card_name="Rhystic Study", ownership_status="in_mail"
        )
        # missing: no instance created for "Cyclonic Rift"

        cards = [
            {"name": "Sol Ring", "quantity": 1},
            {"name": "Command Tower", "quantity": 2},
            {"name": "Mana Crypt", "quantity": 1},
            {"name": "Rhystic Study", "quantity": 1},
            {"name": "Cyclonic Rift", "quantity": 1},
        ]

        result = salvage.match_decklist_to_inventory(self.r, cards)

        self.assertEqual(
            result["summary"], {"have": 1, "steal": 2, "incoming": 1, "missing": 1}
        )
        self.assertEqual(len(result["matches"]), 5)

    def test_empty_decklist_returns_empty_matches_and_zeroed_summary(self):
        result = salvage.match_decklist_to_inventory(self.r, [])

        self.assertEqual(result["matches"], [])
        self.assertEqual(
            result["summary"], {"have": 0, "steal": 0, "incoming": 0, "missing": 0}
        )

    def test_default_quantity_defaults_to_one_when_omitted(self):
        instance_store.create_instance(
            self.r, card_name="Arcane Signet", ownership_status="in_collection"
        )

        result = salvage.match_decklist_to_inventory(
            self.r, [{"name": "Arcane Signet"}]
        )

        match = result["matches"][0]
        self.assertEqual(match["quantity"], 1)
        self.assertEqual(match["status"], "have")


if __name__ == "__main__":
    unittest.main()
