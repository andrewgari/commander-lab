from fastapi.testclient import TestClient
import os
import json
import unittest
from unittest.mock import patch, MagicMock, ANY

# Import FastAPI app
from app import app
from instances import InstanceError
import tags as tag_store
import registry as _real_registry

_REG = {"registry_id_of.side_effect": _real_registry.registry_id_of}

class TestAppRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_root_route(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_decks_route(self):
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)

    def test_inventory_route(self):
        response = self.client.get("/inventory")
        self.assertEqual(response.status_code, 200)

    def test_tags_route(self):
        response = self.client.get("/tags")
        self.assertEqual(response.status_code, 200)

    def test_version_route(self):
        response = self.client.get("/api/version")
        self.assertEqual(response.status_code, 200)
        self.assertIn("version", response.json())

    def test_instances_route(self):
        response = self.client.get("/instances")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_deck_view_route(self):
        response = self.client.get("/deck/TestDeck")
        self.assertEqual(response.status_code, 200)

    @patch("app.registry")
    def test_deck_manage_route(self, mock_registry):
        mock_registry.list_decks.return_value = [
            {"id": 1, "name": "TestDeck", "registry_id": "archidekt:1"}
        ]
        mock_registry.registry_id_of.return_value = "archidekt:1"
        response = self.client.get("/deck/TestDeck/manage")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    @patch("app.registry")
    def test_deck_manage_route_not_found(self, mock_registry):
        mock_registry.list_decks.return_value = []
        response = self.client.get("/deck/UnknownDeck/manage")
        self.assertEqual(response.status_code, 404)
        self.assertIn("text/html", response.headers["content-type"])

    @patch("app.r")
    def test_api_decks(self, mock_redis):
        mock_redis.get.return_value = '[{"id": 1, "name": "Deck A"}]'
        response = self.client.get("/api/decks")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("decks", data)
        self.assertEqual(len(data["decks"]), 1)

    @patch("app.r")
    def test_api_tags(self, mock_redis):
        mock_redis.get.return_value = '{"Card A": ["Ramp", "Draw"]}'
        response = self.client.get("/api/tags")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("tags", data)
        self.assertEqual(data["tags"], ["Draw", "Ramp"])


class TestAddCardToDeck(unittest.TestCase):
    """Integration tests for POST /api/decks/{id}/cards, mocking registry.find_deck
    and the instance_store calls the endpoint delegates to (per the pattern used
    by tests/test_routes.py's @patch("app.r") tests)."""

    def setUp(self):
        self.client = TestClient(app)
        self.deck = {
            "id": 42,
            "name": "Atraxa Superfriends",
            "registry_id": "archidekt:42",
            "status": "physical",
            "cards": [],
        }

    @patch("app.registry")
    @patch("app.instance_store")
    def test_reuses_existing_in_collection_instance(self, mock_store, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        mock_registry.registry_id_of.return_value = "archidekt:42"
        existing_instance = {"id": "inst-1", "card_name": "Sol Ring", "ownership_status": "in_collection"}
        mock_store.list_instances.return_value = [existing_instance]
        mock_store.transition_status.return_value = {**existing_instance, "ownership_status": "in_deck", "deck_id": "archidekt:42"}

        response = self.client.post("/api/decks/archidekt:42/cards", json={"card_name": "Sol Ring"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        mock_store.create_instance.assert_not_called()
        mock_store.transition_status.assert_called_once()
        _, kwargs = mock_store.transition_status.call_args
        self.assertEqual(kwargs["new_status"], "in_deck")
        self.assertEqual(kwargs["deck_id"], "archidekt:42")
        self.assertTrue(kwargs["_allow_physical_lock_bypass"])
        mock_registry.add_card_to_decklist.assert_called_once_with(ANY, "archidekt:42", "Sol Ring")

    @patch("app.registry")
    @patch("app.instance_store")
    def test_creates_new_instance_when_none_in_collection(self, mock_store, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        mock_registry.registry_id_of.return_value = "archidekt:42"
        mock_store.list_instances.return_value = []
        new_instance = {"id": "inst-2", "card_name": "Arcane Signet", "ownership_status": "in_collection"}
        mock_store.create_instance.return_value = new_instance
        mock_store.transition_status.return_value = {**new_instance, "ownership_status": "in_deck", "deck_id": "archidekt:42"}

        response = self.client.post("/api/decks/archidekt:42/cards", json={"card_name": "Arcane Signet"})

        self.assertEqual(response.status_code, 200)
        mock_store.create_instance.assert_called_once()
        self.assertEqual(mock_store.create_instance.call_args.kwargs["card_name"], "Arcane Signet")
        self.assertEqual(mock_store.create_instance.call_args.kwargs["ownership_status"], "in_collection")
        mock_store.transition_status.assert_called_once_with(
            ANY, "inst-2",
            new_status="in_deck", deck_id="archidekt:42", deck_name="Atraxa Superfriends",
            _allow_physical_lock_bypass=True,
        )

    @patch("app.registry")
    @patch("app.instance_store")
    def test_bypasses_physical_lock(self, mock_store, mock_registry):
        """Deck is status=physical; the endpoint must still succeed by passing
        _allow_physical_lock_bypass=True through to transition_status, rather
        than letting the normal is_deck_physical guard reject the move."""
        physical_deck = {**self.deck, "status": "physical"}
        mock_registry.find_deck.return_value = physical_deck
        mock_registry.registry_id_of.return_value = "archidekt:42"
        existing_instance = {"id": "inst-1", "card_name": "Sol Ring", "ownership_status": "in_collection"}
        mock_store.list_instances.return_value = [existing_instance]
        mock_store.transition_status.return_value = {**existing_instance, "ownership_status": "in_deck"}

        response = self.client.post("/api/decks/archidekt:42/cards", json={"card_name": "Sol Ring"})

        self.assertEqual(response.status_code, 200)
        kwargs = mock_store.transition_status.call_args.kwargs
        self.assertTrue(kwargs["_allow_physical_lock_bypass"])

    @patch("app.registry")
    def test_deck_not_found_returns_404(self, mock_registry):
        mock_registry.find_deck.return_value = None
        response = self.client.post("/api/decks/does-not-exist/cards", json={"card_name": "Sol Ring"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    @patch("app.registry")
    def test_missing_card_name_returns_400(self, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        response = self.client.post("/api/decks/archidekt:42/cards", json={})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    @patch("app.registry")
    @patch("app.instance_store")
    def test_instance_error_returns_400(self, mock_store, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        mock_registry.registry_id_of.return_value = "archidekt:42"
        mock_store.list_instances.return_value = []
        mock_store.create_instance.side_effect = InstanceError("invalid ownership_status: bogus")

        response = self.client.post("/api/decks/archidekt:42/cards", json={"card_name": "Sol Ring"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])


class TestAutoTagEndpoints(unittest.TestCase):
    """Tests for the auto-tag and tag-management API endpoints."""

    def setUp(self):
        self.client = TestClient(app)
        # Clean tag-related keys before each test (import the module-level redis client)
        import app as app_mod
        self.r = app_mod.r
        for pattern in ["deck_tags*", "deck_card_tags*"]:
            for key in self.r.keys(pattern):
                self.r.delete(key)

    @patch("app.registry", **_REG)
    def test_auto_tag_success(self, mock_registry):
        mock_registry.find_deck.return_value = {
            "id": 42,
            "name": "Krenko Deck",
            "registry_id": "archidekt:42",
            "color_identity": ["R"],
            "color_identity_size": 1,
            "commanders": [{
                "name": "Krenko, Mob Boss",
                "type_line": "Legendary Creature — Goblin Warrior",
                "oracle_text": "{T}: Create X 1/1 red Goblin creature tokens, where X is the number of Goblins you control.",
                "cmc": 4,
            }],
            "cards": [
                {"name": "Sol Ring", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.", "cmc": 1},
                {"name": "Skirk Prospector", "type_line": "Creature — Goblin", "oracle_text": "Sacrifice a Goblin: Add {R}.", "cmc": 1},
                {"name": "Mountain", "type_line": "Basic Land — Mountain", "oracle_text": "({T}: Add {R}.)", "cmc": 0},
            ],
        }
        response = self.client.post("/api/decks/archidekt:42/auto-tag", json={})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertEqual(data.get("deck_id"), "archidekt:42")
        self.assertIn("card_tags", data)

        # Non-/api path alias
        alias_response = self.client.post("/decks/archidekt:42/auto-tag", json={})
        self.assertEqual(alias_response.status_code, 200)

    @patch("app.registry", **_REG)
    def test_auto_tag_preserves_user_tags(self, mock_registry):
        mock_registry.find_deck.return_value = {
            "id": 42,
            "name": "Test Deck",
            "registry_id": "archidekt:42",
            "color_identity": ["R"],
            "commanders": [],
            "cards": [
                {"name": "Sol Ring", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.", "cmc": 1},
            ],
        }
        # Pre-populate a user tag on Sol Ring and on deck
        tag_store.add_deck_tag(self.r, "archidekt:42", "Custom Deck Tag", source="user")
        tag_store.add_card_tag_to_deck(self.r, "archidekt:42", "Sol Ring", "Custom Sol Tag", source="user")

        # Run auto-tag
        response = self.client.post("/api/decks/archidekt:42/auto-tag", json={})
        self.assertEqual(response.status_code, 200)

        # Check that user tags are preserved
        deck_tags = tag_store.get_deck_tags(self.r, "archidekt:42")
        self.assertTrue(any(t["tag"] == "Custom Deck Tag" and t["source"] == "user" for t in deck_tags))

        sol_tags = tag_store.get_card_tags_for_deck(self.r, "archidekt:42", "Sol Ring")
        self.assertTrue(any(t["tag"] == "Custom Sol Tag" and t["source"] == "user" for t in sol_tags))
        self.assertTrue(any(t["tag"] == "ramp" and t["source"] == "auto" for t in sol_tags))

    @patch("app.registry", **_REG)
    def test_auto_tag_deck_not_found(self, mock_registry):
        mock_registry.find_deck.return_value = None
        response = self.client.post("/api/decks/nonexistent/auto-tag", json={})
        self.assertEqual(response.status_code, 404)

    @patch("app.registry", **_REG)
    def test_get_deck_tags_empty(self, mock_registry):
        mock_registry.find_deck.return_value = {"id": 42, "name": "Test Deck"}
        response = self.client.get("/api/decks/archidekt:42/tags")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["tags"], [])

    @patch("app.registry", **_REG)
    def test_add_deck_tag(self, mock_registry):
        mock_registry.find_deck.return_value = {"id": 42, "name": "Test Deck"}
        response = self.client.post("/api/decks/archidekt:42/tags", json={"tag": "Ramp", "source": "user"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tag"]["tag"], "Ramp")
        self.assertEqual(data["tag"]["source"], "user")

    @patch("app.registry", **_REG)
    def test_remove_deck_tag(self, mock_registry):
        mock_registry.find_deck.return_value = {"id": 42, "name": "Test Deck"}
        self.client.post("/api/decks/archidekt:42/tags", json={"tag": "Ramp", "source": "user"})
        # Verify it was added
        resp = self.client.get("/api/decks/archidekt:42/tags")
        self.assertEqual(len(resp.json()["tags"]), 1)
        # Now remove via direct redis
        removed = tag_store.remove_deck_tag(self.r, "archidekt:42", "Ramp")
        self.assertTrue(removed)
        resp = self.client.get("/api/decks/archidekt:42/tags")
        self.assertEqual(resp.json()["tags"], [])

    @patch("app.registry", **_REG)
    def test_get_deck_card_tags(self, mock_registry):
        mock_registry.find_deck.return_value = {"id": 42, "name": "Test Deck"}
        response = self.client.get("/api/decks/archidekt:42/card-tags")
        self.assertEqual(response.status_code, 200)
        self.assertIn("tags", response.json())

    @patch("app.registry", **_REG)
    def test_add_card_tag(self, mock_registry):
        mock_registry.find_deck.return_value = {"id": 42, "name": "Test Deck"}
        response = self.client.post("/api/decks/archidekt:42/card-tags", json={
            "card_name": "Sol Ring", "tag": "Ramp", "source": "user"
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["tag"]["tag"], "Ramp")

    @patch("app.registry", **_REG)
    def test_remove_card_tag(self, mock_registry):
        mock_registry.find_deck.return_value = {"id": 42, "name": "Test Deck"}
        self.client.post("/api/decks/archidekt:42/card-tags", json={
            "card_name": "Sol Ring", "tag": "Ramp", "source": "user"
        })
        # Verify it was added
        tags = tag_store.get_card_tags_for_deck(self.r, "archidekt:42", "Sol Ring")
        self.assertEqual(len(tags), 1)
        # Now remove
        removed = tag_store.remove_card_tag_from_deck(self.r, "archidekt:42", "Sol Ring", "Ramp")
        self.assertTrue(removed)
        tags = tag_store.get_card_tags_for_deck(self.r, "archidekt:42", "Sol Ring")
        self.assertEqual(tags, [])

    @patch("app.registry", **_REG)
    def test_add_card_tag_missing_fields_returns_400(self, mock_registry):
        mock_registry.find_deck.return_value = {"id": 42, "name": "Test Deck"}
        response = self.client.post("/api/decks/archidekt:42/card-tags", json={"tag": "Ramp"})
        self.assertEqual(response.status_code, 400)

    @patch("app.registry", **_REG)
    def test_auto_tag_not_found_returns_404(self, mock_registry):
        mock_registry.find_deck.return_value = None
        response = self.client.get("/api/decks/nonexistent/tags")
        self.assertEqual(response.status_code, 404)

    @patch("app.registry", **_REG)
    def test_numeric_and_registry_ids_share_tag_store(self, mock_registry):
        """Regression (D3): numeric id and registry id must hit the same storage key."""
        mock_registry.find_deck.return_value = {
            "id": 417707, "source": "archidekt", "source_id": 417707,
            "name": "Split Deck", "color_identity": [], "commanders": [],
            "cards": [{"name": "Sol Ring", "type_line": "Artifact", "oracle_text": "{T}: Add {C}{C}.", "cmc": 1}],
        }
        resp = self.client.post("/api/decks/417707/auto-tag", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.r.keys("deck_tags:417707"), [])
        self.assertEqual(self.r.keys("deck_card_tags:417707"), [])
        via_registry = self.client.get("/api/decks/archidekt:417707/card-tags").json()["tags"]
        via_numeric = self.client.get("/api/decks/417707/card-tags").json()["tags"]
        self.assertTrue(via_registry)
        self.assertEqual(via_registry, via_numeric)
        # deck-level write via numeric id is visible via registry id, and removable via either
        self.client.post("/api/decks/417707/tags", json={"tag": "Goblins"})
        tags = self.client.get("/api/decks/archidekt:417707/tags").json()
        self.assertEqual(tags["deck_id"], "archidekt:417707")
        self.assertTrue(any(t["tag"] == "Goblins" for t in tags["tags"]))
        self.client.request("DELETE", "/api/decks/417707/tags", json={"tag": "Goblins"})
        self.assertEqual(self.client.get("/api/decks/archidekt:417707/tags").json()["tags"], [])


if __name__ == "__main__":
    unittest.main()
