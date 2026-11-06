"""OpenRouterChat + build_chat_client over httpx.MockTransport."""
import json
import os
import sys
import tempfile
import unittest

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.llm.port import (LLMAuthError, LLMNotConfigured, LLMResponseError,  # noqa: E402
                                        LLMUnavailable)
from src.server.infra.llm.factory import build_chat_client  # noqa: E402
from src.server.infra.llm.openrouter_chat import OpenRouterChat  # noqa: E402
from src.server.infra.llm.tasks import TASKS  # noqa: E402

KEY = "sk-or-SECRET123"
OK = {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
      "usage": {"prompt_tokens": 10, "completion_tokens": 3, "cost": 0.002,
                "prompt_tokens_details": {"cached_tokens": 4}}}


def make(handler, **kw):
    return OpenRouterChat(KEY, "m/x", transport=httpx.MockTransport(handler), sleep=lambda s: None, **kw)


class Chat(unittest.TestCase):
    def test_request_shape_and_usage(self):
        seen = {}

        def h(req):
            seen["req"], seen["body"] = req, json.loads(req.content)
            return httpx.Response(200, json=OK)
        c = make(h, max_tokens=500)
        self.assertEqual(c.ask("q", system="s"), "hi")
        b = seen["body"]
        self.assertEqual(b["provider"], {"data_collection": "deny"})
        self.assertEqual((b["max_tokens"], b["stream"], b["usage"]), (500, False, {"include": True}))
        self.assertEqual([m["role"] for m in b["messages"]], ["system", "user"])
        self.assertEqual(seen["req"].url.path, "/api/v1/chat/completions")
        self.assertEqual(seen["req"].headers["X-Title"], "Commander Lab")
        self.assertNotIn(KEY, json.dumps(b))
        u = c.last_usage
        self.assertEqual((u.tokens_in, u.tokens_out, u.cached_tokens, u.cost_usd), (10, 3, 4, 0.002))

    def test_empty_tools_dropped_with_tool_choice(self):
        bodies = []

        def h(req):
            bodies.append(json.loads(req.content))
            return httpx.Response(200, json=OK)
        c = make(h)
        c.chat([{"role": "user", "content": "x"}], tools=[], tool_choice="auto", temperature=0.2)
        self.assertNotIn("tools", bodies[0])
        self.assertNotIn("tool_choice", bodies[0])
        self.assertEqual(bodies[0]["temperature"], 0.2)
        self.assertNotIn("require_parameters", bodies[0]["provider"])

    def test_require_parameters_with_schema_or_tools(self):
        bodies = []

        def h(req):
            bodies.append(json.loads(req.content))
            return httpx.Response(200, json=OK)
        c = make(h)
        c.chat([{"role": "user", "content": "x"}], response_format={"type": "json_object"})
        c.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}], tool_choice="auto")
        for b in bodies:
            self.assertEqual(b["provider"], {"data_collection": "deny", "require_parameters": True})
        self.assertIn("tool_choice", bodies[1])

    def test_unknown_extras_cannot_override_privacy(self):
        seen = {}

        def h(req):
            seen["b"] = json.loads(req.content)
            return httpx.Response(200, json=OK)
        make(h).chat([{"role": "user", "content": "x"}], provider={"data_collection": "allow"}, model="evil/m",
                     messages=[], stream=True)
        b = seen["b"]
        self.assertEqual(b["provider"], {"data_collection": "deny"})
        self.assertEqual((b["model"], b["stream"]), ("m/x", False))
        self.assertEqual(len(b["messages"]), 1)

    def test_list_content_passthrough_and_reply_join(self):
        parts = [{"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}}]
        seen = {}

        def h(req):
            seen["b"] = json.loads(req.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": [{"type": "text", "text": "x"}, {"type": "text", "text": "y"}]}}]})
        out = make(h).chat([{"role": "system", "content": parts}])
        self.assertEqual(seen["b"]["messages"][0]["content"], parts)
        self.assertEqual(out, "xy")

    def test_empty_replies(self):
        def length(req):
            return httpx.Response(200, json={"choices": [{"message": {"content": ""}, "finish_reason": "length"}]})

        def stop(req):
            return httpx.Response(200, json={"choices": [{"message": {"content": None}, "finish_reason": "stop"}]})
        with self.assertRaisesRegex(LLMResponseError, "truncated"):
            make(length).ask("q")
        with self.assertRaisesRegex(LLMResponseError, "empty reply"):
            make(stop).ask("q")

    def test_error_mapping(self):
        for code in (401, 403):
            with self.assertRaises(LLMAuthError) as cm:
                make(lambda r, c=code: httpx.Response(c, text="nope")).ask("q")
            self.assertNotIn(KEY, str(cm.exception))
        with self.assertRaisesRegex(LLMUnavailable, "credits exhausted"):
            make(lambda r: httpx.Response(402, text="x")).ask("q")
        with self.assertRaises(LLMResponseError) as cm:
            make(lambda r: httpx.Response(400, text="bad")).ask("q")
        self.assertNotIn(LLMUnavailable, type(cm.exception).__mro__)

    def test_retries_exhausted_is_unavailable(self):
        n = []

        def h(req):
            n.append(1)
            return httpx.Response(503, text="busy")
        with self.assertRaises(LLMUnavailable):
            make(h, retries=2).ask("q")
        self.assertEqual(len(n), 3)

    def test_ping(self):
        seen = {}

        def ok(req):
            seen["m"], seen["p"] = req.method, req.url.path
            return httpx.Response(200, json={"data": {}})
        make(ok).ping()
        self.assertEqual((seen["m"], seen["p"]), ("GET", "/api/v1/key"))
        with self.assertRaises(LLMAuthError):
            make(lambda r: httpx.Response(401)).ping()
        with self.assertRaises(LLMUnavailable):
            make(lambda r: httpx.Response(402)).ping()

        def boom(req):
            raise httpx.ConnectError("down")
        with self.assertRaises(LLMUnavailable):
            make(boom).ping()


class Factory(unittest.TestCase):
    def test_defaults_and_overrides(self):
        c = build_chat_client("theory", {"OPENROUTER_API_KEY": KEY})
        self.assertEqual((c.model, c.timeout, c.max_tokens), ("anthropic/claude-opus-5.5", 600, 16000))
        c = build_chat_client("theory", {"OPENROUTER_API_KEY": KEY, "LLM_MODEL": "a/b", "LLM_MODEL_THEORY": "c/d", "LLM_TIMEOUT_S": "5"})
        self.assertEqual((c.model, c.timeout), ("c/d", 30))
        c = build_chat_client("theory", {"OPENROUTER_API_KEY": KEY, "LLM_MODEL": "a/b"})
        self.assertEqual(c.model, "a/b")

    def test_not_configured(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(LLMNotConfigured):
                build_chat_client("theory", {"INTERACTIONS_SECRETS_DIR": d})
            open(os.path.join(d, "OPENROUTER_API_KEY"), "w").write(KEY + "\n")
            self.assertEqual(build_chat_client("theory", {"INTERACTIONS_SECRETS_DIR": d})._key, KEY)
        for v in ("none", "bogus"):
            with self.assertRaises(LLMNotConfigured):
                build_chat_client("theory", {"OPENROUTER_API_KEY": KEY, "LLM_PROVIDER": v})
        with self.assertRaises(ValueError):
            build_chat_client("nope", {"OPENROUTER_API_KEY": KEY})

    def test_task_table(self):
        self.assertEqual(set(TASKS), {"card_review", "deck_review", "sim_review", "sim_diagnosis", "personality_read",
                                      "genre_autofill", "deck_writeup", "theory", "reconstruct", "review_writer"})
        self.assertEqual(TASKS["sim_diagnosis"].model, "anthropic/claude-haiku-5.5")
        self.assertEqual((TASKS["deck_review"].timeout_s, TASKS["deck_review"].max_tokens), (300, 8000))

    def test_base_url_env(self):
        c = build_chat_client("theory", {"OPENROUTER_API_KEY": KEY, "OPENROUTER_BASE_URL": "http://x/v1/"})
        self.assertEqual(c._base, "http://x/v1")


if __name__ == "__main__":
    unittest.main()
