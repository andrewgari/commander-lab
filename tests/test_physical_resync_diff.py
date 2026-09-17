"""
Tests for the physical-deck resync diff mechanism:
- pending_removal field on instances
- flag_resync_removed helper in instances.py
- registry.py's resync path calling the helper for physical decks only

See docs/PHYSICAL_RESYNC_ADJUDICATION.md section 3.

Run: pytest tests/test_physical_resync_diff.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import instances as instance_store
import registry


class FakeRedis:
    """Minimal in-memory stand-in for Redis used by instances.py and registry.py."""

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


class TestFlagResyncRemoved(unittest.TestCase):
    """Unit tests for instances.flag_resync_removed()."""

    def setUp(self):
        self.r = FakeRedis()
        # Create a physical deck scenario: 3 cards bound to deck 123
        self.deck_id = "archidekt:123"

        # Create bound instances (in_deck)
        self.inst_sol_ring = instance_store.create_instance(
            self.r,
            card_name="Sol Ring",
            ownership_status="in_deck",
            deck_id=self.deck_id,
            deck_name="Test Deck",
        )
        self.inst_mana_crypt = instance_store.create_instance(
            self.r,
            card_name="Mana Crypt",
            ownership_status="in_deck",
            deck_id=self.deck_id,
            deck_name="Test Deck",
        )
        self.inst_lightning_bolt = instance_store.create_instance(
            self.r,
            card_name="Lightning Bolt",
            ownership_status="in_deck",
            deck_id=self.deck_id,
            deck_name="Test Deck",
        )

    def test_resync_removing_card_sets_pending_removal_without_touching_ownership(self):
        """When a card is removed from remote decklist, flag it without
        touching ownership_status or deck_id."""
        # Remote decklist now only has Sol Ring and Mana Crypt (Lightning Bolt removed)
        new_card_names = {"Sol Ring", "Mana Crypt"}

        flagged = instance_store.flag_resync_removed(self.r, self.deck_id, new_card_names)

        # Only Lightning Bolt should be flagged
        self.assertEqual(len(flagged), 1)
        self.assertEqual(flagged[0]["card_name"], "Lightning Bolt")

        # Verify the instance record directly
        inst = instance_store.get_instance(self.r, self.inst_lightning_bolt["id"])

        # pending_removal should be set
        self.assertIn("pending_removal", inst)
        self.assertEqual(inst["pending_removal"]["reason"], "resync_removed")
        self.assertEqual(inst["pending_removal"]["registry_id"], self.deck_id)

        # ownership_status and deck_id must be UNTOUCHED
        self.assertEqual(inst["ownership_status"], "in_deck")
        self.assertEqual(inst["deck_id"], self.deck_id)
        self.assertEqual(inst["deck_name"], "Test Deck")

    def test_resync_no_changes_does_not_flag_anything(self):
        """When the remote decklist matches current bindings, nothing is flagged."""
        # Remote decklist has all 3 cards
        new_card_names = {"Sol Ring", "Mana Crypt", "Lightning Bolt"}

        flagged = instance_store.flag_resync_removed(self.r, self.deck_id, new_card_names)

        # No instances should be flagged
        self.assertEqual(len(flagged), 0)

        # Verify none have pending_removal
        for inst_id in [self.inst_sol_ring["id"], self.inst_mana_crypt["id"], self.inst_lightning_bolt["id"]]:
            inst = instance_store.get_instance(self.r, inst_id)
            self.assertNotIn("pending_removal", inst)

    def test_flag_is_idempotent_does_not_restamp(self):
        """Calling flag_resync_removed twice for the same removal doesn't
        update the detected_at timestamp."""
        new_card_names = {"Sol Ring", "Mana Crypt"}

        # First call
        flagged1 = instance_store.flag_resync_removed(self.r, self.deck_id, new_card_names)
        inst1 = instance_store.get_instance(self.r, self.inst_lightning_bolt["id"])
        timestamp1 = inst1["pending_removal"]["detected_at"]
        updated_at_1 = inst1["updated_at"]

        # Second call with same conditions
        flagged2 = instance_store.flag_resync_removed(self.r, self.deck_id, new_card_names)
        inst2 = instance_store.get_instance(self.r, self.inst_lightning_bolt["id"])
        timestamp2 = inst2["pending_removal"]["detected_at"]
        updated_at_2 = inst2["updated_at"]

        # Both calls return the flagged instance
        self.assertEqual(len(flagged1), 1)
        self.assertEqual(len(flagged2), 1)

        # Timestamp should NOT have changed (idempotent)
        self.assertEqual(timestamp1, timestamp2)
        self.assertEqual(updated_at_1, updated_at_2)

    def test_multiple_cards_removed(self):
        """Multiple cards removed in one resync are all flagged."""
        # Remote decklist now only has Sol Ring
        new_card_names = {"Sol Ring"}

        flagged = instance_store.flag_resync_removed(self.r, self.deck_id, new_card_names)

        # Both Mana Crypt and Lightning Bolt should be flagged
        self.assertEqual(len(flagged), 2)
        flagged_names = {f["card_name"] for f in flagged}
        self.assertEqual(flagged_names, {"Mana Crypt", "Lightning Bolt"})


class TestComputeResyncAdditions(unittest.TestCase):
    """Unit tests for instances.compute_resync_additions()."""

    def setUp(self):
        self.r = FakeRedis()
        self.deck_id = "archidekt:456"

        # Bind one card to the deck
        self.inst_sol_ring = instance_store.create_instance(
            self.r,
            card_name="Sol Ring",
            ownership_status="in_deck",
            deck_id=self.deck_id,
            deck_name="Test Deck",
        )

    def test_additions_surfaced_correctly(self):
        """Cards in remote that aren't bound are surfaced as additions."""
        # Remote has Sol Ring (bound) plus two new cards
        new_card_names = {"Sol Ring", "Mana Crypt", "Lightning Bolt"}

        additions = instance_store.compute_resync_additions(self.r, self.deck_id, new_card_names)

        # Mana Crypt and Lightning Bolt are additions
        self.assertEqual(set(additions), {"Mana Crypt", "Lightning Bolt"})

    def test_no_additions_when_all_bound(self):
        """No additions when remote matches bound instances."""
        new_card_names = {"Sol Ring"}

        additions = instance_store.compute_resync_additions(self.r, self.deck_id, new_card_names)

        self.assertEqual(additions, [])


class TestClearPendingRemoval(unittest.TestCase):
    """Unit tests for instances.clear_pending_removal()."""

    def setUp(self):
        self.r = FakeRedis()
        self.deck_id = "archidekt:789"

        self.inst = instance_store.create_instance(
            self.r,
            card_name="Sol Ring",
            ownership_status="in_deck",
            deck_id=self.deck_id,
            deck_name="Test Deck",
        )

        # Flag it
        instance_store.flag_resync_removed(self.r, self.deck_id, set())

    def test_clear_removes_marker(self):
        """clear_pending_removal removes the marker from the instance."""
        inst = instance_store.get_instance(self.r, self.inst["id"])
        self.assertIn("pending_removal", inst)

        result = instance_store.clear_pending_removal(self.r, self.inst["id"])

        self.assertIsNotNone(result)
        self.assertNotIn("pending_removal", result)

        # Verify persisted
        inst = instance_store.get_instance(self.r, self.inst["id"])
        self.assertNotIn("pending_removal", inst)

    def test_clear_nonexistent_instance_returns_none(self):
        """Clearing a nonexistent instance returns None."""
        result = instance_store.clear_pending_removal(self.r, "nonexistent-id")
        self.assertIsNone(result)


class TestRegistryResyncIntegration(unittest.TestCase):
    """Integration tests for registry.upsert_deck calling the resync diff."""

    def setUp(self):
        self.r = FakeRedis()

    def _create_physical_deck_with_instances(self, cards):
        """Helper to create a physical deck with bound instances."""
        # Create deck via registry (starts as testing, then we flip to physical)
        normalized = {
            "source": "archidekt",
            "source_id": "999",
            "name": "Test Physical Deck",
            "color": "WUBRG",
            "commanders": ["Kenrith, the Returned King"],
            "commander_uids": [],
            "folder": "Imported",
            "description": "",
            "cards": cards,
            "url": "",
        }
        deck = registry.upsert_deck(self.r, normalized, default_status="physical")
        reg_id = registry.registry_id_of(deck)

        # auto_bind_physical creates in_deck instances for physical decks
        # (this happens via set_status when transitioning to physical, but
        # since we created with default_status="physical", we need to bind manually)
        instance_store.auto_bind_physical(
            self.r,
            deck_registry_id=reg_id,
            deck_name=deck["name"],
            decklist=cards,
        )

        return deck, reg_id

    def test_physical_deck_resync_flags_removed_cards(self):
        """Resyncing a physical deck flags removed cards without mutating instances."""
        original_cards = [
            {"name": "Sol Ring", "quantity": 1},
            {"name": "Mana Crypt", "quantity": 1},
            {"name": "Lightning Bolt", "quantity": 1},
        ]
        deck, reg_id = self._create_physical_deck_with_instances(original_cards)

        # Verify instances are bound
        bound = instance_store.list_instances(self.r, deck_id=reg_id, ownership_status="in_deck")
        self.assertEqual(len(bound), 3)

        # Resync with Lightning Bolt removed from remote
        new_cards = [
            {"name": "Sol Ring", "quantity": 1},
            {"name": "Mana Crypt", "quantity": 1},
        ]
        normalized_update = {
            "source": "archidekt",
            "source_id": "999",
            "name": "Test Physical Deck",
            "color": "WUBRG",
            "commanders": ["Kenrith, the Returned King"],
            "commander_uids": [],
            "folder": "Imported",
            "description": "",
            "cards": new_cards,
            "url": "",
        }
        registry.upsert_deck(self.r, normalized_update)

        # Instances should still all be bound
        bound = instance_store.list_instances(self.r, deck_id=reg_id, ownership_status="in_deck")
        self.assertEqual(len(bound), 3)

        # Lightning Bolt should have pending_removal
        lightning_bolt = next(i for i in bound if i["card_name"] == "Lightning Bolt")
        self.assertIn("pending_removal", lightning_bolt)
        self.assertEqual(lightning_bolt["pending_removal"]["reason"], "resync_removed")
        self.assertEqual(lightning_bolt["ownership_status"], "in_deck")
        self.assertEqual(lightning_bolt["deck_id"], reg_id)

        # Sol Ring and Mana Crypt should NOT have pending_removal
        for name in ["Sol Ring", "Mana Crypt"]:
            inst = next(i for i in bound if i["card_name"] == name)
            self.assertNotIn("pending_removal", inst)

    def test_nonphysical_deck_resync_does_not_touch_instances(self):
        """Resyncing a digital deck does NOT create or touch any instances."""
        # Create digital deck
        normalized = {
            "source": "archidekt",
            "source_id": "888",
            "name": "Test Digital Deck",
            "color": "WU",
            "commanders": ["Brago, King Eternal"],
            "commander_uids": [],
            "folder": "Imported",
            "description": "",
            "cards": [{"name": "Sol Ring", "quantity": 1}],
            "url": "",
        }
        deck = registry.upsert_deck(self.r, normalized, default_status="digital")
        reg_id = registry.registry_id_of(deck)

        # No instances should exist for a non-physical deck
        all_instances = instance_store.list_instances(self.r)
        digital_deck_instances = [i for i in all_instances if i.get("deck_id") == reg_id]
        self.assertEqual(len(digital_deck_instances), 0)

        # Resync with card removed
        normalized_update = {
            "source": "archidekt",
            "source_id": "888",
            "name": "Test Digital Deck",
            "color": "WU",
            "commanders": ["Brago, King Eternal"],
            "commander_uids": [],
            "folder": "Imported",
            "description": "",
            "cards": [],  # Sol Ring removed
            "url": "",
        }
        registry.upsert_deck(self.r, normalized_update)

        # Still no instances should be created or touched
        all_instances = instance_store.list_instances(self.r)
        digital_deck_instances = [i for i in all_instances if i.get("deck_id") == reg_id]
        self.assertEqual(len(digital_deck_instances), 0)


if __name__ == "__main__":
    unittest.main()
