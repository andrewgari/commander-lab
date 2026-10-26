"""Pass A (brief), Pass B (analysis, parallel per axis), drops, and the runner. Fully offline."""
from __future__ import annotations

import json
import unittest

from src.server.domain.analytics.review_pipeline import (a_brief, b_analysis, config, context, facts, llm, names,
                                                         runner, trace)
from src.server.domain.intake.user_intent import DeckVision, UserDeckIntent
from tests import review_pipeline_helpers as h


def make_ctx(intent_text="", owned=None, caps=None, profile=None, extra_foreign=()):
    """intent_text None = no intent object at all; "" = default (empty) intent; else a described intent."""
    intent = None if intent_text is None else UserDeckIntent(deck_vision=DeckVision(description=intent_text))
    return context.ReviewContext.build(
        deck_id="d1", deck_name="Test Deck", fact_pack=h.pack(), deck_cards=h.DECK_CARDS, commanders=["Lara"],
        profile=profile or h.profile(), intent=intent, owned=owned, caps=caps, extra_foreign_names=extra_foreign)


CFG = config.PipelineConfig.from_env({"REVIEW_MODEL_A": "fake:a", "REVIEW_MODEL_B": "fake:b"})


class ContextTests(unittest.TestCase):
    def test_context_resolves_deck_names_and_fact_index(self):
        ctx = make_ctx()
        self.assertEqual(ctx.resolver.resolve("sol ring"), "Sol Ring")
        self.assertTrue(ctx.index.exists("roles.ramp.count"))
        self.assertIn("Sol Ring", ctx.card_table)
        self.assertEqual(len(ctx.fact_pack_hash), 64)

    def test_missing_intent_is_rendered_explicitly(self):
        ctx = make_ctx(intent_text=None)
        self.assertIn("No deck intent recorded", ctx.intent_text)

    def test_intent_text_rendered(self):
        ctx = make_ctx(intent_text="goblin tokens, bracket 3")
        self.assertIn("goblin tokens", ctx.intent_text)

    def test_profile_block_comes_from_render_profile_for_prompt(self):
        ctx = make_ctx()
        self.assertTrue(ctx.profile_block.startswith("PLAYER PROFILE"))
        self.assertIn("Skullclamp", ctx.profile_block)


class BriefTests(unittest.TestCase):
    def run_a(self, reply):
        client = h.ScriptedClient({"A": [reply]})
        rec = trace.TraceRecorder(deck_id="d1", inputs={})
        ctx = make_ctx()
        brief = a_brief.run(ctx, client, CFG, rec)
        return brief, client, rec

    def test_brief_returned_with_ids_assigned_by_code(self):
        brief, client, rec = self.run_a(h.BRIEF_REPLY)
        self.assertEqual(brief["questions_for_owner"][0]["id"], "q-1")
        self.assertEqual(brief["what_it_does"], h.BRIEF_REPLY["what_it_does"])
        self.assertEqual(rec.finish("ok").to_dict()["steps"][0]["pass"], "A")

    def test_model_comes_from_config(self):
        _, client, _ = self.run_a(h.BRIEF_REPLY)
        self.assertEqual(client.requests[0].model, "fake:a")

    def test_request_contains_profile_card_table_and_standard_and_user_turn(self):
        _, client, _ = self.run_a(h.BRIEF_REPLY)
        sys = "\n".join(client.requests[0].system_blocks)
        for needle in ("PLAYER PROFILE", "Sol Ring", "REVIEW QUALITY STANDARD", "NEVER GUESS INTENT", "DECK INTENT"):
            self.assertIn(needle, sys)
        self.assertIn("Test Deck", client.requests[0].messages[0].content)

    def test_unknown_card_names_in_brief_are_repaired_not_shipped(self):
        bad = json.loads(json.dumps(h.BRIEF_REPLY))
        bad["central_concept"]["key_cards"] = ["Skullclamp", "Cyclonic Rift"]
        client = h.ScriptedClient({"A": [bad, h.BRIEF_REPLY]})
        brief = a_brief.run(make_ctx(), client, CFG, trace.TraceRecorder(deck_id="d1", inputs={}))
        self.assertEqual(len(client.requests), 2)
        self.assertIn("Cyclonic Rift", client.requests[1].messages[-1].content)
        self.assertEqual(brief["central_concept"]["key_cards"], ["Skullclamp", "Hordeling Outburst"])

    def test_brief_card_names_canonicalised(self):
        r = json.loads(json.dumps(h.BRIEF_REPLY))
        r["win_paths"][0]["key_cards"] = ["hordeling outburst"]
        brief = a_brief.run(make_ctx(), h.ScriptedClient({"A": [r]}), CFG, trace.TraceRecorder(deck_id="d1", inputs={}))
        self.assertEqual(brief["win_paths"][0]["key_cards"], ["Hordeling Outburst"])

    def test_still_unknown_after_repairs_fails_loudly(self):
        bad = json.loads(json.dumps(h.BRIEF_REPLY))
        bad["central_concept"]["key_cards"] = ["Cyclonic Rift"]
        client = h.ScriptedClient({"A": [bad, bad, bad]})
        from src.server.domain.analytics.review_pipeline.structured import StructuredOutputError
        with self.assertRaises(StructuredOutputError):
            a_brief.run(make_ctx(), client, CFG, trace.TraceRecorder(deck_id="d1", inputs={}))
        self.assertEqual(len(client.requests), 3)

    def test_default_intent_object_counts_as_no_stated_intent(self):
        ctx = make_ctx(intent_text="")
        self.assertFalse(ctx.has_stated_intent)
        self.assertIn("No deck intent recorded", ctx.intent_text)
        self.assertTrue(make_ctx(intent_text="goblins").has_stated_intent)

    def test_stated_win_path_with_no_stated_intent_is_rejected(self):
        """The brief must not claim the owner stated something when no intent exists."""
        r = json.loads(json.dumps(h.BRIEF_REPLY))
        r["win_paths"][0]["basis"] = "stated"
        client = h.ScriptedClient({"A": [r, h.BRIEF_REPLY]})
        brief = a_brief.run(make_ctx(intent_text=None, profile=h.profile()), client, CFG, trace.TraceRecorder(deck_id="d1", inputs={}))
        self.assertEqual(brief["win_paths"][0]["basis"], "inferred_from_list")
        self.assertIn("stated", client.requests[1].messages[-1].content)

    def test_stated_power_target_without_intent_is_rejected(self):
        r = json.loads(json.dumps(h.BRIEF_REPLY))
        r["power_target"] = {"stated": True, "bracket": 3, "note": "x"}
        client = h.ScriptedClient({"A": [r, h.BRIEF_REPLY]})
        a_brief.run(make_ctx(intent_text=None), client, CFG, trace.TraceRecorder(deck_id="d1", inputs={}))
        self.assertEqual(len(client.requests), 2)


def b_reply(**kw):
    return h.analysis(**kw)


def run_b(replies, ctx=None, cfg=CFG, brief=None):
    ctx = ctx or make_ctx()
    brief = brief or a_brief.finalize(h.BRIEF_REPLY)
    client = h.ScriptedClient(replies, default=h.analysis(none=True, score=8, line="roles.ramp.count decides it"))
    rec = trace.TraceRecorder(deck_id="d1", inputs={})
    out = b_analysis.run(ctx, brief, client, cfg, rec)
    return out, client, rec


class AnalysisTests(unittest.TestCase):
    def test_one_call_per_axis_all_seven(self):
        out, client, rec = run_b({})
        labels = sorted(r.label for r in client.requests)
        self.assertEqual(labels, sorted(f"B:{a.id}" for a in config.AXES))
        self.assertEqual({a["axis"] for a in out.axes}, {a.id for a in config.AXES})

    def test_calls_actually_run_in_parallel(self):
        import threading, time
        active, peak = [0], [0]
        lock = threading.Lock()

        class Slow(h.ScriptedClient):
            def complete(self, request):
                if request.label.startswith("B:") and request.label != "B:" + config.AXES[0].id:
                    with lock:
                        active[0] += 1; peak[0] = max(peak[0], active[0])
                    time.sleep(0.05)
                    with lock:
                        active[0] -= 1
                return super().complete(request)

        client = Slow({}, default=h.analysis(none=True, score=8))
        b_analysis.run(make_ctx(), a_brief.finalize(h.BRIEF_REPLY), client, CFG, trace.TraceRecorder(deck_id="d", inputs={}))
        self.assertGreaterEqual(peak[0], 3)

    def test_warm_cache_runs_first_axis_alone_then_the_rest(self):
        order = []
        class Rec(h.ScriptedClient):
            def complete(self, request):
                order.append(request.label)
                return super().complete(request)
        client = Rec({}, default=h.analysis(none=True, score=8))
        b_analysis.run(make_ctx(), a_brief.finalize(h.BRIEF_REPLY), client, CFG, trace.TraceRecorder(deck_id="d", inputs={}))
        self.assertEqual(order[0], f"B:{config.AXES[0].id}")
        cold = config.PipelineConfig.from_env({"REVIEW_MODEL_A": "fake:a", "REVIEW_MODEL_B": "fake:b", "REVIEW_WARM_CACHE": "0"})
        self.assertFalse(cold.warm_cache)

    def test_model_per_pass_and_per_axis_from_config(self):
        cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_A": "fake:a", "REVIEW_MODEL_B": "fake:b", "REVIEW_MODEL_B_MANA_CONSISTENCY": "fake:m"})
        _, client, _ = run_b({}, cfg=cfg)
        by = {r.label: r.model for r in client.requests}
        self.assertEqual(by["B:mana_consistency"], "fake:m")
        self.assertEqual(by["B:plan_synergy"], "fake:b")

    def test_static_prefix_is_identical_across_axes_for_prompt_caching(self):
        _, client, _ = run_b({})
        prefixes = {tuple(r.system_blocks[:r.cache_blocks]) for r in client.requests}
        self.assertEqual(len(prefixes), 1)
        self.assertTrue(all(r.cache_blocks == 2 for r in client.requests))
        self.assertNotEqual(len({r.system_blocks[2] for r in client.requests}), 1)   # axis block differs

    def test_each_request_carries_standard_brief_profile_and_card_table(self):
        _, client, _ = run_b({})
        sys = "\n".join(client.requests[0].system_blocks)
        for needle in ("REVIEW QUALITY STANDARD", "DECK BRIEF", "Skullclamp turns Goblin tokens", "PLAYER PROFILE", "NAME | MV", "CONTRAST EXAMPLES"):
            self.assertIn(needle, sys)
        for r in client.requests:
            self.assertIn("What bracket is the target?", "\n".join(r.system_blocks))   # open questions reach every axis
            self.assertIn("q-1", "\n".join(r.system_blocks))

    def test_axis_sees_only_its_fact_slice(self):
        _, client, _ = run_b({})
        by = {r.label: "\n".join(r.system_blocks) for r in client.requests}
        self.assertIn("mana.lands", by["B:mana_consistency"])
        self.assertNotIn("derived.price.total_usd", by["B:mana_consistency"])
        self.assertIn("derived.price.total_usd", by["B:budget_inventory"])
        self.assertNotIn("mana.lands = ", by["B:budget_inventory"])

    def test_findings_get_deterministic_ids_card_refs_and_axis(self):
        f = h.finding(cards=("skullclamp",), keys=("roles.draw.count",))
        out, _, rec = run_b({"B:plan_synergy": [h.analysis(score=7, strengths=[f])]})
        ax = next(a for a in out.axes if a["axis"] == "plan_synergy")
        fid = ax["finding_ids"][0]
        self.assertEqual(fid, "s-plan_synergy-1")
        s = out.strengths[0]
        self.assertEqual(s["id"], fid)
        self.assertEqual(s["axis"], "plan_synergy")
        self.assertEqual(s["evidence"]["cards"], [{"name": "Skullclamp", "oracle_id": h.OID["Skullclamp"]}])
        self.assertEqual(set(s), {"id", "axis", "claim", "evidence", "severity", "confidence"})   # review_document finding fields only

    def test_kept_findings_validate_against_review_document_finding_schema(self):
        import jsonschema
        doc = json.load(open("schemas/review_document.schema.json"))
        schema = {"$defs": doc["$defs"], "$ref": "#/$defs/finding"}
        out, _, _ = run_b({"B:plan_synergy": [h.analysis(strengths=[h.finding()], problems=[h.finding(severity="med", fix="a cheap outlet")])]})
        self.assertTrue(out.strengths and out.problems)
        for f in out.strengths + out.problems:
            jsonschema.validate(f, schema)

    def test_extra_model_facing_fields_kept_in_sidecar_not_in_document_finding(self):
        out, _, _ = run_b({"B:plan_synergy": [h.analysis(problems=[h.finding(fix="a cheap outlet", deps=["q-1"], confidence=0.4)])]})
        side = out.sidecar[out.problems[0]["id"]]
        self.assertEqual(side["fix_direction"], "a cheap outlet")
        self.assertEqual(side["depends_on_questions"], ["q-1"])

    def test_problems_ordered_by_severity_then_confidence(self):
        ps = [h.finding(claim="Mana Crypt is low. Sol Ring", cards=("Mana Crypt",), severity="low", confidence=0.9),
              h.finding(claim="Sol Ring is crit. Skullclamp", cards=("Sol Ring",), severity="critical", confidence=0.5),
              h.finding(claim="Skullclamp is high. Island", cards=("Skullclamp",), severity="high", confidence=0.7)]
        out, _, _ = run_b({"B:plan_synergy": [h.analysis(problems=ps)]})
        self.assertEqual([p["severity"] for p in out.problems], ["critical", "high", "low"])

    # ---------------------------------------------------------------- drops
    def drops(self, finding, **kw):
        out, _, rec = run_b({"B:plan_synergy": [h.analysis(problems=[finding], **kw)]})
        t = rec.finish("ok").to_dict()
        return out, t["drops"]

    def test_drops_finding_citing_card_not_in_deck_and_logs_it(self):
        out, drops = self.drops(h.finding(claim="Cyclonic Rift would fix this.", cards=("Skullclamp", "Cyclonic Rift")))
        self.assertEqual(out.problems, [])
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0]["reason"], "evidence_card_not_in_deck")
        self.assertEqual(drops[0]["detail"], ["Cyclonic Rift"])
        self.assertEqual(drops[0]["axis"], "plan_synergy")
        self.assertEqual(drops[0]["finding"]["claim"], "Cyclonic Rift would fix this.")

    def test_drops_finding_with_nonexistent_fact_key(self):
        out, drops = self.drops(h.finding(keys=("roles.sac_outlet.count",)))
        self.assertEqual(out.problems, [])
        self.assertEqual((drops[0]["reason"], drops[0]["detail"]), ("fact_key_not_found", ["roles.sac_outlet.count"]))

    def test_drops_finding_citing_an_unavailable_fact(self):
        out, drops = self.drops(h.finding(keys=("salt",)))
        self.assertEqual(out.problems, [])
        self.assertEqual(drops[0]["reason"], "fact_key_unavailable")

    def test_drops_finding_with_no_evidence(self):
        out, drops = self.drops(h.finding(cards=(), keys=()))
        self.assertEqual((out.problems, drops[0]["reason"]), ([], "no_evidence"))

    def test_drops_finding_naming_a_non_deck_card_in_prose_but_not_evidence(self):
        """Prose must not smuggle in a recommendation of a card the deck lacks."""
        out, drops = self.drops(h.finding(claim="Skullclamp is fine but swapping in Cyclonic Rift would be better.", cards=("Skullclamp",)))
        self.assertEqual(out.problems, [])
        self.assertEqual(drops[0]["reason"], "claim_names_card_not_in_deck")

    def test_prose_check_is_not_fooled_by_ordinary_capitalised_words(self):
        out, drops = self.drops(h.finding(claim="Blue is the heaviest colour, so Skullclamp draws arrive late on Turn 3.", cards=("Skullclamp",)))
        self.assertEqual(drops, [])
        self.assertEqual(len(out.problems), 1)

    def test_depends_on_unknown_question_id_is_dropped(self):
        out, drops = self.drops(h.finding(deps=("q-99",)))
        self.assertEqual(drops[0]["reason"], "unknown_question_id")

    def test_confidence_capped_when_finding_depends_on_open_question(self):
        out, _, _ = run_b({"B:plan_synergy": [h.analysis(problems=[h.finding(confidence=0.9, deps=("q-1",))])]})
        self.assertEqual(out.problems[0]["confidence"], 0.5)

    def test_kept_finding_ids_stay_stable_when_a_sibling_is_dropped(self):
        ps = [h.finding(keys=("nope.nope",)), h.finding(claim="Sol Ring speeds Skullclamp.", cards=("Sol Ring",))]
        out, _, _ = run_b({"B:plan_synergy": [h.analysis(problems=ps)]})
        self.assertEqual([p["id"] for p in out.problems], ["p-plan_synergy-2"])

    def test_every_finding_linked_to_the_step_that_produced_it(self):
        out, _, rec = run_b({"B:plan_synergy": [h.analysis(problems=[h.finding()])]})
        t = rec.finish("ok")
        prov = t.provenance_of(out.problems[0]["id"])
        self.assertEqual((prov["pass"], prov["axis"], prov["model"], prov["prompt_version"]), ("B", "plan_synergy", "fake:b", "v1"))

    def test_no_significant_problems_accepted_with_empty_problems(self):
        out, _, _ = run_b({"B:mana_consistency": [h.analysis(none=True, score=9, line="mana.lands is on target")]})
        ax = next(a for a in out.axes if a["axis"] == "mana_consistency")
        self.assertEqual((ax["score"], ax["no_significant_problems"]), (9, True))
        self.assertEqual(ax["finding_ids"], [])

    def test_contradiction_no_problems_flag_with_problems_is_repaired(self):
        bad = h.analysis(none=True, problems=[h.finding()])
        out, client, _ = run_b({"B:plan_synergy": [bad, h.analysis(problems=[h.finding()])]})
        self.assertEqual(len([r for r in client.requests if r.label == "B:plan_synergy"]), 2)

    def test_axis_failure_fails_the_whole_run_loudly_not_partial(self):
        from src.server.domain.analytics.review_pipeline.structured import StructuredOutputError
        with self.assertRaises(StructuredOutputError):
            run_b({"B:power_bracket": ["junk", "junk", "junk"]})

    def test_transport_error_in_one_axis_propagates(self):
        with self.assertRaises(llm.LLMTransportError):
            run_b({"B:power_bracket": [llm.LLMTransportError("HTTP 500")]})

    def test_scorecard_rows_ready_for_document(self):
        out, _, _ = run_b({})
        row = out.axes[0]
        self.assertEqual(set(row), {"axis", "label", "score", "one_line", "finding_ids", "no_significant_problems", "dropped"})


class RunnerTests(unittest.TestCase):
    def test_run_ab_returns_brief_analysis_and_trace_with_both_passes(self):
        client = h.ScriptedClient({"A": [h.BRIEF_REPLY]}, default=h.analysis(none=True, score=8))
        res = runner.run_ab(make_ctx(), client, CFG)
        self.assertEqual(res.trace.status, "ok")
        self.assertEqual({s["pass"] for s in res.trace.steps}, {"A", "B"})
        self.assertEqual(len(res.trace.steps), 1 + 7)
        self.assertEqual(res.trace.inputs["fact_pack_hash"], make_ctx().fact_pack_hash)
        self.assertEqual(res.trace.to_dict()["models"], {"A": "fake:a", "B": "fake:b"})
        self.assertEqual(res.trace.to_dict()["prompt_versions"]["B"], "v1")

    def test_brief_questions_reach_the_result_for_the_document(self):
        client = h.ScriptedClient({"A": [h.BRIEF_REPLY]}, default=h.analysis(none=True, score=8))
        res = runner.run_ab(make_ctx(), client, CFG)
        self.assertEqual(res.brief["questions_for_owner"][0]["id"], "q-1")

    def test_failure_returns_no_result_but_stores_a_failed_trace(self):
        sink = []
        client = h.ScriptedClient({"A": [h.BRIEF_REPLY]}, default="junk")
        from src.server.domain.analytics.review_pipeline.structured import StructuredOutputError
        with self.assertRaises(runner.ReviewRunFailed) as cm:
            runner.run_ab(make_ctx(), client, CFG, on_trace=sink.append)
        self.assertEqual(cm.exception.trace.status, "failed")
        self.assertIsInstance(cm.exception.__cause__, StructuredOutputError)
        self.assertEqual(sink[0].status, "failed")
        self.assertTrue(any(s["verdict"] == "failed" for s in sink[0].steps))

    def test_budget_guard_fails_the_run_explicitly(self):
        cfg = config.PipelineConfig.from_env({"REVIEW_MODEL_A": "fake:a", "REVIEW_MODEL_B": "fake:b", "REVIEW_MAX_COST_USD": "0.015"})
        client = h.ScriptedClient({"A": [h.BRIEF_REPLY]}, default=h.analysis(none=True, score=8))   # 0.01 per call
        with self.assertRaises(runner.ReviewRunFailed) as cm:
            runner.run_ab(make_ctx(), client, cfg)
        self.assertIsInstance(cm.exception.__cause__, trace.BudgetExceeded)

    def test_no_model_calls_without_a_client(self):
        with self.assertRaises(TypeError):
            runner.run_ab(make_ctx(), None, CFG)


if __name__ == "__main__":
    unittest.main()
