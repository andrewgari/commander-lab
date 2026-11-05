"""Pass D code checks: each one must catch its violation, and only that violation. Fully offline, no LLM."""
from __future__ import annotations

import copy
import unittest

from src.server.domain.analytics.review_pipeline import checks
from src.server.domain.analytics.review_pipeline.checks import CheckContext, run_checks
from src.server.domain.profile import player_profile as pp

SCRY = {
    "Meren": {"color_identity": ["B", "G"], "legalities": {"commander": "legal"}},
    "Sol Ring": {"color_identity": [], "legalities": {"commander": "legal"}},
    "Eternal Witness": {"color_identity": ["G"], "legalities": {"commander": "legal"}},
    "Sakura-Tribe Elder": {"color_identity": ["G"], "legalities": {"commander": "legal"}},
    "Viscera Seer": {"color_identity": ["B"], "legalities": {"commander": "legal"}},
    "Skullclamp": {"color_identity": [], "legalities": {"commander": "legal"}},
    "Counterspell": {"color_identity": ["U"], "legalities": {"commander": "legal"}},
    "Mana Crypt": {"color_identity": [], "legalities": {"commander": "banned"}},
    "Jeweled Lotus": {"color_identity": [], "legalities": {"commander": "legal"}},
}


def ctx(**deck):
    base = {"commanders": ["Meren"], "cards": ["Meren", "Sol Ring", "Eternal Witness", "Sakura-Tribe Elder"], "pet_cards": ["Eternal Witness"],
            "budget_hard_cap": 50.0, "rejected_swaps": [], "candidate_pool": ["Viscera Seer", "Skullclamp", "Counterspell", "Mana Crypt", "Jeweled Lotus"],
            "game_changers": ["Jeweled Lotus", "Mana Crypt"]}
    base.update(deck)
    return CheckContext(deck=base, scryfall=copy.deepcopy(SCRY))


def swap(i="sw-1", cut="Sakura-Tribe Elder", add="Viscera Seer", **kw):
    return {"id": i, "cut": {"name": cut}, "add": {"name": add}, "resolves_finding_id": "p-1", "tradeoff": "x", "bracket_effect": "none",
            "budget_effect": "x", "alternative": None, "owned": False, "price": 1.0, **kw}


def doc(*swaps, **extra):
    d = {"meta": {"commanders": [{"name": "Meren"}]}, "problems": [{"id": "p-1", "evidence": {"cards": [{"name": "Sol Ring"}], "fact_keys": []}}],
         "strengths": [], "swaps": list(swaps), "cuts_to_consider": [], "bracket": {"estimated": 2, "target": 3, "reasons": []}}
    d.update(extra)
    return d


def only(violations, gate):
    return [v for v in violations if v.gate == gate]


class ChecksCatchTheirViolation(unittest.TestCase):
    def test_a_clean_document_has_no_violation(self):
        self.assertEqual(run_checks(doc(swap()), ctx(), checks.PIPELINE_CHECKS), [])

    def test_hallucinated_name_in_add_names_the_swap(self):
        v = run_checks(doc(swap(add="Totally Fake Card")), ctx(), checks.PIPELINE_CHECKS)
        h = only(v, "hallucinated_card_names")
        self.assertEqual([x.items for x in h], [("sw-1",)])
        self.assertIn("Totally Fake Card", h[0].message)

    def test_hallucinated_name_in_finding_evidence_names_the_finding(self):
        d = doc(swap())
        d["problems"][0]["evidence"]["cards"] = [{"name": "Imaginary Card"}]
        h = only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "hallucinated_card_names")
        self.assertEqual([x.items for x in h], [("p-1",)])

    def test_color_identity_violation(self):
        v = only(run_checks(doc(swap(add="Counterspell")), ctx(candidate_pool=None), checks.PIPELINE_CHECKS), "add_outside_color_identity")
        self.assertEqual([x.items for x in v], [("sw-1",)])
        self.assertIn("['U']", v[0].message)

    def test_legality_violation(self):
        v = only(run_checks(doc(swap(add="Mana Crypt")), ctx(candidate_pool=None), checks.PIPELINE_CHECKS), "add_not_commander_legal")
        self.assertEqual([x.items for x in v], [("sw-1",)])
        self.assertIn("banned", v[0].message)

    def test_singleton_names_the_swap_that_creates_the_duplicate(self):
        d = doc(swap("sw-1", cut="Sakura-Tribe Elder", add="Viscera Seer"), swap("sw-2", cut="Sol Ring", add="Viscera Seer"))
        v = only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "singleton_violations")
        self.assertEqual([x.items for x in v], [("sw-2",)])

    def test_add_already_in_deck(self):
        v = only(run_checks(doc(swap(add="Sol Ring")), ctx(candidate_pool=None), checks.PIPELINE_CHECKS), "add_already_in_deck")
        self.assertEqual([x.items for x in v], [("sw-1",)])

    def test_pet_card_cut_in_swap_and_in_cuts_to_consider(self):
        d = doc(swap(cut="Eternal Witness"), cuts_to_consider=[{"card": {"name": "Eternal Witness"}, "reason": "x", "evidence": []}])
        v = only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "cut_is_pet_card")
        self.assertEqual(sorted(x.items for x in v), [("cut:Eternal Witness",), ("sw-1",)])

    def test_budget_cap(self):
        v = only(run_checks(doc(swap(price=75.0)), ctx(), checks.PIPELINE_CHECKS), "add_above_budget_cap")
        self.assertEqual([x.items for x in v], [("sw-1",)])
        self.assertEqual(only(run_checks(doc(swap(price=75.0)), ctx(budget_hard_cap=None), checks.PIPELINE_CHECKS), "add_above_budget_cap"), [])

    def test_bracket_delta_names_only_the_lowest_ranked_raising_swaps(self):
        d = doc(swap("sw-1", bracket_effect="raises"), swap("sw-2", cut="Sol Ring", add="Skullclamp", bracket_effect="raises"),
                bracket={"estimated": 2, "target": 3, "reasons": []})
        v = only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "swaps_exceed_target_bracket")
        self.assertEqual([x.items for x in v], [("sw-2",)], "one raise fits under target 3; only the second must go")

    def test_bracket_delta_credits_lowering_swaps(self):
        d = doc(swap("sw-1", bracket_effect="raises"), swap("sw-2", cut="Sol Ring", add="Skullclamp", bracket_effect="raises"),
                swap("sw-3", cut="Meren", add="Counterspell", bracket_effect="lowers"), bracket={"estimated": 2, "target": 3, "reasons": []})
        self.assertEqual(only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "swaps_exceed_target_bracket"), [])

    def test_bracket_not_checked_without_a_target(self):
        d = doc(swap(bracket_effect="raises"), bracket={"estimated": 2, "target": None, "reasons": []})
        self.assertEqual(only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "swaps_exceed_target_bracket"), [])

    def test_bracket_effect_must_match_game_changer_status_from_data(self):
        v = only(run_checks(doc(swap(add="Jeweled Lotus", bracket_effect="none")), ctx(), checks.PIPELINE_CHECKS), "bracket_effect_misstated")
        self.assertEqual([x.items for x in v], [("sw-1",)])
        self.assertIn("'raises'", v[0].message)
        self.assertEqual(only(run_checks(doc(swap(add="Jeweled Lotus", bracket_effect="raises")), ctx(), checks.PIPELINE_CHECKS), "bracket_effect_misstated"), [])
        # cutting a Game Changer for a non-GC must be declared 'lowers'
        d = ctx(cards=["Meren", "Mana Crypt", "Sol Ring"])
        v = only(run_checks(doc(swap(cut="Mana Crypt", add="Viscera Seer", bracket_effect="none")), d, checks.PIPELINE_CHECKS), "bracket_effect_misstated")
        self.assertIn("'lowers'", v[0].message)

    def test_unresolved_finding_id(self):
        v = only(run_checks(doc(swap(resolves_finding_id="p-404")), ctx(), checks.PIPELINE_CHECKS), "unresolved_finding_id")
        self.assertEqual([x.items for x in v], [("sw-1",)])

    def test_empty_evidence(self):
        d = doc(swap())
        d["problems"][0]["evidence"] = {"cards": [], "fact_keys": []}
        v = only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "empty_evidence")
        self.assertEqual([x.items for x in v], [("p-1",)])

    def test_previously_rejected_pair_for_this_deck(self):
        c = ctx(rejected_swaps=[["Sakura-Tribe Elder", "Viscera Seer"]])
        v = only(run_checks(doc(swap()), c, checks.PIPELINE_CHECKS), "previously_rejected_suggestion")
        self.assertEqual([x.items for x in v], [("sw-1",)])
        self.assertIn("already rejected", v[0].message)

    def test_previously_rejected_matches_case_and_accents_and_checks_the_alternative(self):
        c = ctx(rejected_swaps=[["sakura-tribe elder", "VISCERA SEER"]])
        self.assertEqual(len(only(run_checks(doc(swap()), c, checks.PIPELINE_CHECKS), "previously_rejected_suggestion")), 1)
        d = doc(swap(add="Skullclamp", alternative={"name": "Viscera Seer"}))
        self.assertEqual(len(only(run_checks(d, c, checks.PIPELINE_CHECKS), "previously_rejected_suggestion")), 1)

    def test_a_different_cut_for_a_rejected_add_is_allowed(self):
        c = ctx(rejected_swaps=[["Sol Ring", "Viscera Seer"]])
        self.assertEqual(only(run_checks(doc(swap()), c, checks.PIPELINE_CHECKS), "previously_rejected_suggestion"), [])

    def test_previously_rejected_uses_player_profile_semantics(self):
        """The check reads the same pairs player_profile.previously_rejected returns: a later 'accepted' lifts the rejection."""
        prof = pp.profile_from_seed({"name": "t"})
        prof.feedback.append(pp.FeedbackEntry("d1", "r1", "Sakura-Tribe Elder", "Viscera Seer", "rejected", "", "2026-01-01T00:00:00Z"))
        prof.feedback.append(pp.FeedbackEntry("d2", "r2", "Sol Ring", "Skullclamp", "rejected", "", "2026-01-01T00:00:00Z"))
        pairs = [list(p) for p in pp.previously_rejected(prof, "d1")]
        self.assertEqual(pairs, [["Sakura-Tribe Elder", "Viscera Seer"]], "another deck's rejection must not leak in")
        self.assertEqual(len(only(run_checks(doc(swap()), ctx(rejected_swaps=pairs), checks.PIPELINE_CHECKS), "previously_rejected_suggestion")), 1)
        prof.feedback.append(pp.FeedbackEntry("d1", "r3", "Sakura-Tribe Elder", "Viscera Seer", "accepted", "", "2026-02-01T00:00:00Z"))
        self.assertEqual(pp.previously_rejected(prof, "d1"), set())

    def test_add_must_come_from_the_candidate_pool(self):
        v = only(run_checks(doc(swap(add="Counterspell")), ctx(candidate_pool=["Viscera Seer"]), checks.PIPELINE_CHECKS), "add_not_in_candidate_pool")
        self.assertEqual([x.items for x in v], [("sw-1",)])
        d = doc(swap(alternative={"name": "Skullclamp"}))
        self.assertEqual(len(only(run_checks(d, ctx(candidate_pool=["Viscera Seer"]), checks.PIPELINE_CHECKS), "add_not_in_candidate_pool")), 1)

    def test_cut_must_be_a_non_commander_deck_card(self):
        self.assertEqual([x.items for x in only(run_checks(doc(swap(cut="Birds of Paradise")), ctx(), checks.PIPELINE_CHECKS), "cut_not_in_deck")], [("sw-1",)])
        v = only(run_checks(doc(swap(cut="Meren")), ctx(), checks.PIPELINE_CHECKS), "cut_not_in_deck")
        self.assertIn("commander", v[0].message)

    def test_two_swaps_cannot_cut_the_same_card(self):
        v = only(run_checks(doc(swap("sw-1"), swap("sw-2", add="Skullclamp")), ctx(), checks.PIPELINE_CHECKS), "duplicate_swap_cut")
        self.assertEqual([x.items for x in v], [("sw-2",)])

    def test_top_changes_must_reference_existing_distinct_swaps(self):
        d = doc(swap("sw-1"), swap("sw-2", cut="Sol Ring", add="Skullclamp"), swap("sw-3", cut="Meren", add="Counterspell"),
                top_changes={"swap_ids": ["sw-1", "sw-2", "sw-9"], "why_these_first": "x"})
        v = only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "top_changes_unresolved")
        self.assertTrue(v and v[0].items == ("top_changes",))
        d["top_changes"]["swap_ids"] = ["sw-1", "sw-1", "sw-2"]
        self.assertTrue(only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "top_changes_unresolved"))
        d["top_changes"]["swap_ids"] = ["sw-1", "sw-2", "sw-3"]
        self.assertEqual(only(run_checks(d, ctx(), checks.PIPELINE_CHECKS), "top_changes_unresolved"), [])

    def test_pipeline_checks_exclude_only_the_document_assembly_check(self):
        self.assertEqual(set(checks.CHECKS) - set(checks.PIPELINE_CHECKS), {"charts_mismatch_fact_pack"})


class HarnessSharesTheLibrary(unittest.TestCase):
    def test_gates_module_reuses_checks_not_a_copy(self):
        from tests.review_evals import gates
        self.assertIs(gates.GATES, checks.CHECKS)
        self.assertIs(gates.Violation, checks.Violation)
        self.assertIs(gates.EvalContext, checks.CheckContext)
        self.assertIn("previously_rejected_suggestion", gates.GATES)
        self.assertNotIn("def singleton_violations", open(gates.__file__).read(), "gates.py must not carry its own copy of a check")


if __name__ == "__main__":
    unittest.main()
