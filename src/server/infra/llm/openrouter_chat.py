"""OpenRouter text-chat adapter behind the ``ChatClient`` port (spec t_d29ed3c9 §1.3).

Every request carries ``provider.data_collection = "deny"``. The API key is only ever placed in the
Authorization header; it is never logged and never part of an error message.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import httpx

from src.server.domain.analytics.review_pipeline.llm import LLMTransportError
from src.server.domain.llm.port import LLMAuthError, LLMResponseError, LLMUnavailable
from src.server.infra.llm.provider import OPENROUTER_BASE_URL, ProviderUnavailable, _post

REFERER = "https://github.com/andrewgari/commander-lab"
TITLE = "Commander Lab"
ALLOWED_EXTRAS = ("temperature", "response_format", "tools", "tool_choice")
PING_TIMEOUT_S = 8.0


@dataclass(frozen=True)
class LLMUsage:
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0
    cost_usd: Optional[float] = None


class OpenRouterChat:
    provider = "openrouter"

    def __init__(self, api_key: str, model: str, *, base_url: str = OPENROUTER_BASE_URL, timeout: float = 180,
                 max_tokens: Optional[int] = None, retries: int = 2,
                 transport: Optional[httpx.BaseTransport] = None, sleep: Callable[[float], None] = time.sleep):
        self._key = api_key
        self.model = model
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._retries = retries
        self._base = base_url.rstrip("/")
        self._sleep = sleep
        self._http = httpx.Client(timeout=timeout, transport=transport)
        self.last_usage: Optional[LLMUsage] = None

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._key}", "HTTP-Referer": REFERER, "X-Title": TITLE}

    def _body(self, messages: List[Dict[str, Any]], extra: Dict[str, Any]) -> Dict[str, Any]:
        extra = {k: v for k, v in extra.items() if k in ALLOWED_EXTRAS and v is not None}
        if not extra.get("tools"):  # tools=[] is invalid upstream; drop it together with tool_choice
            extra.pop("tools", None)
            extra.pop("tool_choice", None)
        prov: Dict[str, Any] = {"data_collection": "deny"}
        if "response_format" in extra or "tools" in extra:
            prov["require_parameters"] = True
        body: Dict[str, Any] = {"model": self.model, "messages": messages, "stream": False,
                                "usage": {"include": True}, "provider": prov, **extra}
        if self.max_tokens:
            body["max_tokens"] = self.max_tokens
        return body

    def chat(self, messages: List[Dict[str, Any]], **extra) -> str:
        body = self._body(messages, extra)
        url = f"{self._base}/chat/completions"
        try:
            data, _ms = _post(self._http, url, self._headers(), body, retries=self._retries, sleep=self._sleep,
                              unavailable=frozenset({401, 402, 403}))
        except ProviderUnavailable as exc:
            if exc.status in (401, 403):
                raise LLMAuthError("OpenRouter rejected the API key; check OPENROUTER_API_KEY") from None
            if exc.status == 402:
                raise LLMUnavailable("OpenRouter credits exhausted") from None
            raise LLMUnavailable(str(exc)[:200]) from None
        except LLMTransportError as exc:
            raise LLMResponseError(str(exc)[:300]) from None
        return self._parse(data)

    def _parse(self, data: Dict[str, Any]) -> str:
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMResponseError("OpenRouter reply had no message content") from None
        if isinstance(content, list):
            content = "".join(p.get("text") or "" for p in content if isinstance(p, dict))
        text = content or ""
        u = data.get("usage") or {}
        det = u.get("prompt_tokens_details") or {}
        self.last_usage = LLMUsage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0),
                                   int(det.get("cached_tokens") or 0), u.get("cost"))
        if not text.strip():
            if choice.get("finish_reason") == "length":
                raise LLMResponseError("reply truncated at max_tokens")
            raise LLMResponseError("empty reply")
        return text

    def ask(self, prompt: str, *, system: Optional[str] = None, **extra) -> str:
        msgs: List[Dict[str, Any]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        return self.chat(msgs, **extra)

    def ping(self) -> None:
        """GET /key with a short timeout; raises the mapped LLM* error on failure. Never logs the body."""
        try:
            r = self._http.get(f"{self._base}/key", headers=self._headers(), timeout=PING_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise LLMUnavailable(type(exc).__name__) from None
        if r.status_code in (401, 403):
            raise LLMAuthError("OpenRouter rejected the API key; check OPENROUTER_API_KEY")
        if r.status_code == 402:
            raise LLMUnavailable("OpenRouter credits exhausted")
        if r.status_code != 200:
            raise LLMUnavailable(f"HTTP {r.status_code}")
