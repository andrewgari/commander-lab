"""Tests for reconstruct.py and Reconstruct API endpoints in app.py.

Run: pytest tests/test_reconstruct.py
"""
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import instances
import registry
import reconstruct
from fastapi.testclient import TestClient


class FakePipe:
    def __init__(self, r):
        self.r, self.ops, self.executed = r, [], False

    def __getattr__(self, name):
        def queue(*a):
            self.ops.append((name, a))
            return self
        return queue

    def execute(self):
        self.r.pipe_executions += 1
        for name, a in self.ops:
            getattr(self.r, name)(*a)
        self.ops = []


class FakeRedis:
    def __init__(self):
        self.store = {}
        self.sets = {}
        self.pipe_executions = 0

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v

    def sadd(self, k, v):
        self.sets.setdefault(k, set()).add(v)

    def srem(self, k, v):
        self.sets.get(k, set()).discard(v)

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    def sinter(self, *ks):
        out = None
        for k in ks:
            out = self.smembers(k) if out is None else out & self.smembers(k)
        return out or set()

    def keys(self, pat):
        pre = pat.rstrip("*")
        return [k for k in self.store if k.startswith(pre)]

    def delete(self, k):
        self.store.pop(k, None)

    def pipeline(self):
        return FakePipe(self)


def make_deck(deck_id=42, name="Atraxa", status="physical", **kw):
    d = {
        "id": deck_id,
        "name": name,
        "registry_id": f"archidekt:{deck_id}",
        "source": "archidekt",
        "source_id": str(deck_id),
        "status": status,
        "tags": ["removed", "custom_tag"],
        "commanders": ["Atraxa, Praetors' Voice"],
        "cards": [
            {"name": "Sol Ring", "quantity": 1},
            {"name": "Arcane Signet", "quantity": 1},
            {"name": "Doubling Season", "quantity": 1},
        ],
    }
    d.update(kw)
    return d


class TestReconstructCore(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck1 = make_deck(deck_id=42, name="Atraxa Superfriends", status="physical")
        self.deck2 = make_deck(deck_id=43, name="Urza Artifacts", status="physical",
                               commanders=["Urza, Lord High Artificer"],
                               cards=[{"name": "Mana Crypt", "quantity": 1}, {"name": "Mox Opal", "quantity": 1}])
        self.digital_deck = make_deck(deck_id=99, name="Digital Test", status="digital")

        self.r.set("decks", json.dumps([self.deck1, self.deck2, self.digital_deck]))

        # Bind cards in_deck to deck 42
        self.inst1 = instances.create_instance(
            self.r, "Sol Ring", ownership_status="in_deck", deck_id="archidekt:42", deck_name="Atraxa Superfriends"
        )
        self.inst2 = instances.create_instance(
            self.r, "Arcane Signet", ownership_status="in_deck", deck_id="archidekt:42", deck_name="Atraxa Superfriends"
        )

        # Collection card (Tier 2 candidate)
        self.coll_card = instances.create_instance(
            self.r, "Demonic Tutor", ownership_status="in_collection"
        )

        # Reset builder to default stub
        reconstruct.set_builder(reconstruct.StubReconstructBuilder())

    def test_start_reconstruct_single_deck(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        self.assertEqual(session["status"], reconstruct.STATUS_PENDING_PREFERENCES)
        self.assertEqual(session["source_deck_ids"], ["archidekt:42"])
        self.assertEqual(len(session["source_decks"]), 1)

        # Snapshot before retirement should capture original decklist and status
        snap = session["source_decks"][0]
        self.assertEqual(snap["name"], "Atraxa Superfriends")
        self.assertEqual(snap["status"], "physical")
        self.assertEqual(len(snap["cards"]), 3)

        # Source deck in Redis should now be retired
        updated_deck = registry.find_deck(self.r, "archidekt:42")
        self.assertEqual(updated_deck["status"], "retired")
        self.assertNotIn("removed", updated_deck.get("tags", []))
        self.assertIn("custom_tag", updated_deck.get("tags", []))

        # Cards should be unassigned from deck to collection
        inst1_rec = instances.get_instance(self.r, self.inst1["id"])
        self.assertEqual(inst1_rec["ownership_status"], "in_collection")
        self.assertIsNone(inst1_rec["deck_id"])

    def test_start_reconstruct_two_decks(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42", "archidekt:43"])
        self.assertEqual(len(session["source_deck_ids"]), 2)
        self.assertEqual(len(session["source_decks"]), 2)

        deck1 = registry.find_deck(self.r, "archidekt:42")
        deck2 = registry.find_deck(self.r, "archidekt:43")
        self.assertEqual(deck1["status"], "retired")
        self.assertEqual(deck2["status"], "retired")

    def test_start_reconstruct_validation_failures(self):
        # > 2 decks
        with self.assertRaises(ValueError):
            reconstruct.start_reconstruct(self.r, ["archidekt:42", "archidekt:43", "archidekt:99"])

        # Empty deck list
        with self.assertRaises(ValueError):
            reconstruct.start_reconstruct(self.r, [])

        # Non-existent deck
        with self.assertRaises(ValueError):
            reconstruct.start_reconstruct(self.r, ["archidekt:9999"])

        # Non-physical deck rejected
        with self.assertRaises(registry.DecommissionError):
            reconstruct.start_reconstruct(self.r, ["archidekt:99"])

    def test_preferences_submission_and_normalization(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]

        prefs_input = [
            {
                "commander": "Kraum, Ludevic's Opus",
                "themes": ["Spellslinger", "Treasure"],
                "strategies": ["aggro", "tempo"],
                "art_style": ["retro frame", "borderless"],
                "flavor": "Izzet chaos",
                "free_form_notes": "Exclude extra turn spells",
            },
            {
                "commanders": ["Tymna the Weaver", "Thrasios, Triton Hero"],
                "theme": "Midrange",
                "strategy": "control",
                "art style": "modern frame",
                "notes": "Low mana curve",
            },
        ]

        updated = reconstruct.submit_preferences(self.r, sid, prefs_input)
        self.assertEqual(len(updated["preferences"]), 2)

        p1 = updated["preferences"][0]
        self.assertEqual(p1["commanders"], ["Kraum, Ludevic's Opus"])
        self.assertEqual(p1["themes"], ["Spellslinger", "Treasure"])
        self.assertEqual(p1["strategies"], ["aggro", "tempo"])
        self.assertEqual(p1["art_style"], ["retro frame", "borderless"])
        self.assertEqual(p1["flavor"], "Izzet chaos")
        self.assertEqual(p1["notes"], "Exclude extra turn spells")

        p2 = updated["preferences"][1]
        self.assertEqual(p2["commanders"], ["Tymna the Weaver", "Thrasios, Triton Hero"])
        self.assertEqual(p2["themes"], ["Midrange"])
        self.assertEqual(p2["strategies"], ["control"])
        self.assertEqual(p2["art_style"], "modern frame")
        self.assertEqual(p2["notes"], "Low mana curve")

    def test_preferences_limits(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]

        # > 2 target decks rejected
        with self.assertRaises(ValueError):
            reconstruct.submit_preferences(self.r, sid, [{}, {}, {}])

        # Empty preferences rejected
        with self.assertRaises(ValueError):
            reconstruct.submit_preferences(self.r, sid, [])

    def test_trigger_build_sync_and_drafts(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]

        # Cannot trigger build before preferences
        with self.assertRaises(ValueError):
            reconstruct.trigger_build(self.r, sid, sync=True)

        reconstruct.submit_preferences(self.r, sid, [
            {
                "commanders": ["Atraxa, Grand Unifier"],
                "themes": ["Blink"],
                "strategies": ["value"],
            }
        ])

        built = reconstruct.trigger_build(self.r, sid, sync=True)
        self.assertEqual(built["status"], reconstruct.STATUS_DRAFTS_READY)
        self.assertEqual(len(built["drafts"]), 1)

        draft = built["drafts"][0]
        self.assertEqual(draft["commanders"], ["Atraxa, Grand Unifier"])
        self.assertGreater(len(draft["cards"]), 0)

        # Check tiers are properly tagged
        tiers = {c["tier"] for c in draft["cards"]}
        self.assertIn(1, tiers)  # Tier 1 from source deck
        self.assertIn(3, tiers)  # Tier 3 new purchase
        self.assertGreater(draft["total_new_cost"], 0.0)

    def test_pluggable_builder_interface(self):
        class CustomBuilder(reconstruct.BaseReconstructBuilder):
            def build(self, session, r):
                return [{"name": "Custom Draft", "commanders": ["Golos"], "cards": [], "total_new_cost": 0.0}]

        reconstruct.set_builder(CustomBuilder())

        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commander": "Golos"}])
        built = reconstruct.trigger_build(self.r, sid, sync=True)

        self.assertEqual(built["drafts"][0]["name"], "Custom Draft")
        self.assertEqual(built["drafts"][0]["commanders"], ["Golos"])

    def test_accept_draft_creates_deck_and_dedicates_cards(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [
            {"commanders": ["Atraxa, Praetors' Voice"], "themes": ["Superfriends"]}
        ])
        reconstruct.trigger_build(self.r, sid, sync=True)

        res = reconstruct.accept_draft(self.r, sid, draft_indices=[0], status="physical")
        self.assertEqual(res["session"]["status"], reconstruct.STATUS_ACCEPTED)
        self.assertEqual(len(res["created_decks"]), 1)

        created_deck = res["created_decks"][0]
        self.assertEqual(created_deck["status"], "physical")
        created_reg_id = registry.registry_id_of(created_deck)

        # Check that Sol Ring (unassigned on decommission) is now bound in_deck to the new deck!
        new_deck_instances = instances.list_instances(self.r, deck_id=created_reg_id)
        bound_card_names = {i["card_name"] for i in new_deck_instances if i["ownership_status"] == "in_deck"}
        self.assertIn("Sol Ring", bound_card_names)

        # Cannot submit preferences or build on accepted session
        with self.assertRaises(ValueError):
            reconstruct.submit_preferences(self.r, sid, [{"commander": "Urza"}])
        with self.assertRaises(ValueError):
            reconstruct.trigger_build(self.r, sid)
        with self.assertRaises(ValueError):
            reconstruct.abandon_reconstruct(self.r, sid)

    def test_accepted_physical_draft_survives_folder_rule(self):
        """A sync runs apply_folder_rules over every deck; a reconstruct deck accepted
        as physical has no Archidekt folder and must not be demoted to digital."""
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commanders": ["Atraxa, Praetors' Voice"]}])
        reconstruct.trigger_build(self.r, sid, sync=True)
        res = reconstruct.accept_draft(self.r, sid, draft_indices=[0], status="physical")
        reg_id = registry.registry_id_of(res["created_decks"][0])

        changed = registry.apply_folder_rules(self.r)

        self.assertNotIn(reg_id, changed)
        self.assertEqual(registry.find_deck(self.r, reg_id)["status"], "physical")
        bound = {i["card_name"] for i in instances.list_instances(self.r, deck_id=reg_id)
                 if i["ownership_status"] == "in_deck"}
        self.assertIn("Sol Ring", bound)

    def test_abandon_reconstruct(self):
        session = reconstruct.start_reconstruct(self.r, ["archidekt:42"])
        sid = session["session_id"]
        reconstruct.submit_preferences(self.r, sid, [{"commander": "Urza"}])

        abandoned = reconstruct.abandon_reconstruct(self.r, sid, reason="Changed mind")
        self.assertEqual(abandoned["status"], reconstruct.STATUS_ABANDONED)
        self.assertEqual(abandoned["abandon_reason"], "Changed mind")

        with self.assertRaises(ValueError):
            reconstruct.trigger_build(self.r, sid)


class TestReconstructEndpoints(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.r = FakeRedis()
        self.deck1 = make_deck(deck_id=42, name="Atraxa", status="physical")
        self.deck2 = make_deck(deck_id=43, name="Urza", status="physical")
        self.digital_deck = make_deck(deck_id=99, name="Digital", status="digital")
        self.r.set("decks", json.dumps([self.deck1, self.deck2, self.digital_deck]))

        instances.create_instance(
            self.r, "Sol Ring", ownership_status="in_deck", deck_id="archidekt:42", deck_name="Atraxa"
        )

        reconstruct.set_builder(reconstruct.StubReconstructBuilder())

        self.patch = patch.object(app_module, "r", self.r)
        self.patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.patch.stop()

    def test_api_start_reconstruct(self):
        # 400 for empty
        res = self.client.post("/api/reconstruct/start", json={"deck_ids": []})
        self.assertEqual(res.status_code, 400)

        # 404 for unknown deck
        res = self.client.post("/api/reconstruct/start", json={"deck_ids": ["archidekt:9999"]})
        self.assertEqual(res.status_code, 404)

        # 400 for digital deck
        res = self.client.post("/api/reconstruct/start", json={"deck_ids": ["archidekt:99"]})
        self.assertEqual(res.status_code, 400)

        # 201 for valid physical decks
        res = self.client.post("/api/reconstruct/start", json={"deck_ids": ["archidekt:42", "archidekt:43"]})
        self.assertEqual(res.status_code, 201)
        data = res.json()
        self.assertTrue(data["success"])
        self.assertTrue(data["session_id"].startswith("recon_"))
        self.assertEqual(data["session"]["status"], "pending_preferences")
        self.assertEqual(len(data["session"]["source_decks"]), 2)

    def test_api_reconstruct_lifecycle_end_to_end(self):
        # 1. Start reconstruct
        res = self.client.post("/api/reconstruct", json={"deck_id": "archidekt:42"})
        self.assertEqual(res.status_code, 201)
        sid = res.json()["session_id"]

        # 2. Get session
        res = self.client.get(f"/api/reconstruct/{sid}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["session"]["status"], "pending_preferences")

        # 3. Submit preferences
        res = self.client.post(
            f"/api/reconstruct/{sid}/preferences",
            json={
                "preferences": [
                    {
                        "commanders": ["Kraum, Ludevic's Opus", "Tymna the Weaver"],
                        "themes": ["Card Draw"],
                        "strategies": ["midrange"],
                    }
                ]
            },
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.json()["session"]["preferences"]), 1)

        # 4. Trigger build with ?sync=true
        res = self.client.post(f"/api/reconstruct/{sid}/build?sync=true")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "drafts_ready")

        # 5. Fetch drafts
        res = self.client.get(f"/api/reconstruct/{sid}/drafts")
        self.assertEqual(res.status_code, 200)
        drafts = res.json()["drafts"]
        self.assertEqual(len(drafts), 1)
        self.assertIn("cards", drafts[0])

        # 6. Accept draft
        res = self.client.post(f"/api/reconstruct/{sid}/accept", json={"draft_index": 0})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["session"]["status"], "accepted")
        self.assertEqual(len(res.json()["created_decks"]), 1)

        # Check list sessions
        res = self.client.get("/api/reconstruct/sessions")
        self.assertEqual(res.status_code, 200)
        self.assertGreater(len(res.json()["sessions"]), 0)

    def test_api_abandon(self):
        res = self.client.post("/api/reconstruct/start", json={"deck_ids": ["archidekt:42"]})
        sid = res.json()["session_id"]

        res = self.client.post(f"/api/reconstruct/{sid}/abandon", json={"reason": "Testing abandon"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["session"]["status"], "abandoned")


if __name__ == "__main__":
    unittest.main()
