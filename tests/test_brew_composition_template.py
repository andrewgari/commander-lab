"""BREW-1: Brew builds to a user-chosen composition template (form > profile > app default).

Covers each resolution branch, a preset with made-up categories, overlapping categories,
soft target-vs-actual reporting (every miss has a reason) and the profile default field.
"""
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.draft.brew_builder as brew_builder
import src.server.domain.draft.brew_scoring as bs
import src.server.domain.draft.brew_scoring_config as bsc
import src.server.domain.draft.reconstruct_pools as reconstruct_pools
from fastapi.testclient import TestClient

from src.server.domain.analytics import composition as comp
from src.server.domain.analytics import composition_service as svc
from src.server.domain.profile import player_profile as pp
from tests.support.brew_gate_seed import pin_gate_fill, seed_gate_catalog
from tests.test_brew import FakeRedis, make_deck, static_price_lookup

RAMP_PRESET = {"id": "p-ramp", "name": "Ramp heavy", "structure": {"deck_size": 100, "commanders": 1, "lands": 36},
               "slots": [{"ref": "core:ramp", "target": 14, "min": None, "max": None},
                         {"ref": "core:card_advantage", "target": 9, "min": None, "max": None}]}
TOKENS_PRESET = {"id": "p-tokens", "name": "Tokens", "structure": {"deck_size": 100, "commanders": 1, "lands": 35},
                 "slots": [{"ref": "tokens", "target": 15, "min": 14, "max": 16},
                           {"ref": "cat:g-aristocrats", "target": 8}]}
PROFILE_PRESET = {"id": "p-profile", "name": "Profile pick", "structure": {"deck_size": 100, "commanders": 1, "lands": 37},
                  "slots": [{"ref": "core:ramp", "target": 11}]}


def _seed_presets(r, *presets):
    r.set("comp:presets", json.dumps(list(presets)))


class TestResolution(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        _seed_presets(self.r, RAMP_PRESET, TOKENS_PRESET, PROFILE_PRESET)

    def test_app_default_is_command_zone_template(self):
        tpl = svc.resolve_brew_template(self.r)
        self.assertEqual(tpl["source"], "app")
        self.assertIsNone(tpl["preset_id"])
        targets = {s["key"]: s["target"] for s in tpl["slots"]}
        self.assertEqual(targets, {"lands": 38, "ramp": 10, "card_advantage": 12, "targeted_disruption": 12,
                                   "mass_disruption": 6, "plan": 22})
        self.assertEqual(targets, comp.DEFAULT_TEMPLATE)

    def test_form_beats_profile_and_app(self):
        tpl = svc.resolve_brew_template(self.r, "p-ramp", "p-profile")
        self.assertEqual((tpl["source"], tpl["preset_id"]), ("form", "p-ramp"))

    def test_profile_default_beats_app_default(self):
        for choice in ("", "profile", None):
            tpl = svc.resolve_brew_template(self.r, choice, "p-profile")
            self.assertEqual((tpl["source"], tpl["preset_id"]), ("profile", "p-profile"), choice)

    def test_app_choice_skips_profile_default(self):
        tpl = svc.resolve_brew_template(self.r, "app", "p-profile")
        self.assertEqual(tpl["source"], "app")

    def test_no_profile_default_falls_to_app(self):
        self.assertEqual(svc.resolve_brew_template(self.r, "profile", None)["source"], "app")

    def test_stale_profile_default_falls_to_app(self):
        self.assertEqual(svc.resolve_brew_template(self.r, "", "p-deleted")["source"], "app")

    def test_unknown_form_preset_is_an_error(self):
        with self.assertRaises(ValueError):
            svc.resolve_brew_template(self.r, "p-nope", "p-profile")

    def test_custom_categories_and_counts_pass_through(self):
        tpl = svc.resolve_brew_template(self.r, "p-tokens")
        by_key = {s["key"]: s for s in tpl["slots"]}
        self.assertEqual((by_key["tokens"]["target"], by_key["tokens"]["low"], by_key["tokens"]["high"]), (15, 14, 16))
        self.assertIn("cat:g-aristocrats", by_key)
        self.assertEqual(by_key["lands"]["target"], 35)

    def test_skeleton_constant_is_gone(self):
        self.assertFalse(hasattr(bsc, "DEFAULT_SKELETON"))
        self.assertFalse(hasattr(bsc.BrewScoringConfig(), "skeleton"))


class TestCategoryCounting(unittest.TestCase):
    def test_one_card_counts_toward_several_categories(self):
        sk = bsc.skeleton_from_template(svc.template_from_preset(TOKENS_PRESET, "form"))
        cards = [{"name": "A", "tags": ["tokens", "aristocrats"]}, {"name": "B", "tags": ["tokens"]}, {"name": "C"}]
        counts = bs.count_categories(cards, sk)
        self.assertEqual(counts["tokens"], 2)
        self.assertEqual(counts["cat:g-aristocrats"], 1)
        self.assertEqual(bs._matching(cards[0], sk), ["tokens", "cat:g-aristocrats"])

    def test_classifier_rows_count_without_tags(self):
        sk = bsc.skeleton_from_template(svc.app_default_template())
        draw = {"name": "Divination", "type_line": "Sorcery", "oracle_text": "Draw two cards."}
        self.assertEqual(bs._matching(draw, sk), ["card_advantage"])
        land = {"name": "Forest", "type_line": "Basic Land - Forest", "oracle_text": ""}
        self.assertEqual(bs._reported(land, sk), ["lands"])

    def test_lands_are_reported_but_do_not_steer_selection(self):
        sk = bsc.skeleton_from_template(svc.app_default_template())
        land = {"name": "Forest", "type_line": "Basic Land - Forest", "oracle_text": ""}
        self.assertEqual(bs._matching(land, sk), [])
        rows = {r["category"]: r for r in bs.composition_report([{**land, "quantity": 3}], sk)}
        self.assertEqual(rows["lands"]["actual"], 3)
        self.assertEqual(rows["plan"]["actual"], 0)

    def test_report_is_soft_and_plan_counts_the_rest(self):
        sk = bsc.skeleton_from_template(svc.app_default_template())
        cards = [{"name": "Divination", "type_line": "Sorcery", "oracle_text": "Draw two cards.", "quantity": 1},
                 {"name": "Bear", "type_line": "Creature - Bear", "oracle_text": "", "quantity": 2}]
        rows = {r["category"]: r for r in bs.composition_report(cards, sk)}
        self.assertEqual(rows["card_advantage"]["actual"], 1)
        self.assertEqual(rows["plan"]["actual"], 2)
        self.assertEqual(rows["card_advantage"]["delta"], 1 - 12)


class TestBrewEndToEnd(unittest.TestCase):
    def setUp(self):
        import src.server.app.main as app_module
        _p = patch("src.server.domain.inventory.card_search._schedule_pool_refresh")
        _p.start()
        self.addCleanup(_p.stop)
        self.r = FakeRedis()
        seed_gate_catalog(self.r)
        pin_gate_fill(self)
        self.deck1 = make_deck(deck_id=42, name="Atraxa Superfriends", status="physical")
        self.r.set("decks", json.dumps([self.deck1]))
        _seed_presets(self.r, RAMP_PRESET, TOKENS_PRESET, PROFILE_PRESET)
        self._orig = reconstruct_pools.redis_price_lookup
        reconstruct_pools.redis_price_lookup = static_price_lookup({})
        brew_builder.set_builder(brew_builder.StubBrewBuilder())
        self.addCleanup(lambda: setattr(reconstruct_pools, "redis_price_lookup", self._orig))
        p = patch.object(app_module, "r", self.r)
        p.start()
        self.addCleanup(p.stop)
        self.profile_default = None
        pd = patch("src.server.infra.services.player_profile_service.default_composition_preset",
                   side_effect=lambda *a, **k: self.profile_default)
        pd.start()
        self.addCleanup(pd.stop)
        self.client = TestClient(app_module.app)

    def _start(self, **over):
        body = {"commander": "Atraxa, Praetors' Voice", "donor_deck_ids": ["archidekt:42"],
                "build_mode": "owned_first", "name": "t", "power_level": "3"}
        body.update(over)
        return self.client.post("/api/brew/start", json=body)

    def _template(self, **over):
        res = self._start(**over)
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["session"]["composition_template"]

    def test_start_resolution_branches(self):
        self.profile_default = "p-profile"
        t = self._template(composition="p-ramp")
        self.assertEqual((t["source"], t["preset_id"]), ("form", "p-ramp"))
        t = self._template()
        self.assertEqual((t["source"], t["preset_id"]), ("profile", "p-profile"))
        t = self._template(composition="profile")
        self.assertEqual(t["source"], "profile")
        t = self._template(composition="app")
        self.assertEqual(t["source"], "app")
        self.profile_default = None
        self.assertEqual(self._template()["source"], "app")

    def test_unknown_form_preset_is_rejected(self):
        res = self._start(composition="p-nope")
        self.assertEqual(res.status_code, 404)

    def _approve_then_build(self, sid):
        """Hard rules must be approved before a build (BREW-2); an empty approval = no hard rules."""
        ok = self.client.post(f"/api/brew/{sid}/approve-rules", json={"rules": []})
        self.assertEqual(ok.status_code, 200, ok.text)
        return self.client.post(f"/api/brew/{sid}/build?sync=true")

    def _build(self, **over):
        sid = self._start(**over).json()["session_id"]
        res = self._approve_then_build(sid)
        self.assertEqual(res.status_code, 200, res.text)
        draft = self.client.get(f"/api/brew/{sid}/drafts").json()["drafts"][0]
        return sid, draft

    def test_made_up_categories_brew_and_report_soft(self):
        sid, draft = self._build(composition="p-tokens")
        self.assertEqual(draft["composition_template"], {"source": "form", "preset_id": "p-tokens", "name": "Tokens"})
        rows = {r["category"]: r for r in draft["composition"]["rows"]}
        self.assertEqual(rows["tokens"]["target"], 15)
        self.assertIn("cat:g-aristocrats", rows)
        for row in rows.values():
            if row["actual"] != row["target"]:
                self.assertTrue(row.get("reason"), row)
            else:
                self.assertNotIn("reason", row)
        session = self.client.get(f"/api/brew/{sid}").json()["session"]
        self.assertEqual(session["composition_template"]["preset_id"], "p-tokens")

    def test_app_default_report_has_all_template_rows(self):
        _, draft = self._build()
        self.assertEqual(draft["composition_template"]["source"], "app")
        rows = draft["composition"]["rows"]
        self.assertEqual([r["category"] for r in rows], list(comp.ROW_KEYS))
        for row in rows:
            self.assertEqual(row["target"], comp.DEFAULT_TEMPLATE[row["category"]])
            if row["actual"] != row["target"]:
                self.assertTrue(row.get("reason"), row)


class TestProfileDefault(unittest.TestCase):
    def test_default_composition_preset_persists_through_edit(self):
        base = pp.profile_from_seed({"name": "t"}, at="2026-01-01T00:00:00Z")
        self.assertIsNone(base.default_composition_preset)
        data = base.to_dict()
        data["default_composition_preset"] = "p-ramp"
        edited = pp.apply_stated_edit(base, data)
        self.assertEqual(edited.default_composition_preset, "p-ramp")
        again = pp.parse_profile({**edited.to_dict(), "provenance": {k: v.to_dict() for k, v in edited.provenance.items()}})
        self.assertEqual(again.default_composition_preset, "p-ramp")
        cleared = pp.apply_stated_edit(edited, {**edited.to_dict(), "default_composition_preset": None})
        self.assertIsNone(cleared.default_composition_preset)

    def test_schema_rejects_non_string(self):
        ok, errors = pp.validate_profile({"name": "t", "default_composition_preset": 5})
        self.assertFalse(ok)
        self.assertTrue(any("default_composition_preset" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
