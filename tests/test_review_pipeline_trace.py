"""Trace: one per run, enough to answer 'which prompt version and model produced finding X?'."""
from __future__ import annotations

import json
import threading
import unittest

from src.server.domain.analytics.review_pipeline import llm, structured, trace


def attempt(label="B:mana_consistency", verdict="ok", model="anthropic:m1", text='{"a":1}', cost=0.02):
    req = llm.LLMRequest(model=model, system_blocks=("sys",), messages=(llm.Message("user", "q"),), schema_name="s",
                         schema={"type": "object"}, label=label)
    a = structured.Attempt(index=0, request=req, verdict=verdict)
    a.response = llm.LLMResponse(text=text, usage=llm.Usage(1000, 200, 400, 0, cost), model="m1-2026", latency_ms=1500)
    return a


class RecorderTests(unittest.TestCase):
    def rec(self, **kw):
        return trace.TraceRecorder(deck_id="d1", inputs={"fact_pack_hash": "abc"}, **kw)

    def test_step_records_hashes_model_prompt_version_tokens_latency_cost_and_raw_output(self):
        r = self.rec()
        step = r.record_attempt(attempt(), pass_name="B", axis="mana_consistency", prompt_version="v1")
        d = r.finish("ok").to_dict()
        s = d["steps"][0]
        self.assertEqual((s["pass"], s["label"], s["attempt"], s["prompt_version"]), ("B", "B:mana_consistency", 0, "v1"))
        self.assertEqual(s["model"], "anthropic:m1")
        self.assertEqual(s["response_model"], "m1-2026")
        self.assertEqual(s["input_hash"], attempt().request.input_hash)
        self.assertEqual((s["tokens_in"], s["tokens_out"], s["cached_tokens"], s["latency_ms"], s["cost_usd"]), (1000, 200, 400, 1500, 0.02))
        self.assertEqual(s["response"], '{"a":1}')
        self.assertEqual(s["request"]["system_blocks"], ["sys"])
        self.assertEqual(s["verdict"], "ok")
        self.assertEqual(step["index"], 0)
        self.assertEqual(d["inputs"]["fact_pack_hash"], "abc")
        self.assertAlmostEqual(d["cost_usd"], 0.02)

    def test_cost_computed_from_prices_only_when_provider_did_not_report(self):
        prices = {"anthropic:m1": {"in": 2.0, "out": 10.0, "cache_read": 0.2, "cache_write": 2.5}}
        r = self.rec(prices=prices)
        r.record_attempt(attempt(cost=None), pass_name="B", axis="x", prompt_version="v1")
        # 600 uncached in + 400 cached + 200 out
        self.assertAlmostEqual(r.finish("ok").cost_usd, (600 * 2.0 + 400 * 0.2 + 200 * 10.0) / 1e6, places=9)

    def test_cost_is_none_not_zero_when_unknowable(self):
        r = self.rec()
        r.record_attempt(attempt(cost=None), pass_name="A", axis=None, prompt_version="v1")
        t = r.finish("ok")
        self.assertIsNone(t.cost_usd)
        self.assertIsNone(t.to_dict()["steps"][0]["cost_usd"])

    def test_budget_guard_raises_explicitly_when_exceeded(self):
        r = self.rec(max_cost_usd=0.03)
        r.record_attempt(attempt(cost=0.02), pass_name="A", axis=None, prompt_version="v1")
        with self.assertRaises(trace.BudgetExceeded) as cm:
            r.record_attempt(attempt(cost=0.02), pass_name="B", axis="x", prompt_version="v1")
        self.assertIn("0.04", str(cm.exception))

    def test_failed_transport_attempt_is_recorded(self):
        req = attempt().request
        a = structured.Attempt(index=0, request=req, verdict="failed", error="HTTP 503")
        r = self.rec()
        r.record_attempt(a, pass_name="B", axis="x", prompt_version="v1")
        s = r.finish("failed", error="boom").to_dict()
        self.assertEqual(s["steps"][0]["error"], "HTTP 503")
        self.assertIsNone(s["steps"][0]["response"])
        self.assertEqual((s["status"], s["error"]), ("failed", "boom"))

    def test_thread_safe_step_indexes_are_unique(self):
        r = self.rec()
        def work():
            for _ in range(25):
                r.record_attempt(attempt(), pass_name="B", axis="x", prompt_version="v1")
        ts = [threading.Thread(target=work) for _ in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        idx = [s["index"] for s in r.finish("ok").to_dict()["steps"]]
        self.assertEqual(sorted(idx), list(range(200)))

    def test_drops_are_logged_with_reason_and_finding(self):
        r = self.rec()
        r.record_drop(step_index=3, axis="mana_consistency", finding={"claim": "c"}, reason="evidence_card_not_in_deck", detail=["Cyclonic Rift"])
        d = r.finish("ok").to_dict()["drops"][0]
        self.assertEqual((d["reason"], d["axis"], d["step_index"], d["detail"]), ("evidence_card_not_in_deck", "mana_consistency", 3, ["Cyclonic Rift"]))

    def test_provenance_answers_which_prompt_and_model_produced_a_finding(self):
        r = self.rec()
        s = r.record_attempt(attempt(), pass_name="B", axis="mana_consistency", prompt_version="v1")
        r.link_finding("p-mana_consistency-1", s["index"], kept=True)
        t = r.finish("ok")
        p = t.provenance_of("p-mana_consistency-1")
        self.assertEqual((p["pass"], p["axis"], p["prompt_version"], p["model"], p["response_model"]), ("B", "mana_consistency", "v1", "anthropic:m1", "m1-2026"))
        self.assertEqual(p["input_hash"], attempt().request.input_hash)
        with self.assertRaises(KeyError):
            t.provenance_of("nope")

    def test_serialises_to_json_and_round_trips(self):
        r = self.rec()
        r.record_attempt(attempt(), pass_name="A", axis=None, prompt_version="v1")
        t = r.finish("ok")
        again = trace.Trace.from_dict(json.loads(json.dumps(t.to_dict())))
        self.assertEqual(again.to_dict(), t.to_dict())

    def test_models_and_prompt_versions_summarised_from_steps(self):
        r = self.rec()
        r.record_attempt(attempt(model="anthropic:a"), pass_name="A", axis=None, prompt_version="v1")
        r.record_attempt(attempt(model="anthropic:b"), pass_name="B", axis="x", prompt_version="v2")
        t = r.finish("ok").to_dict()
        self.assertEqual(t["models"], {"A": "anthropic:a", "B": "anthropic:b"})
        self.assertEqual(t["prompt_versions"]["B"], "v2")


if __name__ == "__main__":
    unittest.main()
