"""Oracle.run over a scripted fake client (spec oracle-tool-loop §9.2)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.llm.port import LLMError  # noqa: E402
from src.server.domain.oracle import (LoopCaps, Oracle, OracleBudgetExceeded, OracleEvidenceMissing,  # noqa: E402
                                      OracleInvalidAnswer, OracleRunError, Tool, ToolError, ToolRegistry)
from src.server.domain.oracle.loop import EVIDENCE_NUDGE, FINALIZE_PROMPT  # noqa: E402
from src.server.domain.oracle.numbers import allowed_numbers_from  # noqa: E402
from src.server.domain.oracle import play  # noqa: E402
from tests.oracle_fakes import FakeClock, ScriptedToolChat, call, text_turn, tool_turn  # noqa: E402

CAPS = LoopCaps(max_turns=10, max_tool_calls=16, soft_cost_usd=0.60, hard_cost_usd=1.00,
                max_tokens_in=400_000, deadline_s=1000)
STATS_SCHEMA = {"type": "object", "properties": {"sections": {"type": "array", "items": {"type": "string"}}},
                "additionalProperties": False}


def caps(**kw):
    d = dict(CAPS.__dict__)
    d.update(kw)
    return LoopCaps(**d)


def registry(**kw):
    seen = []

    def h(args, ctx):
        seen.append(args)
        return {"curve": [1, 2, 3], "power": 7.5}
    reg = ToolRegistry([Tool(name="deck_stats", description="stats", parameters=STATS_SCHEMA, handler=h, **kw)])
    reg.seen = seen
    return reg


def ok(text, state):
    return []


def run(turns, *, caps_=CAPS, reg=None, validate=ok, evidence=None, rf=None, clock=None, on_call=None):
    client = ScriptedToolChat(turns, on_call=on_call)
    oracle = Oracle(client, caps_, **({"clock": clock} if clock else {}))
    kw = dict(task="deck_review", messages=[{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
              registry=reg or registry(), ctx=None, validate=validate, required_evidence=evidence or {},
              response_format=rf)
    return client, oracle, kw


def stats(sections, id="c1"):
    return call("deck_stats", {"sections": sections}, id=id)


class HappyPath(unittest.TestCase):
    def test_tool_tool_answer(self):
        client, oracle, kw = run([tool_turn(stats(["curve"], "a")), tool_turn(stats(["power"], "b")),
                                  text_turn("final")])
        res = oracle.run(**kw)
        self.assertEqual(res.text, "final")
        self.assertEqual(res.stop_reason, "answered")
        self.assertEqual([o.call_id for o in res.outputs], ["a", "b"])
        third = client.calls[2]["messages"]
        self.assertEqual([m["role"] for m in third], ["system", "user", "assistant", "tool", "assistant", "tool"])
        self.assertEqual(third[2]["tool_calls"][0]["id"], "a")  # verbatim provider list
        self.assertEqual(third[3]["tool_call_id"], "a")
        self.assertIsNone(third[2]["content"])
        self.assertEqual(json.loads(third[3]["content"]), {"ok": True, "data": {"curve": [1, 2, 3], "power": 7.5}})
        self.assertEqual(third[3]["content"], '{"data":{"curve":[1,2,3],"power":7.5},"ok":true}')
        for c in client.calls:
            self.assertEqual(c["tool_choice"], "auto")
            self.assertIsNone(c["response_format"])
            self.assertEqual(c["tools"][0]["function"]["name"], "deck_stats")

    def test_calls_in_one_turn_run_sequentially_in_order(self):
        reg = registry()
        client, oracle, kw = run([tool_turn(stats(["a"], "1"), stats(["b"], "2"), stats(["c"], "3")),
                                  text_turn("done")], reg=reg)
        oracle.run(**kw)
        self.assertEqual([a["sections"] for a in reg.seen], [["a"], ["b"], ["c"]])
        tool_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_msgs], ["1", "2", "3"])

    def test_assistant_tool_calls_match_dispatched_calls(self):
        # raw list has an extra entry and a different id than the parsed call -> rebuilt to match
        t = tool_turn(stats(["a"], "call_1_0"))
        t.raw_message["tool_calls"] = [{"id": "other", "type": "function", "function": {"name": "x", "arguments": "{}"}}]
        client, oracle, kw = run([t, text_turn("done")])
        oracle.run(**kw)
        sent = client.calls[1]["messages"][2]["tool_calls"]
        self.assertEqual([c["id"] for c in sent], ["call_1_0"])
        self.assertEqual(sent[0]["function"]["name"], "deck_stats")

    def test_consumer_messages_not_mutated(self):
        client, oracle, kw = run([text_turn("x")])
        before = json.dumps(kw["messages"])
        oracle.run(**kw)
        self.assertEqual(json.dumps(kw["messages"]), before)

    def test_error_is_llm_error(self):
        self.assertTrue(issubclass(OracleRunError, LLMError))
        for e in (OracleBudgetExceeded, OracleEvidenceMissing, OracleInvalidAnswer):
            self.assertTrue(issubclass(e, OracleRunError))


class Evidence(unittest.TestCase):
    EV = {"deck_stats": frozenset({"curve", "bracket", "power"})}

    def test_nudge_then_success(self):
        client, oracle, kw = run([text_turn("early"), tool_turn(stats(["curve", "bracket", "power"])),
                                  text_turn("final")], evidence=self.EV)
        res = oracle.run(**kw)
        self.assertEqual(res.text, "final")
        nudge = client.calls[1]["messages"][-1]
        self.assertEqual(nudge["role"], "user")
        self.assertEqual(nudge["content"], EVIDENCE_NUDGE.format(missing="deck_stats (bracket, curve, power)"))
        self.assertEqual([t["kind"] for t in res.trace.turns], ["nudge", "tool", "answer"])

    def test_union_over_calls_counts(self):
        client, oracle, kw = run([tool_turn(stats(["curve"], "a")), tool_turn(stats(["bracket", "power"], "b")),
                                  text_turn("final")], evidence=self.EV)
        self.assertEqual(oracle.run(**kw).text, "final")

    def test_failed_call_is_not_evidence(self):
        reg = ToolRegistry([Tool(name="deck_stats", description="s", parameters=STATS_SCHEMA,
                                 handler=lambda a, c: (_ for _ in ()).throw(ToolError("x", "y")))])
        client, oracle, kw = run([tool_turn(stats(["curve", "bracket", "power"])), text_turn("a"), text_turn("b")],
                                 reg=reg, evidence=self.EV)
        with self.assertRaises(OracleEvidenceMissing):
            oracle.run(**kw)

    def test_missing_twice_raises_with_trace(self):
        client, oracle, kw = run([text_turn("a"), text_turn("b")], evidence=self.EV)
        with self.assertRaises(OracleEvidenceMissing) as cm:
            oracle.run(**kw)
        self.assertEqual(cm.exception.trace.stop_reason, "evidence_missing")
        self.assertEqual(len(cm.exception.trace.turns), 2)
        self.assertEqual(cm.exception.missing, {"deck_stats": ["bracket", "curve", "power"]})


class Validation(unittest.TestCase):
    def test_repair_then_accept(self):
        calls = []

        def v(text, state):
            calls.append(text)
            if text == "bad":
                return ["e1", "e2"]
            state.parsed = {"p": text}
            return []
        client, oracle, kw = run([text_turn("bad"), text_turn("good")], validate=v)
        res = oracle.run(**kw)
        self.assertEqual(res.parsed, {"p": "good"})
        msgs = client.calls[1]["messages"]
        self.assertEqual(msgs[-2], {"role": "assistant", "content": "bad"})
        self.assertEqual(msgs[-1]["role"], "user")
        self.assertIn("e1\ne2", msgs[-1]["content"])
        self.assertEqual([t["kind"] for t in res.trace.turns], ["repair", "answer"])

    def test_invalid_twice_raises(self):
        client, oracle, kw = run([text_turn("a"), text_turn("b")], validate=lambda t, s: ["nope " + t])
        with self.assertRaises(OracleInvalidAnswer) as cm:
            oracle.run(**kw)
        self.assertEqual(cm.exception.errors, ["nope b"])
        self.assertIsNotNone(cm.exception.trace)

    def test_only_first_five_errors_sent(self):
        client, oracle, kw = run([text_turn("a"), text_turn("b")],
                                 validate=lambda t, s: ["e%d" % i for i in range(8)] if t == "a" else [])
        oracle.run(**kw)
        self.assertIn("e4", client.calls[1]["messages"][-1]["content"])
        self.assertNotIn("e5", client.calls[1]["messages"][-1]["content"])


class Caps(unittest.TestCase):
    def test_max_turns_finalizes_with_tool_choice_none_and_response_format(self):
        rf = {"type": "json_schema"}
        turns = [tool_turn(stats(["curve"], "t%d" % i)) for i in range(2)] + [text_turn("final")]
        client, oracle, kw = run(turns, caps_=caps(max_turns=3), rf=rf)
        res = oracle.run(**kw)
        self.assertEqual(res.stop_reason, "answered_after_finalize")
        self.assertEqual([c["tool_choice"] for c in client.calls], ["auto", "auto", "none"])
        self.assertEqual([c["response_format"] for c in client.calls], [None, None, rf])
        self.assertEqual(client.calls[2]["messages"][-1], {"role": "user", "content": FINALIZE_PROMPT})
        self.assertEqual(res.trace.turns[-1]["kind"], "finalize")

    def test_tools_after_finalize_raises_budget(self):
        turns = [tool_turn(stats(["curve"], "a")), tool_turn(stats(["curve"], "b"))]
        client, oracle, kw = run(turns, caps_=caps(max_turns=2))
        with self.assertRaises(OracleBudgetExceeded) as cm:
            oracle.run(**kw)
        self.assertIn("kept calling tools after finalize", str(cm.exception))
        self.assertEqual(len(client.calls), 2)
        self.assertIsNotNone(cm.exception.trace)

    def test_tool_call_budget_finalizes(self):
        client, oracle, kw = run([tool_turn(stats(["curve"], "a")), text_turn("final")],
                                 caps_=caps(max_tool_calls=1))
        res = oracle.run(**kw)
        self.assertEqual(res.stop_reason, "answered_after_finalize")
        self.assertEqual(client.calls[1]["tool_choice"], "none")

    def test_finalize_happens_once(self):
        # finalize answer is invalid -> repair is allowed only while turns remain
        client, oracle, kw = run([tool_turn(stats(["a"], "a")), text_turn("bad"), text_turn("bad2")],
                                 caps_=caps(max_turns=2), validate=lambda t, s: ["x"])
        with self.assertRaises(OracleInvalidAnswer):
            oracle.run(**kw)
        self.assertEqual(sum(1 for c in client.calls for m in c["messages"][-1:] if m["content"] == FINALIZE_PROMPT), 1)
        self.assertEqual(len(client.calls), 2)

    def test_repair_after_finalize_when_turns_remain(self):
        client, oracle, kw = run([tool_turn(stats(["a"], "a")), text_turn("bad"), text_turn("good")],
                                 caps_=caps(max_turns=4, max_tool_calls=1), validate=lambda t, s: ["x"] if t == "bad" else [])
        res = oracle.run(**kw)
        self.assertEqual(res.text, "good")
        self.assertEqual(client.calls[2]["tool_choice"], "none")
        self.assertEqual(sum(1 for c in client.calls for m in c["messages"] if m["content"] == FINALIZE_PROMPT), 2)  # carried, not re-sent

    def test_cost_soft_finalizes(self):
        client, oracle, kw = run([tool_turn(stats(["a"]), cost=0.7), text_turn("final")])
        res = oracle.run(**kw)
        self.assertEqual(res.stop_reason, "answered_after_finalize")
        self.assertEqual(client.calls[1]["tool_choice"], "none")

    def test_cost_hard_aborts_before_call(self):
        client, oracle, kw = run([tool_turn(stats(["a"]), cost=1.2), text_turn("never")])
        with self.assertRaises(OracleBudgetExceeded) as cm:
            oracle.run(**kw)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(cm.exception.trace.stop_reason, "budget_cost")

    def test_cost_none_switches_to_token_meter(self):
        c = caps(max_tokens_in=100)
        client, oracle, kw = run([tool_turn(stats(["a"]), cost=None, tokens_in=85), text_turn("final")], caps_=c)
        res = oracle.run(**kw)  # 85 >= 0.8*100 -> finalize
        self.assertEqual(client.calls[1]["tool_choice"], "none")
        self.assertIsNone(res.trace.to_job_meta()["cost_usd"])
        client, oracle, kw = run([tool_turn(stats(["a"]), cost=None, tokens_in=100), text_turn("never")], caps_=c)
        with self.assertRaises(OracleBudgetExceeded):
            oracle.run(**kw)

    def test_token_meter_ignores_cost_once_any_is_none(self):
        c = caps(max_tokens_in=1000, soft_cost_usd=0.1, hard_cost_usd=0.2)
        client, oracle, kw = run([tool_turn(stats(["a"]), cost=0.5), tool_turn(stats(["b"]), cost=None),
                                  text_turn("final")], caps_=c)
        # first turn: cost 0.5 >= soft 0.1 -> finalize on turn 2 (tools called -> budget error is expected)
        with self.assertRaises(OracleBudgetExceeded):
            oracle.run(**kw)

    def test_deadline_finalizes_then_aborts(self):
        clock = FakeClock()
        client, oracle, kw = run([tool_turn(stats(["a"])), text_turn("final")], clock=clock,
                                 caps_=caps(deadline_s=100), on_call=lambda n: setattr(clock, "t", 200.0))
        res = oracle.run(**kw)
        self.assertEqual(res.stop_reason, "answered_after_finalize")
        self.assertEqual(client.calls[1]["tool_choice"], "none")

    def test_deadline_after_finalize_raises(self):
        clock = FakeClock()
        client, oracle, kw = run([tool_turn(stats(["a"])), text_turn("bad"), text_turn("x")], clock=clock,
                                 caps_=caps(deadline_s=100, max_tool_calls=1), validate=lambda t, s: ["x"],
                                 on_call=lambda n: setattr(clock, "t", 200.0 if n >= 2 else 0.0))
        with self.assertRaises(OracleBudgetExceeded) as cm:
            oracle.run(**kw)
        self.assertEqual(str(cm.exception), "deadline")

    def test_deadline_skips_tool_calls(self):
        clock = FakeClock()
        client, oracle, kw = run([tool_turn(stats(["a"], "1")), text_turn("final")], clock=clock,
                                 caps_=caps(deadline_s=100), on_call=lambda n: setattr(clock, "t", 500.0))
        res = oracle.run(**kw)
        self.assertEqual(res.outputs[0].error_kind, "deadline")
        self.assertEqual(client.calls[1]["messages"][3]["tool_call_id"], "1")

    def test_too_many_calls_answered_not_run(self):
        reg = registry()
        calls = [stats(["s%d" % i], "c%d" % i) for i in range(4)]
        client, oracle, kw = run([tool_turn(*calls), text_turn("done")], caps_=caps(max_calls_per_turn=2), reg=reg)
        res = oracle.run(**kw)
        self.assertEqual(len(reg.seen), 2)
        self.assertEqual([o.error_kind for o in res.outputs], [None, None, "too_many_calls", "too_many_calls"])
        tool_msgs = [m for m in client.calls[1]["messages"] if m["role"] == "tool"]
        self.assertEqual([m["tool_call_id"] for m in tool_msgs], ["c0", "c1", "c2", "c3"])
        self.assertEqual(res.trace.totals["tool_calls"], 2)


class ToolFeedback(unittest.TestCase):
    def test_unknown_bad_args_and_failure_fed_back(self):
        def boom(a, c):
            raise RuntimeError("secret url")
        reg = ToolRegistry([Tool(name="deck_stats", description="s", parameters=STATS_SCHEMA, handler=boom)])
        turns = [tool_turn(call("nope", {}, "1"), call("deck_stats", {"sections": 5}, "2"),
                           call("deck_stats", {"sections": ["a"]}, "3")), text_turn("done")]
        client, oracle, kw = run(turns, reg=reg)
        with self.assertLogs("src.server.domain.oracle.registry", level="ERROR"):
            res = oracle.run(**kw)
        self.assertEqual([o.error_kind for o in res.outputs], ["unknown_tool", "bad_arguments", "tool_failed"])
        body = " ".join(m["content"] for m in client.calls[1]["messages"] if m["role"] == "tool")
        self.assertNotIn("secret url", body)
        self.assertIn("RuntimeError", body)

    def test_three_failures_disable_via_loop(self):
        def boom(a, c):
            raise ToolError("k", "m")
        reg = ToolRegistry([Tool(name="deck_stats", description="s", parameters=STATS_SCHEMA, handler=boom)])
        turns = [tool_turn(stats(["a"], "1")), tool_turn(stats(["a"], "2")), tool_turn(stats(["a"], "3")),
                 tool_turn(stats(["a"], "4")), text_turn("done")]
        client, oracle, kw = run(turns, reg=reg)
        res = oracle.run(**kw)
        self.assertEqual([o.error_kind for o in res.outputs], ["k", "k", "k", "disabled"])

    def test_max_calls_via_loop(self):
        reg = registry(max_calls=1)
        client, oracle, kw = run([tool_turn(stats(["a"], "1"), stats(["b"], "2")), text_turn("done")], reg=reg)
        res = oracle.run(**kw)
        self.assertEqual([o.error_kind for o in res.outputs], [None, "call_limit"])

    def test_oversize_result_truncated_in_message(self):
        reg = ToolRegistry([Tool(name="deck_stats", description="s", parameters=STATS_SCHEMA,
                                 handler=lambda a, c: {"rows": ["x" * 50] * 400}, max_result_chars=1000)])
        client, oracle, kw = run([tool_turn(stats(["a"])), text_turn("done")], reg=reg)
        res = oracle.run(**kw)
        content = client.calls[1]["messages"][3]["content"]
        self.assertLessEqual(len(content), 1000)
        self.assertTrue(json.loads(content)["truncated"])
        self.assertTrue(res.outputs[0].truncated)


class TraceAndNumbers(unittest.TestCase):
    def test_trace_totals_and_job_meta(self):
        client, oracle, kw = run([tool_turn(stats(["curve"]), cost=0.02, tokens_in=100), text_turn("f", cost=0.03,
                                                                                                  tokens_in=50)])
        res = oracle.run(**kw)
        t = res.trace
        self.assertEqual((t.totals["tokens_in"], t.totals["tokens_out"], t.totals["tool_calls"]), (150, 10, 1))
        self.assertAlmostEqual(t.totals["cost_usd"], 0.05)
        meta = t.to_job_meta()
        self.assertEqual(set(meta), {"task", "model", "pack", "turns", "tool_calls", "cost_usd", "stop_reason"})
        self.assertEqual((meta["task"], meta["turns"], meta["tool_calls"], meta["stop_reason"]),
                         ("deck_review", 2, 1, "answered"))
        self.assertEqual(meta["model"], "m/served")
        call_rec = t.turns[0]["calls"][0]
        self.assertEqual(set(call_rec), {"id", "tool", "args_hash", "ok", "error_kind", "ms", "result_chars",
                                         "truncated"})
        self.assertEqual(len(call_rec["args_hash"]), 12)

    def test_allowed_numbers_from_tool_outputs(self):
        client, oracle, kw = run([tool_turn(stats(["curve"])), text_turn("f")])
        res = oracle.run(**kw)
        nums = allowed_numbers_from(res.outputs, {"metrics": {"x": 42}})
        self.assertIn("7.5", nums)
        self.assertIn("42", nums)
        self.assertNotIn("99", nums)

    def test_numbers_exclude_failed_outputs(self):
        from src.server.domain.oracle.types import ToolOutcome
        bad = ToolOutcome("c", "t", {}, False, {"ok": False, "error": {"kind": "k", "message": "313"}})
        self.assertNotIn("313", allowed_numbers_from([bad]))


class Play(unittest.TestCase):
    def test_reserved(self):
        self.assertEqual(play.PLAY_TOOLS, ())
        with self.assertRaises(NotImplementedError):
            play.start_play()


if __name__ == "__main__":
    unittest.main()
