"""
End-to-end integration tests for the deck manage flow (categories, add-card,
remove-card) driven through the real FastAPI routes, real registry.py /
instances.py logic, and a real fakeredis backend (no mocks) — as opposed to
tests/test_routes.py, tests/test_remove_card_from_deck.py, and
tests/test_deck_categories.py, which exercise the same endpoints but stub out
registry/instance_store so they don't prove the pieces actually persist
correctly together.

Covers (per task spec):
  - category add/remove persistence (round-trips through real Redis-shaped
    storage, not a mock assertion)
  - add-card creates a new instance when none exists, or reuses an
    in_collection one, and flips ownership_status to in_deck
  - remove-card clears deck_id and returns the instance to in_collection
  - physical-deck lock bypass on the manage endpoints (add/remove-card)
    specifically, while a direct instance PATCH against the same physical
    deck stays locked and is rejected
  - duplicate-instance removal: when two instances of the same card are
    bound to a deck, DELETE against a specific instance_id only ever
    touches that instance, not its sibling

Run: pytest tests/test_deck_manage_integration.py -q
"""
import json
import os
import sys
import unittest

import fakeredis
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import registry
import instances as instance_store


class DeckManageIntegrationTestCase(unittest.TestCase):
    """Base: swaps app.r for a fresh fakeredis instance per test and seeds
    one deck via registry.upsert_deck (the real deck-creation path)."""

    def setUp(self):
        self.fake_r = fakeredis.FakeRedis(decode_responses=True)
        self._orig_r = app_module.r
        app_module.r = self.fake_r
        self.client = TestClient(app_module.app)

        self.deck = registry.upsert_deck(
            self.fake_r,
            {
                "source": "archidekt",
                "source_id": "999",
                "name": "Atraxa Superfriends",
                "color": "WUBG",
                "commanders": ["Atraxa, Praetors' Voice"],
                "commander_uids": [],
                "folder": "Imported",
                "description": "",
                "cards": [{"name": "Rhystic Study", "quantity": 1}],
                "url": "",
            },
            default_status="testing",
        )
        self.registry_id = registry.registry_id_of(self.deck)

    def tearDown(self):
        app_module.r = self._orig_r


class TestCategoryPersistence(DeckManageIntegrationTestCase):
    def test_add_category_persists_across_requests(self):
        resp = self.client.post(
            f"/api/decks/{self.registry_id}/categories",
            json={"card_name": "Rhystic Study", "action": "add", "tag": "Draw"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["categories"], ["Draw"])

        # Persisted in Redis directly, and visible on a fresh GET — proves
        # this isn't just an in-memory response echo.
        raw = self.fake_r.get(f"deck_categories:{self.registry_id}")
        self.assertEqual(json.loads(raw), {"Rhystic Study": ["Draw"]})

        get_resp = self.client.get(f"/api/decks/{self.registry_id}/categories")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["categories"], {"Rhystic Study": ["Draw"]})

    def test_remove_category_cleanly_disassociates(self):
        self.client.post(
            f"/api/decks/{self.registry_id}/categories",
            json={"card_name": "Rhystic Study", "action": "add", "tag": "Draw"},
        )
        self.client.post(
            f"/api/decks/{self.registry_id}/categories",
            json={"card_name": "Rhystic Study", "action": "add", "tag": "Utility"},
        )

        resp = self.client.post(
            f"/api/decks/{self.registry_id}/categories",
            json={"card_name": "Rhystic Study", "action": "remove", "tag": "Draw"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["categories"], ["Utility"])

        # Removing the last tag should drop the card entry entirely rather
        # than leaving an empty list dangling.
        self.client.post(
            f"/api/decks/{self.registry_id}/categories",
            json={"card_name": "Rhystic Study", "action": "remove", "tag": "Utility"},
        )
        raw = self.fake_r.get(f"deck_categories:{self.registry_id}")
        self.assertEqual(json.loads(raw), {})


class TestAddCardLifecycle(DeckManageIntegrationTestCase):
    def test_add_card_creates_new_instance_and_flips_status(self):
        resp = self.client.post(
            f"/api/decks/{self.registry_id}/cards",
            json={"card_name": "Sol Ring"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["success"])
        instance = data["instance"]
        self.assertEqual(instance["ownership_status"], "in_deck")
        self.assertEqual(str(instance["deck_id"]), str(self.registry_id))

        # Real persistence check, not a mock assertion.
        stored = instance_store.get_instance(self.fake_r, instance["id"])
        self.assertEqual(stored["ownership_status"], "in_deck")

        # Decklist got the manual add appended.
        deck = registry.find_deck(self.fake_r, self.registry_id)
        names = [c["name"] for c in deck["cards"]]
        self.assertIn("Sol Ring", names)

    def test_add_card_reuses_existing_in_collection_instance(self):
        pre_existing = instance_store.create_instance(
            self.fake_r, card_name="Arcane Signet", ownership_status="in_collection"
        )

        resp = self.client.post(
            f"/api/decks/{self.registry_id}/cards",
            json={"card_name": "Arcane Signet"},
        )
        self.assertEqual(resp.status_code, 200)
        instance = resp.json()["instance"]

        # Same instance id was reused, not a new one minted.
        self.assertEqual(instance["id"], pre_existing["id"])
        self.assertEqual(instance["ownership_status"], "in_deck")

        all_arcane = instance_store.list_instances(self.fake_r, card_name="Arcane Signet")
        self.assertEqual(len(all_arcane), 1)


class TestRemoveCardLifecycle(DeckManageIntegrationTestCase):
    def test_remove_card_clears_deck_id_and_returns_to_in_collection(self):
        add_resp = self.client.post(
            f"/api/decks/{self.registry_id}/cards",
            json={"card_name": "Sol Ring"},
        )
        instance_id = add_resp.json()["instance"]["id"]

        del_resp = self.client.delete(f"/api/decks/{self.registry_id}/cards/{instance_id}")
        self.assertEqual(del_resp.status_code, 200)
        removed = del_resp.json()["instance"]
        self.assertEqual(removed["ownership_status"], "in_collection")
        self.assertIsNone(removed["deck_id"])

        stored = instance_store.get_instance(self.fake_r, instance_id)
        self.assertEqual(stored["ownership_status"], "in_collection")
        self.assertIsNone(stored["deck_id"])

        # No longer indexed under the deck.
        deck_instances = instance_store.list_instances(self.fake_r, deck_id=self.registry_id)
        self.assertNotIn(instance_id, [i["id"] for i in deck_instances])

    def test_duplicate_instance_removal_picks_the_right_instance_id(self):
        """Two Sol Ring copies bound to the deck; removing one by instance_id
        must leave the other one in_deck untouched."""
        first = instance_store.create_instance(self.fake_r, card_name="Sol Ring", ownership_status="in_collection")
        second = instance_store.create_instance(self.fake_r, card_name="Sol Ring", ownership_status="in_collection")
        first = instance_store.transition_status(
            self.fake_r, first["id"], new_status="in_deck",
            deck_id=self.registry_id, deck_name=self.deck["name"],
        )
        second = instance_store.transition_status(
            self.fake_r, second["id"], new_status="in_deck",
            deck_id=self.registry_id, deck_name=self.deck["name"],
        )

        del_resp = self.client.delete(f"/api/decks/{self.registry_id}/cards/{second['id']}")
        self.assertEqual(del_resp.status_code, 200)
        self.assertEqual(del_resp.json()["instance_id"], second["id"])

        untouched = instance_store.get_instance(self.fake_r, first["id"])
        self.assertEqual(untouched["ownership_status"], "in_deck")
        self.assertEqual(str(untouched["deck_id"]), str(self.registry_id))

        removed = instance_store.get_instance(self.fake_r, second["id"])
        self.assertEqual(removed["ownership_status"], "in_collection")
        self.assertIsNone(removed["deck_id"])

        remaining_in_deck = instance_store.list_instances(self.fake_r, deck_id=self.registry_id)
        self.assertEqual([i["id"] for i in remaining_in_deck], [first["id"]])


class TestPhysicalLockBypass(DeckManageIntegrationTestCase):
    def setUp(self):
        super().setUp()
        registry.set_status(self.fake_r, self.registry_id, "physical")
        self.deck = registry.find_deck(self.fake_r, self.registry_id)
        self.assertEqual(self.deck["status"], "physical")

    def test_manage_add_card_bypasses_physical_lock(self):
        """add-card on a physical deck must succeed — it's the sanctioned
        exception to the lock."""
        resp = self.client.post(
            f"/api/decks/{self.registry_id}/cards",
            json={"card_name": "Command Tower"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["instance"]["ownership_status"], "in_deck")

    def test_manage_remove_card_bypasses_physical_lock(self):
        """remove-card must also succeed on a physical deck via the manage
        endpoint's explicit bypass. Bind the instance via the manage
        add-card endpoint (which keys deck_id consistently off
        registry_id_of) rather than relying on auto_bind_physical, which
        keys off the deck's raw numeric id and would give remove-card a
        deck_id it can't match against the registry_id URL segment — that
        mismatch is a pre-existing quirk outside this task's scope.
        """
        add_resp = self.client.post(
            f"/api/decks/{self.registry_id}/cards",
            json={"card_name": "Command Tower"},
        )
        self.assertEqual(add_resp.status_code, 200)
        instance_id = add_resp.json()["instance"]["id"]

        resp = self.client.delete(f"/api/decks/{self.registry_id}/cards/{instance_id}")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["instance"]["ownership_status"], "in_collection")

    def test_direct_instance_patch_stays_locked_on_physical_deck(self):
        """The normal PATCH /api/instances/{id} path does NOT get the
        bypass — moving a physically-locked instance out of in_deck through
        it must be rejected, unlike the manage endpoints above."""
        bound = instance_store.list_instances(self.fake_r, deck_id=self.deck["id"])
        self.assertTrue(bound)
        instance_id = bound[0]["id"]

        resp = self.client.patch(
            f"/api/instances/{instance_id}",
            json={"ownership_status": "in_collection"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["success"])
        self.assertIn("physical deck", resp.json()["error"])

        # Instance is untouched — still in_deck, still bound.
        stored = instance_store.get_instance(self.fake_r, instance_id)
        self.assertEqual(stored["ownership_status"], "in_deck")
        self.assertEqual(str(stored["deck_id"]), str(self.deck["id"]))


if __name__ == "__main__":
    unittest.main()
