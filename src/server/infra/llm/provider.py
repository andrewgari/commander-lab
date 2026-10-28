"""HTTP implementations of the pipeline's ``LLMClient`` (DECISIONS D3). The only place review passes
reach a model. A model spec is ``<provider>:<model id>``; ``RoutingClient`` picks the transport.

* ``openrouter:<id>``  OpenAI-compatible chat completions with strict ``json_schema`` output,
  ``cache_control`` on the static prefix, and ``usage.cost`` / cached-token accounting (verified live).
* ``anthropic:<id>``   Messages API; the schema is enforced through a forced tool call.
Keys come from env or a file in the secrets dir (never from code or git).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Mapping

import httpx

from src.server.domain.analytics.review_pipeline.llm import (LLMRequest, LLMResponse, LLMTransportError, Usage)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
SECRETS_DIR_DEFAULT = "/run/commander-lab-secrets"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}


def _secret(name: str, env: Mapping[str, str]) -> str | None:
    if env.get(name):
        return env[name].strip()
    try:
        with open(os.path.join(env.get("INTERACTIONS_SECRETS_DIR") or SECRETS_DIR_DEFAULT, name), encoding="utf-8") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def _post(client: httpx.Client, url: str, headers: dict, body: dict, *, retries: int, sleep=time.sleep) -> tuple[dict, int]:
    """POST with bounded retry on 429/5xx. Raises LLMTransportError (key never included in the message)."""
    last = ""
    for i in range(retries + 1):
        t0 = time.monotonic()
        try:
            r = client.post(url, headers=headers, json=body)
        except httpx.HTTPError as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if r.status_code == 200:
                try:
                    return r.json(), int((time.monotonic() - t0) * 1000)
                except ValueError as exc:
                    raise LLMTransportError(f"non-JSON 200 response from {url}: {exc}") from exc
            last = f"HTTP {r.status_code}: {r.text[:300]}"
            if r.status_code not in RETRY_STATUS:
                break
        if i < retries:
            sleep(min(2 ** i, 8))
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


class RoutingClient:
    """Routes ``<provider>:<model>`` to a transport; unknown or unconfigured providers fail loudly."""

    def __init__(self, clients: Mapping[str, Any]):
        self._clients = dict(clients)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "RoutingClient":
        env = os.environ if env is None else env
        clients: dict[str, Any] = {}
        if k := _secret("OPENROUTER_API_KEY", env):
            clients["openrouter"] = OpenRouterClient(k)
        if k := _secret("ANTHROPIC_API_KEY", env):
            clients["anthropic"] = AnthropicClient(k)
        return cls(clients)

    def complete(self, request: LLMRequest) -> LLMResponse:
        prov = request.model.split(":", 1)[0]
        if prov not in self._clients:
            raise LLMTransportError(f"no credentials configured for provider {prov!r} (model {request.model!r}); "
                                    f"set {prov.upper()}_API_KEY as an env var or secret file")
        return self._clients[prov].complete(request)
