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


class SpotOnTests(unittest.TestCase):
    """SpotOn AI gateway: Anthropic Messages API, `authorization: Bearer` ONLY, short-lived token from a command."""

    @staticmethod
    def ok(text='{"a":"b"}', **usage):
        return httpx.Response(200, json={"model": "claude-sonnet-5-5", "id": "msg_1", "content": [{"type": "text", "text": text}],
                                         "usage": {"input_tokens": 10, "output_tokens": 3, **usage}})

    def make(self, handler, tokens=("tok-1",), **kw):
        issued = list(tokens)
        calls = []
        def token():
            calls.append(1)
            return issued.pop(0) if len(issued) > 1 else issued[0]
        return provider.SpotOnClient(token, client=client(handler), **kw), calls

    def test_parses_an_openai_chat_completion_reply_too(self):
        # mistral / deepseek on the gateway answer in chat.completion shape (choices[0].message.content), not content[] blocks
        def h(r):
            return httpx.Response(200, json={"id": "cmpl_1", "model": "mistral-large-3", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"v":1}'}}],
                                             "usage": {"prompt_tokens": 21, "completion_tokens": 4}})
        c, _ = self.make(h)
        resp = c.complete(req(model="spoton:mistral-large-3"))
        self.assertEqual((resp.text, resp.model, resp.usage.tokens_in, resp.usage.tokens_out, resp.provider_request_id), ('{"v":1}', "mistral-large-3", 21, 4, "cmpl_1"))

    def test_an_empty_openai_shaped_reply_is_a_transport_error_naming_the_finish_reason(self):
        c, _ = self.make(lambda r: httpx.Response(200, json={"choices": [{"finish_reason": "length", "message": {"content": ""}}]}))
        with self.assertRaises(provider.LLMTransportError) as cm:
            c.complete(req(model="spoton:mistral-large-3"))
        self.assertIn("length", str(cm.exception))

    def test_bearer_only_auth_and_anthropic_messages_shape(self):
        seen = {}
        def h(r):
            seen.update(url=str(r.url), headers=dict(r.headers), body=json.loads(r.content))
            return self.ok()
        c, _ = self.make(h)
        resp = c.complete(req(model="spoton:claude-sonnet-5-5"))
        self.assertEqual(seen["url"], provider.SPOTON_URL_DEFAULT)
        self.assertEqual(seen["url"], "https://ai.spoton.com:14443/anthropic/v1/messages")
        self.assertEqual(seen["headers"]["authorization"], "Bearer tok-1")
        self.assertNotIn("x-api-key", seen["headers"])          # sending both returns 401 on the gateway
        self.assertEqual(seen["headers"]["anthropic-version"], "2023-06-01")
        body = seen["body"]
        self.assertEqual(body["model"], "claude-sonnet-5-5")
        self.assertNotIn("tool_choice", body)                   # claude-sonnet-5-5 rejects forced tool_choice
        self.assertEqual(body["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(body["output_config"]["format"]["schema"], req().schema)
        self.assertEqual(body["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn('"properties"', body["system"][-1]["text"])   # models that ignore output_config still see the schema
        self.assertEqual((resp.text, resp.model, resp.usage.tokens_in, resp.usage.tokens_out, resp.usage.cost_usd), ('{"a":"b"}', "claude-sonnet-5-5", 10, 3, None))

    def test_base_url_override_and_text_blocks_only(self):
        seen = {}
        def h(r):
            seen["url"] = str(r.url)
            return httpx.Response(200, json={"model": "m", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": '{"a"'}, {"type": "text", "text": ':"b"}'}],
                                             "usage": {"input_tokens": 5, "output_tokens": 2, "cache_read_input_tokens": 90, "cache_creation_input_tokens": 1}})
        c = provider.SpotOnClient(lambda: "t", url="https://gw.example/anthropic/v1/messages", client=client(h))
        resp = c.complete(req(model="spoton:m"))
        self.assertEqual(seen["url"], "https://gw.example/anthropic/v1/messages")
        self.assertEqual(resp.text, '{"a":"b"}')
        self.assertEqual((resp.usage.tokens_in, resp.usage.cached_tokens, resp.usage.cache_write_tokens), (96, 90, 1))

    def test_reasoning_models_are_capped_so_the_answer_fits_in_max_tokens(self):
        bodies = {}
        def h(r):
            b = json.loads(r.content); bodies[b["model"]] = b
            return self.ok()
        c, _ = self.make(h)
        c.complete(req(model="spoton:grok-4-7")); c.complete(req(model="spoton:claude-haiku-4-5"))
        self.assertEqual(bodies["grok-4-7"]["reasoning_effort"], "low")        # grok otherwise burns max_tokens thinking: no text, 3 min a call
        self.assertNotIn("reasoning_effort", bodies["claude-haiku-4-5"])
        self.assertNotIn("cache_control", json.dumps(bodies["grok-4-7"]["system"]))      # only Claude models get the cache marker

    def test_token_is_fetched_once_and_reused_across_calls(self):
        c, calls = self.make(lambda r: self.ok())
        c.complete(req(model="spoton:m")); c.complete(req(model="spoton:m"))
        self.assertEqual(len(calls), 1)

    def test_expired_token_is_refetched_once_on_401(self):
        auth = []
        def h(r):
            auth.append(r.headers["authorization"])
            return httpx.Response(401, text="expired") if len(auth) == 1 else self.ok()
        c, calls = self.make(h, tokens=("old", "new"))
        self.assertEqual(c.complete(req(model="spoton:m")).text, '{"a":"b"}')
        self.assertEqual(auth, ["Bearer old", "Bearer new"])
        self.assertEqual(len(calls), 2)

    def test_persistent_401_is_provider_unavailable_and_never_leaks_the_token(self):
        c, _ = self.make(lambda r: httpx.Response(401, text="nope"), tokens=("SECRETTOKEN",))
        with self.assertRaises(provider.ProviderUnavailable) as cm:
            c.complete(req(model="spoton:m"))
        self.assertNotIn("SECRETTOKEN", str(cm.exception))
        self.assertEqual(cm.exception.status, 401)

    def test_unreachable_gateway_and_exhausted_5xx_are_unavailable_but_400_is_not(self):
        def boom(r):
            raise httpx.ConnectError("refused")
        orig = provider.time.sleep
        provider.time.sleep = lambda s: None
        try:
            with self.assertRaises(provider.ProviderUnavailable):
                self.make(boom, retries=1)[0].complete(req(model="spoton:m"))
            with self.assertRaises(provider.ProviderUnavailable):
                self.make(lambda r: httpx.Response(503, text="down"), retries=1)[0].complete(req(model="spoton:m"))
        finally:
            provider.time.sleep = orig
        with self.assertRaises(llm.LLMTransportError) as cm:
            self.make(lambda r: httpx.Response(400, text="bad schema"))[0].complete(req(model="spoton:m"))
        self.assertNotIsInstance(cm.exception, provider.ProviderUnavailable)

    def test_reply_without_text_is_a_transport_error(self):
        c, _ = self.make(lambda r: httpx.Response(200, json={"content": [{"type": "thinking", "thinking": ""}]}))
        with self.assertRaises(llm.LLMTransportError):
            c.complete(req(model="spoton:m"))


class SpotOnTokenCommandTests(unittest.TestCase):
    def test_token_comes_from_the_configured_command_without_a_shell(self):
        import sys
        cmd = f"{sys.executable} -c \"print('abc123')\""
        self.assertEqual(provider.spoton_token({"REVIEW_SPOTON_TOKEN_CMD": cmd}), "abc123")

    def test_default_command_is_spoton_cli_ai_token(self):
        self.assertEqual(provider.spoton_token_argv({}), ["spoton-cli", "ai", "token"])
        self.assertEqual(provider.spoton_token_argv({"REVIEW_SPOTON_TOKEN_CMD": "my-cli get 'a b'"}), ["my-cli", "get", "a b"])

    def test_failing_empty_or_missing_command_is_unavailable_and_clear(self):
        import sys
        for cmd in (f"{sys.executable} -c \"import sys; sys.exit(3)\"", f"{sys.executable} -c \"pass\"", "/nonexistent/spoton-cli ai token"):
            with self.assertRaises(provider.ProviderUnavailable) as cm:
                provider.spoton_token({"REVIEW_SPOTON_TOKEN_CMD": cmd})
            self.assertIn("REVIEW_SPOTON_TOKEN_CMD", str(cm.exception))

    def test_from_env_registers_spoton_only_when_the_token_command_exists(self):
        import sys
        on = provider.RoutingClient.from_env({"REVIEW_SPOTON_TOKEN_CMD": f"{sys.executable} -c \"print(1)\"", "INTERACTIONS_SECRETS_DIR": "/nonexistent"})
        off = provider.RoutingClient.from_env({"REVIEW_SPOTON_TOKEN_CMD": "/nonexistent/spoton-cli ai token", "REVIEW_CLAUDE_CMD": "/nonexistent/claude",
                                               "INTERACTIONS_SECRETS_DIR": "/nonexistent"})
        self.assertIn("spoton", on.providers)
        self.assertNotIn("spoton", off.providers)
        self.assertNotIn("claude", off.providers)
        status = provider.provider_status({"REVIEW_SPOTON_TOKEN_CMD": "/nonexistent/spoton-cli ai token", "REVIEW_CLAUDE_CMD": "/nonexistent/claude",
                                           "INTERACTIONS_SECRETS_DIR": "/nonexistent"})
        self.assertIn("REVIEW_SPOTON_TOKEN_CMD", status["spoton"])
        self.assertIn("GEMINI_API_KEY", status["gemini"])
        self.assertIn("OPENROUTER_API_KEY", status["openrouter"])


class ClaudeSubscriptionTests(unittest.TestCase):
    def test_runs_the_claude_cli_with_the_schema_and_reads_structured_output_and_cost(self):
        seen = {}
        def runner(argv, stdin, env):
            seen.update(argv=argv, stdin=stdin)
            return 0, json.dumps({"is_error": False, "structured_output": {"a": "b"}, "total_cost_usd": 0.0019, "duration_api_ms": 1500,
                                  "usage": {"input_tokens": 100, "output_tokens": 7, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0}}), ""
        resp = provider.ClaudeSubscriptionClient(runner=runner).complete(req(model="claude:claude-haiku-4-5"))
        a = seen["argv"]
        self.assertEqual(a[a.index("--model") + 1], "claude-haiku-4-5")
        self.assertEqual(json.loads(a[a.index("--json-schema") + 1]), req().schema)
        self.assertEqual(a[a.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", a)
        self.assertIn("q", seen["stdin"])                          # the user message goes over stdin, not argv
        self.assertEqual(json.loads(resp.text), {"a": "b"})
        self.assertEqual((resp.usage.tokens_in, resp.usage.tokens_out, resp.usage.cached_tokens, resp.usage.cost_usd), (150, 7, 50, 0.0019))

    def test_cli_missing_logged_out_or_nonzero_is_unavailable(self):
        def missing(argv, stdin, env):
            raise FileNotFoundError("claude")
        def logged_out(argv, stdin, env):
            return 1, json.dumps({"is_error": True, "result": "Invalid API key - Please run /login"}), ""
        for r in (missing, logged_out):
            with self.assertRaises(provider.ProviderUnavailable):
                provider.ClaudeSubscriptionClient(runner=r).complete(req(model="claude:m"))

    def test_cli_reply_without_structured_output_is_a_plain_transport_error(self):
        runner = lambda argv, stdin, env: (0, json.dumps({"is_error": False, "result": "hi"}), "")
        with self.assertRaises(llm.LLMTransportError) as cm:
            provider.ClaudeSubscriptionClient(runner=runner).complete(req(model="claude:m"))
        self.assertNotIsInstance(cm.exception, provider.ProviderUnavailable)


class GeminiTests(unittest.TestCase):
    def test_generate_content_shape_key_header_and_usage(self):
        seen = {}
        def h(r):
            seen.update(url=str(r.url), key=r.headers.get("x-goog-api-key"), body=json.loads(r.content))
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": '{"a":"b"}'}]}}], "modelVersion": "gemini-x-001",
                                             "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 4, "cachedContentTokenCount": 10}})
        resp = provider.GeminiClient("GKEY", client=client(h)).complete(req(model="gemini:gemini-x"))
        self.assertTrue(seen["url"].endswith("/models/gemini-x:generateContent"))
        self.assertEqual(seen["key"], "GKEY")
        cfg = seen["body"]["generationConfig"]
        self.assertEqual((cfg["responseMimeType"], cfg["responseJsonSchema"]), ("application/json", req().schema))
        self.assertEqual(seen["body"]["systemInstruction"]["parts"][0]["text"], "static")
        self.assertEqual((resp.text, resp.usage.tokens_in, resp.usage.tokens_out, resp.usage.cached_tokens), ('{"a":"b"}', 40, 4, 10))

    def test_auth_failure_is_unavailable_and_key_not_leaked(self):
        with self.assertRaises(provider.ProviderUnavailable) as cm:
            provider.GeminiClient("SECRETKEY", client=client(lambda r: httpx.Response(403, text="denied"))).complete(req(model="gemini:m"))
        self.assertNotIn("SECRETKEY", str(cm.exception))


class ModelFamilyTests(unittest.TestCase):
    def test_gateway_and_cli_models_resolve_to_their_vendor_family(self):
        from src.server.domain.analytics.review_pipeline import config
        fam = config.model_family
        self.assertEqual([fam("spoton:claude-sonnet-5-5"), fam("spoton:claude-haiku-4-5"), fam("claude:claude-haiku-4-5")], ["anthropic"] * 3)
        self.assertEqual([fam("spoton:gpt-5-6-sol"), fam("spoton:grok-4-7"), fam("spoton:gemini-3-pro")], ["openai", "xai", "google"])
        self.assertEqual([fam("spoton:mistral-large-3"), fam("spoton:deepseek-v3-2")], ["mistral", "deepseek"])
        self.assertEqual(fam("spoton:some-new-model"), "spoton")             # unknown gateway model: its own family, never merged
        self.assertEqual([fam("openrouter:anthropic/x"), fam("anthropic:x"), fam("gemini:x")], ["anthropic", "anthropic", "google"])


class FallbackChainTests(unittest.TestCase):
    def test_chain_order_is_spoton_then_claude_then_gemini_then_openrouter(self):
        self.assertEqual(provider.fallback_chain("spoton:claude-sonnet-5-5"),
                         ["spoton:claude-sonnet-5-5", "claude:claude-sonnet-5-5", "openrouter:anthropic/claude-sonnet-5.5"])
        self.assertEqual(provider.fallback_chain("spoton:gpt-5-6-sol"), ["spoton:gpt-5-6-sol", "openrouter:openai/gpt-5.6-sol"])
        self.assertEqual(provider.fallback_chain("spoton:gemini-3-pro"), ["spoton:gemini-3-pro", "gemini:gemini-3-pro", "openrouter:google/gemini-3-pro"])
        self.assertEqual(provider.fallback_chain("spoton:grok-4-7"), ["spoton:grok-4-7", "openrouter:x-ai/grok-4.7"])
        self.assertEqual(provider.fallback_chain("spoton:mistral-large-3"), ["spoton:mistral-large-3"])      # no known vendor: no guessing

    def test_an_explicit_provider_is_never_rerouted(self):
        for spec in ("anthropic:claude-x", "openrouter:anthropic/claude-x", "claude:claude-x", "gemini:g"):
            self.assertEqual(provider.fallback_chain(spec), [spec])


class FakeProvider:
    def __init__(self, name, outcome):
        self.name, self.outcome, self.models = name, outcome, []

    def complete(self, request):
        self.models.append(request.model)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return llm.LLMResponse(text=self.name, model=request.model)


class FallbackClientTests(unittest.TestCase):
    def build(self, **outcomes):
        fakes = {n: FakeProvider(n, o) for n, o in outcomes.items()}
        return provider.FallbackClient(provider.RoutingClient(fakes)), fakes

    def test_first_available_provider_serves_and_is_logged(self):
        fb, f = self.build(spoton="ok", claude="ok")
        self.assertEqual(fb.complete(req(model="spoton:claude-haiku-4-5")).text, "spoton")
        self.assertEqual(f["claude"].models, [])
        self.assertEqual(fb.drain_log()["served"], {"spoton:claude-haiku-4-5": 1})

    def test_unavailable_provider_falls_through_in_order_with_the_translated_model(self):
        down = provider.ProviderUnavailable("HTTP 401: no", status=401)
        fb, f = self.build(spoton=down, claude=down, gemini="ok", openrouter="ok")
        self.assertEqual(fb.complete(req(model="spoton:claude-haiku-4-5")).text, "openrouter")
        self.assertEqual(f["spoton"].models, ["spoton:claude-haiku-4-5"])
        self.assertEqual(f["claude"].models, ["claude:claude-haiku-4-5"])
        self.assertEqual(f["gemini"].models, [])                                  # gemini cannot serve a Claude model
        self.assertEqual(f["openrouter"].models, ["openrouter:anthropic/claude-haiku-4.5"])
        log = fb.drain_log()
        self.assertEqual(log["served"], {"openrouter:anthropic/claude-haiku-4.5": 1})
        self.assertEqual([e["provider"] for e in log["failed"]], ["spoton", "claude"])
        self.assertIn("401", log["failed"][0]["reason"])

    def test_claude_subscription_is_second_for_an_anthropic_model(self):
        fb, f = self.build(spoton=provider.ProviderUnavailable("refused"), claude="ok", openrouter="ok")
        self.assertEqual(fb.complete(req(model="spoton:claude-sonnet-5-5")).text, "claude")
        self.assertEqual(f["openrouter"].models, [])

    def test_unconfigured_providers_are_skipped_not_errors(self):
        fb, f = self.build(openrouter="ok")
        self.assertEqual(fb.complete(req(model="spoton:gpt-5-6-sol")).text, "openrouter")
        self.assertEqual(f["openrouter"].models, ["openrouter:openai/gpt-5.6-sol"])

    def test_a_provider_that_failed_auth_is_not_retried_for_later_calls(self):
        fb, f = self.build(spoton=provider.ProviderUnavailable("401", status=401), claude="ok")
        fb.complete(req(model="spoton:claude-haiku-4-5")); fb.complete(req(model="spoton:claude-sonnet-5-5"))
        self.assertEqual(len(f["spoton"].models), 1)

    def test_a_missing_model_route_only_skips_that_model(self):
        fb, f = self.build(spoton=provider.ProviderUnavailable("no route", status=404), openrouter="ok")
        fb.complete(req(model="spoton:gpt-5-6-sol")); fb.complete(req(model="spoton:gpt-5-6-sol"))
        self.assertEqual(len(f["spoton"].models), 1)
        f["spoton"].outcome = "ok"
        self.assertEqual(fb.complete(req(model="spoton:claude-haiku-4-5")).text, "spoton")

    def test_a_non_availability_error_is_raised_not_rerouted(self):
        fb, f = self.build(spoton=llm.LLMTransportError("HTTP 400: bad schema"), claude="ok")
        with self.assertRaises(llm.LLMTransportError) as cm:
            fb.complete(req(model="spoton:claude-haiku-4-5"))
        self.assertNotIsInstance(cm.exception, provider.ProviderUnavailable)
        self.assertEqual(f["claude"].models, [])

    def test_every_provider_down_raises_one_error_naming_each(self):
        down = provider.ProviderUnavailable("HTTP 401: no", status=401)
        fb, _ = self.build(spoton=down, claude=provider.ProviderUnavailable("claude CLI failed"))
        with self.assertRaises(provider.ProviderUnavailable) as cm:
            fb.complete(req(model="spoton:claude-haiku-4-5"))
        msg = str(cm.exception)
        self.assertIn("spoton", msg); self.assertIn("claude CLI failed", msg); self.assertIn("openrouter", msg)   # openrouter: not configured
        self.assertIn("OPENROUTER_API_KEY", msg)

    def test_nothing_configured_names_every_provider_to_set_up(self):
        fb, _ = self.build()
        with self.assertRaises(llm.LLMTransportError) as cm:
            fb.complete(req(model="spoton:claude-haiku-4-5"))
        self.assertIn("REVIEW_SPOTON_TOKEN_CMD", str(cm.exception))

    def test_drain_log_resets(self):
        fb, _ = self.build(spoton="ok")
        fb.complete(req(model="spoton:claude-haiku-4-5"))
        fb.drain_log()
        self.assertEqual(fb.drain_log(), {"served": {}, "failed": []})


if __name__ == "__main__":
    unittest.main()
