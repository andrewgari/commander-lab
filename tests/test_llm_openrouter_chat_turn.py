"""OpenRouterChat.chat_turn() tool-call parsing over httpx.MockTransport (spec oracle-tool-loop §2.1/§9.1)."""
import json
import os
import sys
import unittest

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.llm.port import ChatTurn, LLMResponseError, ToolCall, Usage  # noqa: E402
from src.server.infra.llm.openrouter_chat import LLMUsage, OpenRouterChat  # noqa: E402

KEY = "sk-or-secret-key-123"


def tc(name="deck_stats", args='{"a": 1}', id="call_x", type="function"):
    return {"id": id, "type": type, "function": {"name": name, "arguments": args}}


def reply(message, finish="tool_calls"):
    return {"model": "m/served", "choices": [{"message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 2}}


def make(handler):
    return OpenRouterChat(KEY, "m/x", transport=httpx.MockTransport(handler), sleep=lambda s: None)


def turn(message, finish="tool_calls", **kw):
    return make(lambda r: httpx.Response(200, json=reply(message, finish))).chat_turn(
        [{"role": "user", "content": "q"}], **kw)


class ChatTurnTests(unittest.TestCase):
    def test_single_call(self):
        t = turn({"content": None, "tool_calls": [tc()]})
        self.assertIsInstance(t, ChatTurn)
        self.assertEqual(t.content, "")
        c = t.tool_calls[0]
        self.assertEqual((c.id, c.name, c.arguments, c.arguments_raw, c.parse_error),
                         ("call_x", "deck_stats", {"a": 1}, '{"a": 1}', None))
        self.assertEqual((t.finish_reason, t.model, t.usage), ("tool_calls", "m/served", Usage(5, 2)))
        self.assertIs(LLMUsage, Usage)

    def test_parallel_order(self):
        t = turn({"content": "", "tool_calls": [tc("a", id="1"), tc("b", id="2"), tc("c", id="3")]})
        self.assertEqual([c.name for c in t.tool_calls], ["a", "b", "c"])

    def test_stop_with_calls(self):
        t = turn({"content": "", "tool_calls": [tc()]}, finish="stop")
        self.assertEqual(len(t.tool_calls), 1)

    def test_invalid_json(self):
        c = turn({"content": "", "tool_calls": [tc(args="{nope")]}).tool_calls[0]
        self.assertIsNone(c.arguments)
        self.assertTrue(c.parse_error.startswith("invalid JSON"))
        self.assertEqual(c.arguments_raw, "{nope")

    def test_non_object_json(self):
        c = turn({"content": "", "tool_calls": [tc(args="[1]")]}).tool_calls[0]
        self.assertIsNone(c.arguments)
        self.assertEqual(c.parse_error, "arguments must be an object")

    def test_missing_id(self):
        m = tc()
        del m["id"]
        self.assertEqual(turn({"content": "", "tool_calls": [m]}).tool_calls[0].id, "call_1_0")

    def test_non_function_dropped_with_warning(self):
        with self.assertLogs("src.server.infra.llm.openrouter_chat", "WARNING"):
            t = turn({"content": "", "tool_calls": [tc(type="retrieval"), tc("keep")]})
        self.assertEqual([c.name for c in t.tool_calls], ["keep"])
        self.assertIsInstance(t.tool_calls[0], ToolCall)

    def test_empty_no_calls(self):
        with self.assertRaisesRegex(LLMResponseError, "empty reply"):
            turn({"content": ""}, finish="stop")

    def test_length(self):
        with self.assertRaisesRegex(LLMResponseError, "truncated"):
            turn({"content": "abc"}, finish="length")

    def _err(self, payload=None, handler=None):
        h = handler or (lambda r: httpx.Response(200, json=payload))
        with self.assertRaises(LLMResponseError) as cm:
            make(h).chat_turn([{"role": "user", "content": "q"}])
        return cm.exception

    def test_failed_replies_carry_usage(self):
        usage = {"prompt_tokens": 5, "completion_tokens": 2, "cost": 0.003}
        e = self._err({"choices": [{"message": {"content": "abc"}, "finish_reason": "length"}], "usage": usage})
        self.assertEqual(e.usage, Usage(5, 2, 0, 0.003))
        e = self._err(reply({"content": ""}, "stop"))
        self.assertEqual(e.usage.tokens_in, 5)
        e = self._err(reply({"content": "t", "tool_calls": "oops"}))
        self.assertIsInstance(e.usage, Usage)
        e = self._err({"usage": usage, "choices": []})
        self.assertIsInstance(e.usage, Usage)

    def test_http_error_has_no_usage(self):
        e = self._err(handler=lambda r: httpx.Response(400, json={"error": "bad"}))
        self.assertIsNone(e.usage)

    def test_request_body(self):
        seen = {}

        def h(req):
            seen.update(json.loads(req.content))
            return httpx.Response(200, json=reply({"content": "ok"}, "stop"))
        tools = [{"type": "function", "function": {"name": "x", "parameters": {}}}]
        make(h).chat_turn([{"role": "user", "content": "q"}], tools=tools, tool_choice="auto", temperature=0.2)
        self.assertEqual(seen["tools"], tools)
        self.assertEqual(seen["tool_choice"], "auto")
        self.assertEqual(seen["provider"], {"data_collection": "deny", "require_parameters": True})

    def test_key_not_leaked(self):
        def h(req):
            return httpx.Response(400, text=f"bad Bearer {KEY} and {KEY}")
        with self.assertRaises(LLMResponseError) as cm:
            make(h).chat_turn([{"role": "user", "content": "q"}])
        self.assertNotIn(KEY, str(cm.exception))

    def test_malformed_tool_calls_container_rejected(self):
        with self.assertRaises(LLMResponseError):
            turn({"content": "text", "tool_calls": "oops"})

    def test_tool_calls_finish_without_usable_calls_rejected(self):
        with self.assertRaises(LLMResponseError):
            turn({"content": "text", "tool_calls": [{"type": "x"}]})

    def test_synthesized_id_avoids_provider_ids(self):
        t = turn({"content": "", "tool_calls": [tc(id=""), tc(id="call_1_0")]})
        ids = [c.id for c in t.tool_calls]
        self.assertEqual(len(set(ids)), 2)
        self.assertIn("call_1_0", ids)

    def test_bad_argument_shapes_report_parse_error(self):
        for bad in ("", "  ", None, 7, "[1]", "7", "null", '{"a": NaN}', '{"a": Infinity}'):
            c = turn({"content": "", "tool_calls": [tc(args=bad)]}).tool_calls[0]
            self.assertIsNotNone(c.parse_error, bad)
            self.assertIsNone(c.arguments, bad)


if __name__ == "__main__":
    unittest.main()
