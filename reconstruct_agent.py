"""Reconstruct: the LLM agent call that drafts up to two Commander decks.

This module is only the agent-call layer. It does not build pools, check legality, or store
drafts. Those live in the pool module, the deterministic validator, and the Reconstruct
builder/orchestrator.

    call_agent(preferences, tier1, tier2, tier3, num_decks, validation_errors=None,
               previous_drafts=None, client=...) -> AgentResult

Prompt:
    system = prompts/reconstruct_builder_guide.md (oracle's rubric, used verbatim) plus a short
             app addendum: the tier-preference rule, the shared-quantity rule, and the output contract.
    user   = preferences (JSON) + the three pools as compact tables + the JSON schema to return.
    repair = the same, plus the previous drafts and the validator's errors, with instructions
             to fix only those errors.

Provider: the app's existing oracle client (analytics.oracle_client.OracleClient, an
OpenAI-compatible chat endpoint). No new provider. Any object with
`chat(messages, **extra) -> str` works, which is how the tests mock it.

Structured output: the request sends an OpenAI `response_format` json_schema built from the
pydantic models below. The prompt also asks for one fenced ```json block that matches the same
schema, so a server that ignores `response_format` still yields parseable output. The reply is
always parsed and schema-validated here and never trusted as-is.

Errors:
    AgentOutputError (retryable=True)  the reply is unusable. Subclasses:
        MalformedJSONError             no parseable JSON object in the reply
        SchemaMismatchError            JSON parsed but does not match the schema / deck count
    AgentInputError                    bad arguments from the caller (not retryable)
    analytics.oracle_client.OracleError propagates unchanged (offline/auth), like Theory/Review do.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

GUIDE_PATH = Path(__file__).resolve().parent / "prompts" / "reconstruct_builder_guide.md"
SCHEMA_NAME = "reconstruct_drafts"
MAX_DECKS = 2
DECK_SIZE = 100

TIER_LABELS = {1: "previous_deck", 2: "owned", 3: "new"}

TIER_PREFERENCE_INSTRUCTION = (
    "TIER PREFERENCE (mandatory): strongly prefer Tier 1 (cards from the retired source decks), "
    "then Tier 2 (owned cards not in any other active deck). Use Tier 3 (cards to buy) only when "
    "no Tier 1 or Tier 2 card is close enough for the slot, as defined in section 4. Every Tier 3 "
    "card's `why` must name the free card it beat."
)
SHARED_QUANTITY_INSTRUCTION = (
    "OWNED QUANTITIES (mandatory): each Tier 1 and Tier 2 card lists its owned quantity `qty`. "
    "Across ALL decks you return, the total copies of that card must not exceed `qty`. A card with "
    "qty 1 can go in only one deck. Settle contests with section 5 and report them in the explanation."
)
OUTPUT_INSTRUCTION = (
    "OUTPUT: reply with exactly one fenced ```json block that matches the JSON schema in the request, "
    "and nothing after it. `cards` is the full 100-card list INCLUDING the commander(s). Use exact card "
    "names from the pools. Tag each card with the tier of the pool it came from. Basic lands may repeat "
    "via `quantity`; they count as free (tier 2 unless they appear in Tier 1). Put the section 8 draft "
    "text (brief, structure, sourcing, buys, contested cards, flags) in `explanation`."
)


# ---------------------------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------------------------

class ReconstructAgentError(Exception):
    """Base class for every reconstruct_agent failure."""

    retryable = False


class AgentInputError(ReconstructAgentError, ValueError):
    """The caller passed bad arguments (e.g. num_decks out of range). Not retryable."""


class AgentOutputError(ReconstructAgentError):
    """The model's reply could not be used. Retryable: call the agent again."""

    retryable = True

    def __init__(self, message: str, *, raw: Optional[str] = None, details: Optional[list] = None):
        super().__init__(message)
        self.raw = raw
        self.details = details or []


class MalformedJSONError(AgentOutputError):
    """No parseable JSON object in the reply."""


class SchemaMismatchError(AgentOutputError):
    """The JSON parsed but does not match the drafts schema (or the requested deck count)."""


# ---------------------------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------------------------

class DraftCard(BaseModel):
    """One card line. Fields follow the guide's section 8 per-card rationale shape."""

    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1, le=99)
    tier: Literal[1, 2, 3]
    source_deck: Optional[str] = None
    roles: List[str] = Field(default_factory=list)
    pillar: Optional[str] = None
    cost_usd: Optional[float] = Field(default=None, ge=0)
    flags: List[str] = Field(default_factory=list)
    why: str = Field(min_length=1, description="Rationale naming the pillar or role this card serves.")

    @field_validator("name", "why")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class DraftDeck(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = Field(min_length=1)
    commanders: List[str] = Field(min_length=1, max_length=2)
    cards: List[DraftCard] = Field(min_length=1, description="All 100 cards, commander(s) included.")
    explanation: str = Field(min_length=1, description="Overall explanation (section 8 draft text).")
    open_questions: List[str] = Field(default_factory=list)

    @field_validator("commanders")
    @classmethod
    def _commanders(cls, v: List[str]) -> List[str]:
        out = [c.strip() for c in v if isinstance(c, str) and c.strip()]
        if len(out) != len(v):
            raise ValueError("commander names must be non-blank strings")
        return out

    @property
    def card_count(self) -> int:
        return sum(c.quantity for c in self.cards)


class DraftsResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    decks: List[DraftDeck] = Field(min_length=1, max_length=MAX_DECKS)


@lru_cache(maxsize=1)
def drafts_json_schema() -> Dict[str, Any]:
    """JSON Schema for the reply (sent as response_format and embedded in the prompt)."""
    return DraftsResponse.model_json_schema()


def response_format() -> Dict[str, Any]:
    """OpenAI-compatible structured-output request field."""
    return {"type": "json_schema", "json_schema": {"name": SCHEMA_NAME, "schema": drafts_json_schema()}}


# ---------------------------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------------------------

@dataclass
class AgentResult:
    decks: List[DraftDeck]
    raw: str
    repair: bool = False
    messages: List[Dict[str, str]] = field(default_factory=list)

    def as_dicts(self) -> List[dict]:
        return [d.model_dump() for d in self.decks]


# ---------------------------------------------------------------------------------------------
# Prompt composition
# ---------------------------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _read_guide(path: str) -> str:
    return Path(path).read_text(encoding="utf-8").strip()


def load_guide(path: Optional[Path] = None) -> str:
    return _read_guide(str(path or GUIDE_PATH))


def build_system_prompt(guide: Optional[str] = None) -> str:
    guide = load_guide() if guide is None else guide
    return "\n\n".join([
        guide,
        "---",
        "## Commander Lab instructions for this request",
        TIER_PREFERENCE_INSTRUCTION,
        SHARED_QUANTITY_INSTRUCTION,
        OUTPUT_INSTRUCTION,
    ])


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """Read the first present field from a mapping or an object (pool dataclasses or dicts)."""
    for n in names:
        if isinstance(obj, Mapping):
            if obj.get(n) is not None:
                return obj[n]
        elif getattr(obj, n, None) is not None:
            return getattr(obj, n)
    return default


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (list, tuple, set)):
        v = "".join(str(x) for x in v) if all(len(str(x)) == 1 for x in v) else ",".join(str(x) for x in v)
    if isinstance(v, float):
        v = f"{v:g}"
    return str(v).replace("|", "/").replace("\n", " ").strip()


def _entries(pool: Any) -> List[Any]:
    if pool is None:
        return []
    if isinstance(pool, Mapping):
        # {name: qty} or {name: {...}}
        out = []
        for name, v in pool.items():
            if isinstance(v, Mapping):
                out.append({"name": name, **v})
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append({"name": name, "quantity": v})
            else:
                out.append({"name": name})
        return out
    return list(pool)


def _price(e: Any) -> Optional[float]:
    p = _get(e, "price", "price_usd", "market_price", "cost_usd", "usd")
    try:
        return None if p is None else round(float(p), 2)
    except (TypeError, ValueError):
        return None


def format_pool(tier: int, pool: Any) -> str:
    """Compact pipe table. T1/T2 carry owned qty; T3 carries price. Optional columns appear
    only when some entry has them, to keep token use low."""
    rows = _entries(pool)
    if not rows:
        return f"### Tier {tier}: (empty)"
    opt = [("ci", ("color_identity", "ci")), ("mv", ("mv", "cmc", "mana_value")),
           ("type", ("type_line", "type"))]
    if tier == 1:
        opt.insert(0, ("deck", ("source_deck", "source_decks", "deck")))
    present = [(label, keys) for label, keys in opt if any(_get(e, *keys) is not None for e in rows)]
    lead = "qty" if tier in (1, 2) else "usd"
    header = ["name", lead] + [label for label, _ in present]
    lines = [f"### Tier {tier} ({TIER_LABELS[tier]}): {len(rows)} cards", "|".join(header)]
    for e in rows:
        name = _cell(_get(e, "name", "card_name"))
        if not name:
            continue
        if tier in (1, 2):
            lead_v = _cell(_get(e, "available", "quantity", "qty", "owned_quantity", default=1))
        else:
            p = _price(e)
            lead_v = "?" if p is None else f"{p:.2f}"
        lines.append("|".join([name, lead_v] + [_cell(_get(e, *keys)) for _, keys in present]))
    return "\n".join(lines)


def _jsonable(v: Any) -> Any:
    if isinstance(v, BaseModel):
        return v.model_dump()
    if hasattr(v, "__dataclass_fields__"):
        from dataclasses import asdict

        return asdict(v)
    if hasattr(v, "to_dict") and callable(v.to_dict):
        return v.to_dict()
    return str(v)


def _dumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, indent=1, default=_jsonable)


def format_errors(errors: Iterable[Any]) -> str:
    return _dumps([e if isinstance(e, (str, Mapping)) else _jsonable(e) for e in errors])


def build_user_prompt(preferences: Sequence[Mapping], tier1: Any, tier2: Any, tier3: Any, num_decks: int, *,
                      validation_errors: Optional[Sequence[Any]] = None,
                      previous_drafts: Optional[Sequence[Any]] = None,
                      context: Optional[Mapping[str, Any]] = None) -> str:
    parts = [
        f"Reconstruct request: build exactly {num_decks} Commander deck{'s' if num_decks > 1 else ''} "
        f"({DECK_SIZE} cards each, commander(s) included).",
        "## Preferences (one object per target deck, in order)",
        "```json\n" + _dumps(list(preferences)) + "\n```",
    ]
    if context:
        parts += ["## Context", "```json\n" + _dumps(dict(context)) + "\n```"]
    parts += [
        "## Card pools",
        "Columns are pipe-separated. qty = owned copies available across all decks. "
        "usd = market price ('?' = unknown, avoid unless essential). ci = color identity.",
        format_pool(1, tier1),
        format_pool(2, tier2),
        format_pool(3, tier3),
        "Cards not listed in any pool are not available. Do not invent cards.",
        TIER_PREFERENCE_INSTRUCTION,
        SHARED_QUANTITY_INSTRUCTION,
    ]
    if validation_errors:
        parts += [
            "## REPAIR MODE",
            "Your previous drafts failed deterministic validation. Fix ONLY the issues listed below. "
            "Keep every other card, commander, and the rationale unchanged unless a fix forces a change. "
            "When you swap a card, follow the same tier preference and give a new `why`. "
            "Return the complete corrected drafts (all decks, all 100 cards each), not a diff.",
            "### Validator errors",
            "```json\n" + format_errors(validation_errors) + "\n```",
        ]
        if previous_drafts:
            parts += ["### Previous drafts", "```json\n" + _dumps(list(previous_drafts)) + "\n```"]
    parts += [
        "## Output JSON schema",
        "```json\n" + json.dumps(drafts_json_schema(), separators=(",", ":")) + "\n```",
        OUTPUT_INSTRUCTION,
    ]
    return "\n\n".join(parts)


def build_messages(preferences: Sequence[Mapping], tier1: Any, tier2: Any, tier3: Any, num_decks: int, *,
                   validation_errors: Optional[Sequence[Any]] = None,
                   previous_drafts: Optional[Sequence[Any]] = None,
                   context: Optional[Mapping[str, Any]] = None,
                   guide: Optional[str] = None) -> List[Dict[str, str]]:
    return [
        {"role": "system", "content": build_system_prompt(guide)},
        {"role": "user", "content": build_user_prompt(
            preferences, tier1, tier2, tier3, num_decks, validation_errors=validation_errors,
            previous_drafts=previous_drafts, context=context)},
    ]


# ---------------------------------------------------------------------------------------------
# Reply parsing
# ---------------------------------------------------------------------------------------------

_FENCE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.S)


def parse_json_reply(text: Any) -> dict:
    """The last fenced json block that parses to an object, else the whole reply as bare JSON."""
    if not isinstance(text, str) or not text.strip():
        raise MalformedJSONError("agent returned an empty reply", raw=text if isinstance(text, str) else None)
    candidates = list(reversed(_FENCE.findall(text))) + [text.strip()]
    for c in candidates:
        try:
            v = json.loads(c.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(v, dict):
            return v
        if isinstance(v, list):  # tolerate a bare list of decks
            return {"decks": v}
    raise MalformedJSONError("agent reply has no parseable JSON object", raw=text)


def validate_reply(data: Mapping, num_decks: int, *, raw: Optional[str] = None) -> List[DraftDeck]:
    try:
        parsed = DraftsResponse.model_validate(data)
    except ValidationError as exc:
        details = [{"loc": ".".join(str(p) for p in e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
        head = "; ".join(f"{d['loc']}: {d['msg']}" for d in details[:5])
        raise SchemaMismatchError(f"agent reply failed schema validation: {head}", raw=raw, details=details) from exc
    if len(parsed.decks) != num_decks:
        raise SchemaMismatchError(f"agent returned {len(parsed.decks)} deck(s), expected {num_decks}", raw=raw,
                                  details=[{"loc": "decks", "msg": f"expected {num_decks} decks"}])
    return parsed.decks


def parse_reply(text: Any, num_decks: int) -> List[DraftDeck]:
    return validate_reply(parse_json_reply(text), num_decks, raw=text if isinstance(text, str) else None)


# ---------------------------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------------------------

def default_client():
    from analytics.oracle_client import OracleClient

    return OracleClient.from_env()


def call_agent(preferences: Sequence[Mapping], tier1: Any, tier2: Any, tier3: Any, num_decks: int,
               validation_errors: Optional[Sequence[Any]] = None, *,
               previous_drafts: Optional[Sequence[Any]] = None,
               context: Optional[Mapping[str, Any]] = None,
               client: Any = None,
               client_factory: Optional[Callable[[], Any]] = None,
               structured: bool = True,
               guide: Optional[str] = None) -> AgentResult:
    """Ask the agent for `num_decks` drafts and return schema-validated decks.

    preferences       one normalized preference dict per target deck (reconstruct.normalize_preference)
    tier1/tier2/tier3 pools: iterables of dicts/objects with `name` plus qty (T1/T2) or price (T3),
                      or {name: qty} mappings. Optional ci/mv/type/source_deck columns are passed on.
    validation_errors prior validator errors. Non-empty => repair mode (pass previous_drafts too).
    structured        send OpenAI `response_format` json_schema (the prompt asks for JSON regardless).

    Raises AgentOutputError subclasses on unusable output (retryable), AgentInputError on bad
    arguments, and lets analytics.oracle_client.OracleError through.
    """
    if not isinstance(num_decks, int) or isinstance(num_decks, bool) or not 1 <= num_decks <= MAX_DECKS:
        raise AgentInputError(f"num_decks must be 1 or 2, got {num_decks!r}")
    preferences = list(preferences or [])
    if len(preferences) > num_decks:
        raise AgentInputError(f"{len(preferences)} preference sets for {num_decks} deck(s)")

    messages = build_messages(preferences, tier1, tier2, tier3, num_decks, validation_errors=validation_errors,
                              previous_drafts=previous_drafts, context=context, guide=guide)
    if client is None:
        client = (client_factory or default_client)()
    extra = {"response_format": response_format()} if structured else {}
    reply = client.chat(messages, **extra)
    decks = parse_reply(reply, num_decks)
    return AgentResult(decks=decks, raw=reply, repair=bool(validation_errors), messages=messages)
