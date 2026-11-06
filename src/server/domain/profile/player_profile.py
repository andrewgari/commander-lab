"""Player taste profile (deck reviewer card 04).

Pure domain: dataclasses, validator, defaults resolver, provenance rules,
deck-intent precedence, feedback summarizer and the prompt renderer. Persistence
lives in ``infra/services/player_profile_service.py`` (Postgres, D2).
Conventions follow ``domain/intake/user_intent.py``.

Rules (docs/reviewer/CONTRACT.md §7):
- every populated value has provenance ``stated`` | ``learned_from_feedback``;
- a learned value never overwrites a stated one;
- learned values are only *proposed* by the summarizer and join the profile
  after the owner confirms them;
- deck-level intent beats the profile on any conflict.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.server.domain.intake.user_intent import (
    ALLOWED_CURRENCIES,
    ALLOWED_POWER_TIERS,
    UserDeckIntent,
)

SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "player_profile.schema.json"

STATED = "stated"
LEARNED = "learned_from_feedback"
SOURCES: Set[str] = {STATED, LEARNED}
ALLOWED_DEPTHS: Set[str] = {"brief", "standard", "deep"}
ALLOWED_TONES: Set[str] = {"direct", "neutral", "encouraging"}
DECISIONS: Set[str] = {"accepted", "rejected", "maybe"}
DEFAULT_CURRENCY = "USD"
# currency has a structural default, so it only needs provenance when non-default.
CURRENCY_KEY = "budget_norms.currency"
DEFAULT_INTENT_TIER = "optimized"


class ProfileNotFound(LookupError):
    pass


class ProvenanceConflict(ValueError):
    """A learned value tried to overwrite a stated one."""


def load_schema() -> Dict[str, Any]:
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Dataclasses
# --------------------------------------------------------------------------

@dataclass
class Provenance:
    source: str
    at: str
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"source": self.source, "at": self.at}
        if self.evidence:
            d["evidence"] = list(self.evidence)
        return d


@dataclass
class BuilderIdentity:
    text: str = ""
    tags: List[str] = field(default_factory=list)


@dataclass
class Dislikes:
    cards: List[str] = field(default_factory=list)
    strategies: List[str] = field(default_factory=list)
    play_patterns: List[str] = field(default_factory=list)


@dataclass
class PowerTendencies:
    tier: Optional[str] = None
    notes: str = ""


@dataclass
class BudgetNorms:
    hard_cap: Optional[float] = None
    soft_cap: Optional[float] = None
    currency: str = DEFAULT_CURRENCY


@dataclass
class ReviewPreferences:
    depth: Optional[str] = None
    tone: Optional[str] = None
    skip: List[str] = field(default_factory=list)
    welcome_generic_staples: Optional[bool] = None


@dataclass
class FeedbackEntry:
    deck_id: str
    review_id: str
    cut: str
    add: str
    decision: str
    reason: str = ""
    at: str = ""


@dataclass
class PlayerProfile:
    name: str = ""
    builder_identity: BuilderIdentity = field(default_factory=BuilderIdentity)
    color_affinities: Dict[str, float] = field(default_factory=dict)
    archetype_affinities: Dict[str, float] = field(default_factory=dict)
    favored_mechanics: List[str] = field(default_factory=list)
    pet_cards: List[str] = field(default_factory=list)
    dislikes: Dislikes = field(default_factory=Dislikes)
    power_tendencies: PowerTendencies = field(default_factory=PowerTendencies)
    budget_norms: BudgetNorms = field(default_factory=BudgetNorms)
    review_preferences: ReviewPreferences = field(default_factory=ReviewPreferences)
    done_when: List[str] = field(default_factory=list)
    provenance: Dict[str, Provenance] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    # Loaded alongside the profile for the verifier; not part of the schema.
    feedback: List[FeedbackEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Schema-shaped dict (values + provenance). Excludes ``feedback``."""
        d = _dc_to_dict(self)
        d.pop("feedback", None)
        d["provenance"] = {k: p.to_dict() for k, p in self.provenance.items()}
        return d


def _dc_to_dict(obj: Any) -> Any:
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _dc_to_dict(getattr(obj, k)) for k in obj.__dataclass_fields__
                if k != "provenance"}
    if isinstance(obj, dict):
        return {k: _dc_to_dict(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_dc_to_dict(v) for v in obj]
    return obj


# --------------------------------------------------------------------------
# Leaf keys (the unit provenance attaches to)
# --------------------------------------------------------------------------

_VALUE_FIELDS = (
    "builder_identity", "color_affinities", "archetype_affinities",
    "favored_mechanics", "pet_cards", "dislikes", "power_tendencies",
    "budget_norms", "review_preferences", "done_when",
)


def leaf_keys(data: Dict[str, Any]) -> List[str]:
    """Provenance keys for every populated value in a (resolved) profile dict.

    scalar -> ``budget_norms.hard_cap``; list item -> ``pet_cards/Elspeth``;
    weight -> ``color_affinities.Bant``. Unset (None / "") produces no key;
    the default currency produces no key.
    """
    out: List[str] = []

    def walk(prefix: str, value: Any) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}.{k}" if prefix else k, v)
        elif isinstance(value, list):
            for item in value:
                out.append(f"{prefix}/{item}")
        elif value is None or value == "":
            return
        else:
            out.append(prefix)

    for f in _VALUE_FIELDS:
        if f in data:
            walk(f, data[f])
    if data.get("budget_norms", {}).get("currency") == DEFAULT_CURRENCY and CURRENCY_KEY in out:
        out.remove(CURRENCY_KEY)
    return out


# --------------------------------------------------------------------------
# Defaults resolver / parser
# --------------------------------------------------------------------------

def resolve_profile_defaults(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Backfill every missing field; unknown top-level keys go to metadata."""
    p = payload or {}
    bi = p.get("builder_identity") or {}
    dl = p.get("dislikes") or {}
    pt = p.get("power_tendencies") or {}
    bn = p.get("budget_norms") or {}
    rp = p.get("review_preferences") or {}
    known = set(_VALUE_FIELDS) | {"name", "provenance", "metadata"}
    metadata = dict(p.get("metadata") or {})
    for k, v in p.items():
        if k not in known:
            metadata[k] = v
    return {
        "name": p.get("name", ""),
        "builder_identity": {"text": bi.get("text", ""), "tags": list(bi.get("tags") or [])},
        "color_affinities": dict(p.get("color_affinities") or {}),
        "archetype_affinities": dict(p.get("archetype_affinities") or {}),
        "favored_mechanics": list(p.get("favored_mechanics") or []),
        "pet_cards": list(p.get("pet_cards") or []),
        "dislikes": {
            "cards": list(dl.get("cards") or []),
            "strategies": list(dl.get("strategies") or []),
            "play_patterns": list(dl.get("play_patterns") or []),
        },
        "power_tendencies": {"tier": pt.get("tier"), "notes": pt.get("notes", "")},
        "budget_norms": {
            "hard_cap": bn.get("hard_cap"),
            "soft_cap": bn.get("soft_cap"),
            "currency": bn.get("currency") or DEFAULT_CURRENCY,
        },
        "review_preferences": {
            "depth": rp.get("depth"),
            "tone": rp.get("tone"),
            "skip": list(rp.get("skip") or []),
            "welcome_generic_staples": rp.get("welcome_generic_staples"),
        },
        "done_when": list(p.get("done_when") or []),
        "provenance": dict(p.get("provenance") or {}),
        "metadata": metadata,
    }


def _norm(s: str) -> str:
    return " ".join(str(s).casefold().split())


def validate_profile(payload: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Return (is_valid, errors): JSON-schema structure, business rules, and
    provenance coverage (every populated value has it; no orphan entries)."""
    if not isinstance(payload, dict):
        return False, ["Payload must be a JSON object (dict)."]
    errors: List[str] = []
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(load_schema())
        for e in sorted(validator.iter_errors(payload), key=lambda e: list(map(str, e.absolute_path))):
            loc = ".".join(str(x) for x in e.absolute_path) or "(root)"
            errors.append(f"{loc}: {e.message}")
    except ImportError:  # pragma: no cover - jsonschema is in requirements.txt
        errors.append("jsonschema is not installed; cannot validate profile.")
    if errors:
        return False, errors

    r = resolve_profile_defaults(payload)
    bn = r["budget_norms"]
    hard, soft = bn["hard_cap"], bn["soft_cap"]
    if hard is not None and soft is not None and soft > hard:
        errors.append(f"budget_norms.soft_cap ({soft}) cannot exceed hard_cap ({hard}).")
    if bn["currency"] not in ALLOWED_CURRENCIES:
        errors.append(f"budget_norms.currency '{bn['currency']}' is invalid.")
    tier = r["power_tendencies"]["tier"]
    if tier is not None and tier not in ALLOWED_POWER_TIERS:
        errors.append(f"power_tendencies.tier '{tier}' is invalid.")
    pets = {_norm(c) for c in r["pet_cards"]}
    for c in r["dislikes"]["cards"]:
        if _norm(c) in pets:
            errors.append(f"Card conflict: '{c}' cannot be both a pet card and disliked.")

    prov = r["provenance"]
    needed = set(leaf_keys(r))
    for key in sorted(needed - set(prov)):
        errors.append(f"provenance missing for '{key}'.")
    for key in sorted(set(prov) - needed - {CURRENCY_KEY}):
        errors.append(f"provenance entry '{key}' has no matching value.")
    return (not errors), errors


def parse_profile(payload: Optional[Dict[str, Any]] = None) -> PlayerProfile:
    """Validate, resolve defaults, return a PlayerProfile. ValueError on invalid."""
    payload = payload or {}
    ok, errors = validate_profile(payload)
    if not ok:
        raise ValueError(f"Invalid player profile: {'; '.join(errors)}")
    r = resolve_profile_defaults(payload)
    return PlayerProfile(
        name=r["name"],
        builder_identity=BuilderIdentity(**r["builder_identity"]),
        color_affinities={k: float(v) for k, v in r["color_affinities"].items()},
        archetype_affinities={k: float(v) for k, v in r["archetype_affinities"].items()},
        favored_mechanics=r["favored_mechanics"],
        pet_cards=r["pet_cards"],
        dislikes=Dislikes(**r["dislikes"]),
        power_tendencies=PowerTendencies(**r["power_tendencies"]),
        budget_norms=BudgetNorms(
            hard_cap=None if r["budget_norms"]["hard_cap"] is None else float(r["budget_norms"]["hard_cap"]),
            soft_cap=None if r["budget_norms"]["soft_cap"] is None else float(r["budget_norms"]["soft_cap"]),
            currency=r["budget_norms"]["currency"],
        ),
        review_preferences=ReviewPreferences(**r["review_preferences"]),
        done_when=r["done_when"],
        provenance={k: Provenance(v["source"], v["at"], list(v.get("evidence") or []))
                    for k, v in r["provenance"].items()},
        metadata=r["metadata"],
    )


def stamp_provenance(values: Dict[str, Any], source: str, at: str) -> Dict[str, Any]:
    """Return ``values`` with a provenance entry for every populated leaf."""
    out = copy.deepcopy(values)
    r = resolve_profile_defaults(out)
    out["provenance"] = {k: {"source": source, "at": at} for k in leaf_keys(r)}
    return out


def profile_from_seed(seed: Dict[str, Any], at: Optional[str] = None) -> PlayerProfile:
    """Seed files hold plain values; every value is stamped ``stated``."""
    values = {k: v for k, v in seed.items() if k != "provenance"}
    return parse_profile(stamp_provenance(values, STATED, at or utc_now()))


# --------------------------------------------------------------------------
# Stated edits and learned (confirmed) proposals
# --------------------------------------------------------------------------

def apply_stated_edit(current: PlayerProfile, new_values: Dict[str, Any],
                      now: Optional[str] = None) -> PlayerProfile:
    """The owner edits values. Unchanged leaves keep provenance; new or changed
    leaves become ``stated``; removed leaves lose their provenance. A stated
    edit may replace anything, including learned values."""
    now = now or utc_now()
    new = resolve_profile_defaults({k: v for k, v in new_values.items() if k != "provenance"})
    new["name"] = new["name"] or current.name
    old_prov = current.provenance
    prov: Dict[str, Dict[str, Any]] = {}
    old_vals = _leaf_values(resolve_profile_defaults(current.to_dict()))
    new_vals = _leaf_values(new)
    for key in leaf_keys(new):
        if key in old_prov and old_vals.get(key) == new_vals.get(key):
            prov[key] = old_prov[key].to_dict()
        else:
            prov[key] = {"source": STATED, "at": now}
    new["provenance"] = prov
    return parse_profile(new)


def _leaf_values(data: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key in leaf_keys(data):
        if "/" in key:
            out[key] = True
        else:
            node: Any = data
            for part in key.split("."):
                node = node[part]
            out[key] = node
    return out


@dataclass
class Proposal:
    """A learned preference awaiting confirmation. ``op`` is ``add`` (list
    field, value is an item) or ``set`` (scalar leaf or weight key)."""
    path: str
    op: str
    value: Any
    rationale: str = ""
    evidence: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"path": self.path, "op": self.op, "value": self.value,
                "rationale": self.rationale, "evidence": list(self.evidence)}


def _leaf_key_for(proposal: Proposal) -> str:
    return f"{proposal.path}/{proposal.value}" if proposal.op == "add" else proposal.path


def apply_confirmed_proposal(profile: PlayerProfile, proposal: Proposal,
                             now: Optional[str] = None) -> PlayerProfile:
    """Apply one proposal the owner has confirmed. Raises ProvenanceConflict
    when the target already holds a stated value (stated always wins)."""
    now = now or utc_now()
    key = _leaf_key_for(proposal)
    existing = profile.provenance.get(key)
    if existing is not None and existing.source == STATED:
        raise ProvenanceConflict(f"'{key}' is stated by the owner; a learned value cannot overwrite it.")
    data = profile.to_dict()
    node: Any = data
    parts = proposal.path.split(".")
    for part in parts[:-1]:
        node = node[part]
    last = parts[-1]
    if proposal.op == "add":
        lst = node[last]
        if not isinstance(lst, list):
            raise ValueError(f"'{proposal.path}' is not a list field.")
        if proposal.value not in lst:
            lst.append(proposal.value)
    elif proposal.op == "set":
        node[last] = proposal.value
    else:
        raise ValueError(f"unknown proposal op '{proposal.op}'")
    data["provenance"][key] = {"source": LEARNED, "at": now, "evidence": list(proposal.evidence)}
    return parse_profile(data)


def summarize_feedback(profile: PlayerProfile, min_rejections: int = 2) -> List[Proposal]:
    """Deterministic summarizer: PROPOSES learned entries, never applies them.

    A card the owner rejected as an *add* in >= ``min_rejections`` distinct
    reviews is proposed for ``dislikes.cards``. Cards already disliked or
    pet cards are skipped; stated conflicts are filtered at confirm time.
    """
    latest = _latest_feedback(profile.feedback)
    rejected_reviews: Dict[str, Set[str]] = {}
    for e in latest.values():
        if e.decision == "rejected":
            rejected_reviews.setdefault(e.add, set()).add(e.review_id)
    skip = {_norm(c) for c in profile.dislikes.cards} | {_norm(c) for c in profile.pet_cards}
    out: List[Proposal] = []
    for card in sorted(rejected_reviews):
        revs = rejected_reviews[card]
        if len(revs) >= min_rejections and _norm(card) not in skip:
            out.append(Proposal(
                path="dislikes.cards", op="add", value=card,
                rationale=f"Rejected as a suggested add in {len(revs)} reviews.",
                evidence=sorted(revs)))
    return out


# --------------------------------------------------------------------------
# Feedback queries
# --------------------------------------------------------------------------

def _latest_feedback(entries: List[FeedbackEntry]) -> Dict[Tuple[str, str, str, str], FeedbackEntry]:
    """Latest decision per (deck, review, cut, add). Entries are in write order."""
    latest: Dict[Tuple[str, str, str, str], FeedbackEntry] = {}
    for e in sorted(entries, key=lambda x: x.at):
        latest[(e.deck_id, e.review_id, e.cut, e.add)] = e
    return latest


def previously_rejected(profile: PlayerProfile, deck_id: str) -> Set[Tuple[str, str]]:
    """(cut, add) swaps rejected for this deck whose most recent decision on
    that pair (across reviews) is still ``rejected``. For the verifier."""
    by_pair: Dict[Tuple[str, str], FeedbackEntry] = {}
    for e in sorted((x for x in profile.feedback if x.deck_id == deck_id), key=lambda x: x.at):
        by_pair[(e.cut, e.add)] = e
    return {pair for pair, e in by_pair.items() if e.decision == "rejected"}


# --------------------------------------------------------------------------
# Deck-intent precedence + prompt rendering
# --------------------------------------------------------------------------

def resolve_with_intent(profile: PlayerProfile,
                        intent: Optional[UserDeckIntent]) -> Tuple[PlayerProfile, List[str]]:
    """Deck-level intent beats the profile. Returns (effective profile,
    human-readable list of overrides applied). The input is not mutated."""
    eff = copy.deepcopy(profile)
    notes: List[str] = []
    if intent is None:
        return eff, notes

    bc = intent.budget_constraints
    if bc.no_budget:
        if eff.budget_norms.hard_cap is not None or eff.budget_norms.soft_cap is not None:
            notes.append("budget: deck says no budget; profile caps ignored")
        eff.budget_norms.hard_cap = None
        eff.budget_norms.soft_cap = None
    else:
        if bc.hard_cap is not None:
            eff.budget_norms.hard_cap = bc.hard_cap
            notes.append(f"budget.hard_cap: deck intent {bc.hard_cap:g} replaces profile")
        if bc.soft_cap is not None:
            eff.budget_norms.soft_cap = bc.soft_cap
            notes.append(f"budget.soft_cap: deck intent {bc.soft_cap:g} replaces profile")
        if bc.hard_cap is not None or bc.soft_cap is not None:
            eff.budget_norms.currency = bc.currency

    # UserDeckIntent cannot tell "unset" from its default (7 / optimized), so an
    # intent at the default tier is treated as unset and does not override.
    tier = intent.target_power_level.tier
    if tier != DEFAULT_INTENT_TIER:
        if eff.power_tendencies.tier not in (None, tier):
            notes.append(f"power: deck intent '{tier}' replaces profile '{eff.power_tendencies.tier}'")
        eff.power_tendencies.tier = tier

    ap = intent.archetype_preferences
    for ex in ap.excluded_archetypes:
        if eff.archetype_affinities.pop(ex, None) is not None:
            notes.append(f"archetype: deck excludes '{ex}'")
    if ap.primary_archetype:
        eff.archetype_affinities[ap.primary_archetype] = 1.0

    deck_pets = list(intent.pet_cards)
    deck_pet_norm = {_norm(c) for c in deck_pets}
    kept = [c for c in eff.dislikes.cards if _norm(c) not in deck_pet_norm]
    if len(kept) != len(eff.dislikes.cards):
        notes.append("dislikes.cards: deck pet cards override profile dislikes")
    eff.dislikes.cards = kept
    eff.pet_cards = deck_pets + [c for c in eff.pet_cards if _norm(c) not in deck_pet_norm]
    return eff, notes


def _fmt_weights(w: Dict[str, float]) -> str:
    return ", ".join(f"{k} ({v:.2f})" for k, v in sorted(w.items(), key=lambda kv: (-kv[1], kv[0])))


def _tagged(profile: PlayerProfile, field_path: str, items: List[str]) -> str:
    parts = []
    for it in items:
        p = profile.provenance.get(f"{field_path}/{it}")
        parts.append(f"{it} [learned]" if p is not None and p.source == LEARNED else it)
    return ", ".join(parts)


def render_profile_for_prompt(profile: PlayerProfile,
                              deck_intent: Optional[UserDeckIntent] = None) -> str:
    """Deterministic plain-text profile block for LLM prompts: same input,
    same bytes. Deck intent overrides are applied and listed."""
    p, notes = resolve_with_intent(profile, deck_intent)
    lines: List[str] = ["PLAYER PROFILE"]
    if p.builder_identity.text:
        lines.append(f"Identity: {p.builder_identity.text}")
    if p.builder_identity.tags:
        lines.append(f"Identity tags: {', '.join(p.builder_identity.tags)}")
    if p.color_affinities:
        lines.append(f"Color affinities: {_fmt_weights(p.color_affinities)}")
    if p.archetype_affinities:
        lines.append(f"Archetype affinities: {_fmt_weights(p.archetype_affinities)}")
    if p.favored_mechanics:
        lines.append(f"Favored mechanics: {_tagged(profile, 'favored_mechanics', p.favored_mechanics)}")
    if p.pet_cards:
        lines.append(f"Pet cards (protect these): {_tagged(profile, 'pet_cards', p.pet_cards)}")
    d = p.dislikes
    for label, attr in (("cards", "cards"), ("strategies", "strategies"), ("play patterns", "play_patterns")):
        items = getattr(d, attr)
        if items:
            lines.append(f"Dislikes ({label}): {_tagged(profile, 'dislikes.' + attr, items)}")
    pt = p.power_tendencies
    if pt.tier or pt.notes:
        lines.append("Power: " + "; ".join(x for x in (pt.tier, pt.notes) if x))
    b = p.budget_norms
    if b.hard_cap is not None or b.soft_cap is not None:
        bits = []
        if b.soft_cap is not None:
            bits.append(f"soft cap {b.soft_cap:g}")
        if b.hard_cap is not None:
            bits.append(f"hard cap {b.hard_cap:g}")
        lines.append(f"Budget per card ({b.currency}): {', '.join(bits)}")
    rp = p.review_preferences
    lines.append(f"Review depth: {rp.depth or 'standard'}")
    lines.append(f"Review tone: {rp.tone or 'direct'}")
    if rp.skip:
        lines.append(f"Skip in reviews: {', '.join(rp.skip)}")
    if rp.welcome_generic_staples is not None:
        lines.append("Generic staples: " + ("welcome" if rp.welcome_generic_staples else "not welcome"))
    if p.done_when:
        lines.append("A deck feels finished when: " + "; ".join(p.done_when))
    if notes:
        lines.append("Deck intent overrides profile:")
        lines.extend(f"- {n}" for n in notes)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Store access (interface mandated by the card: load_profile(name))
# --------------------------------------------------------------------------

def load_profile(name: str) -> PlayerProfile:
    """Current profile (latest version) plus its feedback history, from
    Postgres. Raises ProfileNotFound if never seeded. The infra import is
    lazy because domain must not import infra at module load (AGENT_RULES §2);
    the signature is fixed by the card for downstream consumers."""
    from src.server.infra.services import player_profile_service as svc  # noqa: PLC0415
    from src.server.infra.db.session import session_scope  # noqa: PLC0415

    with session_scope() as session:
        return svc.load_profile(session, name)
