"""
Integration tests for the Deck Tuner overlay shell (DT-1,
docs/architecture/deck-tuner-overlay-spec.md §2-3): the /tuneup/draft and
/tuneup/tag-flags endpoints driven through the real FastAPI routes against
an in-memory fake Redis (same pattern as test_deck_manage_integration.py).

Covers:
  - GET returns the synced decklist (commanders + main 99 + maybeboard
    slot), live card tags, columns, counts, and no draft on first visit
  - POST is paper-only: it writes tuneup:draft but never mutates the
    synced decklist or card instances
  - validation: unknown cards (vs card_meta:*), bad qty, missing name,
    bad slot
  - drift detection when the synced decklist changes under an active
    draft, and the rebase escape hatch
  - tag flags round-trip and the §3.2 exclusion rule in counts (a card
    drops out only when EVERY tag it carries is excluded)

Run: python -m unittest discover -s tests
"""
import json
import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
import registry


class FakeRedis:
    """Minimal in-memory stand-in, mirrored from test_deck_manage_integration."""

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


def _tag(tag, source="auto", version=1):
    return {"tag": tag, "source": source, "created_at": "2026-10-24T00:00:00+00:00", "version": version}


class TuneOverlayTestCase(unittest.TestCase):
    """Seeds one deck: Atraxa (commander) + Sol Ring x1, Rhystic Study x1,
    Forest x10 mainboard, Cultivate on the maybeboard. Tags: Sol Ring=ramp,
    Rhystic Study=card_advantage+ramp."""

    def setUp(self):
        self.fake_r = FakeRedis()
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
                "cards": [
                    {"name": "Sol Ring", "quantity": 1},
                    {"name": "Rhystic Study", "quantity": 1},
                    {"name": "Forest", "quantity": 10},
                ],
                "slots": {"maybeboard": [{"name": "Cultivate", "quantity": 1}]},
                "url": "",
            },
            default_status="testing",
        )
        self.rid = registry.registry_id_of(self.deck)
        self.fake_r.set(f"deck_card_tags:{self.rid}", json.dumps({
            "Sol Ring": [_tag("ramp")],
            "Rhystic Study": [_tag("card_advantage"), _tag("ramp", version=2)],
        }))

    def tearDown(self):
        app_module.r = self._orig_r

    def _draft_url(self, rid=None):
        return f"/api/decks/{rid or self.rid}/tuneup/draft"

    def _flags_url(self, rid=None):
        return f"/api/decks/{rid or self.rid}/tuneup/tag-flags"


class TestGetState(TuneOverlayTestCase):
    def test_first_visit_returns_synced_state_without_draft(self):
        resp = self.client.get(self._draft_url())
        self.assertEqual(resp.status_code, 200)
        j = resp.json()
        self.assertIsNone(j["draft"])
        self.assertEqual(j["tag_flags"], {})
        self.assertFalse(j["drifted"])
        self.assertTrue(j["base_decklist_hash"])

        slots = {c["name"]: c["slot"] for c in j["cards"]}
        self.assertEqual(slots["Atraxa, Praetors' Voice"], "commander")
        self.assertEqual(slots["Forest"], "mainboard")
        self.assertEqual(slots["Cultivate"], "maybeboard")

    def test_columns_cover_slots_tags_and_pool_with_labels(self):
        j = self.client.get(self._draft_url()).json()
        cols = {c["id"]: c for c in j["columns"]}
        self.assertIn("slot:commander", cols)
        self.assertIn("slot:maybeboard", cols)
        self.assertNotIn("slot:considering", cols)   # empty slots are not shown
        # Core tag ids resolve to their display names.
        self.assertEqual(cols["tag:ramp"]["label"], "Ramp")
        self.assertEqual(cols["tag:card_advantage"]["label"], "Card Draw")
        self.assertEqual(j["columns"][-1]["id"], "uncategorized")

    def test_counts_include_commander_and_exclude_slots(self):
        j = self.client.get(self._draft_url()).json()
        # 1 commander + 1 + 1 + 10 mainboard; the maybeboard Cultivate never counts.
        self.assertEqual(j["counts"], {"total": 13, "excluded": []})


class TestDraftSave(TuneOverlayTestCase):
    def test_post_creates_draft_and_get_returns_it(self):
        cards = [
            {"name": "Atraxa, Praetors' Voice", "qty": 1, "slot": "commander"},
            {"name": "Sol Ring", "qty": 1},
            {"name": "Rhystic Study", "qty": 1},
            {"name": "Forest", "qty": 9},
            {"name": "Arcane Signet", "qty": 1, "source": "manual"},
        ]
        resp = self.client.post(self._draft_url(), json={"cards": cards})
        self.assertEqual(resp.status_code, 200)
        j = resp.json()
        self.assertTrue(j["success"])
        self.assertEqual(j["draft"]["status"], "active")

        got = self.client.get(self._draft_url()).json()
        names = [c["name"] for c in got["cards"]]
        self.assertIn("Arcane Signet", names)
        self.assertNotIn("Cultivate", names)   # dropped from the draft list
        # Draft is durable state, keyed per deck.
        self.assertIsNotNone(self.fake_r.get(f"tuneup:draft:{self.rid}"))

    def test_post_is_paper_only(self):
        """The draft save must not mutate the synced decklist or instances
        (that is the DT-2 apply path)."""
        cards = [{"name": "Sol Ring", "qty": 1}]
        resp = self.client.post(self._draft_url(), json={"cards": cards})
        self.assertEqual(resp.status_code, 200)

        deck = registry.find_deck(self.fake_r, self.rid)
        self.assertEqual(len(deck["cards"]), 3)   # synced list untouched
        self.assertFalse(self.fake_r.keys("instance:*"))

    def test_post_rejects_unknown_card_when_card_meta_known(self):
        for n in ("Sol Ring", "Forest"):
            self.fake_r.set(f"card_meta:{n}", json.dumps({"type": "Artifact", "cmc": 1}))
        resp = self.client.post(self._draft_url(), json={"cards": [
            {"name": "Sol Ring", "qty": 1},
            {"name": "Not A Real Card", "qty": 1},
        ]})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("Not A Real Card", resp.json()["error"])

    def test_post_accepts_anything_when_card_meta_empty(self):
        # No card_meta:* keys at all -> validation is skipped (sync never ran).
        resp = self.client.post(self._draft_url(), json={"cards": [{"name": "Anything", "qty": 1}]})
        self.assertEqual(resp.status_code, 200)

    def test_post_validation_errors(self):
        self.assertEqual(self.client.post(self._draft_url(), json={}).status_code, 400)
        self.assertEqual(self.client.post(self._draft_url(), json={"cards": [{"qty": 1}]}).status_code, 400)
        self.assertEqual(self.client.post(self._draft_url(), json={"cards": [{"name": "Sol Ring", "qty": 0}]}).status_code, 400)
        self.assertEqual(
            self.client.post(self._draft_url(), json={"cards": [{"name": "Sol Ring", "qty": 1, "slot": "sideboard"}]}).status_code,
            400)

    def test_drift_detection_and_rebase(self):
        self.client.post(self._draft_url(), json={"cards": [{"name": "Sol Ring", "qty": 1}]})
        self.assertFalse(self.client.get(self._draft_url()).json()["drifted"])

        # The synced decklist changes under the active draft (e.g. a sync).
        registry.upsert_deck(
            self.fake_r,
            {"source": "archidekt", "source_id": "999", "name": "Atraxa Superfriends",
             "color": "WUBG", "commanders": ["Atraxa, Praetors' Voice"], "commander_uids": [],
             "folder": "Imported", "description": "",
             "cards": [{"name": "Sol Ring", "quantity": 1}, {"name": "Forest", "quantity": 11}], "url": ""},
            default_status="testing",
        )
        self.assertTrue(self.client.get(self._draft_url()).json()["drifted"])

        # Rebase keeps the draft cards but accepts the new baseline.
        resp = self.client.post(self._draft_url(), json={"cards": [{"name": "Sol Ring", "qty": 1}], "rebase": True})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json()["drifted"])


class TestTagFlags(TuneOverlayTestCase):
    def test_round_trip(self):
        self.assertEqual(self.client.get(self._flags_url()).json()["flags"], {})
        resp = self.client.put(self._flags_url(), json={"tag": "ramp", "in_decklist": False})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["success"])
        flags = self.client.get(self._flags_url()).json()["flags"]
        self.assertEqual(flags["ramp"]["in_decklist"], False)
        self.assertTrue(flags["ramp"]["set_at"])

    def test_exclusion_rule_in_counts(self):
        """§3.2: a card drops out of the count only when EVERY tag it
        carries is excluded — Sol Ring (ramp only) drops, Rhystic Study
        (ramp + card_advantage) stays, untagged Forest always counts."""
        resp = self.client.put(self._flags_url(), json={"tag": "ramp", "in_decklist": False})
        counts = resp.json()["counts"]
        self.assertEqual(counts["excluded"], ["Sol Ring"])
        self.assertEqual(counts["total"], 12)   # 13 - Sol Ring

        # Flagging the second tag too drops Rhystic Study as well.
        self.client.put(self._flags_url(), json={"tag": "card_advantage", "in_decklist": False})
        counts = self.client.get(self._draft_url()).json()["counts"]
        self.assertEqual(sorted(counts["excluded"]), ["Rhystic Study", "Sol Ring"])
        self.assertEqual(counts["total"], 11)

    def test_requires_tag(self):
        self.assertEqual(self.client.put(self._flags_url(), json={"in_decklist": False}).status_code, 400)


class TestUnknownDeck(TuneOverlayTestCase):
    def test_all_endpoints_404(self):
        self.assertEqual(self.client.get(self._draft_url("nope")).status_code, 404)
        self.assertEqual(self.client.post(self._draft_url("nope"), json={"cards": []}).status_code, 404)
        self.assertEqual(self.client.get(self._flags_url("nope")).status_code, 404)
        self.assertEqual(self.client.put(self._flags_url("nope"), json={"tag": "x", "in_decklist": True}).status_code, 404)
        self.assertEqual(self.client.get("/api/decks/nope/tuneup/recommendations").status_code, 404)


class TestRecommendations(TuneOverlayTestCase):
    """DT-6 (spec §2/§3.3): GET /tuneup/recommendations feeds the overlay float
    panel — open swap picks, inventory-filtered and purchase-flagged."""

    def _recs_url(self, rid=None):
        return f"/api/decks/{rid or self.rid}/tuneup/recommendations"

    def _save_active(self, payload):
        from analytics import tune_store

        deck_id = tune_store.deck_key(self.deck)
        rec_id = "rec-test-1"
        self.fake_r.set(tune_store.REC_PREFIX + rec_id, json.dumps({
            "id": rec_id, "deck_id": deck_id, "status": "active",
            "created_at": "2026-10-24T00:00:00+00:00", "params": {}, "payload": payload}))
        self.fake_r.set(tune_store.ACTIVE_PREFIX + deck_id, rec_id)

    def _swap(self, cut, pick, own, buy, decision=None):
        return {"cut": {"name": cut}, "pick": pick, "own": own, "buy": buy, "decision": decision, "why": None}

    def test_empty_when_no_active_recommendation(self):
        j = self.client.get(self._recs_url()).json()
        self.assertTrue(j["success"])
        self.assertEqual(j["adds"], [])

    def test_unowned_pick_is_flagged_and_owned_free_pick_is_suppressed(self):
        import instances as instance_store

        instance_store.create_instance(self.fake_r, "Owned Bomb", ownership_status="in_collection")
        self._save_active({"swaps": [
            self._swap("Weak Plan", "own",
                       {"name": "Owned Bomb", "source": "free", "price": 0.0}, None),
            self._swap("Weak Removal", "buy", None,
                       {"name": "Rhystic Study", "source": "buy", "price": 12.0}),
            self._swap("Weak Draw", "buy", None,
                       {"name": "Decided Card", "source": "buy", "price": 1.0}, decision="buy"),
        ]})
        j = self.client.get(self._recs_url()).json()
        self.assertEqual([a["card"] for a in j["adds"]], ["Rhystic Study"])
        entry = j["adds"][0]
        self.assertTrue(entry["needs_purchase"])
        self.assertEqual(entry["kind"], "add")
        self.assertEqual(entry["cut"], "Weak Removal")
        self.assertEqual(entry["status"], "open")
        self.assertTrue(entry["id"].startswith("add-"))

    def test_owned_but_sleeved_pick_shows_without_purchase_flag(self):
        import instances as instance_store

        instance_store.create_instance(self.fake_r, "Sleeved Bomb", ownership_status="in_deck",
                                       deck_id="archidekt:1", deck_name="Other Deck")
        self._save_active({"swaps": [
            self._swap("Weak Plan", "own",
                       {"name": "Sleeved Bomb", "source": "deck", "price": 0.0}, None),
        ]})
        adds = self.client.get(self._recs_url()).json()["adds"]
        self.assertEqual(len(adds), 1)
        self.assertFalse(adds[0]["needs_purchase"])


class TestTemplateShell(unittest.TestCase):
    """The overlay view markup + script wiring live in deck_tune.html."""

    def setUp(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "templates", "deck_tune.html"), encoding="utf-8") as f:
            self.html = f.read()

    def test_overlay_markup_and_script(self):
        self.assertIn('id="tuneRecsView"', self.html)
        self.assertIn('id="tuneOverlayView"', self.html)
        self.assertIn('id="view-overlay"', self.html)
        self.assertIn('id="tuneOvColumns"', self.html)
        self.assertIn('id="tuneOvSaveDraft"', self.html)
        self.assertIn("/static/js/tune_overlay.js", self.html)
        self.assertIn("window.TuneOverlay.init", self.html)

    def test_dt6_float_panel_markup_and_js(self):
        self.assertIn('id="tuneRecPanel"', self.html)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "static", "js", "tune_overlay.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("/tuneup/recommendations", js)
        self.assertIn("TuneOverlay.adds", js)
        # Panel cards are draggable; dropping one adds it to the draft as a recommendation.
        self.assertIn('addCard(name, to, "recommendation")', js)

    def test_overlay_js_uses_existing_tag_endpoints(self):
        """DT-1 must not fork the tag system: drag = POST/DELETE card-tags."""
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "static", "js", "tune_overlay.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn('send("POST", "/card-tags"', js)
        self.assertIn('send("DELETE", "/card-tags"', js)
        self.assertIn('"/tuneup/tag-flags"', js)
        self.assertNotIn("deck_card_tags:", js)   # never writes the Redis key shape itself


if __name__ == "__main__":
    unittest.main()
