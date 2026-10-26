"""Manage-tab editor for the Simulate Focus chip catalog: store semantics, validation, UI wiring."""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import sim_focus as sf
from tests.test_sim_store_and_diff import FakeRedis

ROOT = Path(__file__).resolve().parent.parent
STATS = {"built": True, "mv": 9, "counts": {"lands": 20, "ramp": 0, "card_advantage": 0, "removal": 0, "finisher": 0},
         "floors": {"ramp": 10, "card_advantage": 7, "removal": 10, "finisher": 2}, "targets": {"lands": 38},
         "commander_roles": 4}


class TestManageStore(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        sf.list_catalog(self.r)

    def slugs(self):
        return [c["slug"] for c in sf.list_catalog(self.r)]

    def test_add_generates_slug_from_label(self):
        chip = sf.create(self.r, {"label": "Stax Pieces!", "hint": "Hate"})
        self.assertEqual(chip["slug"], "stax-pieces")
        self.assertEqual(self.slugs()[-1], "stax-pieces")

    def test_generated_slug_is_unique(self):
        a = sf.create(self.r, {"label": "Ramp", "hint": "x"})  # seed already has "ramp"
        b = sf.create(self.r, {"label": "Ramp", "hint": "x"})
        self.assertEqual((a["slug"], b["slug"]), ("ramp-2", "ramp-3"))

    def test_explicit_duplicate_slug_rejected(self):
        with self.assertRaises(sf.FocusError):
            sf.create(self.r, {"slug": "ramp", "label": "Other", "hint": "x"})

    def test_empty_label_rejected(self):
        for label in ("", "   ", None, "!!!"):
            with self.assertRaises(sf.FocusError, msg=repr(label)):
                sf.create(self.r, {"label": label, "hint": "x"})

    def test_length_limits(self):
        with self.assertRaises(sf.FocusError):
            sf.create(self.r, {"label": "x" * 41, "hint": "x"})
        with self.assertRaises(sf.FocusError):
            sf.create(self.r, {"label": "ok", "hint": "x" * 141})
        with self.assertRaises(sf.FocusError):
            sf.update(self.r, "ramp", {"hint": "x" * 141})

    def test_rename_keeps_slug_and_edit_hint(self):
        sf.update(self.r, "ramp", {"label": "Mana base 2", "slug": "hacked", "hint": "New hint"})
        chip = [c for c in sf.list_catalog(self.r) if c["slug"] == "ramp"][0]
        self.assertEqual((chip["label"], chip["hint"]), ("Mana base 2", "New hint"))
        self.assertNotIn("hacked", self.slugs())

    def test_reorder_persists(self):
        order = list(reversed(self.slugs()))
        sf.reorder(self.r, order)
        self.assertEqual(self.slugs(), order)

    def test_delete_is_final_even_for_last_chip(self):
        for s in self.slugs():
            sf.delete(self.r, s)
        sf.ensure_seeded(self.r)
        self.assertEqual(self.slugs(), [])

    def test_pickers_limited_to_known_metrics_and_rules(self):
        with self.assertRaises(sf.FocusError):
            sf.update(self.r, "ramp", {"telemetry_key": "free_typed"})
        with self.assertRaises(sf.FocusError):
            sf.update(self.r, "ramp", {"rules": [{"kind": "below_floor", "row": "nonsense"}]})
        with self.assertRaises(sf.FocusError):
            sf.update(self.r, "ramp", {"rules": [{"kind": "custom"}]})
        sf.update(self.r, "ramp", {"telemetry_key": "win_turn", "rules": [{"kind": "below_floor", "row": "ramp"}]})
        opts = sf.options()
        self.assertIn("win_turn", opts["telemetry_keys"])
        self.assertTrue(set(sf.SEED[i]["telemetry_key"] for i in range(len(sf.SEED))) <= set(opts["telemetry_keys"]))

    def test_ruleless_chip_never_suggested_and_rule_removal(self):
        sf.create(self.r, {"label": "Mine", "hint": "x", "telemetry_key": "win_turn"})
        out = [x["slug"] for x in sf.suggest(sf.list_catalog(self.r), STATS)]
        self.assertNotIn("mine", out)
        sf.update(self.r, "closing", {"rules": []})
        out = [x["slug"] for x in sf.suggest(sf.list_catalog(self.r), STATS)]
        self.assertNotIn("closing", out)

    def test_run_snapshot_survives_rename_and_delete(self):
        slugs, snap = sf.normalize_focus(["ramp"], sf.list_catalog(self.r))
        sf.update(self.r, "ramp", {"label": "Renamed"})
        sf.delete(self.r, "ramp")
        self.assertEqual(snap, [{"slug": "ramp", "label": "Ramp"}])  # frozen at run time
        self.assertEqual(sf.normalize_focus(["ramp"], sf.list_catalog(self.r))[0], [])

    def test_simulate_reads_edits_from_store(self):
        sf.update(self.r, "ramp", {"label": "Edited"})
        sf.create(self.r, {"label": "Fresh", "hint": "x"})
        sf.delete(self.r, "tempo")
        labels = {c["slug"]: c["label"] for c in sf.available_catalog(self.r, sf.SUPPORTED_TELEMETRY | {"mana_pool"})}
        self.assertEqual(labels["ramp"], "Edited")
        self.assertIn("fresh", labels)
        self.assertNotIn("tempo", labels)


class TestManageUi(unittest.TestCase):
    def test_tags_page_has_focus_section_with_confirm_and_selects(self):
        html = (ROOT / "src/web/templates" / "tags.html").read_text()
        for needle in ("Simulate focus chips", "/api/sim/focus-chips", "/api/sim/focus-chips/order",
                       "confirm(", "createFocusChip", "deleteFocusChip", "moveFocusChip"):
            self.assertIn(needle, html)
        self.assertNotIn('type="number"', html.split("Simulate focus chips", 1)[1].split("search-container", 1)[0])

    def test_report_renders_from_snapshot(self):
        js = (ROOT / "src/web/static" / "js" / "deck_sim.js").read_text()
        self.assertIn("focus_snapshot", js)


if __name__ == "__main__":
    unittest.main()
