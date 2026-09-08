"""
Integration tests for the deck-import -> wishlist-instance pipeline.

Unlike tests/test_instances.py (which unit-tests instances.ensure_wishlist_instances
and instances.auto_bind_physical directly), these tests exercise the real entry
points a user/provider sync would hit: registry.upsert_deck (deck import/re-import)
and registry.set_status (marking a deck physical), verifying the two modules are
wired together correctly end-to-end:

1. Importing a digital/testing deck via registry.upsert_deck creates not_owned
   inventory instances with considered_for_deck set for every card.
2. Promoting that deck to physical (registry.set_status) binds and promotes the
   existing not_owned placeholders in place, without creating duplicate instances.
3. Re-importing a deck whose cards already exist in inventory (registry.upsert_deck
   called again) creates zero new instances.

Run: python tests/test_deck_wishlist_integration.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import registry
import instances as instance_store


class FakeRedis:
    """In-memory stand-in supporting both the key/value calls registry.py makes
    and the set-index calls instances.py makes (same pattern as
    tests/test_instances.py's FakeRedis)."""

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


def _normalized_deck(source_id="101", name="Krenko Goblins", cards=None):
    """Build a normalized provider payload matching providers/__init__.py's
    NORMALIZED_DECK_SHAPE, as passed to registry.upsert_deck."""
    return {
        "source": "archidekt",
        "source_id": source_id,
        "name": name,
        "color": "R",
        "commanders": ["Krenko, Mob Boss"],
        "commander_uids": ["krenko-mob-boss"],
        "folder": "Imported",
        "description": "",
        "cards": cards if cards is not None else [
            {"name": "Sol Ring", "quantity": 1},
            {"name": "Arcane Signet", "quantity": 1},
            {"name": "Goblin Bombardment", "quantity": 1},
        ],
        "url": "",
    }


class TestDeckImportCreatesWishlistInstances(unittest.TestCase):
    """1) Importing a digital/testing deck creates not_owned instances with
    considered_for_deck set, via the real registry.upsert_deck entry point."""

    def setUp(self):
        self.r = FakeRedis()

    def test_upsert_deck_creates_wishlist_placeholders_for_testing_deck(self):
        deck = registry.upsert_deck(self.r, _normalized_deck(), default_status="testing")
        self.assertEqual(deck["status"], "testing")
        reg_id = registry.registry_id_of(deck)

        for card_name in ("Sol Ring", "Arcane Signet", "Goblin Bombardment"):
            found = instance_store.list_instances(self.r, card_name=card_name)
            self.assertEqual(len(found), 1, f"expected exactly one instance for {card_name}")
            self.assertEqual(found[0]["ownership_status"], "not_owned")
            self.assertEqual(found[0]["considered_for_deck"], reg_id)

    def test_upsert_deck_creates_wishlist_placeholders_for_digital_deck(self):
        deck = registry.upsert_deck(self.r, _normalized_deck(source_id="202"), default_status="digital")
        self.assertEqual(deck["status"], "digital")
        reg_id = registry.registry_id_of(deck)

        found = instance_store.list_instances(self.r, card_name="Sol Ring", ownership_status="not_owned")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["considered_for_deck"], reg_id)

    def test_inventory_shows_placeholders_immediately_after_import(self):
        # Acceptance criterion phrased as: /inventory (not_owned listing) reflects
        # the import without any separate sync/bind step.
        registry.upsert_deck(self.r, _normalized_deck(), default_status="testing")
        not_owned = instance_store.list_instances(self.r, ownership_status="not_owned")
        names = {inst["card_name"] for inst in not_owned}
        self.assertEqual(names, {"Sol Ring", "Arcane Signet", "Goblin Bombardment"})


class TestPromotionBindsPlaceholdersWithoutDuplicating(unittest.TestCase):
    """2) Promoting/marking a deck physical (registry.set_status) binds and
    promotes existing not_owned placeholders instead of creating duplicates."""

    def setUp(self):
        self.r = FakeRedis()
        self.deck = registry.upsert_deck(self.r, _normalized_deck(), default_status="testing")
        self.reg_id = registry.registry_id_of(self.deck)

    def test_set_status_to_physical_promotes_existing_placeholders(self):
        placeholders_before = {
            inst["card_name"]: inst["id"]
            for inst in instance_store.list_instances(self.r, ownership_status="not_owned")
        }
        self.assertEqual(len(placeholders_before), 3)

        result = registry.set_status(self.r, self.reg_id, "physical")
        self.assertEqual(result["deck"]["status"], "physical")
        self.assertIsNotNone(result["auto_bind"])

        for card_name, placeholder_id in placeholders_before.items():
            all_instances = instance_store.list_instances(self.r, card_name=card_name)
            # No duplication: still exactly one instance for this card.
            self.assertEqual(
                len(all_instances), 1,
                f"expected exactly one instance for {card_name} after promotion",
            )
            promoted = all_instances[0]
            # Same underlying instance was promoted in place, not replaced.
            self.assertEqual(promoted["id"], placeholder_id)
            self.assertEqual(promoted["ownership_status"], "in_deck")

        # No leftover not_owned placeholders for this deck's cards.
        self.assertEqual(len(instance_store.list_instances(self.r, ownership_status="not_owned")), 0)

    def test_set_status_to_physical_reports_zero_new_creations(self):
        result = registry.set_status(self.r, self.reg_id, "physical")
        for card_name, report in result["auto_bind"]["cards"].items():
            self.assertEqual(
                report["created"], 0,
                f"{card_name} should have been bound from an existing placeholder, not newly created",
            )
        self.assertEqual(result["auto_bind"]["shortfalls"], [])

    def test_promotion_does_not_disturb_cards_already_owned_elsewhere(self):
        # A card already in_collection (unrelated to this deck's import) must be
        # picked up as the physical bind source in preference to leaving a
        # not_owned placeholder behind, and must not be duplicated either.
        owned = instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        owned_id = owned["id"]
        # Two Sol Ring instances now exist: the wishlist placeholder from setUp's
        # import, and this manually-owned one.
        self.assertEqual(len(instance_store.list_instances(self.r, card_name="Sol Ring")), 2)

        registry.set_status(self.r, self.reg_id, "physical")

        sol_ring_instances = instance_store.list_instances(self.r, card_name="Sol Ring")
        # Still just two total instances (no new ones created); this deck only
        # needs quantity=1 so only one gets bound in_deck.
        self.assertEqual(len(sol_ring_instances), 2)
        in_deck = [i for i in sol_ring_instances if i["ownership_status"] == "in_deck"]
        self.assertEqual(len(in_deck), 1)
        # The already-owned instance specifically must be the one promoted,
        # not the wishlist placeholder from setUp's import.
        self.assertEqual(
            in_deck[0]["id"], owned_id,
            "expected the pre-existing owned Sol Ring instance to be bound in_deck, "
            "not the wishlist placeholder",
        )


class TestReimportCreatesZeroNewInstances(unittest.TestCase):
    """3) Re-importing a deck whose cards already exist in inventory creates
    zero new instances, whether those cards are wishlist placeholders or
    already fully owned."""

    def setUp(self):
        self.r = FakeRedis()

    def test_reimport_after_initial_import_creates_no_new_instances(self):
        normalized = _normalized_deck()
        registry.upsert_deck(self.r, normalized, default_status="testing")
        before = {
            card_name: len(instance_store.list_instances(self.r, card_name=card_name))
            for card_name in ("Sol Ring", "Arcane Signet", "Goblin Bombardment")
        }

        # Re-import: same provider payload synced again (e.g. periodic sync).
        registry.upsert_deck(self.r, normalized, default_status="testing")

        after = {
            card_name: len(instance_store.list_instances(self.r, card_name=card_name))
            for card_name in ("Sol Ring", "Arcane Signet", "Goblin Bombardment")
        }
        self.assertEqual(before, after)
        self.assertEqual(set(before.values()), {1})

    def test_reimport_of_already_owned_deck_creates_zero_new_instances(self):
        # Simulate cards the user already owns, acquired independently of any
        # deck import (e.g. bought loose, or bound to a different deck).
        instance_store.create_instance(self.r, card_name="Sol Ring", ownership_status="in_collection")
        instance_store.create_instance(self.r, card_name="Arcane Signet", ownership_status="in_collection")
        instance_store.create_instance(self.r, card_name="Goblin Bombardment", ownership_status="in_collection")

        total_before = sum(
            len(instance_store.list_instances(self.r, card_name=n))
            for n in ("Sol Ring", "Arcane Signet", "Goblin Bombardment")
        )
        self.assertEqual(total_before, 3)

        registry.upsert_deck(self.r, _normalized_deck(), default_status="testing")

        total_after = sum(
            len(instance_store.list_instances(self.r, card_name=n))
            for n in ("Sol Ring", "Arcane Signet", "Goblin Bombardment")
        )
        self.assertEqual(total_after, 3, "cards already in inventory must not get wishlist duplicates")
        # None of the pre-existing instances should have been turned into
        # not_owned placeholders or otherwise mutated.
        for name in ("Sol Ring", "Arcane Signet", "Goblin Bombardment"):
            inst = instance_store.list_instances(self.r, card_name=name)[0]
            self.assertEqual(inst["ownership_status"], "in_collection")

    def test_reimport_after_physical_promotion_creates_no_new_instances(self):
        deck = registry.upsert_deck(self.r, _normalized_deck(), default_status="testing")
        reg_id = registry.registry_id_of(deck)
        registry.set_status(self.r, reg_id, "physical")

        total_before = sum(
            len(instance_store.list_instances(self.r, card_name=n))
            for n in ("Sol Ring", "Arcane Signet", "Goblin Bombardment")
        )
        self.assertEqual(total_before, 3)

        # Re-import (e.g. a resync) of the now-physical deck must not create
        # new wishlist instances for cards already bound in_deck.
        registry.upsert_deck(self.r, _normalized_deck(), default_status="testing")

        total_after = sum(
            len(instance_store.list_instances(self.r, card_name=n))
            for n in ("Sol Ring", "Arcane Signet", "Goblin Bombardment")
        )
        self.assertEqual(total_after, 3)
        for name in ("Sol Ring", "Arcane Signet", "Goblin Bombardment"):
            inst = instance_store.list_instances(self.r, card_name=name)[0]
            self.assertEqual(inst["ownership_status"], "in_deck")


if __name__ == "__main__":
    unittest.main()
