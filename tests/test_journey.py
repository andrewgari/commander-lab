"""
DT-8: Deck journey timeline (docs/architecture/deck-tuner-overlay-spec.md §3.4).

- analytics/journey.py store: journey:entry:{id} + journey:entries:{registry_id}
  LIST (newest first, capped at HISTORY_MAX), kinds milestone/tune_applied/
  sync/manual, auto-drafted titles + descriptions, in-place edit, and
  redraft-as-replacement (new id, same slot, redrafted_from provenance).
- app.py endpoints: GET/POST /api/decks/{id}/journey(/entries),
  PATCH .../entries/{entry_id}, POST .../entries/{entry_id}/redraft.
- templates/deck_journal.html renders the timeline section.

No network: Redis is faked; the API tests patch app.r and the deck lookup.

Run: python -m pytest tests/test_journey.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import app as app_mod
from analytics import journey
from app import app
from tests.test_deck_stats_ui import DECK
from tests.test_interactions import FakeRedis

DECK_ID = "archidekt:417707"


def fresh():
    return FakeRedis()


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.r = fresh()

    def test_append_and_list_newest_first(self):
        a = journey.append_entry(self.r, DECK, kind="manual", title="First build")
        b = journey.append_entry(self.r, DECK, kind="milestone", title="First win")
        entries = journey.list_entries(self.r, DECK)
        self.assertEqual([e["id"] for e in entries], [b["id"], a["id"]])
        self.assertEqual(entries[0]["deck_id"], DECK_ID)
        self.assertEqual(entries[0]["schema_version"], journey.SCHEMA_VERSION)
        self.assertIsNone(entries[0]["redrafted_from"])

    def test_tune_applied_auto_title_and_draft(self):
        e = journey.append_entry(self.r, DECK, kind="tune_applied",
                                 diff={"added": ["Sol Ring", "Arcane Signet"], "cut": ["Plains"]})
        self.assertEqual(e["title"], "Swapped 3 cards after tune-up")
        self.assertIn("added Sol Ring, Arcane Signet", e["description"])
        self.assertIn("cut Plains", e["description"])
        self.assertEqual(e["diff"], {"added": ["Sol Ring", "Arcane Signet"], "cut": ["Plains"]})

    def test_sync_auto_title_no_changes(self):
        e = journey.append_entry(self.r, DECK, kind="sync")
        self.assertEqual(e["title"], "Synced with no card changes")
        self.assertIn("no card changes", e["description"])

    def test_diff_deduped_and_cleaned(self):
        e = journey.append_entry(self.r, DECK, kind="sync",
                                 diff={"added": [" Sol Ring ", "Sol Ring"], "cut": None})
        self.assertEqual(e["diff"]["added"], ["Sol Ring"])
        self.assertEqual(e["diff"]["cut"], [])

    def test_append_validation(self):
        with self.assertRaises(journey.JourneyError):
            journey.append_entry(self.r, DECK, kind="nonsense", title="x")
        with self.assertRaises(journey.JourneyError):
            journey.append_entry(self.r, DECK, kind="manual", title="x", diff={"added": "Sol Ring"})
        with self.assertRaises(journey.JourneyError):
            journey.append_entry(self.r, DECK, kind="manual", title="x" * 201)
        with self.assertRaises(journey.JourneyError):
            journey.append_entry(self.r, DECK, kind="manual", title=123)

    def test_history_cap_drops_oldest_and_deletes_orphan_keys(self):
        ids = [journey.append_entry(self.r, DECK, kind="manual", title=f"t{i}")["id"]
               for i in range(journey.HISTORY_MAX + 2)]
        entries = journey.list_entries(self.r, DECK, limit=journey.HISTORY_MAX + 10)
        self.assertEqual(len(entries), journey.HISTORY_MAX)
        self.assertEqual(entries[0]["id"], ids[-1])          # newest kept
        self.assertNotIn(ids[0], [e["id"] for e in entries]) # oldest dropped
        self.assertIsNone(self.r.get(journey.ENTRY_PREFIX + ids[0]))  # orphan key gone

    def test_update_entry_in_place(self):
        e = journey.append_entry(self.r, DECK, kind="manual", title="Old title", now="2026-10-24T10:00:00+00:00")
        updated = journey.update_entry(self.r, DECK, e["id"], title="New title",
                                       description="It played great.", now="2026-10-24T11:00:00+00:00")
        self.assertEqual(updated["id"], e["id"])
        self.assertEqual(updated["title"], "New title")
        self.assertEqual(updated["description"], "It played great.")
        self.assertEqual(updated["created_at"], "2026-10-24T10:00:00+00:00")
        self.assertEqual(updated["updated_at"], "2026-10-24T11:00:00+00:00")
        self.assertEqual(journey.list_entries(self.r, DECK)[0]["title"], "New title")

    def test_update_validation(self):
        e = journey.append_entry(self.r, DECK, kind="manual", title="x")
        with self.assertRaises(journey.JourneyError):
            journey.update_entry(self.r, DECK, e["id"])  # nothing to update
        with self.assertRaises(journey.JourneyError):
            journey.update_entry(self.r, DECK, e["id"], title="   ")  # empty title
        with self.assertRaises(journey.EntryNotFound):
            journey.update_entry(self.r, DECK, "missing", title="y")
        with self.assertRaises(journey.EntryNotFound):  # entry belongs to another deck
            journey.update_entry(self.r, {"registry_id": "archidekt:999"}, e["id"], title="y")

    def test_redraft_replaces_in_same_slot(self):
        a = journey.append_entry(self.r, DECK, kind="manual", title="Old note", now="2026-10-20T10:00:00+00:00")
        b = journey.append_entry(self.r, DECK, kind="tune_applied",
                                 diff={"added": ["Sol Ring"], "cut": ["Plains"]}, now="2026-10-21T10:00:00+00:00")
        c = journey.append_entry(self.r, DECK, kind="manual", title="Newest", now="2026-10-22T10:00:00+00:00")
        journey.update_entry(self.r, DECK, b["id"], description="My own words.")
        re = journey.redraft_entry(self.r, DECK, b["id"], now="2026-10-24T12:00:00+00:00")
        self.assertNotEqual(re["id"], b["id"])
        self.assertEqual(re["redrafted_from"], b["id"])
        self.assertEqual(re["created_at"], "2026-10-21T10:00:00+00:00")
        self.assertEqual(re["updated_at"], "2026-10-24T12:00:00+00:00")
        self.assertEqual(re["description"], journey.draft_description("tune_applied", re["title"], re["diff"]))
        self.assertNotIn("My own words", re["description"])
        self.assertEqual([e["id"] for e in journey.list_entries(self.r, DECK)],
                         [c["id"], re["id"], a["id"]])   # same slot, order preserved
        self.assertIsNone(self.r.get(journey.ENTRY_PREFIX + b["id"]))  # old key deleted
        with self.assertRaises(journey.EntryNotFound):
            journey.redraft_entry(self.r, DECK, b["id"])  # the old id is gone

    def test_redraft_unknown_entry(self):
        with self.assertRaises(journey.EntryNotFound):
            journey.redraft_entry(self.r, DECK, "missing")


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class ApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        self.fake = fresh()
        self.fake.set("decks", json.dumps([DECK]))   # registry._load_decks reads this key
        self.patches = [patch.object(app_mod, "r", self.fake)]
        for p in self.patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self.patches])

    def url(self, suffix=""):
        return f"/api/decks/{DECK_ID}/journey{suffix}"

    def test_full_lifecycle(self):
        res = self.client.get(self.url())
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["entries"], [])

        res = self.client.post(self.url("/entries"),
                               json={"kind": "tune_applied",
                                     "diff": {"added": ["Sol Ring"], "cut": ["Plains"]},
                                     "source_draft_id": "draft-1"})
        self.assertEqual(res.status_code, 200)
        entry = res.json()["entry"]
        self.assertEqual(entry["title"], "Swapped 2 cards after tune-up")
        self.assertEqual(entry["source_draft_id"], "draft-1")

        res = self.client.get(self.url())
        self.assertEqual([e["id"] for e in res.json()["entries"]], [entry["id"]])
        self.assertIn("tune_applied", res.json()["kind_labels"])

        res = self.client.patch(self.url(f"/entries/{entry['id']}"),
                                json={"description": "Mana fixed, still light on draw."})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["entry"]["description"], "Mana fixed, still light on draw.")
        self.assertEqual(res.json()["entry"]["id"], entry["id"])

        res = self.client.post(self.url(f"/entries/{entry['id']}/redraft"))
        self.assertEqual(res.status_code, 200)
        redrafted = res.json()["entry"]
        self.assertNotEqual(redrafted["id"], entry["id"])
        self.assertEqual(redrafted["redrafted_from"], entry["id"])
        self.assertIn("added Sol Ring", redrafted["description"])

        res = self.client.get(self.url())
        self.assertEqual([e["id"] for e in res.json()["entries"]], [redrafted["id"]])

    def test_manual_entry_defaults_to_kind_manual(self):
        res = self.client.post(self.url("/entries"), json={"title": "First win"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["entry"]["kind"], "manual")

    def test_validation_errors(self):
        res = self.client.post(self.url("/entries"), json={"kind": "bogus", "title": "x"})
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["success"])
        res = self.client.post(self.url("/entries"), data="not json",
                               headers={"Content-Type": "application/json"})
        self.assertEqual(res.status_code, 400)
        res = self.client.patch(self.url("/entries/missing"), json={"title": "x"})
        self.assertEqual(res.status_code, 404)
        res = self.client.post(self.url("/entries/missing/redraft"))
        self.assertEqual(res.status_code, 404)
        res = self.client.get("/api/decks/archidekt:999999/journey")
        self.assertEqual(res.status_code, 404)


# ---------------------------------------------------------------------------
# Journal page renders the timeline section
# ---------------------------------------------------------------------------


class JournalPageJourneyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_deck_stats_ui import INVENTORY
        cls.client = TestClient(app)
        cls.fake = fresh()
        journey.append_entry(cls.fake, DECK, kind="milestone", title="Seeded")
        with patch.object(app_mod, "r", cls.fake), \
                patch("app.registry.list_decks", return_value=[DECK]), \
                patch("app.inventory_view.build_inventory", return_value=INVENTORY):
            cls.res = cls.client.get("/deck/Holy%20Hellfire/journal")
        cls.html = cls.res.text

    def test_renders(self):
        self.assertEqual(self.res.status_code, 200)

    def test_journey_section_markup(self):
        self.assertIn("Deck journey", self.html)
        self.assertIn('id="jnTimeline"', self.html)
        self.assertIn('id="jnAddBtn"', self.html)
        self.assertIn('id="jnTitle"', self.html)
        self.assertIn('id="jnEmpty"', self.html)

    def test_journey_js_and_endpoints(self):
        self.assertIn("/journey", self.html)
        self.assertIn('data-act="redraft"', self.html)
        self.assertIn('data-act="edit"', self.html)

    def test_page_css_classes_exist(self):
        style = self.html.split("<style>", 1)[1].split("</style>", 1)[0]
        for cls in [".jn-timeline", ".jn-entry", ".jn-dot", ".jn-add", ".jn-edit", ".jn-diffs"]:
            with self.subTest(cls=cls):
                self.assertIn(cls, style)


if __name__ == "__main__":
    unittest.main()
