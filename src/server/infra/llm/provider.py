"""HTTP implementations of the pipeline's ``LLMClient`` (DECISIONS D3). The only place review passes
reach a model. A model spec is ``<provider>:<model id>``; ``RoutingClient`` picks the transport.

* ``openrouter:<id>``  OpenAI-compatible chat completions with strict ``json_schema`` output,
  ``cache_control`` on the static prefix, and ``usage.cost`` / cached-token accounting (verified live).
* ``anthropic:<id>``   Messages API; the schema is enforced through a forced tool call.
* ``spoton:<id>``      the SpotOn AI gateway (Anthropic Messages wire format, ``authorization: Bearer`` only). The
  short-lived token comes from ``REVIEW_SPOTON_TOKEN_CMD`` (default ``spoton-cli ai token``) at run time; the base URL
  from ``REVIEW_SPOTON_URL``. Models that reject a forced ``tool_choice`` get the schema through ``output_config``.
* ``claude:<id>``      the Claude subscription through the ``claude`` CLI (``-p --json-schema``); no key is read.
* ``gemini:<id>``      Gemini ``generateContent`` with ``responseJsonSchema``.
Keys come from env or a file in the secrets dir (never from code or git). ``ProviderUnavailable`` marks "this provider
cannot serve the call" (unreachable, auth failed, route missing) so a caller can fall back to the next provider.
"""
from __future__ import annotations

import dataclasses
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Any, Callable, Mapping

import httpx

from src.server.domain.analytics.review_pipeline.llm import (LLMRequest, LLMResponse, LLMTransportError, Usage)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
SPOTON_URL_DEFAULT = "https://ai.spoton.com:14443/anthropic/v1/messages"
SPOTON_TOKEN_CMD_DEFAULT = "spoton-cli ai token"
SECRETS_DIR_DEFAULT = "/run/commander-lab-secrets"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
AUTH_STATUS = frozenset({401, 403})


class ProviderUnavailable(LLMTransportError):
    """This provider cannot serve the call right now (unreachable, retries exhausted, auth failed, no route for the
    model, CLI missing or logged out). A fallback chain moves to the next provider; a plain ``LLMTransportError``
    (bad request, bad reply) is not an availability problem and must not be retried elsewhere."""

    def __init__(self, message: str, *, status: int | None = None):
        super().__init__(message)
        self.status = status


def _secret(name: str, env: Mapping[str, str]) -> str | None:
    if env.get(name):
        return env[name].strip()
    try:
        with open(os.path.join(env.get("INTERACTIONS_SECRETS_DIR") or SECRETS_DIR_DEFAULT, name), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _post(client: httpx.Client, url: str, headers: dict, body: dict, *, retries: int, sleep=time.sleep,
          unavailable: frozenset[int] = frozenset()) -> tuple[dict, int]:
    """POST with bounded retry on 429/5xx. Raises LLMTransportError (key never included in the message);
    ``ProviderUnavailable`` when the host is unreachable, retries ran out, or the status is in ``unavailable``."""
    last = ""
    status: int | None = None
    retryable = True
    for i in range(retries + 1):
        t0 = time.monotonic()
        try:
            r = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            last, status = f"{type(exc).__name__}: {exc}", None
        else:
            if r.status_code == 200:
                try:
                    return r.json(), int((time.monotonic() - t0) * 1000)
                except ValueError as exc:
                    raise LLMTransportError(f"non-JSON 200 response from {url}: {exc}") from exc
            last, status = f"HTTP {r.status_code}: {r.text[:300]}", r.status_code
            if r.status_code not in RETRY_STATUS:
                retryable = False
                break
        if i < retries:
            sleep(min(2 ** i, 8))
    if retryable or status in unavailable:
        raise ProviderUnavailable(f"{url}: {last}", status=status)
    raise LLMTransportError(f"{url}: {last}")


class OpenRouterClient:
    def __init__(self, api_key: str, *, client: httpx.Client | None = None, timeout: float = 600.0, retries: int = 2):
        self._key, self._retries = api_key, retries
        self._http = client or httpx.Client(timeout=timeout)

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model.split(":", 1)[1]
        sys_content = []
        for i, block in enumerate(request.system_blocks):
            part: dict[str, Any] = {"type": "text", "text": block}
            if request.cache_blocks and i == request.cache_blocks - 1:
                part["cache_control"] = {"type": "ephemeral"}
            sys_content.append(part)
        body: dict[str, Any] = {
            "model": model, "max_tokens": request.max_tokens, "usage": {"include": True},
            "messages": [{"role": "system", "content": sys_content},
                         *[{"role": m.role, "content": m.content} for m in request.messages]],
            "response_format": {"type": "json_schema", "json_schema": {"name": request.schema_name, "strict": True, "schema": dict(request.schema)}},
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        data, ms = _post(self._http, OPENROUTER_URL, {"Authorization": f"Bearer {self._key}"}, body, retries=self._retries)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMTransportError(f"openrouter reply had no message content: {str(data)[:300]}") from exc
        u = data.get("usage") or {}
        det = u.get("prompt_tokens_details") or {}
        return LLMResponse(text=text, model=str(data.get("model") or model), latency_ms=ms, provider_request_id=data.get("id"),
                           usage=Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0),
                                       int(det.get("cached_tokens") or 0), int(det.get("cache_write_tokens") or 0),
                                       u.get("cost")))


class AnthropicClient:
    def __init__(self, api_key: str, *, client: httpx.Client | None = None, timeout: float = 600.0, retries: int = 2):
        self._key, self._retries = api_key, retries
        self._http = client or httpx.Client(timeout=timeout)

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model.split(":", 1)[1]
        system = []
        for i, block in enumerate(request.system_blocks):
            part: dict[str, Any] = {"type": "text", "text": block}
            if request.cache_blocks and i == request.cache_blocks - 1:
                part["cache_control"] = {"type": "ephemeral"}
            system.append(part)
        body = {"model": model, "max_tokens": request.max_tokens, "system": system,
                "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                "tools": [{"name": request.schema_name, "description": "Return the result.", "input_schema": dict(request.schema)}],
                "tool_choice": {"type": "tool", "name": request.schema_name}}
        data, ms = _post(self._http, ANTHROPIC_URL, {"x-api-key": self._key, "anthropic-version": "2023-06-01"}, body, retries=self._retries)
        block = next((b for b in data.get("content") or [] if b.get("type") == "tool_use"), None)
        if block is None:
            raise LLMTransportError(f"anthropic reply had no tool_use block (stop_reason={data.get('stop_reason')})")
        u = data.get("usage") or {}
        read, write = int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)
        return LLMResponse(text=json.dumps(block.get("input") or {}), model=str(data.get("model") or model), latency_ms=ms,
                           provider_request_id=data.get("id"),
                           usage=Usage(int(u.get("input_tokens") or 0) + read + write, int(u.get("output_tokens") or 0), read, write, None))


def _system_blocks(request: LLMRequest, *, cache: bool = True) -> list[dict[str, Any]]:
    out = []
    for i, block in enumerate(request.system_blocks):
        part: dict[str, Any] = {"type": "text", "text": block}
        if cache and request.cache_blocks and i == request.cache_blocks - 1:
            part["cache_control"] = {"type": "ephemeral"}
        out.append(part)
    return out


def _schema_note(request: LLMRequest) -> str:
    return ("Respond with ONLY one JSON object that validates against this JSON schema, no prose and no code fences.\n"
            f"Schema `{request.schema_name}`: {json.dumps(dict(request.schema), separators=(',', ':'))}")


def spoton_url(env: Mapping[str, str]) -> str:
    """REVIEW_SPOTON_URL may be the full messages URL or a base (``.../anthropic`` or ``.../anthropic/v1``)."""
    url = (env.get("REVIEW_SPOTON_URL") or SPOTON_URL_DEFAULT).strip().rstrip("/")
    if url.endswith("/messages"):
        return url
    return url + ("/messages" if url.endswith("/v1") else "/v1/messages")


def spoton_token_argv(env: Mapping[str, str]) -> list[str]:
    return shlex.split(env.get("REVIEW_SPOTON_TOKEN_CMD") or SPOTON_TOKEN_CMD_DEFAULT)


def spoton_token(env: Mapping[str, str]) -> str:
    """The gateway's short-lived bearer token, from the token command (run directly, never through a shell)."""
    argv = spoton_token_argv(env)
    hint = f"REVIEW_SPOTON_TOKEN_CMD ({shlex.join(argv) if argv else 'unset'})"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        raise ProviderUnavailable(f"{hint} could not run: {type(exc).__name__}: {exc}") from exc
    token = r.stdout.strip()
    if r.returncode != 0 or not token:
        why = (r.stderr.strip().splitlines() or ["no output"])[0][:200] if r.returncode != 0 or not r.stdout.strip() else ""
        raise ProviderUnavailable(f"{hint} gave no token (exit {r.returncode}): {why}; log in with `spoton-cli ai auth`")
    return token


def _reply_text(data: dict) -> str:
    """Text of a gateway reply. Claude models answer in Anthropic shape (``content[]`` blocks); some others (mistral)
    come back as an OpenAI ``chat.completion`` (``choices[0].message.content``). Accept both."""
    blocks = data.get("content")
    if isinstance(blocks, list):
        text = "".join(b.get("text") or "" for b in blocks if isinstance(b, dict) and b.get("type") == "text")
        if text.strip():
            return text
    choices = data.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(b.get("text") or "" for b in content if isinstance(b, dict))
    return ""


def _finish_reason(data: dict) -> str | None:
    choices = data.get("choices")
    return choices[0].get("finish_reason") if isinstance(choices, list) and choices and isinstance(choices[0], dict) else None


class SpotOnClient:
    """SpotOn AI gateway, Anthropic Messages wire format at ``.../anthropic/v1/messages``. Auth is the
    ``authorization: Bearer`` header ONLY (adding ``x-api-key`` makes the gateway answer 401). The token is fetched
    lazily, reused, and re-fetched once on a 401 (it is short-lived). Some gateway models (claude-sonnet-5-5) reject
    ``tool_choice`` type tool/any, so the schema goes through ``output_config.format`` and is restated in a final system
    block for models that ignore it (gpt, grok); the pipeline's own validator and repair loop still enforce it."""

    def __init__(self, token: Callable[[], str], *, url: str = SPOTON_URL_DEFAULT, client: httpx.Client | None = None,
                 timeout: float = 600.0, retries: int = 2):
        self._token_fn, self._url, self._retries = token, url, retries
        self._http = client or httpx.Client(timeout=timeout)
        self._token: str | None = None
        self._lock = threading.Lock()                      # Pass B axes run in parallel: fetch the token once, not per thread

    def _bearer(self, refresh: bool = False, stale: str | None = None) -> str:
        with self._lock:
            if self._token is None or (refresh and self._token == stale):
                self._token = self._token_fn()
            return self._token

    def _post(self, body: dict) -> tuple[dict, int]:
        unavailable = AUTH_STATUS | {404}                           # 404: "No matching route" = model not on the gateway
        used = self._bearer()
        try:
            return _post(self._http, self._url, {"authorization": f"Bearer {used}", "anthropic-version": "2023-06-01"}, body,
                         retries=self._retries, unavailable=unavailable)
        except ProviderUnavailable as exc:
            if exc.status != 401:
                raise
        fresh = self._bearer(refresh=True, stale=used)      # a 401 on a short-lived token: fetch a new one, once
        return _post(self._http, self._url, {"authorization": f"Bearer {fresh}", "anthropic-version": "2023-06-01"}, body,
                     retries=self._retries, unavailable=unavailable)

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model.split(":", 1)[1]
        system = _system_blocks(request, cache=model.startswith("claude"))
        system.append({"type": "text", "text": _schema_note(request)})
        body = {"model": model, "max_tokens": request.max_tokens, "system": system,
                "messages": [{"role": m.role, "content": m.content} for m in request.messages],
                "output_config": {"format": {"type": "json_schema", "schema": dict(request.schema)}}}
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if model.lower().startswith("grok"):
            body["reasoning_effort"] = "low"     # grok reasons until max_tokens (no text, ~3 min a call) unless capped
        data, ms = self._post(body)
        text = _reply_text(data)
        if not text.strip():
            raise LLMTransportError(f"spoton reply had no text content (stop_reason={data.get('stop_reason') or _finish_reason(data)})")
        u = data.get("usage") or {}
        read, write = int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)
        tokens_in = u.get("input_tokens") if "input_tokens" in u else u.get("prompt_tokens")          # OpenAI-shaped replies say prompt_/completion_
        tokens_out = u.get("output_tokens") if "output_tokens" in u else u.get("completion_tokens")
        return LLMResponse(text=text, model=str(data.get("model") or model), latency_ms=ms, provider_request_id=data.get("id"),
                           usage=Usage(int(tokens_in or 0) + read + write, int(tokens_out or 0), read, write, None))


def _run_cli(argv: list[str], stdin: str, env: Mapping[str, str]) -> tuple[int, str, str]:
    with tempfile.TemporaryDirectory() as cwd:                      # no repo, so no project CLAUDE.md or settings leak in
        r = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=900, check=False, cwd=cwd, env=dict(env))
    return r.returncode, r.stdout, r.stderr


class ClaudeSubscriptionClient:
    """The Claude subscription through the ``claude`` CLI (print mode with ``--json-schema``). No key or OAuth token is
    read here: the CLI owns its login. ANTHROPIC_* variables are removed from the child so it bills the subscription, not
    an API key. The system blocks and the user messages travel over stdin (argv has a per-argument size limit)."""

    def __init__(self, *, command: str = "claude", runner: Callable[[list[str], str, Mapping[str, str]], tuple[int, str, str]] = _run_cli,
                 env: Mapping[str, str] | None = None):
        self._cmd, self._run = command, runner
        base = dict(os.environ if env is None else env)
        self._env = {k: v for k, v in base.items() if not k.startswith("ANTHROPIC_")}

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model.split(":", 1)[1]
        argv = [*shlex.split(self._cmd), "-p", "--model", model, "--output-format", "json", "--json-schema", json.dumps(dict(request.schema)),
                "--tools", "", "--setting-sources", "", "--no-session-persistence",
                "--system-prompt", "You are a structured-output API. Follow the instructions in the input and reply only through the structured output."]
        stdin = "\n\n".join([*request.system_blocks, *(f"[{m.role}]\n{m.content}" for m in request.messages)])
        t0 = time.monotonic()
        try:
            rc, out, err = self._run(argv, stdin, self._env)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ProviderUnavailable(f"claude CLI could not run: {type(exc).__name__}: {exc}") from exc
        ms = int((time.monotonic() - t0) * 1000)
        try:
            data = json.loads(out)
        except ValueError:
            data = None
        if rc != 0 or not isinstance(data, dict) or data.get("is_error"):
            detail = (data.get("result") if isinstance(data, dict) else None) or err.strip() or out.strip() or "no output"
            raise ProviderUnavailable(f"claude CLI failed (exit {rc}): {str(detail)[:200]}; run `claude` once to log in")
        structured = data.get("structured_output")
        if not isinstance(structured, (dict, list)):
            raise LLMTransportError(f"claude CLI reply had no structured_output: {str(data.get('result'))[:200]}")
        u = data.get("usage") or {}
        read, write = int(u.get("cache_read_input_tokens") or 0), int(u.get("cache_creation_input_tokens") or 0)
        return LLMResponse(text=json.dumps(structured), model=model, latency_ms=int(data.get("duration_api_ms") or ms),
                           provider_request_id=data.get("session_id"),
                           usage=Usage(int(u.get("input_tokens") or 0) + read + write, int(u.get("output_tokens") or 0), read, write,
                                       data.get("total_cost_usd")))


class GeminiClient:
    def __init__(self, api_key: str, *, client: httpx.Client | None = None, timeout: float = 600.0, retries: int = 2):
        self._key, self._retries = api_key, retries
        self._http = client or httpx.Client(timeout=timeout)

    def complete(self, request: LLMRequest) -> LLMResponse:
        model = request.model.split(":", 1)[1]
        body: dict[str, Any] = {
            "systemInstruction": {"parts": [{"text": b} for b in request.system_blocks]},
            "contents": [{"role": "model" if m.role == "assistant" else "user", "parts": [{"text": m.content}]} for m in request.messages],
            "generationConfig": {"responseMimeType": "application/json", "responseJsonSchema": dict(request.schema),
                                 "maxOutputTokens": request.max_tokens}}
        if request.temperature is not None:
            body["generationConfig"]["temperature"] = request.temperature
        data, ms = _post(self._http, GEMINI_URL.format(model=model), {"x-goog-api-key": self._key}, body, retries=self._retries, unavailable=AUTH_STATUS)
        try:
            parts = data["candidates"][0]["content"]["parts"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMTransportError(f"gemini reply had no candidate content: {str(data)[:300]}") from exc
        text = "".join(p.get("text") or "" for p in parts if not p.get("thought"))
        u = data.get("usageMetadata") or {}
        return LLMResponse(text=text, model=str(data.get("modelVersion") or model), latency_ms=ms, provider_request_id=data.get("responseId"),
                           usage=Usage(int(u.get("promptTokenCount") or 0), int(u.get("candidatesTokenCount") or 0) + int(u.get("thoughtsTokenCount") or 0),
                                       int(u.get("cachedContentTokenCount") or 0), 0, None))


SETUP_HINT = {
    "spoton": "set REVIEW_SPOTON_TOKEN_CMD (a command that prints the gateway token; default `spoton-cli ai token`, must be on PATH) "
              "and optionally REVIEW_SPOTON_URL",
    "claude": "install and log in to the `claude` CLI (REVIEW_CLAUDE_CMD overrides the command)",
    "gemini": "set GEMINI_API_KEY (or GOOGLE_API_KEY) as an env var or secret file",
    "openrouter": "set OPENROUTER_API_KEY as an env var or secret file",
    "anthropic": "set ANTHROPIC_API_KEY as an env var or secret file",
}


def _executable(argv: list[str]) -> bool:
    return bool(argv) and shutil.which(argv[0]) is not None


def _gemini_key(env: Mapping[str, str]) -> str | None:
    return _secret("GEMINI_API_KEY", env) or _secret("GOOGLE_API_KEY", env)


def provider_status(env: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Per provider: ``None`` when it can be used from this environment, else what is missing. Makes no network call."""
    env = os.environ if env is None else env
    ok = {
        "spoton": _executable(spoton_token_argv(env)),
        "claude": _executable(shlex.split(env.get("REVIEW_CLAUDE_CMD") or "claude")),
        "gemini": bool(_gemini_key(env)),
        "openrouter": bool(_secret("OPENROUTER_API_KEY", env)),
        "anthropic": bool(_secret("ANTHROPIC_API_KEY", env)),
    }
    return {name: None if ok[name] else SETUP_HINT[name] for name in ok}


# Fallback chain for ``spoton:<model>`` (Andrew's order: SpotOn gateway -> Claude subscription -> Gemini -> OpenRouter).
# A provider is only a candidate when it can serve that vendor's model: the subscription CLI serves Claude, Gemini serves
# Gemini, OpenRouter serves everything it lists as ``<vendor>/<model>``.
FALLBACK_ORDER = ("spoton", "claude", "gemini", "openrouter")
_VENDOR_PREFIXES = (("claude", "anthropic"), ("gpt", "openai"), ("gemini", "google"), ("grok", "xai"))
_OPENROUTER_VENDOR = {"xai": "x-ai"}


def vendor_of(model_id: str) -> str | None:
    m = model_id.strip().lower()
    return next((v for prefix, v in _VENDOR_PREFIXES if m.startswith(prefix)), None)


def fallback_chain(spec: str) -> list[str]:
    """``spoton:claude-sonnet-5-5`` -> [spoton:claude-sonnet-5-5, claude:claude-sonnet-5-5, openrouter:anthropic/claude-sonnet-5.5].
    Any other provider prefix is returned alone: an explicit provider choice is never rerouted."""
    prov, _, model = spec.partition(":")
    if prov != "spoton":
        return [spec]
    vendor = vendor_of(model)
    chain = [spec]
    if vendor == "anthropic":
        chain.append(f"claude:{model}")
    if vendor == "google":
        chain.append(f"gemini:{model}")
    if vendor:
        dotted = re.sub(r"(?<=\d)-(?=\d)", ".", model)           # claude-sonnet-5-5 -> claude-sonnet-5.5, gpt-5-6-sol -> gpt-5.6-sol
        chain.append(f"openrouter:{_OPENROUTER_VENDOR.get(vendor, vendor)}/{dotted}")
    return chain


class RoutingClient:
    """Routes ``<provider>:<model>`` to a transport; unknown or unconfigured providers fail loudly."""

    def __init__(self, clients: Mapping[str, Any]):
        self._clients = dict(clients)

    @property
    def providers(self) -> frozenset[str]:
        return frozenset(self._clients)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RoutingClient":
        env = os.environ if env is None else env
        clients: dict[str, Any] = {}
        status = provider_status(env)
        if status["spoton"] is None:
            clients["spoton"] = SpotOnClient(lambda: spoton_token(env), url=spoton_url(env))
        if status["claude"] is None:
            clients["claude"] = ClaudeSubscriptionClient(command=env.get("REVIEW_CLAUDE_CMD") or "claude", env=env)
        if k := _gemini_key(env):
            clients["gemini"] = GeminiClient(k)
        if k := _secret("OPENROUTER_API_KEY", env):
            clients["openrouter"] = OpenRouterClient(k)
        if k := _secret("ANTHROPIC_API_KEY", env):
            clients["anthropic"] = AnthropicClient(k)
        return cls(clients)

    def complete(self, request: LLMRequest) -> LLMResponse:
        prov = request.model.split(":", 1)[0]
        if prov not in self._clients:
            raise LLMTransportError(f"no credentials configured for provider {prov!r} (model {request.model!r}); "
                                    f"{SETUP_HINT.get(prov) or f'set {prov.upper()}_API_KEY as an env var or secret file'}")
        return self._clients[prov].complete(request)


class FallbackClient:
    """Serves a ``spoton:<model>`` request from the first provider in ``fallback_chain`` that can: SpotOn gateway, the Claude
    subscription, Gemini, then OpenRouter. A provider is skipped when it is not configured here, and tried again no more
    this run once its auth failed or it was unreachable (so a dead gateway costs one failure, not one per call). Any
    other error (a 400, an unusable reply) is raised as-is: it is not an availability problem and a different provider
    would not fix it. Every request that is not ``spoton:`` goes to its own provider unchanged.

    ``drain_log()`` returns what served the calls since the last drain and which providers failed, for per-deck reporting."""

    def __init__(self, routing: RoutingClient):
        self._routing = routing
        self._dead: set[str] = set()                       # providers out for the whole run
        self._dead_models: set[tuple[str, str]] = set()    # (provider, model) with no route
        self._served: dict[str, int] = {}
        self._failed: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    @property
    def providers(self) -> frozenset[str]:
        return self._routing.providers

    def drain_log(self) -> dict[str, Any]:
        with self._lock:
            log = {"served": self._served, "failed": self._failed}
            self._served, self._failed = {}, []
        return log

    def complete(self, request: LLMRequest) -> LLMResponse:
        chain = fallback_chain(request.model)
        skipped: list[str] = []
        failures: list[str] = []
        for spec in chain:
            prov = spec.split(":", 1)[0]
            if prov not in self._routing.providers:
                skipped.append(f"{prov}: not configured ({SETUP_HINT.get(prov, 'no credentials')})")
                continue
            with self._lock:
                out = prov in self._dead or (prov, spec) in self._dead_models
            if out:
                skipped.append(f"{prov}: unavailable earlier in this run")
                continue
            try:
                resp = self._routing.complete(request if spec == request.model else dataclasses.replace(request, model=spec))
            except ProviderUnavailable as exc:
                reason = str(exc)[:300]
                failures.append(f"{prov}: {reason}")
                with self._lock:
                    self._failed.append({"provider": prov, "model": spec, "reason": reason})
                    if exc.status == 404:
                        self._dead_models.add((prov, spec))
                    elif exc.status is None or exc.status in AUTH_STATUS:
                        self._dead.add(prov)
                continue
            with self._lock:
                self._served[spec] = self._served.get(spec, 0) + 1
            return resp
        detail = "; ".join([*failures, *skipped])
        raise ProviderUnavailable(f"no provider could serve {request.model!r} (tried in order): {detail}")

