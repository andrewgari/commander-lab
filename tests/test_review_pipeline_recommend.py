"""Pass C (recommendations): swaps come from the candidate pool by id, never by name. Fully offline."""
from __future__ import annotations

import json
import unittest

from src.server.domain.analytics.review_pipeline import c_recommend, config, prompts, schemas, trace
from src.server.domain.analytics.review_pipeline.structured import StructuredOutputError, validate_schema
from tests import review_pipeline_cd_helpers as t


def run_c(replies, *, ctx=None, pool=None, analysis=None, max_cards=150):
    ctx = ctx or t.make_ctx()
    brief = t.make_brief(ctx)
    analysis = analysis or t.analysis(ctx)
    view = c_recommend.PoolView.from_pool(pool or t.pool(), max_cards=max_cards)
    client = t.Handlers(C=list(replies))
    rec = trace.TraceRecorder(deck_id="d1", inputs={})
    out, step = c_recommend.run(ctx, brief, analysis, view, client, t.CFG, rec)
    return out, client, rec, step


class PoolViewTests(unittest.TestCase):
    def test_ids_follow_pool_rank_and_resolve_to_cards(self):
        v = c_recommend.PoolView.from_pool(t.pool())
        self.assertEqual(v.get("pc-1")["name"], "Mind Stone")
        self.assertEqual(v.get("pc-6")["name"], "Goblin Bombardment")
        self.assertIsNone(v.get("Mind Stone"))
        self.assertIsNone(v.get("pc-99"))

    def test_render_groups_by_role_and_shows_price_owned_edhrec_and_game_changer(self):
        text = c_recommend.PoolView.from_pool(t.pool()).render()
        self.assertIn("== RAMP (3) ==", text)
        self.assertIn("pc-1 | Mind Stone | mv 2 | Artifact | roles: ramp | $1.00 | OWNED x1 (1 free) | EDHREC 40% of decks, synergy +0.10", text)
        self.assertIn("Jeska's Will [GC]", text)
        self.assertIn("not owned", text)

    def test_unknown_ownership_is_said_not_guessed(self):
        text = c_recommend.PoolView.from_pool(t.pool(ownership_known=False)).render()
        self.assertIn("Ownership is UNKNOWN", text)
        self.assertNotIn("not owned", text)
        self.assertNotIn("OWNED", text)

    def test_capped_view_keeps_every_role_and_rejects_ids_the_model_never_saw(self):
        many = [(f"Card {i}", "Artifact", ["ramp"] if i % 2 else ["draw"], 1.0, False, "x", False, ["U"]) for i in range(1, 101)]
        v = c_recommend.PoolView.from_pool(t.pool(cards=many), max_cards=30)
        self.assertEqual(len(v.shown), 30)
        self.assertTrue({"ramp", "draw"} <= {r for c in v.shown for r in c["roles"]})
        hidden = next(c["pool_id"] for c in v.all_cards if c["pool_id"] not in v.shown_ids())
        self.assertIsNone(v.get(hidden), "an id the model was not shown must not resolve")
        self.assertIsNotNone(v.get(v.shown[0]["pool_id"]))


class PromptTests(unittest.TestCase):
    def test_request_has_three_cached_static_blocks_and_the_rules(self):
        ctx = t.make_ctx()
        view = c_recommend.PoolView.from_pool(t.pool())
        req = c_recommend.build_request(ctx, t.make_brief(ctx), t.analysis(ctx), view, t.CFG)
        self.assertEqual((req.cache_blocks, len(req.system_blocks)), (3, 3))
        role, context_block, evidence = req.system_blocks
        for needle in ("ONLY FROM THE POOL, BY ID", "OWNED CARDS WIN TIES", "ALREADY REJECTED", "ONE ALTERNATIVE",
                                                          "TOP CHANGES", "CONTRAST EXAMPLES", "Specific over generic"):
            self.assertIn(needle, role)
        self.assertIn("PLAYER PROFILE", context_block)
        self.assertIn("Skullclamp", context_block)           # pet card
        self.assertIn("pc-1 | Mind Stone", evidence)
        self.assertIn("p-plan_synergy-1", evidence)
        self.assertIn("roles.draw.count = 1", evidence)
        self.assertIn("Estimated bracket", evidence)

    def test_rejected_pairs_are_shown_to_the_model(self):
        ctx = t.make_ctx(rejected=[("Mana Crypt", "Mind Stone")])
        view = c_recommend.PoolView.from_pool(t.pool())
        blocks = c_recommend.system_blocks(ctx, t.make_brief(ctx), [], [], {}, view, t.CFG)
        self.assertIn("- Mana Crypt -> Mind Stone", blocks[1])

    def test_schema_requires_ids_not_names(self):
        props = schemas.SWAP_IN["properties"]
        self.assertIn("add_id", props)
        self.assertNotIn("add", props)
        self.assertNotIn("add_name", props)

    def test_prompt_file_is_versioned_and_embeds_the_quality_standard(self):
        t_ = prompts.load("c_recommend")
        self.assertEqual(t_.version, "v1")
        rendered = t_.render("system.role", quality_standard=prompts.quality_standard().text)
        self.assertIn("Every swap states its trade-off", rendered)


class MaterialiseTests(unittest.TestCase):
    def test_pool_ids_become_card_refs_with_price_ownership_and_budget_text_from_data(self):
        out, client, rec, step = run_c([t.recommend_reply([t.swap_in()])])
        s = out.swaps[0]
        self.assertEqual(s["id"], "sw-1")
        self.assertEqual(s["add"], {"name": "Mind Stone", "oracle_id": t.oid(1)})
        self.assertEqual(s["alternative"], {"name": "Thought Vessel", "oracle_id": t.oid(2)})
        self.assertEqual(s["cut"]["name"], "Mana Crypt")
        self.assertEqual((s["owned"], s["price"]), (True, 1.0))
        self.assertIn("net -$149.00 (adds $1.00, cuts $150.00)", s["budget_effect"])
        self.assertIn("you own a free copy", s["budget_effect"])
        self.assertEqual(out.swap_meta["sw-1"], {"add_pool_id": "pc-1", "alternative_pool_id": "pc-2"})
        self.assertEqual(out.top_changes, {"swap_ids": ["sw-1"], "why_these_first": "Swap 1 removes the life payment and the Game Changer in one move."})

    def test_model_cannot_set_price_or_owned(self):
        reply = t.recommend_reply([{**t.swap_in(), "price": 0.01, "owned": True}])
        with self.assertRaises(StructuredOutputError) as cm:
            run_c([reply, reply, reply])
        self.assertIn("additional properties", str(cm.exception).lower())

    def test_swap_document_matches_the_review_document_schema(self):
        from tests.review_document_refs import load_schema
        three = [t.swap_in(), t.swap_in("Sol Ring", "pc-2", "pc-3", bracket="none"), t.swap_in("Arcane Signet", "pc-3", "pc-4", bracket="none")]
        out, *_ = run_c([t.recommend_reply(three)])
        sch = load_schema()
        wrapper = {"$schema": sch.get("$schema"), "$defs": sch["$defs"], "type": "array", "items": sch["properties"]["swaps"]["items"]}
        self.assertEqual(validate_schema(out.swaps, wrapper), [])
        top_schema = {"$defs": sch["$defs"], **sch["properties"]["top_changes"]}
        self.assertEqual(validate_schema(out.top_changes, top_schema), [])

    def test_fewer_than_three_swaps_validate_without_padding(self):
        from tests.review_document_refs import load_schema
        out, *_ = run_c([t.recommend_reply([t.swap_in()])])
        sch = load_schema()
        top_schema = {"$defs": sch["$defs"], **sch["properties"]["top_changes"]}
        self.assertEqual(validate_schema(out.top_changes, top_schema), [])
        self.assertEqual(out.top_changes["swap_ids"], ["sw-1"])

    def test_ownership_unknown_budget_text_says_so(self):
        out, *_ = run_c([t.recommend_reply([t.swap_in()])], pool=t.pool(ownership_known=False))
        self.assertIn("ownership unknown", out.swaps[0]["budget_effect"])

    def test_name_instead_of_id_triggers_a_repair_that_names_the_rule(self):
        bad = t.recommend_reply([t.swap_in(add_id="Mind Stone")])
        out, client, rec, _ = run_c([bad, t.recommend_reply([t.swap_in()])])
        self.assertEqual(out.swaps[0]["add"]["name"], "Mind Stone")
        self.assertEqual(len(client.requests), 2)
        repair = client.requests[1].messages[-1].content
        self.assertIn("is not a pool id", repair)
        self.assertIn("never a name", repair)

    def test_every_invalid_choice_is_reported_in_one_repair_turn(self):
        bad = t.recommend_reply([
            t.swap_in(cut="Lara"),                      # commander
            t.swap_in(cut="Nonexistent Card"),          # not in deck
            t.swap_in(resolves="s-plan_synergy-1"),     # a strength, not a problem
            t.swap_in(alt="pc-1"),                      # alternative == add
            t.swap_in(add_id="pc-99")])                 # unknown id
        good = t.recommend_reply([t.swap_in()])
        _, client, *_ = run_c([bad, good])
        msg = client.requests[1].messages[-1].content
        for needle in ("commander and can never be cut", "not a card in this deck", "is not a PROBLEM id", "must differ from add_id", "pc-99"):
            self.assertIn(needle, msg)

    def test_unrecoverable_output_raises_rather_than_returning_a_partial_result(self):
        bad = t.recommend_reply([t.swap_in(add_id="Mind Stone")])
        with self.assertRaises(StructuredOutputError):
            run_c([bad, bad, bad])

    def test_top_changes_must_pick_exactly_three_when_there_are_at_least_three(self):
        swaps = [t.swap_in("Mana Crypt", "pc-1", "pc-2"), t.swap_in("Sol Ring", "pc-2", "pc-3", bracket="none"),
                 t.swap_in("Arcane Signet", "pc-3", "pc-4", bracket="none"), t.swap_in("Island", "pc-4", "pc-6", bracket="none")]
        good = t.recommend_reply(swaps, top=[2, 1, 4])
        out, *_ = run_c([good])
        self.assertEqual(out.top_changes["swap_ids"], ["sw-2", "sw-1", "sw-4"])
        two = t.recommend_reply(swaps, top=[1, 2])
        _, client, *_ = run_c([two, good])
        self.assertIn("exactly 3 position(s)", client.requests[1].messages[-1].content)
        dup = t.recommend_reply(swaps, top=[1, 1, 2])
        _, client, *_ = run_c([dup, good])
        self.assertIn("distinct positions", client.requests[1].messages[-1].content)
        out_of_range = t.recommend_reply(swaps, top=[1, 2, 9])
        _, client, *_ = run_c([out_of_range, good])
        self.assertIn("between 1 and 4", client.requests[1].messages[-1].content)

    def test_fewer_than_three_swaps_means_top_changes_lists_them_all(self):
        out, *_ = run_c([t.recommend_reply([t.swap_in(), t.swap_in("Sol Ring", "pc-2", "pc-3", bracket="none")])])
        self.assertEqual(out.top_changes["swap_ids"], ["sw-1", "sw-2"])

    def test_no_swaps_and_no_cuts_is_a_valid_answer(self):
        reply = {"swaps": [], "top_changes": {"ranks": [], "why_these_first": ""}, "cuts_to_consider": []}
        out, *_ = run_c([reply], analysis=t.analysis(t.make_ctx(), problems=[]))
        self.assertEqual((out.swaps, out.cuts_to_consider, out.top_changes), ([], [], None))

    def test_cuts_to_consider_need_real_facts_and_cannot_repeat_a_swap_cut(self):
        ok_cut = {"card": "Island", "reason": "A lone basic in a deck with 38 land target is the weakest land slot.", "fact_keys": ["mana.lands"]}
        out, *_ = run_c([t.recommend_reply([t.swap_in()], cuts=[ok_cut])])
        self.assertEqual(out.cuts_to_consider[0]["card"]["name"], "Island")
        self.assertEqual(out.cuts_to_consider[0]["evidence"], ["mana.lands"])
        bad_key = {**ok_cut, "fact_keys": ["mana.nope"]}
        _, client, *_ = run_c([t.recommend_reply([t.swap_in()], cuts=[bad_key]), t.recommend_reply([t.swap_in()], cuts=[ok_cut])])
        self.assertIn("do not exist or are unavailable", client.requests[1].messages[-1].content)
        dup = {**ok_cut, "card": "Mana Crypt"}
        _, client, *_ = run_c([t.recommend_reply([t.swap_in()], cuts=[dup]), t.recommend_reply([t.swap_in()], cuts=[ok_cut])])
        self.assertIn("already the cut of a swap", client.requests[1].messages[-1].content)

    def test_call_is_traced_as_pass_c_with_the_configured_model_and_swaps_link_to_the_step(self):
        out, client, rec, step = run_c([t.recommend_reply([t.swap_in()])])
        tr = rec.finish("ok")
        self.assertEqual((tr.steps[step]["pass"], tr.steps[step]["model"], tr.steps[step]["prompt_version"]), ("C", "anthropic:gen-c", "v1"))
        self.assertEqual(tr.provenance_of("sw-1")["pass"], "C")
        json.dumps(tr.to_dict())


if __name__ == "__main__":
    unittest.main()
