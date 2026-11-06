"""POST/DELETE /api/v2/brew/{session_id}/cards: edit the brewed draft in place.

Redis fixture session (FakeRedis, same one test_brew uses); the draft comes
from the real StubBrewBuilder so tiers/costs are computed by the real pools.

Run: pytest tests/test_brew_cards_api.py
"""
import copy
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import src.server.domain.draft.brew as brew
import src.server.domain.draft.brew_builder as brew_builder
import src.server.domain.draft.reconstruct_pools as reconstruct_pools
import src.server.domain.inventory.instances as instances
from tests.test_brew import FakeRedis, make_deck, static_price_lookup

OID_RIFT = "11111111-1111-4111-8111-111111111111"
OID_TUTOR = "22222222-2222-4222-8222-222222222222"
OID_SOL_RING = "33333333-3333-4333-8333-333333333333"
OID_FOREST = "44444444-4444-4444-8444-444444444444"
OID_ATRAXA = "55555555-5555-4555-8555-555555555555"
OID_UNKNOWN = "99999999-9999-4999-8999-999999999999"


class BrewCardsAPITestBase(unittest.TestCase):
    def setUp(self):
        import src.server.app.main as app_module

        self.r = FakeRedis()
        self.deck1 = make_deck(deck_id=42, name="Atraxa Superfriends", status="physical")
        self.r.set("decks", json.dumps([self.deck1]))
        instances.create_instance(self.r, "Sol Ring", ownership_status="in_deck",
                                  deck_id="archidekt:42", deck_name="Atraxa Superfriends")
        instances.create_instance(self.r, "Demonic Tutor", ownership_status="in_collection")
        self.r.set("card_meta:Lightning Greaves",
                   json.dumps({"color": "", "type": "Artifact — Equipment"}))
        # Catalog records so DELETE can resolve oracle_id -> name for cards the
        # builder wrote (which carry no oracle_id).
        for oid, name in ((OID_SOL_RING, "Sol Ring"), (OID_TUTOR, "Demonic Tutor"),
                          (OID_ATRAXA, "Atraxa, Praetors' Voice")):
            self.r.set(f"card:{oid}", json.dumps({"name": name, "oracle_id": oid}))

        self._orig_lookup = reconstruct_pools.redis_price_lookup
        reconstruct_pools.redis_price_lookup = static_price_lookup(
            {"Cyclonic Rift": 4.25, "Forest": None})
        brew_builder.set_builder(brew_builder.StubBrewBuilder())

        self.patch = patch.object(app_module, "r", self.r)
        self.patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.patch.stop()
        reconstruct_pools.redis_price_lookup = self._orig_lookup

    # -- helpers --------------------------------------------------------

    def _built(self) -> str:
        s = brew.start_brew(self.r, "Atraxa, Praetors' Voice", ["archidekt:42"],
                            "owned_first", name="Atraxa brew")
        brew.trigger_build(self.r, s["session_id"], sync=True)
        return s["session_id"]

    def _cards(self, sid):
        return brew.get_session(self.r, sid)["drafts"][0]["cards"]

    def _by_name(self, sid):
        return {c["name"]: c for c in self._cards(sid)}

    def _post(self, sid, oracle_id, name, qty=1):
        return self.client.post(f"/api/v2/brew/{sid}/cards",
                                json={"oracle_id": oracle_id, "name": name, "qty": qty})

    def _delete(self, sid, oracle_id):
        return self.client.delete(f"/api/v2/brew/{sid}/cards/{oracle_id}")


class TestAddCard(BrewCardsAPITestBase):
    def test_add_card_appears_in_draft_with_tier_recomputed_from_pools(self):
        sid = self._built()
        res = self._post(sid, OID_RIFT, "Cyclonic Rift")
        self.assertEqual(res.status_code, 200, res.text)
        card = self._by_name(sid)["Cyclonic Rift"]
        self.assertEqual(card["oracle_id"], OID_RIFT)
        self.assertEqual(card["quantity"], 1)
        # Not owned anywhere: Tier 3 (buy), priced from the price lookup.
        self.assertEqual(card["tier"], 3)
        self.assertEqual(card["tier_label"], "new")
        self.assertEqual(card["cost"], 4.25)
        # Response carries the whole edited draft.
        body = res.json()
        self.assertIn("Cyclonic Rift", {c["name"] for c in body["draft"]["cards"]})
        self.assertGreaterEqual(body["draft"]["total_new_cost"], 4.25)

    def test_add_free_inventory_card_is_tier_2_and_free(self):
        sid = self._built()
        # Builder already took Demonic Tutor into the draft; drop it, then re-add.
        self.assertEqual(self._delete(sid, OID_TUTOR).status_code, 200)
        before_cost = brew.get_session(self.r, sid)["drafts"][0]["total_new_cost"]
        res = self._post(sid, OID_TUTOR, "Demonic Tutor")
        self.assertEqual(res.status_code, 200, res.text)
        card = self._by_name(sid)["Demonic Tutor"]
        self.assertEqual((card["tier"], card["tier_label"], card["cost"]), (2, "owned", 0.0))
        self.assertEqual(res.json()["draft"]["total_new_cost"], before_cost)

    def test_add_updates_draft_total_cost(self):
        sid = self._built()
        before = brew.get_session(self.r, sid)["drafts"][0]["total_new_cost"]
        self._post(sid, OID_RIFT, "Cyclonic Rift")
        after = brew.get_session(self.r, sid)["drafts"][0]["total_new_cost"]
        self.assertEqual(after, round(before + 4.25, 2))

    def test_non_basic_duplicate_by_name_is_409(self):
        sid = self._built()
        self.assertIn("Sol Ring", self._by_name(sid))
        before = copy.deepcopy(self._cards(sid))
        res = self._post(sid, OID_SOL_RING, "Sol Ring")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"]["error"], "duplicate_card")
        self.assertEqual(self._cards(sid), before)

    def test_non_basic_duplicate_by_oracle_id_is_409_even_if_name_differs(self):
        sid = self._built()
        self.assertEqual(self._post(sid, OID_RIFT, "Cyclonic Rift").status_code, 200)
        res = self._post(sid, OID_RIFT, "Cyclonic Rift (alt printing name)")
        self.assertEqual(res.status_code, 409)

    def test_non_basic_qty_above_one_is_409(self):
        sid = self._built()
        res = self._post(sid, OID_RIFT, "Cyclonic Rift", qty=2)
        self.assertEqual(res.status_code, 409)
        self.assertNotIn("Cyclonic Rift", self._by_name(sid))

    def test_basic_land_quantities_stack_instead_of_409(self):
        sid = self._built()
        self.assertEqual(self._post(sid, OID_FOREST, "Forest", qty=3).status_code, 200)
        self.assertEqual(self._post(sid, OID_FOREST, "Forest", qty=2).status_code, 200)
        forests = [c for c in self._cards(sid) if c["name"] == "Forest"]
        self.assertEqual(len(forests), 1)
        self.assertEqual(forests[0]["quantity"], 5)

    def test_invalid_bodies_are_422(self):
        sid = self._built()
        for body in ({"oracle_id": OID_RIFT, "name": "Cyclonic Rift", "qty": 0},
                     {"oracle_id": "", "name": "Cyclonic Rift", "qty": 1},
                     {"oracle_id": OID_RIFT, "name": "  ", "qty": 1},
                     {"name": "Cyclonic Rift", "qty": 1}):
            res = self.client.post(f"/api/v2/brew/{sid}/cards", json=body)
            self.assertEqual(res.status_code, 422, body)

    def test_unknown_session_is_404(self):
        res = self._post("brew_nope", OID_RIFT, "Cyclonic Rift")
        self.assertEqual(res.status_code, 404)

    def test_session_without_a_draft_is_409(self):
        s = brew.start_brew(self.r, "Atraxa, Praetors' Voice", ["archidekt:42"], "owned_first")
        res = self._post(s["session_id"], OID_RIFT, "Cyclonic Rift")
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"]["error"], "brew_not_editable")

    def test_accepted_session_is_not_editable(self):
        sid = self._built()
        brew.accept_draft(self.r, sid)
        self.assertEqual(self._post(sid, OID_RIFT, "Cyclonic Rift").status_code, 409)
        self.assertEqual(self._delete(sid, OID_SOL_RING).status_code, 409)

    def test_accept_includes_the_added_card(self):
        sid = self._built()
        self._post(sid, OID_RIFT, "Cyclonic Rift")
        result = brew.accept_draft(self.r, sid)
        names = {c["name"] for c in result["created_decks"][0]["cards"]}
        self.assertIn("Cyclonic Rift", names)
        prov = {p["name"]: p["source"] for p in result["session"]["drafts"][0]["provenance"]}
        self.assertEqual(prov["Cyclonic Rift"], "buy")


class TestRemoveCard(BrewCardsAPITestBase):
    def test_remove_added_card_by_oracle_id(self):
        sid = self._built()
        self._post(sid, OID_RIFT, "Cyclonic Rift")
        res = self._delete(sid, OID_RIFT)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("Cyclonic Rift", self._by_name(sid))
        self.assertNotIn("Cyclonic Rift", {c["name"] for c in res.json()["draft"]["cards"]})

    def test_remove_builder_card_resolves_oracle_id_through_catalog(self):
        sid = self._built()
        self.assertIn("Sol Ring", self._by_name(sid))
        res = self._delete(sid, OID_SOL_RING)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn("Sol Ring", self._by_name(sid))

    def test_remove_recomputes_total_cost(self):
        sid = self._built()
        base = brew.get_session(self.r, sid)["drafts"][0]["total_new_cost"]
        self._post(sid, OID_RIFT, "Cyclonic Rift")
        self._delete(sid, OID_RIFT)
        self.assertEqual(brew.get_session(self.r, sid)["drafts"][0]["total_new_cost"], base)

    def test_remove_card_not_in_draft_is_404(self):
        sid = self._built()
        res = self._delete(sid, OID_UNKNOWN)
        self.assertEqual(res.status_code, 404)
        self.assertEqual(res.json()["detail"]["error"], "card_not_in_draft")

    def test_cannot_remove_the_commander(self):
        sid = self._built()
        res = self._delete(sid, OID_ATRAXA)
        self.assertEqual(res.status_code, 409)
        self.assertEqual(res.json()["detail"]["error"], "cannot_remove_commander")
        self.assertIn("Atraxa, Praetors' Voice", self._by_name(sid))

    def test_unknown_session_is_404(self):
        self.assertEqual(self._delete("brew_nope", OID_SOL_RING).status_code, 404)


class TestUneditedSessionUnchanged(BrewCardsAPITestBase):
    """The new endpoints must not change what accept_draft produces."""

    def _accept_shape(self, sid):
        result = brew.accept_draft(self.r, sid)
        deck = result["created_decks"][0]
        draft = result["session"]["drafts"][0]
        return (
            sorted((c["name"], c["quantity"]) for c in deck["cards"]),
            list(deck["commanders"]),
            sorted((p["name"], p["quantity"], p["source"]) for p in draft["provenance"]),
        )

    def test_unedited_draft_has_no_new_keys_and_accepts_same_as_before(self):
        sid = self._built()
        draft = brew.get_session(self.r, sid)["drafts"][0]
        self.assertTrue(all("oracle_id" not in c for c in draft["cards"]))
        names_qty = sorted((c["name"], c["quantity"]) for c in draft["cards"])
        deck_cards, _, _ = self._accept_shape(sid)
        self.assertEqual(deck_cards, names_qty)

    def test_add_then_remove_round_trip_accepts_identically_to_unedited(self):
        pristine = self._accept_shape(self._built())
        # Fresh Redis state for the second run is not needed: accept_draft
        # upserts by commander, so compare the shape of two independent sessions.
        sid = self._built()
        self.assertEqual(self._post(sid, OID_RIFT, "Cyclonic Rift").status_code, 200)
        self.assertEqual(self._delete(sid, OID_RIFT).status_code, 200)
        edited = self._accept_shape(sid)
        self.assertEqual(edited, pristine)

    def test_other_cards_keep_their_tier_after_an_edit(self):
        sid = self._built()
        before = {c["name"]: (c["tier"], c["quantity"]) for c in self._cards(sid)}
        self._post(sid, OID_RIFT, "Cyclonic Rift")
        after = {c["name"]: (c["tier"], c["quantity"]) for c in self._cards(sid)}
        after.pop("Cyclonic Rift")
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
