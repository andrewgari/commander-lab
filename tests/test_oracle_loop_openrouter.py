"""Oracle over a real OpenRouterChat + httpx.MockTransport (spec oracle-tool-loop §9.3)."""
import json
import os
import sys
import unittest

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.oracle import LoopCaps, Oracle, Tool, ToolRegistry  # noqa: E402
from src.server.infra.llm.openrouter_chat import OpenRouterChat  # noqa: E402


def tc(id, name, args):
    return {"id": id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def reply(message, finish):
    return {"model": "m/served", "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2, "cost": 0.001}}


class OracleOverOpenRouterTests(unittest.TestCase):
    def test_call_run_feed_back(self):
        bodies = []
        script = [
            reply({"content": None, "tool_calls": [tc("call_a", "deck_stats", {"sections": ["curve"]})]}, "tool_calls"),
            reply({"content": "", "tool_calls": [tc("call_b", "deck_stats", {"sections": ["power"]})]}, "tool_calls"),
            reply({"content": "{\"verdict\": \"ok\"}"}, "stop"),
        ]

        def handler(request):
            bodies.append(json.loads(request.content))
            return httpx.Response(200, json=script[len(bodies) - 1])

        client = OpenRouterChat("sk-or-...-123", "m/x", transport=httpx.MockTransport(handler), sleep=lambda s: None)
        reg = ToolRegistry([Tool(
            name="deck_stats", description="stats",
            parameters={"type": "object", "properties": {"sections": {"type": "array", "items": {"type": "string"}}},
                        "required": ["sections"], "additionalProperties": False},
            handler=lambda a, c: {"asked": a["sections"], "value": 7})])
        oracle = Oracle(client, LoopCaps(max_turns=6, max_tool_calls=8, soft_cost_usd=0.5, hard_cost_usd=1.0,
                                         max_tokens_in=100_000, deadline_s=60))
        res = oracle.run(task="deck_review", messages=[{"role": "system", "content": "s"},
                                                       {"role": "user", "content": "review"}],
                         registry=reg, ctx=None, validate=lambda t, s: [],
                         required_evidence={"deck_stats": frozenset({"curve", "power"})})

        self.assertEqual(res.text, "{\"verdict\": \"ok\"}")
        self.assertEqual(len(bodies), 3)
        self.assertEqual(bodies[0]["tools"][0]["function"]["name"], "deck_stats")
        second = bodies[1]["messages"]
        self.assertEqual([m["role"] for m in second], ["system", "user", "assistant", "tool"])
        self.assertEqual(second[2]["tool_calls"][0]["id"], "call_a")
        self.assertEqual(second[3]["tool_call_id"], "call_a")
        self.assertEqual(json.loads(second[3]["content"]), {"ok": True, "data": {"asked": ["curve"], "value": 7}})
        third = bodies[2]["messages"]
        self.assertEqual([m["role"] for m in third], ["system", "user", "assistant", "tool", "assistant", "tool"])
        self.assertEqual(third[5]["tool_call_id"], "call_b")
        self.assertEqual(res.trace.to_job_meta()["tool_calls"], 2)
        self.assertAlmostEqual(res.trace.totals["cost_usd"], 0.003)


if __name__ == "__main__":
    unittest.main()
