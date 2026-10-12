"""
LLM pass for produce/consume tagging.

Provider interface + fallback chain:
    gemini   (primary, GOOGLE_API_KEY / GEMINI_API_KEY)
    nous     (fallback, NOUS_API_KEY; OpenAI-compatible Nous inference API)
    anthropic (empty slot: present in the chain, never configured, raises)

The LLM only LABELS cards with keys from the fixed vocabulary (vocab.py).
Anything outside the vocabulary is dropped. Every call's tokens and an
estimated USD cost land in the run log (service.RunLog).
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from .vocab import RESOURCES, normalize_resources


class LLMProviderError(Exception):
    pass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


# USD per 1M tokens (input, output). Estimates for the run log, not billing.
# Gemini: ai.google.dev/gemini-api/docs/pricing, paid tier, checked 2026-10-12
# (3.8 Flash is at its introductory rate until 2026-12-31: $1.50/$7.50 after).
# Gemini 2.5 models are no longer served to new API keys (404).
# Nous: Nous Portal list price for Hermes-4-70B.
DEFAULT_GEMINI_MODEL = "gemini-3.8-flash"
PRICING: Dict[str, Tuple[float, float]] = {
    "gemini-3.8-flash": (0.75, 3.75),
    "gemini-3.5-flash-lite": (0.30, 2.50),
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "gemini-2.5-flash": (0.30, 2.50),
    "Hermes-4-70B": (0.13, 0.40),
    "Hermes-4-405B": (1.00, 3.00),
}


def _thinking_config(model: str) -> dict:
    # Gemini 2.x takes a token budget; Gemini 3+ takes a thinking level.
    if model.startswith("gemini-2"):
        return {"thinkingBudget": 0}
    return {"thinkingLevel": "low"}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = PRICING.get(model, (0.0, 0.0))
    return round((input_tokens * pin + output_tokens * pout) / 1_000_000, 8)


def _vocab_block() -> str:
    lines = []
    for r in RESOURCES:
        lines.append(f'- "{r.key}": PRODUCES = {r.produce}; CONSUMES = {r.consume}')
    return "\n".join(lines)


SYSTEM_PROMPT = (
    "You tag Magic: The Gathering cards for a Commander deck interaction graph.\n"
    "For each card, list which RESOURCES it PRODUCES and which it CONSUMES, using ONLY these keys:\n"
    + _vocab_block()
    + "\n\nRules:\n"
    "- Tag what the card meaningfully does in a Commander game, from its oracle text.\n"
    "- A card may produce and consume nothing (e.g. a plain removal spell or a vanilla creature): return empty lists.\n"
    "- Do not tag a resource just because the card is a creature or a permanent.\n"
    "- Return JSON only: {\"cards\": [{\"name\": <exact name given>, \"produces\": [...], \"consumes\": [...]}]}"
)


def _card_prompt(cards: List[dict]) -> str:
    parts = []
    for c in cards:
        parts.append(
            f"### {c['name']}\nType: {c.get('type_line', '')}\nCost: {c.get('mana_cost', '')}\n"
            f"Text: {c.get('oracle_text', '')}"
        )
    return "Tag these cards:\n\n" + "\n\n".join(parts)


def _parse_cards_json(text: str, cards: List[dict]) -> Dict[str, dict]:
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMProviderError(f"unparseable LLM response: {exc}") from exc
    items = data.get("cards") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise LLMProviderError("LLM response has no 'cards' list")
    wanted = {c["name"] for c in cards}
    lower = {n.lower(): n for n in wanted}
    out: Dict[str, dict] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name") or ""
        name = name if name in wanted else lower.get(name.lower())
        if not name:
            continue
        out[name] = {
            "produces": normalize_resources(item.get("produces") or []),
            "consumes": normalize_resources(item.get("consumes") or []),
        }
    missing = wanted - set(out)
    if missing and len(missing) == len(wanted):
        raise LLMProviderError("LLM response matched none of the requested cards")
    return out


def _default_post(url, json=None, headers=None, timeout=None):
    import requests

    return requests.post(url, json=json, headers=headers, timeout=timeout)


class LLMTagProvider:
    """Interface: tag a batch of cards -> ({name: {produces, consumes}}, Usage)."""

    model: str = ""

    @property
    def name(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError

    def is_configured(self) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def tag_batch(self, cards: List[dict]) -> Tuple[Dict[str, dict], Usage]:  # pragma: no cover
        raise NotImplementedError


class GeminiProvider(LLMTagProvider):
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: Optional[str], model: str = DEFAULT_GEMINI_MODEL,
                 http_post: Optional[Callable] = None, timeout: float = 90.0):
        self.api_key = api_key
        self.model = model
        self._post = http_post or _default_post
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "gemini"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def tag_batch(self, cards):
        if not self.api_key:
            raise LLMProviderError("gemini: no API key")
        keys = [r.key for r in RESOURCES]
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": _card_prompt(cards)}]}],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "OBJECT",
                    "properties": {"cards": {"type": "ARRAY", "items": {
                        "type": "OBJECT",
                        "properties": {
                            "name": {"type": "STRING"},
                            "produces": {"type": "ARRAY", "items": {"type": "STRING", "enum": keys}},
                            "consumes": {"type": "ARRAY", "items": {"type": "STRING", "enum": keys}},
                        },
                        "required": ["name", "produces", "consumes"],
                    }}},
                    "required": ["cards"],
                },
                "thinkingConfig": _thinking_config(self.model),
            },
        }
        url = f"{self.BASE}/{self.model}:generateContent"
        try:
            resp = self._post(url, json=payload, headers={"x-goog-api-key": self.api_key}, timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
        except LLMProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise LLMProviderError(f"gemini request failed: {_scrub(str(exc), self.api_key)}") from exc
        try:
            text = "".join(p.get("text", "") for p in body["candidates"][0]["content"]["parts"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMProviderError(f"gemini: unexpected response shape: {str(body)[:200]}") from exc
        meta = body.get("usageMetadata") or {}
        usage = Usage(
            input_tokens=int(meta.get("promptTokenCount") or 0),
            output_tokens=int(meta.get("candidatesTokenCount") or 0) + int(meta.get("thoughtsTokenCount") or 0),
        )
        return _parse_cards_json(text, cards), usage


class NousProvider(LLMTagProvider):
    """Nous Portal inference API (OpenAI-compatible chat completions)."""

    def __init__(self, api_key: Optional[str], model: str = "Hermes-4-70B",
                 base_url: str = "https://inference-api.nousresearch.com/v1",
                 http_post: Optional[Callable] = None, timeout: float = 120.0):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self._post = http_post or _default_post
        self.timeout = timeout

    @property
    def name(self) -> str:
        return "nous"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def tag_batch(self, cards):
        if not self.api_key:
            raise LLMProviderError("nous: no API key")
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _card_prompt(cards)},
            ],
        }
        try:
            resp = self._post(f"{self.base_url}/chat/completions", json=payload,
                              headers={"Authorization": f"Bearer {self.api_key}"}, timeout=self.timeout)
            resp.raise_for_status()
            body = resp.json()
            text = body["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001
            raise LLMProviderError(f"nous request failed: {_scrub(str(exc), self.api_key)}") from exc
        u = body.get("usage") or {}
        usage = Usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0))
        return _parse_cards_json(text, cards), usage


class AnthropicProvider(LLMTagProvider):
    """Empty slot for a future Anthropic API provider. Never configured by
    default_chain; calling it raises so the chain falls through."""

    def __init__(self, api_key: Optional[str] = None, model: str = ""):
        self.api_key = api_key
        self.model = model

    @property
    def name(self) -> str:
        return "anthropic"

    def is_configured(self) -> bool:
        return False

    def tag_batch(self, cards):
        raise LLMProviderError("anthropic provider slot is not implemented yet")


def _scrub(msg: str, secret: Optional[str]) -> str:
    return msg.replace(secret, "***") if secret else msg


class LLMChain:
    """Ordered providers; tag_batch tries each configured one until one works.
    `on_call` receives one dict per attempt (provider, model, ok, tokens, cost, error)."""

    def __init__(self, providers: List[LLMTagProvider]):
        self.providers = list(providers)

    def available(self) -> List[LLMTagProvider]:
        return [p for p in self.providers if p.is_configured()]

    def tag_batch(self, cards: List[dict], on_call: Optional[Callable[[dict], None]] = None):
        """Returns (results, provider_name) or (None, None) if every provider failed."""
        for p in self.available():
            try:
                results, usage = p.tag_batch(cards)
            except LLMProviderError as exc:
                if on_call:
                    on_call({"provider": p.name, "model": p.model, "ok": False, "cards": len(cards),
                             "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "error": str(exc)[:300]})
                continue
            if on_call:
                on_call({"provider": p.name, "model": p.model, "ok": True, "cards": len(cards),
                         "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
                         "cost_usd": estimate_cost(p.model, usage.input_tokens, usage.output_tokens)})
            return results, p.name
        return None, None


SECRETS_DIR_DEFAULT = "/run/commander-lab-secrets"
SECRET_NAMES = ("GOOGLE_API_KEY", "GEMINI_API_KEY", "NOUS_API_KEY", "ANTHROPIC_API_KEY")


def resolve_env(env: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """os.environ, plus API keys read from files in INTERACTIONS_SECRETS_DIR
    (one file per key, named like the variable). Prod mounts a root-only
    host dir there (see docs/GITOPS.md); real env vars win over files."""
    base = dict(os.environ if env is None else env)
    secrets_dir = base.get("INTERACTIONS_SECRETS_DIR") or SECRETS_DIR_DEFAULT
    for name in SECRET_NAMES:
        if base.get(name):
            continue
        path = os.path.join(secrets_dir, name)
        try:
            with open(path, encoding="utf-8") as fh:
                value = fh.read().strip().strip('"').strip("'")
        except OSError:
            continue
        if value:
            base[name] = value
    return base


def default_chain(env: Optional[Mapping[str, str]] = None) -> LLMChain:
    env = resolve_env(env)
    return LLMChain([
        GeminiProvider(env.get("GOOGLE_API_KEY") or env.get("GEMINI_API_KEY"),
                       model=env.get("INTERACTIONS_GEMINI_MODEL") or DEFAULT_GEMINI_MODEL),
        NousProvider(env.get("NOUS_API_KEY"),
                     model=env.get("INTERACTIONS_NOUS_MODEL") or "Hermes-4-70B",
                     base_url=env.get("NOUS_BASE_URL") or "https://inference-api.nousresearch.com/v1"),
        AnthropicProvider(),
    ])
