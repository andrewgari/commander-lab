"""OpenRouter text-chat adapter behind the ``ChatClient`` port (spec t_d29ed3c9 §1.3).

Every request carries ``provider.data_collection = "deny"``. The API key is only ever placed in the
Authorization header; it is never logged and never part of an error message.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

import httpx

from src.server.domain.analytics.review_pipeline.llm import LLMTransportError
from src.server.domain.llm.port import (ChatTurn, LLMAuthError, LLMResponseError, LLMUnavailable, ToolCall,
                                        Usage)
from src.server.infra.llm.provider import OPENROUTER_BASE_URL, ProviderUnavailable, _post

REFERER = "https://github.com/andrewgari/commander-lab"
TITLE = "Commander Lab"
ALLOWED_EXTRAS = ("temperature", "response_format", "tools", "tool_choice")
PING_TIMEOUT_S = 8.0


LLMUsage = Usage  # re-export under the historical name
log = logging.getLogger(__name__)


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
        self.last_usage: Optional[Usage] = None
        self._turns = 0

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
        return self._parse(self._request(messages, extra))

    def _request(self, messages: List[Dict[str, Any]], extra: Dict[str, Any]) -> Dict[str, Any]:
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
            raise LLMUnavailable(self._redact(str(exc))[:200]) from None
        except LLMTransportError as exc:
            raise LLMResponseError(self._redact(str(exc))[:300]) from None
        return data

    def _redact(self, text: str) -> str:
        """Remove the API key (and any bearer token) from upstream/transport text before it reaches an error."""
        if self._key:
            text = text.replace(self._key, "***")
        return re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", text)

    def _parse(self, data: Dict[str, Any]) -> str:
        usage = self._set_usage(data) if isinstance(data, dict) else None
        try:
            choice = data["choices"][0]
            content = choice["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMResponseError("OpenRouter reply had no message content", usage=usage) from None
        content = self._norm_content(content, usage)
        if choice.get("finish_reason") == "length":
            raise LLMResponseError("reply truncated at max_tokens", usage=usage)
        if not content.strip():
            raise LLMResponseError("empty reply", usage=usage)
        return content

    @staticmethod
    def _norm_content(content: Any, usage: Optional[Usage] = None) -> str:
        if content is None:
            return ""
        if isinstance(content, list):
            return "".join(p["text"] for p in content if isinstance(p, dict) and isinstance(p.get("text"), str))
        if not isinstance(content, str):
            raise LLMResponseError("OpenRouter reply content had an unexpected type", usage=usage)
        return content

    def _set_usage(self, data: Dict[str, Any]) -> Usage:
        u = data.get("usage") or {}
        try:
            det = u.get("prompt_tokens_details") or {}
            cost = u.get("cost")
            usage = Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0),
                          int(det.get("cached_tokens") or 0), float(cost) if cost is not None else None)
        except (AttributeError, TypeError, ValueError):
            raise LLMResponseError("OpenRouter reply had malformed usage") from None
        self.last_usage = usage
        return usage

    def chat_turn(self, messages: List[Dict[str, Any]], *, tools: Optional[List[Dict[str, Any]]] = None,
                  tool_choice: Union[str, Dict[str, Any], None] = None,
                  response_format: Optional[Dict[str, Any]] = None,
                  temperature: Optional[float] = None) -> ChatTurn:
        data = self._request(messages, {"tools": tools, "tool_choice": tool_choice,
                                        "response_format": response_format, "temperature": temperature})
        self._turns += 1
        usage = self._set_usage(data) if isinstance(data, dict) else None
        try:
            choice = data["choices"][0]
            message = choice["message"]
            if not isinstance(message, dict):
                raise TypeError
        except (KeyError, IndexError, TypeError):
            raise LLMResponseError("OpenRouter reply had no message content", usage=usage) from None
        content = self._norm_content(message.get("content"), usage)
        finish = choice.get("finish_reason") or ""
        if finish == "length":
            raise LLMResponseError("reply truncated at max_tokens", usage=usage)
        raw_calls = message.get("tool_calls")
        if raw_calls is not None and not isinstance(raw_calls, list):
            raise LLMResponseError("malformed tool_calls in reply", usage=usage)
        calls = self._parse_tool_calls(raw_calls, self._turns)
        if finish == "tool_calls" and not calls:
            raise LLMResponseError("tool_calls finish with no usable calls", usage=usage)
        if not calls and not content.strip():
            raise LLMResponseError("empty reply", usage=usage)
        model = data.get("model")
        return ChatTurn(content, calls, str(finish), usage, model if isinstance(model, str) and model else self.model,
                        message)

    @staticmethod
    def _parse_tool_calls(raw: Any, turn: int) -> Tuple[ToolCall, ...]:
        if not isinstance(raw, list):
            return ()
        out: List[ToolCall] = []
        used = {e["id"] for e in raw if isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"].strip()}
        for i, entry in enumerate(raw):
            fn = entry.get("function") if isinstance(entry, dict) else None
            name = fn.get("name") if isinstance(fn, dict) else None
            if (not isinstance(entry, dict) or entry.get("type", "function") != "function"
                    or not isinstance(name, str) or not name):
                log.warning("dropping non-function tool call entry at index %d", i)
                continue
            cid = entry.get("id")
            if not isinstance(cid, str) or not cid.strip():
                cid = f"call_{turn}_{i}"
                n = 0
                while cid in used:
                    n += 1
                    cid = f"call_{turn}_{i}_{n}"
                used.add(cid)
            args = fn.get("arguments")
            if isinstance(args, dict):
                args_raw, parsed, err = json.dumps(args), args, None
            else:
                args_raw = args if isinstance(args, str) else ""
                parsed, err = None, None
                try:
                    if not isinstance(args, str):
                        raise ValueError("arguments must be a JSON string or object")
                    if not args_raw.strip():
                        raise ValueError("arguments are empty")

                    def _bad(c: str) -> Any:
                        raise ValueError(f"non-finite number {c}")
                    val = json.loads(args_raw, parse_constant=_bad)
                    if isinstance(val, dict):
                        parsed = val
                    else:
                        err = "arguments must be an object"
                except ValueError as exc:
                    err = f"invalid JSON: {exc}"
            out.append(ToolCall(cid, name, parsed, args_raw, err))
        return tuple(out)

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
