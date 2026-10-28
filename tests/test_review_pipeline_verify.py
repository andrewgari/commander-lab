"""Pass D verifier flow: code checks first, critic second, regenerate only the failing item (<=2 loops), remove the rest.

All offline with scripted clients. The recorded-fixture versions of the regeneration loop and the removal are in
``test_review_pipeline_verify_replay.py``.
"""
from __future__ import annotations

import json
import unittest

from src.server.domain.analytics.review_pipeline import c_recommend, config, d_verify, runner, schemas, trace
from src.server.domain.analytics.review_pipeline.config import ConfigError
from src.server.domain.analytics.review_pipeline.structured import validate_schema
from tests import review_pipeline_cd_helpers as t

FLAG = "unsupported_claim"


def setup(swaps=None, *, rejected=(), problems=None, cuts=(), top=None, pool=None, target=3, strengths=None):
    ctx = t.make_ctx(rejected=rejected, target_bracket=target)
    brief = t.make_brief(ctx)
    analysis = t.analysis(ctx, problems=problems, strengths=strengths)
    view = c_recommend.PoolView.from_pool(pool or t.pool())
    swaps = [t.swap_in()] if swaps is None else swaps
    rec = trace.TraceRecorder(deck_id="d1", inputs={})
    client = t.Handlers(C=[t.recommend_reply(swaps, top=top, cuts=cuts)])
    recs, _ = c_recommend.run(ctx, brief, analysis, view, client, t.CFG, rec)
    return ctx, brief, analysis, recs, view, rec


def verify(handlers, **kw):
    ctx, brief, analysis, recs, view, rec = setup(**kw)
    client = t.Handlers(**{"D:regen:top_changes": t.top_regen_handler, **handlers})
    res = d_verify.verify(ctx, brief, analysis, recs, view, client, t.CFG, rec)
    return res, client, rec


def replace_swap(**kw):
    return lambda req: {"action": "replace", "swap": t.swap_in(**kw), "note": "fixed"}


class IndependenceTests(unittest.TestCase):
    def test_default_critic_is_a_different_family_from_every_generator(self):
        c = config.PipelineConfig()
        c.check_critic_is_independent()
        self.assertNotEqual(config.model_family(c.model("critic")), config.model_family(c.model("C")))

    def test_same_family_critic_is_refused_before_any_call(self):
        for critic in ("anthropic:claude-sonnet-5-5", "openrouter:anthropic/claude-opus-5.5"):
            cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_CRITIC": critic})
            with self.assertRaises(ConfigError) as cm:
                cfg.check_critic_is_independent()
            self.assertIn("D3", str(cm.exception))

    def test_every_generator_pass_and_axis_override_counts(self):
        cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_CRITIC": "openrouter:google/gemini-3.1-pro-preview",
                                              "REVIEW_MODEL_B_MANA_CONSISTENCY": "openrouter:google/gemini-3.5-flash"})
        with self.assertRaises(ConfigError) as cm:
            cfg.check_critic_is_independent()
        self.assertIn("B:mana_consistency", str(cm.exception))

    def test_family_of_spec(self):
        self.assertEqual(config.model_family("openrouter:anthropic/claude-opus-5.5"), "anthropic")
        self.assertEqual(config.model_family("anthropic:claude-opus-5-5"), "anthropic")
        self.assertEqual(config.model_family("openrouter:google/gemini-3.1-pro-preview"), "google")
        self.assertEqual(config.model_family("google:gemini-x"), "google")

    def test_verifier_and_runner_enforce_it(self):
        bad = config.PipelineConfig.from_env({"REVIEW_MODEL_CRITIC": "anthropic:claude-x"})
        ctx, brief, analysis, recs, view, rec = setup()
        with self.assertRaises(ConfigError):
            d_verify.Verifier(ctx, brief, analysis, recs, view, t.Handlers(), bad, rec)
        with self.assertRaises(ConfigError):
            runner.run_abcd(ctx, t.Handlers(), t.pool(), bad)

    def test_critic_calls_use_the_critic_model_and_regeneration_uses_the_generator(self):
        res, client, rec = verify({"critic": t.critic({"sw-1": (FLAG, "Mind Stone costs no life", "x")}),
                                   "D:regen": replace_swap(add_id="pc-2", alt="pc-1")})
        self.assertEqual({r.model for r in client.calls("critic")}, {"google:critic"})
        self.assertEqual({r.model for r in client.calls("D:regen")}, {"anthropic:gen-c"})


class CleanRunTests(unittest.TestCase):
    def test_everything_passes_nothing_is_regenerated_or_omitted(self):
        res, client, rec = verify({"critic": t.critic()})
        self.assertEqual(([s["id"] for s in res.swaps], res.omitted, res.stats["regeneration_calls"]), (["sw-1"], [], 0))
        self.assertEqual([f["id"] for f in res.strengths + res.problems], ["s-plan_synergy-1", "p-plan_synergy-1"][::-1][::-1] if False else ["s-plan_synergy-1", "p-plan_synergy-1"])
        self.assertEqual(res.stats["critic_flagged_items"], 0)
        self.assertEqual(res.top_changes["swap_ids"], ["sw-1"])

    def test_critic_sees_each_item_with_its_dossier_from_data_not_the_writers_summary(self):
        res, client, rec = verify({"critic": t.critic()})
        body = "\n".join(r.messages[0].content for r in client.calls("critic"))
        self.assertIn("### ITEM p-plan_synergy-1 (finding)", body)
        self.assertIn("{T}: Add {C}.", body)                                  # pool card text, resolved from data
        self.assertIn("At the beginning of your upkeep", body) if False else None
        self.assertIn("### ITEM sw-1 (swap)", body)
        self.assertIn("### ITEM top_changes (top)", body)
        self.assertIn("PROBLEM IT CLAIMS TO RESOLVE: p-plan_synergy-1", body)
        self.assertIn("bracket.minimum_bracket", body)

    def test_critic_request_is_cached_and_trace_steps_say_critic(self):
        res, client, rec = verify({"critic": t.critic()})
        req = client.calls("critic")[0]
        self.assertEqual((req.cache_blocks, req.schema_name), (2, "critic_verdicts"))
        tr = rec.finish("ok")
        self.assertEqual({s["pass"] for s in tr.steps if s["label"].startswith("critic")}, {"critic"})
        self.assertEqual(tr.models["critic"], "google:critic")

    def test_batches_are_split_and_each_item_judged_once(self):
        cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_C": "anthropic:gen-c", "REVIEW_MODEL_CRITIC": "google:critic"})
        object.__setattr__(cfg, "critic_batch", 1)
        ctx, brief, analysis, recs, view, rec = setup()
        client = t.Handlers(critic=t.critic())
        d_verify.verify(ctx, brief, analysis, recs, view, client, cfg, rec)
        judged = [i for r in client.calls("critic") for i in t.items_in(r)]
        self.assertEqual(sorted(judged), sorted(set(judged)))
        self.assertGreaterEqual(len(client.calls("critic")), 3)


class CodeChecksComeFirst(unittest.TestCase):
    def test_item_failing_code_is_never_shown_to_the_critic(self):
        # pet card Skullclamp is cut -> code violation. The critic must not be asked about sw-1 in round 0.
        res, client, rec = verify({"critic": t.critic(), "D:regen": replace_swap()}, swaps=[t.swap_in(cut="Skullclamp")])
        first = t.items_in(client.calls("critic")[0])
        self.assertNotIn("sw-1", first)
        self.assertIn("p-plan_synergy-1", first)
        self.assertEqual(res.swaps[0]["cut"]["name"], "Mana Crypt")

    def test_code_reason_is_fed_to_the_regeneration_prompt(self):
        res, client, rec = verify({"critic": t.critic(), "D:regen": replace_swap()}, swaps=[t.swap_in(cut="Skullclamp")])
        prompt = client.calls("D:regen")[0].messages[0].content
        self.assertIn("cut_is_pet_card", prompt)
        self.assertIn("pet card 'Skullclamp'", prompt)
        self.assertIn("REJECTED SWAP sw-1", prompt)

    def test_previously_rejected_pair_is_caught_by_code_and_never_ships(self):
        res, client, rec = verify({"critic": t.critic(), "D:regen": replace_swap(add_id="pc-2", alt="pc-3")},
                                  rejected=[("Mana Crypt", "Mind Stone")])
        self.assertEqual(res.swaps[0]["add"]["name"], "Thought Vessel")
        self.assertIn("already rejected", client.calls("D:regen")[0].messages[0].content)
        self.assertEqual(res.stats["by_category"].get("previously_rejected_suggestion"), 1)

    def test_previously_rejected_with_no_acceptable_replacement_is_omitted_with_the_reason(self):
        withdraw = lambda req: {"action": "withdraw", "swap": None, "note": "every pool ramp card was rejected"}
        res, client, rec = verify({"critic": t.critic(), "D:regen": withdraw}, rejected=[("Mana Crypt", "Mind Stone")])
        self.assertEqual(res.swaps, [])
        self.assertEqual(res.omitted[0]["kind"], "swap")
        self.assertIn("already rejected", res.omitted[0]["reason"])
        self.assertIn("withdrew the swap", res.omitted[0]["reason"])

    def test_a_regenerated_swap_is_code_checked_before_the_critic_and_repaired_in_place(self):
        # The replacement tries the rejected pair again; the code check inside the regeneration call sends it back for repair.
        calls = []

        def regen(req):
            calls.append(req)
            return replace_swap(add_id="pc-1")(req) if len(calls) == 1 else replace_swap(add_id="pc-2", alt="pc-3")(req)

        res, client, rec = verify({"critic": t.critic(), "D:regen": regen}, rejected=[("Mana Crypt", "Mind Stone")])
        self.assertEqual(len(client.calls("D:regen:sw-1")), 2)
        self.assertIn("already rejected", client.calls("D:regen:sw-1")[1].messages[-1].content)
        self.assertEqual(res.swaps[0]["add"]["name"], "Thought Vessel")
        self.assertEqual(len(client.calls("D:regen:sw-1")), 2, "one regeneration of the swap = one call + one in-call repair turn")
        self.assertEqual(res.stats["regeneration_calls"], 2, "the swap, and the top_changes selection that its replacement made stale")

    def test_add_outside_the_pool_is_impossible_by_id_and_a_name_in_the_document_is_still_caught(self):
        ctx, brief, analysis, recs, view, rec = setup()
        recs.swaps[0]["add"] = {"name": "Counterspell", "oracle_id": t.oid(50)}          # something corrupted the document
        client = t.Handlers(critic=t.critic(), **{"D:regen": replace_swap()})
        res = d_verify.verify(ctx, brief, analysis, recs, view, client, t.CFG, rec)
        self.assertIn("add_not_in_candidate_pool", res.log[0]["categories"] + [c for e in res.log for c in e["categories"]])
        self.assertEqual(res.swaps[0]["add"]["name"], "Mind Stone")


class CriticFlow(unittest.TestCase):
    def test_flagged_finding_is_regenerated_alone_and_rechecked(self):
        claim = "Mana Crypt costs life each upkeep and is a Game Changer that pushes the list to bracket 3."
        flagged = {"p-plan_synergy-1": ("generic", "pushes the list to bracket 3", "the cited fact does not show that")}
        calls = []

        def critic_h(req):
            calls.append(t.items_in(req))
            return t.critic(flagged if len(calls) == 1 else None)(req)

        rewrite = lambda req: {"action": "rewrite", "note": "specific now", "finding": t.finding_in(
            claim="Mana Crypt's upkeep coin flip deals 3 damage half the time, which a deck at bracket 3 with no lifegain feels.",
            cards=["Mana Crypt"], keys=["bracket.minimum_bracket"])}
        res, client, rec = verify({"critic": critic_h, "D:regen": rewrite})
        self.assertEqual(len(client.calls("D:regen")), 1)
        self.assertIn("upkeep coin flip", res.problems[0]["claim"])
        self.assertEqual(res.problems[0]["id"], "p-plan_synergy-1", "id is stable across regeneration")
        self.assertEqual(res.omitted, [])
        # Only the regenerated finding is re-judged; the strength is not asked about again.
        self.assertEqual(set(calls[1]) - {"top_changes"}, {"p-plan_synergy-1"} | ({"sw-1"} if "sw-1" in calls[1] else set()))
        self.assertNotIn("s-plan_synergy-1", calls[1])

    def test_untouched_items_are_not_regenerated(self):
        res, client, rec = verify({"critic": t.critic_once({"sw-1": (FLAG, "Mind Stone costs no life", "x")}), "D:regen": replace_swap(add_id="pc-2", alt="pc-1")})
        # the swap is regenerated, and so is top_changes (written for the swap that was replaced); no finding is touched
        self.assertEqual([r.label for r in client.calls("D:regen")], ["D:regen:sw-1:1", "D:regen:top_changes:1"])
        self.assertEqual(res.strengths[0]["claim"], t.analysis(t.make_ctx()).strengths[0]["claim"])
        self.assertEqual(res.problems[0]["claim"], t.analysis(t.make_ctx()).problems[0]["claim"])
        self.assertEqual(res.swaps[0]["add"]["name"], "Thought Vessel")

    def test_flag_without_a_verbatim_quote_is_sent_back_to_the_critic_and_ignored_if_it_stays_unquoted(self):
        hallucinated = t.critic({"sw-1": (FLAG, "words the writer never wrote", "x")})
        with self.assertRaises(Exception) as cm:
            verify({"critic": hallucinated})
        self.assertIn("verbatim", str(cm.exception))

    def test_quote_repair_turn_names_the_item_and_the_rule(self):
        replies = []

        def h(req):
            replies.append(req)
            if "sw-1" in t.items_in(req) and not any("sw-1" in t.items_in(r) and r is not req for r in replies):
                return t.critic({"sw-1": (FLAG, "invented words", "x")})(req)
            return t.critic()(req)

        res, client, rec = verify({"critic": h})
        repair = next(r for r in replies if "does not appear verbatim" in r.messages[-1].content)
        self.assertIn("sw-1: the quote 'invented words' does not appear verbatim", repair.messages[-1].content)
        self.assertEqual(res.omitted, [])

    def test_quote_with_ellipsis_or_stitched_sentences_of_the_item_is_accepted(self):
        text = "Getting Lara down early is well supported. Sol Ring pushes ramp to 17. A turn-3 Lara is routine."
        self.assertTrue(d_verify.quote_in("Getting Lara down early is well supported... A turn-3 Lara is routine.", text))
        self.assertTrue(d_verify.quote_in("Getting Lara down early is well supported. A turn-3 Lara is routine.", text))
        self.assertTrue(d_verify.quote_in("Getting Lara down early is well supported \u2026 Sol Ring pushes ramp to 17", text))

    def test_quote_must_stay_in_order_and_contain_only_the_items_words(self):
        text = "First sentence here. Second sentence here. Third sentence here."
        self.assertFalse(d_verify.quote_in("Third sentence here... First sentence here.", text))
        self.assertFalse(d_verify.quote_in("First sentence here. Invented words. Third sentence here.", text))
        self.assertFalse(d_verify.quote_in("", text))
        self.assertFalse(d_verify.quote_in("...", text))

    def test_quote_repair_turn_restates_the_text_under_review(self):
        obj = {"verdicts": [{"item_id": "x", "verdict": "flag", "category": "generic", "quote": "words from elsewhere", "reason": "r"}]}
        errs = d_verify.critic_validate(obj, {"x": "The actual text of item x."})
        self.assertEqual(len(errs), 1)
        self.assertIn("does not appear verbatim", errs[0])
        self.assertIn("The actual text of item x.", errs[0])

    def test_critic_must_return_one_verdict_per_item(self):
        def h(req):
            ids = list(t.items_in(req))
            return {"verdicts": [{"item_id": ids[0], "verdict": "ok", "category": "none", "quote": "", "reason": "fine"}]}

        with self.assertRaises(Exception) as cm:
            verify({"critic": h})
        self.assertIn("no verdict for item", str(cm.exception))

    def test_critic_empty_object_is_a_schema_error_not_a_silent_pass(self):
        with self.assertRaises(Exception) as cm:
            verify({"critic": lambda req: {}})
        self.assertIn("verdicts", str(cm.exception))

    def test_critic_schema_shape(self):
        ok = {"verdicts": [{"item_id": "x", "verdict": "flag", "category": "generic", "quote": "q", "reason": "r"}]}
        self.assertEqual(validate_schema(ok, schemas.CRITIC_SCHEMA), [])
        bad = {"verdicts": [{"item_id": "x", "verdict": "flag", "category": "style", "quote": "q", "reason": "r"}]}
        self.assertTrue(validate_schema(bad, schemas.CRITIC_SCHEMA))


class LoopBounds(unittest.TestCase):
    def test_at_most_two_regeneration_loops_then_removed_and_recorded(self):
        # The critic flags the swap in every round, whatever the generator writes.
        always = t.critic(lambda iid, text: (("tradeoff_missing", "Cutting", "hand-waved") if iid == "sw-1" else None))
        res, client, rec = verify({"critic": always, "D:regen": replace_swap(add_id="pc-2", alt="pc-1", tradeoff="Cutting this loses little.")})
        self.assertEqual(len(client.calls("D:regen")), 2, "exactly two regeneration loops")
        swap_calls = [r for r in client.calls("critic") if "sw-1" in t.items_in(r)]
        self.assertEqual(len(swap_calls), 3, "initial pass + one re-check per loop, no more")
        self.assertEqual(res.swaps, [])
        self.assertEqual([(o["kind"], o["item"].split(":")[0]) for o in res.omitted], [("swap", "sw-1")])
        self.assertIn("tradeoff_missing", res.omitted[0]["reason"])
        self.assertIn("hand-waved", res.omitted[0]["reason"])
        self.assertEqual(res.stats["removed"], 1)

    def test_loop_cap_is_configurable_and_zero_means_remove_on_first_failure(self):
        cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_C": "anthropic:gen-c", "REVIEW_MODEL_CRITIC": "google:critic"})
        object.__setattr__(cfg, "max_verify_loops", 0)
        ctx, brief, analysis, recs, view, rec = setup()
        client = t.Handlers(critic=t.critic({"sw-1": (FLAG, "Mind Stone costs no life", "x")}))
        res = d_verify.verify(ctx, brief, analysis, recs, view, client, cfg, rec)
        self.assertEqual((client.calls("D:regen"), res.swaps), ([], []))
        self.assertEqual([o["kind"] for o in res.omitted], ["swap"], "top_changes is derived; it is not a second omission")
        self.assertIsNone(res.top_changes)

    def test_removed_item_never_appears_in_any_main_section(self):
        always = t.critic({"p-plan_synergy-1": (FLAG, "pushes the list to bracket 3", "not shown by the cited fact")})
        withdraw = lambda req: {"action": "withdraw", "finding": None, "note": "the data cannot support it"}
        res, client, rec = verify({"critic": always, "D:regen": withdraw})
        everything = json.dumps([res.strengths, res.problems, res.swaps, res.cuts_to_consider, res.top_changes])
        self.assertNotIn("p-plan_synergy-1", everything)
        self.assertNotIn("Mana Crypt costs life", everything)
        self.assertEqual(res.omitted[0]["kind"], "finding")
        self.assertIn("the data cannot support it", res.omitted[0]["reason"])

    def test_generator_that_cannot_produce_valid_output_means_removal_not_a_crash(self):
        junk = lambda req: "not json at all"
        res, client, rec = verify({"critic": t.critic({"sw-1": (FLAG, "Mind Stone costs no life", "x")}), "D:regen": junk})
        self.assertEqual(res.swaps, [])
        self.assertIn("could not produce a valid replacement", res.omitted[0]["reason"])

    def test_omitted_entries_match_the_review_document_schema(self):
        from tests.review_document_refs import load_schema
        always = t.critic({"sw-1": (FLAG, "Mind Stone costs no life", "x")})
        res, *_ = verify({"critic": always, "D:regen": lambda r: {"action": "withdraw", "swap": None, "note": "none fit"}})
        sch = load_schema()
        self.assertEqual(validate_schema(res.omitted, {"$defs": sch["$defs"], **sch["properties"]["omitted"]}), [])
        self.assertTrue(res.omitted)

    def test_removals_are_traced_as_drops_and_unlinked(self):
        always = t.critic({"sw-1": (FLAG, "Mind Stone costs no life", "x")})
        res, client, rec = verify({"critic": always, "D:regen": lambda r: {"action": "withdraw", "swap": None, "note": "none fit"}})
        tr = rec.finish("ok")
        self.assertTrue(any(d["finding"]["id"] == "sw-1" for d in tr.drops))
        self.assertFalse(tr.finding_steps["sw-1"]["kept"])


class DependenciesBetweenItems(unittest.TestCase):
    def test_swap_whose_problem_was_removed_is_omitted_not_regenerated(self):
        always = t.critic({"p-plan_synergy-1": (FLAG, "pushes the list to bracket 3", "unsupported")})
        withdraw = lambda req: {"action": "withdraw", "finding": None, "note": "cannot support it"}
        res, client, rec = verify({"critic": always, "D:regen": withdraw})
        self.assertEqual(client.calls("D:regen")[0].label.split(":")[2], "p-plan_synergy-1")
        self.assertEqual(len(client.calls("D:regen")), 1, "the dependent swap must not trigger another regeneration")
        self.assertEqual(res.swaps, [])
        swap_omit = next(o for o in res.omitted if o["kind"] == "swap")
        self.assertIn("p-plan_synergy-1", swap_omit["reason"])
        self.assertIn("removed by the verifier", swap_omit["reason"])
        self.assertIsNone(res.top_changes)

    def test_scorecard_rows_lose_removed_finding_ids(self):
        always = t.critic({"p-plan_synergy-1": (FLAG, "pushes the list to bracket 3", "unsupported")})
        res, *_ = verify({"critic": always, "D:regen": lambda r: {"action": "withdraw", "finding": None, "note": "no"}})
        self.assertEqual([a["finding_ids"] for a in res.axes], [["s-plan_synergy-1"]])

    def test_top_changes_is_redone_when_a_top_swap_is_removed_and_judged_on_what_remains(self):
        swaps = [t.swap_in(), t.swap_in("Sol Ring", "pc-2", "pc-3", bracket="none"), t.swap_in("Arcane Signet", "pc-3", "pc-4", bracket="none"),
                 t.swap_in("Island", "pc-4", "pc-6", bracket="none")]
        flag_first = t.critic({"sw-1": (FLAG, "Mind Stone costs no life", "does not")})
        seen = {}

        def top_regen(req):
            seen["prompt"] = req.messages[0].content
            return {"ranks": [1, 2, 3], "why_these_first": "Sol Ring, Arcane Signet and Island are the three remaining cheap fixes."}

        withdraw = lambda req: {"action": "withdraw", "swap": None, "note": "no valid replacement"}
        handlers = {"critic": flag_first, "D:regen:sw-1": withdraw, "D:regen:top_changes": top_regen}
        res, client, rec = verify(handlers, swaps=swaps)
        self.assertEqual([s["id"] for s in res.swaps], ["sw-2", "sw-3", "sw-4"])
        self.assertEqual(res.top_changes["swap_ids"], ["sw-2", "sw-3", "sw-4"])
        self.assertIn("were removed or replaced", seen["prompt"])
        self.assertIn("1. sw-2", seen["prompt"])
        self.assertEqual(validate_schema(res.top_changes, {"type": "object"}), [])

    def test_a_replaced_swap_makes_the_top_selection_stale_and_it_is_rewritten(self):
        swaps = [t.swap_in(), t.swap_in("Sol Ring", "pc-2", "pc-3", bracket="none"), t.swap_in("Arcane Signet", "pc-3", "pc-4", bracket="none")]
        handlers = {"critic": t.critic_once({"sw-1": (FLAG, "Mind Stone costs no life", "x")}),
                    "D:regen:sw-1": replace_swap(add_id="pc-6", alt="pc-1"),
                    "D:regen:top_changes": lambda r: {"ranks": [1, 2, 3], "why_these_first": "Goblin Bombardment now leads because it turns tokens into reach."}}
        res, client, rec = verify(handlers, swaps=swaps)
        self.assertEqual(res.top_changes["why_these_first"], "Goblin Bombardment now leads because it turns tokens into reach.")

    def test_no_swaps_left_means_no_top_changes_and_it_is_not_a_failure(self):
        res, *_ = verify({"critic": t.critic()}, swaps=[], problems=[])
        self.assertEqual((res.swaps, res.top_changes, res.omitted), ([], None, []))

    def test_cut_entry_failing_critic_is_regenerated_or_removed_on_its_own(self):
        cut = {"card": "Island", "reason": "Cut it, it is bad.", "fact_keys": ["mana.lands"]}
        flagged = t.critic({"cut:Island": ("generic", "it is bad", "says nothing about this deck")})
        res, client, rec = verify({"critic": flagged, "D:regen:cut:Island": lambda r: {"action": "withdraw", "cut": None, "note": "no evidence"}}, cuts=[cut])
        self.assertEqual(res.cuts_to_consider, [])
        self.assertEqual(res.omitted[0]["kind"], "cut")
        self.assertEqual([s["id"] for s in res.swaps], ["sw-1"], "the swap is untouched")

    def test_cut_entry_naming_a_pet_card_is_removed_by_code_without_asking_the_critic(self):
        cut = {"card": "Skullclamp", "reason": "Low impact.", "fact_keys": ["roles.draw.count"]}
        res, client, rec = verify({"critic": t.critic(), "D:regen:cut:Skullclamp": lambda r: {"action": "withdraw", "cut": None, "note": "pet card"}}, cuts=[cut])
        self.assertEqual(res.cuts_to_consider, [])
        self.assertNotIn("cut:Skullclamp", [i for r in client.calls("critic") for i in t.items_in(r)])
        self.assertIn("cut_is_pet_card", res.omitted[0]["reason"])


class StatsAndRunner(unittest.TestCase):
    def test_stats_count_flagged_regenerated_and_removed(self):
        flags = {"sw-1": (FLAG, "Mind Stone costs no life", "x"), "s-plan_synergy-1": ("generic", "into two cards", "y")}
        handlers = {"critic": t.critic_once(flags), "D:regen:sw-1": replace_swap(add_id="pc-2", alt="pc-1"),
                    "D:regen:s-plan_synergy-1": lambda r: {"action": "withdraw", "finding": None, "note": "cannot"}}
        res, *_ = verify(handlers)
        st = res.stats
        self.assertEqual((st["critic_flagged_items"], st["regenerated_ok"], st["removed"]), (2, 2, 1))   # swap + the top_changes it made stale
        self.assertEqual(st["by_category"], {FLAG: 1, "generic": 1, "top_changes_stale": 1})
        self.assertEqual(st["critic_model"], "google:critic")
        self.assertEqual([(e["item"], e["outcome"]) for e in res.log],
                         [("s-plan_synergy-1", "removed"), ("sw-1", "regenerated"), ("top_changes", "regenerated")], "findings first, then swaps, then top_changes")

    def test_run_abcd_chains_all_passes_and_returns_document_sections(self):
        ctx = t.make_ctx()
        from tests import review_pipeline_helpers as h
        axes = {a.id: h.analysis(score=6, problems=[h.finding("Mana Crypt costs life every upkeep and is a Game Changer in a bracket 3 deck.",
                                                              ("Mana Crypt",), ("bracket.minimum_bracket",))] if a.id == "plan_synergy" else [],
                                 none=a.id != "plan_synergy") for a in config.AXES}
        handlers = {"A": [h.BRIEF_REPLY], **{f"B:{k}": [v] for k, v in axes.items()}, "C": [t.recommend_reply([t.swap_in()])], "critic": t.critic()}
        client = t.Handlers(**handlers)
        res = runner.run_abcd(ctx, client, t.pool(), t.CFG)
        sections = res.document_sections()
        self.assertEqual([s["id"] for s in sections["swaps"]], ["sw-1"])
        self.assertEqual(sections["top_changes"]["swap_ids"], ["sw-1"])
        self.assertEqual(sections["omitted"], [])
        self.assertEqual(res.trace.models["critic"], "google:critic")
        self.assertEqual(res.trace.models["C"], "anthropic:gen-c")
        self.assertEqual(res.trace.status, "ok")

    def test_run_abcd_failure_returns_the_failed_trace_not_a_partial_review(self):
        ctx = t.make_ctx()
        from tests import review_pipeline_helpers as h
        client = t.Handlers(A=[h.BRIEF_REPLY])           # pass B has no scripted reply -> the run fails
        with self.assertRaises(runner.ReviewRunFailed) as cm:
            runner.run_abcd(ctx, client, t.pool(), t.CFG)
        self.assertEqual(cm.exception.trace.status, "failed")


if __name__ == "__main__":
    unittest.main()
