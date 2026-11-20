"""LLM request/response types and the injected client protocol.

Nothing here performs I/O. ``src/server/infra/llm/provider.py`` implements ``LLMClient`` over HTTP;
tests inject a scripted or replaying client, so CI never makes a live LLM call.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable


class LLMTransportError(RuntimeError):
    """The model could not be reached or answered with an HTTP/API error (not a bad-JSON problem)."""


@dataclass(frozen=True)
class Message:
    role: str  # "user" | "assistant"
    content: str


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    cached_tokens: int = 0        # prompt tokens served from the provider's prompt cache
    cache_write_tokens: int = 0   # prompt tokens written to the cache by this call
    cost_usd: float | None = None  # as reported by the provider; None = not reported (never guessed)

    def to_dict(self) -> dict[str, Any]:
        return {"tokens_in": self.tokens_in, "tokens_out": self.tokens_out, "cached_tokens": self.cached_tokens,
                "cache_write_tokens": self.cache_write_tokens, "cost_usd": self.cost_usd}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Usage":
        return cls(int(d.get("tokens_in") or 0), int(d.get("tokens_out") or 0), int(d.get("cached_tokens") or 0),
                   int(d.get("cache_write_tokens") or 0), d.get("cost_usd"))


@dataclass(frozen=True)
class LLMRequest:
    """One structured-output call.

    ``system_blocks`` hold the static context (quality standard, profile, brief, card table, axis
    facts). ``cache_blocks`` is how many *leading* system blocks form the cacheable prefix: the
    transport marks the end of block ``cache_blocks`` for provider prompt caching, so those blocks
    must be byte-identical across the calls that should share the cache (0 = no caching).
    Per-call content goes in ``messages``. ``label`` ("A", "B:mana") names the call in traces and
    replay fixtures and is deliberately NOT part of ``input_hash``.
    """
    model: str
    system_blocks: tuple[str, ...]
    messages: tuple[Message, ...]
    schema_name: str
    schema: Mapping[str, Any]
    max_tokens: int = 8000
    cache_blocks: int = 0
    temperature: float | None = None
    label: str = ""

    @property
    def input_hash(self) -> str:
        blob = json.dumps({"model": self.model, "system": list(self.system_blocks),
                           "messages": [[m.role, m.content] for m in self.messages],
                           "schema_name": self.schema_name, "schema": self.schema, "max_tokens": self.max_tokens,
                           "cache_blocks": self.cache_blocks, "temperature": self.temperature},
                          sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def with_messages(self, messages: tuple[Message, ...]) -> "LLMRequest":
        return LLMRequest(self.model, self.system_blocks, messages, self.schema_name, self.schema,
                          self.max_tokens, self.cache_blocks, self.temperature, self.label)


@dataclass(frozen=True)
class LLMResponse:
    text: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""          # the model the provider says answered (may be more specific than requested)
    latency_ms: int = 0
    provider_request_id: str | None = None


@runtime_checkable
class LLMClient(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse:
        """Return the model's raw text. Raise ``LLMTransportError`` on transport/API failure."""
