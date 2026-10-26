"""Deck reviewer card 04: player profile + feedback loop.

D7: in-memory SQLite only, no live Postgres/Redis. unittest style so CI's
discovery collects it. Snapshot: tests/snapshots/player_profile_prompt.txt
(regenerate with UPDATE_SNAPSHOTS=1).
"""
from __future__ import annotations

import copy
import json
import os
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.app.api import profile as profile_api
from src.server.app.api.deps import db_session
from src.server.domain.intake.user_intent import parse_user_intent
from src.server.domain.profile import player_profile as pp
from src.server.infra.db.orm_models import Base
from src.server.infra.services import player_profile_service as svc

SEED = Path(__file__).resolve().parents[1] / "profiles" / "andrew.json"
SNAP = Path(__file__).parent / "snapshots" / "player_profile_prompt.txt"
T0 = "2026-01-01T00:00:00Z"
T1 = "2026-02-01T00:00:00Z"


def sample_values() -> dict:
    return {
        "name": "tester",
        "builder_identity": {"text": "Builds more than pilots.", "tags": ["builder", "wacky"]},
        "color_affinities": {"Bant": 0.8, "Naya": 0.6},
        "archetype_affinities": {"equipment": 0.9, "auras": 0.7},
        "favored_mechanics": ["proliferate"],
        "pet_cards": ["Elspeth, Sun's Champion"],
        "dislikes": {"cards": ["Stax Piece"], "strategies": ["mass land destruction"], "play_patterns": []},
        "power_tendencies": {"tier": "focused", "notes": "tuned but not cutthroat"},
        "budget_norms": {"hard_cap": 50, "soft_cap": 20, "currency": "USD"},
        "review_preferences": {"depth": "deep", "tone": "direct", "skip": ["padding"],
                               "welcome_generic_staples": False},
        "done_when": ["curve is smooth", "concept intact"],
    }


def sample_profile() -> pp.PlayerProfile:
    return pp.parse_profile(pp.stamp_provenance(sample_values(), pp.STATED, T0))


def make_session():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


class ValidationTests(unittest.TestCase):
    def test_valid_profile_roundtrips(self):
        ok, errs = pp.validate_profile(pp.stamp_provenance(sample_values(), pp.STATED, T0))
        self.assertTrue(ok, errs)

    def test_defaults_resolver_backfills(self):
        r = pp.resolve_profile_defaults({"weird_key": 1})
        self.assertEqual(r["budget_norms"]["currency"], "USD")
        self.assertEqual(r["dislikes"], {"cards": [], "strategies": [], "play_patterns": []})
        self.assertEqual(r["metadata"], {"weird_key": 1})

    def test_empty_profile_is_valid(self):
        p = pp.parse_profile({})
        self.assertEqual(p.pet_cards, [])
        self.assertEqual(p.budget_norms.currency, "USD")

    def test_rejects_bad_values(self):
        base = pp.stamp_provenance(sample_values(), pp.STATED, T0)
        for mutate, needle in [
            (lambda d: d["color_affinities"].update(Bant=1.5), "Bant"),
            (lambda d: d["budget_norms"].update(currency="XXX"), "currency"),
            (lambda d: d["review_preferences"].update(depth="epic"), "depth"),
            (lambda d: d["budget_norms"].update(soft_cap=99), "soft_cap"),
            (lambda d: d.update(surprise=1), "surprise"),
        ]:
            d = copy.deepcopy(base)
            mutate(d)
            ok, errs = pp.validate_profile(d)
            self.assertFalse(ok)
            self.assertTrue(any(needle in e for e in errs), (needle, errs))

    def test_pet_and_disliked_card_conflict(self):
        v = sample_values()
        v["dislikes"]["cards"].append("elspeth, sun's champion")
        ok, errs = pp.validate_profile(pp.stamp_provenance(v, pp.STATED, T0))
        self.assertFalse(ok)
        self.assertTrue(any("pet card" in e for e in errs))

    def test_provenance_required_for_every_value(self):
        d = pp.stamp_provenance(sample_values(), pp.STATED, T0)
        del d["provenance"]["pet_cards/Elspeth, Sun's Champion"]
        ok, errs = pp.validate_profile(d)
        self.assertFalse(ok)
        self.assertTrue(any("provenance missing" in e for e in errs))

    def test_provenance_source_enum(self):
        d = pp.stamp_provenance(sample_values(), pp.STATED, T0)
        d["provenance"]["budget_norms.hard_cap"]["source"] = "guessed"
        ok, _ = pp.validate_profile(d)
        self.assertFalse(ok)

    def test_seed_file_is_valid_and_all_stated(self):
        profile = pp.profile_from_seed(json.loads(SEED.read_text()), T0)
        self.assertTrue(profile.provenance)
        self.assertTrue(all(p.source == pp.STATED for p in profile.provenance.values()))
        self.assertIn("Bant", profile.color_affinities)
        self.assertIn("equipment", profile.archetype_affinities)
        self.assertEqual(profile.review_preferences.tone, "direct")
        self.assertTrue(any("Elspeth" in t for t in profile.builder_identity.tags))
        self.assertTrue(any("Venser" in t for t in profile.builder_identity.tags))


class ProvenanceTests(unittest.TestCase):
    def test_learned_cannot_overwrite_stated(self):
        profile = sample_profile()
        prop = pp.Proposal("dislikes.cards", "add", "Stax Piece", "x")
        with self.assertRaises(pp.ProvenanceConflict):
            pp.apply_confirmed_proposal(profile, prop, now=T1)
        self.assertEqual(profile.provenance["dislikes.cards/Stax Piece"].source, pp.STATED)

    def test_learned_set_on_stated_scalar_refused(self):
        prop = pp.Proposal("budget_norms.soft_cap", "set", 5.0)
        with self.assertRaises(pp.ProvenanceConflict):
            pp.apply_confirmed_proposal(sample_profile(), prop, now=T1)

    def test_learned_on_new_value_is_tagged_with_time_and_evidence(self):
        prop = pp.Proposal("dislikes.cards", "add", "Rhystic Study", "r", ["rev1", "rev2"])
        out = pp.apply_confirmed_proposal(sample_profile(), prop, now=T1)
        self.assertIn("Rhystic Study", out.dislikes.cards)
        p = out.provenance["dislikes.cards/Rhystic Study"]
        self.assertEqual((p.source, p.at, p.evidence), (pp.LEARNED, T1, ["rev1", "rev2"]))
        # stated values untouched
        self.assertEqual(out.provenance["pet_cards/Elspeth, Sun's Champion"].at, T0)

    def test_apply_does_not_mutate_input(self):
        profile = sample_profile()
        before = profile.to_dict()
        pp.apply_confirmed_proposal(profile, pp.Proposal("dislikes.cards", "add", "New"), now=T1)
        self.assertEqual(profile.to_dict(), before)

    def test_stated_edit_keeps_unchanged_provenance_and_stamps_changes(self):
        profile = sample_profile()
        v = sample_values()
        v["budget_norms"]["hard_cap"] = 75
        out = pp.apply_stated_edit(profile, v, now=T1)
        self.assertEqual(out.provenance["budget_norms.hard_cap"].at, T1)
        self.assertEqual(out.provenance["budget_norms.soft_cap"].at, T0)

    def test_stated_edit_can_replace_a_learned_value(self):
        learned = pp.apply_confirmed_proposal(
            sample_profile(), pp.Proposal("dislikes.cards", "add", "Rhystic Study"), now=T1)
        v = learned.to_dict()
        v.pop("provenance")
        v["dislikes"]["cards"].remove("Rhystic Study")
        v["dislikes"]["cards"].append("Rhystic Study")  # re-stated by owner
        out = pp.apply_stated_edit(learned, v, now="2026-03-01T00:00:00Z")
        # unchanged leaf keeps its learned provenance (owner did not touch it)
        self.assertEqual(out.provenance["dislikes.cards/Rhystic Study"].source, pp.LEARNED)
        v["dislikes"]["cards"].remove("Rhystic Study")
        out2 = pp.apply_stated_edit(out, v, now="2026-03-02T00:00:00Z")
        self.assertNotIn("dislikes.cards/Rhystic Study", out2.provenance)


class PrecedenceTests(unittest.TestCase):
    def test_deck_budget_beats_profile(self):
        intent = parse_user_intent({"budget_constraints": {"hard_cap": 10, "soft_cap": 5}})
        eff, notes = pp.resolve_with_intent(sample_profile(), intent)
        self.assertEqual((eff.budget_norms.hard_cap, eff.budget_norms.soft_cap), (10.0, 5.0))
        self.assertTrue(any("budget.hard_cap" in n for n in notes))

    def test_no_budget_clears_caps(self):
        intent = parse_user_intent({"budget_constraints": {"no_budget": True}})
        eff, _ = pp.resolve_with_intent(sample_profile(), intent)
        self.assertIsNone(eff.budget_norms.hard_cap)

    def test_deck_pet_card_beats_profile_dislike(self):
        intent = parse_user_intent({"pet_cards": ["Stax Piece"]})
        eff, notes = pp.resolve_with_intent(sample_profile(), intent)
        self.assertNotIn("Stax Piece", eff.dislikes.cards)
        self.assertEqual(eff.pet_cards[0], "Stax Piece")
        self.assertTrue(any("pet cards" in n for n in notes))

    def test_deck_power_beats_profile(self):
        intent = parse_user_intent({"target_power_level": {"scale": 9, "tier": "competitive"}},
                                   strict_coherence=False)
        eff, notes = pp.resolve_with_intent(sample_profile(), intent)
        self.assertEqual(eff.power_tendencies.tier, "competitive")
        self.assertTrue(any("power" in n for n in notes))

    def test_input_profile_not_mutated_and_none_intent_is_identity(self):
        profile = sample_profile()
        before = profile.to_dict()
        pp.resolve_with_intent(profile, parse_user_intent({"budget_constraints": {"hard_cap": 1}}))
        self.assertEqual(profile.to_dict(), before)
        eff, notes = pp.resolve_with_intent(profile, None)
        self.assertEqual((eff.to_dict(), notes), (before, []))


class RenderTests(unittest.TestCase):
    def test_deterministic(self):
        a = pp.render_profile_for_prompt(sample_profile(), None)
        b = pp.render_profile_for_prompt(sample_profile(), None)
        self.assertEqual(a.encode(), b.encode())

    def test_order_independent_of_dict_insertion(self):
        v = sample_values()
        v2 = copy.deepcopy(v)
        v2["color_affinities"] = dict(reversed(list(v["color_affinities"].items())))
        a = pp.render_profile_for_prompt(pp.parse_profile(pp.stamp_provenance(v, pp.STATED, T0)))
        b = pp.render_profile_for_prompt(pp.parse_profile(pp.stamp_provenance(v2, pp.STATED, T0)))
        self.assertEqual(a, b)

    def test_snapshot(self):
        intent = parse_user_intent({"budget_constraints": {"hard_cap": 10}, "pet_cards": ["Stax Piece"]})
        out = pp.render_profile_for_prompt(sample_profile(), intent)
        if os.environ.get("UPDATE_SNAPSHOTS"):
            SNAP.parent.mkdir(exist_ok=True)
            SNAP.write_text(out, encoding="utf-8")
        self.assertEqual(out, SNAP.read_text(encoding="utf-8"))

    def test_learned_values_are_marked(self):
        p = pp.apply_confirmed_proposal(sample_profile(), pp.Proposal("dislikes.cards", "add", "Rhystic Study"), now=T1)
        self.assertIn("Rhystic Study [learned]", pp.render_profile_for_prompt(p))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.factory = make_session()
        self.s = self.factory()
        svc.seed_profile(self.s, "andrew", SEED)

    def tearDown(self):
        self.s.close()

    def fb(self, review, cut, add, decision, reason=""):
        svc.record_feedback(self.s, "andrew", deck_id="d1", review_id=review, cut=cut,
                            add=add, decision=decision, reason=reason)

    def test_seed_is_idempotent_and_stated(self):
        p = svc.seed_profile(self.s, "andrew", SEED)
        self.assertEqual(len(svc.list_versions(self.s, "andrew")), 1)
        self.assertTrue(all(x.source == pp.STATED for x in p.provenance.values()))

    def test_load_profile_unknown_raises(self):
        with self.assertRaises(pp.ProfileNotFound):
            svc.load_profile(self.s, "nobody")

    def test_feedback_roundtrip(self):
        self.fb("r1", "Sol Ring", "Mana Vault", "rejected", "too fast")
        self.fb("r1", "Island", "Command Tower", "accepted")
        self.fb("r1", "Plains", "Path", "maybe")
        entries = svc.get_feedback(self.s, "andrew", "d1")
        self.assertEqual([(e.cut, e.add, e.decision) for e in entries],
                         [("Sol Ring", "Mana Vault", "rejected"),
                          ("Island", "Command Tower", "accepted"),
                          ("Plains", "Path", "maybe")])
        self.assertEqual(entries[0].reason, "too fast")
        self.assertEqual(svc.previously_rejected(self.s, "andrew", "d1"), {("Sol Ring", "Mana Vault")})
        self.assertEqual(svc.previously_rejected(self.s, "andrew", "other-deck"), set())

    def test_later_acceptance_clears_rejection(self):
        self.fb("r1", "A", "B", "rejected")
        self.fb("r2", "A", "B", "accepted")
        self.assertEqual(svc.previously_rejected(self.s, "andrew", "d1"), set())

    def test_bad_decision_rejected(self):
        with self.assertRaises(ValueError):
            self.fb("r1", "A", "B", "yolo")

    def test_summarizer_proposes_but_never_applies(self):
        self.fb("r1", "A", "Rhystic Study", "rejected")
        self.fb("r2", "C", "Rhystic Study", "rejected")
        before = svc.load_profile(self.s, "andrew").to_dict()
        pending = svc.generate_proposals(self.s, "andrew")
        self.assertEqual([(p["path"], p["value"]) for p in pending],
                         [("dislikes.cards", "Rhystic Study")])
        after = svc.load_profile(self.s, "andrew")
        self.assertEqual(after.to_dict(), before)  # nothing auto-applied
        self.assertEqual(len(svc.list_versions(self.s, "andrew")), 1)
        self.assertNotIn("Rhystic Study", after.dislikes.cards)

    def test_single_rejection_proposes_nothing(self):
        self.fb("r1", "A", "B", "rejected")
        self.assertEqual(svc.generate_proposals(self.s, "andrew"), [])

    def test_confirm_applies_as_learned_and_appends_version(self):
        self.fb("r1", "A", "Rhystic Study", "rejected")
        self.fb("r2", "C", "Rhystic Study", "rejected")
        pid = svc.generate_proposals(self.s, "andrew")[0]["id"]
        p = svc.confirm_proposal(self.s, "andrew", pid)
        self.assertIn("Rhystic Study", p.dislikes.cards)
        self.assertEqual(p.provenance["dislikes.cards/Rhystic Study"].source, pp.LEARNED)
        self.assertEqual(len(svc.list_versions(self.s, "andrew")), 2)
        self.assertEqual(svc.list_proposals(self.s, "andrew"), [])
        with self.assertRaises(ValueError):  # cannot confirm twice
            svc.confirm_proposal(self.s, "andrew", pid)

    def test_dismissed_is_not_reproposed_or_applied(self):
        self.fb("r1", "A", "X", "rejected")
        self.fb("r2", "B", "X", "rejected")
        pid = svc.generate_proposals(self.s, "andrew")[0]["id"]
        svc.dismiss_proposal(self.s, "andrew", pid)
        self.assertEqual(svc.generate_proposals(self.s, "andrew"), [])
        self.assertNotIn("X", svc.load_profile(self.s, "andrew").dislikes.cards)

    def test_confirm_refused_when_owner_stated_it_meanwhile(self):
        self.fb("r1", "A", "X", "rejected")
        self.fb("r2", "B", "X", "rejected")
        pid = svc.generate_proposals(self.s, "andrew")[0]["id"]
        v = svc.load_profile(self.s, "andrew").to_dict()
        v.pop("provenance")
        v["dislikes"]["cards"].append("X")
        svc.save_stated_edit(self.s, "andrew", v)
        with self.assertRaises(pp.ProvenanceConflict):
            svc.confirm_proposal(self.s, "andrew", pid)

    def test_stated_edit_appends_history(self):
        v = svc.load_profile(self.s, "andrew").to_dict()
        v.pop("provenance")
        v["budget_norms"]["hard_cap"] = 30
        p = svc.save_stated_edit(self.s, "andrew", v)
        self.assertEqual(p.budget_norms.hard_cap, 30.0)
        self.assertEqual(p.provenance["budget_norms.hard_cap"].source, pp.STATED)
        self.assertEqual([x["version"] for x in svc.list_versions(self.s, "andrew")], [2, 1])


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.factory = make_session()

        def override():
            s = self.factory()
            try:
                yield s
                s.commit()
            finally:
                s.close()

        app = FastAPI()
        app.include_router(profile_api.router, prefix="/api/v2")
        app.dependency_overrides[db_session] = override
        self.c = TestClient(app)

    def test_flow(self):
        base = "/api/v2/profile/player/andrew"
        self.assertEqual(self.c.get(base).status_code, 404)
        self.assertEqual(self.c.post(f"{base}/seed").status_code, 200)
        body = self.c.get(base).json()
        self.assertIn("pet_cards", body["profile"])
        for r in ("r1", "r2"):
            self.assertEqual(self.c.post(f"{base}/feedback", json={
                "deck_id": "d", "review_id": r, "cut": "A", "add": "Z", "decision": "rejected"}).status_code, 200)
        self.assertEqual(self.c.post(f"{base}/feedback", json={
            "deck_id": "d", "review_id": "r", "cut": "A", "add": "Z", "decision": "nope"}).status_code, 422)
        pend = self.c.post(f"{base}/proposals/generate").json()["pending"]
        self.assertEqual(len(pend), 1)
        # still unapplied
        self.assertNotIn("Z", self.c.get(base).json()["profile"]["dislikes"]["cards"])
        out = self.c.post(f"{base}/proposals/{pend[0]['id']}/confirm").json()
        self.assertIn("Z", out["profile"]["dislikes"]["cards"])
        self.assertEqual(out["provenance"]["dislikes.cards/Z"]["source"], "learned_from_feedback")
        # invalid edit -> 422
        bad = self.c.put(base, json={"values": {"budget_norms": {"currency": "XXX"}}})
        self.assertEqual(bad.status_code, 422)


class MigrationTests(unittest.TestCase):
    def test_a009_chain_and_ddl(self):
        from src.server.infra.db.migrations.versions import a009_player_profile as m
        self.assertEqual(m.down_revision, "a008_card_dossier")
        self.assertEqual(len(m.ALL_DDLS), 4)
        self.assertTrue(all("IF NOT EXISTS" in d for d in m.ALL_DDLS if "CREATE" in d))


if __name__ == "__main__":
    unittest.main()
