"""Provider transports against a fake HTTP layer (no network, no keys)."""
from __future__ import annotations

import json
import unittest

import httpx

from src.server.domain.analytics.review_pipeline import llm
from src.server.infra.llm import provider


def req(**kw):
    base = dict(model="openrouter:anthropic/x", system_blocks=("static", "axis"), messages=(llm.Message("user", "q"),),
                schema_name="t", schema={"type": "object", "properties": {"a": {"type": "string"}}}, cache_blocks=1)
    base.update(kw)
    return llm.LLMRequest(**base)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class OpenRouterTests(unittest.TestCase):
    def test_request_shape_cache_marker_and_usage_parsing(self):
        seen = {}
        def h(r):
            seen["body"], seen["auth"] = json.loads(r.content), r.headers["authorization"]
            return httpx.Response(200, json={"id": "g1", "model": "anthropic/x-2", "choices": [{"message": {"content": '{"a":"b"}'}}],
                                             "usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost": 0.5,
                                                       "prompt_tokens_details": {"cached_tokens": 60, "cache_write_tokens": 0}}})
        resp = provider.OpenRouterClient("k", client=client(h)).complete(req())
        sysmsg = seen["body"]["messages"][0]["content"]
        self.assertEqual(seen["body"]["model"], "anthropic/x")
        self.assertEqual(["cache_control" in p for p in sysmsg], [True, False])      # marker only at the end of the cached prefix
        self.assertTrue(seen["body"]["response_format"]["json_schema"]["strict"])
        self.assertEqual(seen["auth"], "Bearer k")
        self.assertEqual((resp.text, resp.model, resp.usage.tokens_in, resp.usage.cached_tokens, resp.usage.cost_usd), ('{"a":"b"}', "anthropic/x-2", 100, 60, 0.5))

    def test_retries_5xx_then_succeeds_and_gives_up_loudly(self):
        calls = []
        def h(r):
            calls.append(1)
            return httpx.Response(503, text="busy") if len(calls) < 3 else httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
        provider._post.__defaults__  # noqa: B018 (signature check)
        orig = provider.time.sleep
        provider.time.sleep = lambda s: None
        try:
            self.assertEqual(provider.OpenRouterClient("k", client=client(h), retries=2).complete(req()).text, "{}")
            with self.assertRaises(llm.LLMTransportError) as cm:
                provider.OpenRouterClient("k", client=client(lambda r: httpx.Response(503, text="busy")), retries=1).complete(req())
            self.assertIn("503", str(cm.exception))
        finally:
            provider.time.sleep = orig

    def test_4xx_not_retried_and_key_not_leaked(self):
        calls = []
        def h(r):
            calls.append(1)
            return httpx.Response(401, text="bad key")
        with self.assertRaises(llm.LLMTransportError) as cm:
            provider.OpenRouterClient("SECRETKEY", client=client(h), retries=3).complete(req())
        self.assertEqual(len(calls), 1)
        self.assertNotIn("SECRETKEY", str(cm.exception))


class AnthropicTests(unittest.TestCase):
    def test_forced_tool_call_returns_input_as_json_text(self):
        seen = {}
        def h(r):
            seen["body"], seen["key"] = json.loads(r.content), r.headers["x-api-key"]
            return httpx.Response(200, json={"model": "m", "content": [{"type": "tool_use", "input": {"a": "b"}}],
                                             "usage": {"input_tokens": 5, "output_tokens": 2, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 0}})
        resp = provider.AnthropicClient("k", client=client(h)).complete(req(model="anthropic:m"))
        self.assertEqual(seen["body"]["tool_choice"], {"type": "tool", "name": "t"})
        self.assertEqual(json.loads(resp.text), {"a": "b"})
        self.assertEqual((resp.usage.tokens_in, resp.usage.cached_tokens, resp.usage.cost_usd), (95, 90, None))

    def test_missing_tool_use_is_a_transport_error(self):
        with self.assertRaises(llm.LLMTransportError):
            provider.AnthropicClient("k", client=client(lambda r: httpx.Response(200, json={"content": [{"type": "text", "text": "hi"}]}))).complete(req(model="anthropic:m"))


class RoutingTests(unittest.TestCase):
    def test_unconfigured_provider_fails_naming_the_variable(self):
        rc = provider.RoutingClient.from_env({"INTERACTIONS_SECRETS_DIR": "/nonexistent"})
        with self.assertRaises(llm.LLMTransportError) as cm:
            rc.complete(req(model="anthropic:m"))
        self.assertIn("ANTHROPIC_API_KEY", str(cm.exception))

    def test_routes_by_prefix(self):
        class C:
            def __init__(self, n): self.n = n
            def complete(self, r): return llm.LLMResponse(text=self.n)
        rc = provider.RoutingClient({"openrouter": C("or"), "anthropic": C("an")})
        self.assertEqual(rc.complete(req(model="anthropic:m")).text, "an")
        self.assertEqual(rc.complete(req(model="openrouter:m")).text, "or")


if __name__ == "__main__":
    unittest.main()
