import json
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from app import app
import instances as instance_store
from instances import InstanceError
import registry


class FakeRedis:
    """In-memory Redis stand-in for unit and integration testing."""

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
            self.sets.pop(k, None)

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


class TestResolveResyncRemovalHelper(unittest.TestCase):
    """Unit tests for instances.resolve_resync_removal helper function."""

    def setUp(self):
        self.r = FakeRedis()
        self.registry_id = "archidekt:100"

        # Set up a physical deck in the registry
        deck = {
            "id": 100,
            "name": "Commander Deck",
            "registry_id": self.registry_id,
            "source": "archidekt",
            "source_id": "100",
            "status": "physical",
            "cards": [{"name": "Sol Ring", "quantity": 1}],
        }
        self.r.set("decks", json.dumps([deck]))
        self.r.set("deck_status:100", "physical")

        # Create an instance bound to this deck and flag with pending_removal
        self.instance = instance_store.create_instance(
            self.r,
            card_name="Sol Ring",
            ownership_status="in_deck",
            deck_id=self.registry_id,
            deck_name="Commander Deck",
        )
        instance_store.set_pending_removal(self.r, self.instance["id"], self.registry_id)

    def test_keep_transitions_to_in_collection_and_clears_pending(self):
        """Action 'keep' transitions in_deck -> in_collection, clears deck_id, clears pending_removal."""
        updated = instance_store.resolve_resync_removal(
            self.r,
            self.instance["id"],
            action="keep",
            deck_id=self.registry_id,
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["ownership_status"], "in_collection")
        self.assertIsNone(updated.get("deck_id"))
        self.assertEqual(updated.get("deck_name"), "")
        self.assertIsNone(updated.get("pending_removal"))

        # Verify index was cleaned up
        flagged = instance_store.list_pending_removal(self.r, self.registry_id)
        self.assertEqual(flagged, [])

    def test_save_alias_transitions_to_in_collection(self):
        """Action 'save' is a synonym for 'keep'."""
        updated = instance_store.resolve_resync_removal(
            self.r,
            self.instance["id"],
            action="save",
            deck_id=self.registry_id,
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["ownership_status"], "in_collection")
        self.assertIsNone(updated.get("deck_id"))
        self.assertIsNone(updated.get("pending_removal"))

    def test_toss_transitions_to_not_owned_and_clears_pending(self):
        """Action 'toss' transitions in_deck -> not_owned, clears deck_id, clears pending_removal."""
        updated = instance_store.resolve_resync_removal(
            self.r,
            self.instance["id"],
            action="toss",
            deck_id=self.registry_id,
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated["ownership_status"], "not_owned")
        self.assertIsNone(updated.get("deck_id"))
        self.assertIsNone(updated.get("pending_removal"))

        # Verify index was cleaned up
        flagged = instance_store.list_pending_removal(self.r, self.registry_id)
        self.assertEqual(flagged, [])

    def test_bypasses_physical_deck_lock(self):
        """Resolution succeeds even though deck status is 'physical'."""
        self.assertTrue(registry.is_deck_physical(self.r, self.registry_id))
        updated = instance_store.resolve_resync_removal(
            self.r,
            self.instance["id"],
            action="keep",
        )
        self.assertEqual(updated["ownership_status"], "in_collection")

    def test_raises_if_instance_not_found(self):
        with self.assertRaises(InstanceError):
            instance_store.resolve_resync_removal(self.r, "nonexistent-id", action="keep")

    def test_raises_if_not_flagged(self):
        # Create unflagged instance
        inst2 = instance_store.create_instance(
            self.r,
            card_name="Arcane Signet",
            ownership_status="in_deck",
            deck_id=self.registry_id,
        )
        with self.assertRaises(InstanceError):
            instance_store.resolve_resync_removal(self.r, inst2["id"], action="keep")

    def test_raises_if_deck_mismatch(self):
        with self.assertRaises(InstanceError):
            instance_store.resolve_resync_removal(
                self.r,
                self.instance["id"],
                action="keep",
                deck_id="archidekt:other-deck",
            )

    def test_raises_for_invalid_action(self):
        with self.assertRaises(InstanceError):
            instance_store.resolve_resync_removal(
                self.r,
                self.instance["id"],
                action="destroy",
            )


class TestResyncReviewAPI(unittest.TestCase):
    """FastAPI TestClient integration tests for GET and POST resync-review endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        self.r = FakeRedis()
        self.registry_id = "archidekt:42"

        # Mock global redis in app
        self.redis_patcher_app = patch("app.r", self.r)
        self.redis_patcher_app.start()

        # Seed a physical deck with 2 cards
        self.deck = {
            "id": 42,
            "name": "Urza High Artificer",
            "registry_id": self.registry_id,
            "source": "archidekt",
            "source_id": "42",
            "status": "physical",
            "cards": [
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Mox Diamond", "quantity": 1},
            ],
        }
        self.r.set("decks", json.dumps([self.deck]))
        self.r.set("deck_status:42", "physical")

        # Create bound instance for Sol Ring
        self.sol_ring_inst = instance_store.create_instance(
            self.r,
            card_name="Sol Ring",
            ownership_status="in_deck",
            deck_id=self.registry_id,
            deck_name="Urza High Artificer",
        )
        # Create bound instance for removed card (Arcane Signet)
        self.signet_inst = instance_store.create_instance(
            self.r,
            card_name="Arcane Signet",
            ownership_status="in_deck",
            deck_id=self.registry_id,
            deck_name="Urza High Artificer",
        )
        # Flag Arcane Signet with pending_removal
        instance_store.set_pending_removal(self.r, self.signet_inst["id"], self.registry_id)

    def tearDown(self):
        self.redis_patcher_app.stop()

    def test_get_resync_review_success(self):
        """GET /api/decks/{id}/resync-review returns pending removal and unbound cards."""
        response = self.client.get(f"/api/decks/{self.registry_id}/resync-review")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["deck_id"], self.registry_id)

        # Check pending removal list
        self.assertEqual(len(data["pending_removal"]), 1)
        self.assertEqual(data["pending_removal"][0]["id"], self.signet_inst["id"])
        self.assertEqual(data["pending_removal"][0]["card_name"], "Arcane Signet")

        # Check unbound cards (Mox Diamond is in deck list but has no bound instance)
        unbound_names = data["unbound_card_names"]
        self.assertIn("Mox Diamond", unbound_names)
        self.assertNotIn("Sol Ring", unbound_names)

    def test_get_resync_review_resolves_numeric_id(self):
        """GET /api/decks/42/resync-review resolves numeric deck id correctly."""
        response = self.client.get("/api/decks/42/resync-review")
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["pending_removal"]), 1)
        self.assertEqual(data["pending_removal"][0]["id"], self.signet_inst["id"])

    def test_get_resync_review_deck_not_found(self):
        """GET returns 404 when deck does not exist."""
        response = self.client.get("/api/decks/nonexistent:999/resync-review")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.assertIn("Deck not found", response.json()["error"])

    def test_post_resync_review_keep(self):
        """POST with action 'keep' resolves instance to in_collection."""
        response = self.client.post(
            f"/api/decks/{self.registry_id}/resync-review/{self.signet_inst['id']}",
            json={"action": "keep"},
        )
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["action"], "keep")
        self.assertEqual(data["instance"]["ownership_status"], "in_collection")
        self.assertIsNone(data["instance"].get("deck_id"))
        self.assertIsNone(data["instance"].get("pending_removal"))

        # Verify instance in Redis is updated
        updated = instance_store.get_instance(self.r, self.signet_inst["id"])
        self.assertEqual(updated["ownership_status"], "in_collection")
        self.assertIsNone(updated.get("deck_id"))
        self.assertIsNone(updated.get("pending_removal"))

        # GET should now return empty pending_removal
        get_resp = self.client.get(f"/api/decks/{self.registry_id}/resync-review")
        self.assertEqual(get_resp.json()["pending_removal"], [])

    def test_post_resync_review_save_alias(self):
        """POST with action 'save' resolves instance to in_collection."""
        response = self.client.post(
            f"/api/decks/{self.registry_id}/resync-review/{self.signet_inst['id']}",
            json={"action": "save"},
        )
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["instance"]["ownership_status"], "in_collection")
        self.assertIsNone(data["instance"].get("pending_removal"))

    def test_post_resync_review_toss(self):
        """POST with action 'toss' resolves instance to not_owned."""
        response = self.client.post(
            f"/api/decks/{self.registry_id}/resync-review/{self.signet_inst['id']}",
            json={"action": "toss"},
        )
        self.assertEqual(response.status_code, 200)

        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["action"], "toss")
        self.assertEqual(data["instance"]["ownership_status"], "not_owned")
        self.assertIsNone(data["instance"].get("deck_id"))
        self.assertIsNone(data["instance"].get("pending_removal"))

        # Verify instance in Redis is updated
        updated = instance_store.get_instance(self.r, self.signet_inst["id"])
        self.assertEqual(updated["ownership_status"], "not_owned")
        self.assertIsNone(updated.get("pending_removal"))

    def test_post_resync_review_deck_not_found(self):
        """POST returns 404 when deck does not exist."""
        response = self.client.post(
            f"/api/decks/nonexistent:999/resync-review/{self.signet_inst['id']}",
            json={"action": "keep"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.assertIn("Deck not found", response.json()["error"])

    def test_post_resync_review_instance_not_found(self):
        """POST returns 404 when instance does not exist."""
        response = self.client.post(
            f"/api/decks/{self.registry_id}/resync-review/nonexistent-instance",
            json={"action": "keep"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.assertIn("Instance not found", response.json()["error"])

    def test_post_resync_review_instance_lacks_pending_removal(self):
        """POST returns 404 when instance does not have pending_removal marker."""
        response = self.client.post(
            f"/api/decks/{self.registry_id}/resync-review/{self.sol_ring_inst['id']}",
            json={"action": "keep"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.assertIn("pending_removal marker", response.json()["error"])

    def test_post_resync_review_instance_belongs_to_different_deck(self):
        """POST returns 404 when instance is flagged for a different deck."""
        # Create second deck
        other_deck = {
            "id": 99,
            "name": "Other Deck",
            "registry_id": "archidekt:99",
            "source": "archidekt",
            "source_id": "99",
            "status": "physical",
            "cards": [],
        }
        decks = json.loads(self.r.get("decks"))
        decks.append(other_deck)
        self.r.set("decks", json.dumps(decks))

        # Create instance flagged for other deck
        other_inst = instance_store.create_instance(
            self.r,
            card_name="Mana Crypt",
            ownership_status="in_deck",
            deck_id="archidekt:99",
            deck_name="Other Deck",
        )
        instance_store.set_pending_removal(self.r, other_inst["id"], "archidekt:99")

        # Try to resolve other_inst via our deck
        response = self.client.post(
            f"/api/decks/{self.registry_id}/resync-review/{other_inst['id']}",
            json={"action": "keep"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])
        self.assertIn("does not belong to this deck", response.json()["error"])

    def test_post_resync_review_invalid_action(self):
        """POST returns 400 when action is invalid."""
        response = self.client.post(
            f"/api/decks/{self.registry_id}/resync-review/{self.signet_inst['id']}",
            json={"action": "disintegrate"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])
        self.assertIn("Invalid action", response.json()["error"])


if __name__ == "__main__":
    unittest.main()
