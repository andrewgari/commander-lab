"""
PS-6: Oracle makes the final deck personality call, with rulebook, CommanderSalt,
combos, engines, and journal as evidence.

Flow: build_request() -> oracle (skill `personality-read`) -> parse_reply() ->
PersonalityReadDoc (pydantic) -> store_read().

Copies the Theory async job pattern (queue / status / poll).
Deck page load never blocks on it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import journal as journal_mod
from . import personality as ps
from . import personality_store

log = logging.getLogger("commander_lab.personality_read")

SCHEMA_VERSION = "1"
READ_PREFIX = "personality:read:"
JOB_PREFIX = "personality_read_job:"
JOB_TTL_S = 7 * 24 * 3600
MAX_INFLIGHT = 2
_INFLIGHT: Dict[str, str] = {}  # deck_id -> job_id
_INFLIGHT_LOCK = threading.Lock()

SKILL_NAME = "personality-read"
SYSTEM_PROMPT = (
    "Commander Lab request. Load and follow your `personality-read` skill exactly "
    "(skill_view name='personality-read'). Reply with prose of at most 15 lines, then exactly one "
    "fenced ```json block matching personality-read.schema.json. Nothing after it."
)


class PersonalityReadError(ValueError):
    """Base error for personality read failures."""


class PersonalityReadParseError(PersonalityReadError):
    """Oracle's reply failed schema validation or could not be parsed."""


class PersonalityReadBusy(PersonalityReadError):
    """A personality read is already running for this deck, or overall limit reached."""

    def __init__(self, message: str, per_deck: bool):
        self.per_deck = per_deck
        super().__init__(message)


# ---------------------------------------------------------------------------
# Models & Schema
# ---------------------------------------------------------------------------


class AxisRead(BaseModel):
    model_config = ConfigDict(extra="ignore")

    score: int = Field(ge=0, le=100)
    justification: str = Field(min_length=1)
    cards: List[str] = Field(default_factory=list)


class Scores(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timmy: AxisRead
    johnny: AxisRead
    spike: AxisRead
    vorthos: AxisRead
    melvin: AxisRead


class Override(BaseModel):
    model_config = ConfigDict(extra="ignore")

    axis: Literal["timmy", "johnny", "spike", "vorthos", "melvin"]
    rulebook_score: int = Field(ge=0, le=100)
    oracle_score: int = Field(ge=0, le=100)
    delta: int
    reason: str = Field(min_length=1)


class TableImpactFlag(BaseModel):
    model_config = ConfigDict(extra="ignore")

    card: str = Field(min_length=1)
    category: str = Field(min_length=1)
    severity: Literal["low", "medium", "high"]
    reason: str = Field(min_length=1)


class PersonalityReadDoc(BaseModel):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["1"]
    deck_id: Optional[str] = None
    scores: Scores
    leans_toward: Literal["timmy", "johnny", "spike", "vorthos", "melvin", "balanced"]
    runner_up: Optional[Literal["timmy", "johnny", "spike", "vorthos", "melvin"]] = None
    summary: str = Field(min_length=1)
    overrides: List[Override]
    table_impact_flags: List[TableImpactFlag]
    rule0_pitch: Optional[str] = None


def parse_reply(reply: str) -> dict:
    """Extract and parse the fenced JSON block in Oracle's response."""
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", reply or "")
    if not blocks:
        raise PersonalityReadParseError("oracle reply contains no fenced ```json block")
    target = blocks[-1].strip()
    try:
        data = json.loads(target)
    except json.JSONDecodeError as exc:
        raise PersonalityReadParseError(f"oracle JSON block is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise PersonalityReadParseError("oracle JSON block must be an object")
    return data


def validate_doc(raw: Mapping) -> PersonalityReadDoc:
    try:
        return PersonalityReadDoc.model_validate(raw)
    except ValidationError as exc:
        raise PersonalityReadParseError(f"personality read failed schema validation: {exc.errors()[:3]}") from exc


# ---------------------------------------------------------------------------
# Input Hashing (same deck + same journal = same stored read)
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rid(deck: Mapping) -> str:
    return journal_mod._registry_id(deck)


def _decklist_canonical(deck: Mapping) -> dict:
    cards: Dict[str, int] = {}
    for c in deck.get("cards") or []:
        name = c.get("name") if isinstance(c, Mapping) else c
        if not name:
            continue
        qty = c.get("quantity") if isinstance(c, Mapping) else 1
        try:
            qty = int(qty or 1)
        except (TypeError, ValueError):
            qty = 1
        cards[str(name)] = cards.get(str(name), 0) + qty
    commanders = sorted(str(c) for c in deck.get("commanders") or [] if c)
    return {"commanders": commanders, "cards": sorted(cards.items())}


def resolve_journal(r, deck: Mapping) -> dict:
    """The journal inputs the read actually uses, resolved the way theory.py does:
    Identity thesis/live pillars, with the journal's deck_vision as the thesis fallback.
    Single source for the payload AND the input hash."""
    from . import identity

    raw = journal_mod.load_journal(r, deck)
    rec = journal_mod.public(raw) if raw else {}
    ident = identity.load_identity(r, dict(deck)) or {}
    vision = (rec.get("deck_vision") or {}).get("description") or ""
    tb = rec.get("target_bracket")
    if isinstance(tb, Mapping):
        tb = tb.get("value", tb.get("bracket", tb))
    return {
        "thesis": ((ident.get("thesis") or {}).get("text")) or vision or "",
        "deck_vision": vision,
        "pillars": [{"name": p.get("name"), "description": p.get("description")}
                    for p in identity.live_pillars(ident)],
        "pet_cards": list(rec.get("pet_cards") or []),
        "novelties": rec.get("novelties") or "",
        "target_bracket": tb if raw else None,
        "target_power_level": rec.get("target_power_level") if raw else None,
        "archetype_preferences": rec.get("archetype_preferences") or {},
        "preferred_win_conditions": rec.get("preferred_win_conditions") or {},
    }


def _journal_canonical(journal: Optional[Mapping]) -> dict:
    if not journal:
        return {}
    return {
        "thesis": journal.get("thesis") or "",
        "deck_vision": journal.get("deck_vision") or "",
        "pillars": journal.get("pillars") or [],
        "pet_cards": sorted(journal.get("pet_cards") or []),
        "novelties": journal.get("novelties") or "",
        "target_bracket": journal.get("target_bracket"),
        "target_power_level": journal.get("target_power_level"),
        "archetype_preferences": journal.get("archetype_preferences") or {},
        "preferred_win_conditions": journal.get("preferred_win_conditions") or {},
    }


def compute_input_hash(deck: Mapping, journal: Optional[Mapping] = None) -> str:
    """Deterministic hash of decklist + resolved journal inputs (see resolve_journal)."""
    canonical = {
        "version": SCHEMA_VERSION,
        "decklist": _decklist_canonical(deck),
        "journal": _journal_canonical(journal),
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def current_input_hash(r, deck: Mapping) -> str:
    """Hash of the deck + journal exactly as they stand now (job creation and freshness checks)."""
    return compute_input_hash(deck, resolve_journal(r, deck))


# ---------------------------------------------------------------------------
# Payload Builder (cache reads only; no new scraping or providers)
# ---------------------------------------------------------------------------


def _extract_cached_commandersalt(r, rid: str, deck: Mapping) -> Optional[dict]:
    """Read CommanderSalt data already cached by bracket evaluation or its provider."""
    # 1. Check bracket:eval:{rid}
    raw_eval = r.get(f"bracket:eval:{rid}")
    cs_out: Dict[str, Any] = {}
    if raw_eval:
        try:
            eval_data = json.loads(raw_eval)
            res = eval_data.get("result") or {}
            expl = res.get("explanation") or {}
            signals = expl.get("signals") or []
            for s in signals:
                sig_name = s.get("signal")
                if sig_name == "commandersalt_power":
                    cs_out["power"] = s.get("value")
                elif sig_name == "commandersalt_bracket_rating":
                    cs_out["bracket_rating"] = s.get("value")
                elif sig_name == "commandersalt_salt":
                    cs_out["salt"] = s.get("value")
        except Exception:  # noqa: BLE001
            pass

    try:
        from .providers.commandersalt import resolve_source_url
        from .registry import get_provider

        provider = get_provider("commandersalt")
        source_url = resolve_source_url(rid)
        cached = provider._cache.get(source_url)
        if cached and provider._now() - cached[0] < provider._cache_ttl_seconds:
            raw = cached[1]
            if isinstance(raw, Mapping):
                for key, source_key in (
                    ("power", "powerLevelRating"),
                    ("bracket_rating", "bracketRating"),
                    ("salt", "saltRating"),
                    ("synergy_rating", "synergyRating"),
                    ("threat_rating", "threatRating"),
                    ("archetype_label", "archetypeLabel"),
                ):
                    if raw.get(source_key) is not None:
                        cs_out.setdefault(key, raw[source_key])
    except Exception:  # noqa: BLE001
        pass

    return cs_out or None


def _extract_cached_edhrec_salt(r, deck: Mapping) -> Optional[dict]:
    """Read EDHREC salt where cached."""
    from .providers.edhrec import commander_slug

    cmdrs = [c for c in deck.get("commanders") or [] if c]
    if not cmdrs:
        return None
    slug = commander_slug(cmdrs)
    raw = r.get(f"edhrec:commander:{slug}")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        cards = data.get("cards") or {}
        # edhrec_inclusion.parse_page retains salt for the commander and per card.
        salt_cards = {v.get("name") or k: v.get("salt") for k, v in cards.items()
                      if isinstance(v, Mapping) and v.get("salt") is not None}
        commander_salt = data.get("commander_salt")
        if commander_salt is None and not salt_cards:
            return None
        return {"commander_slug": slug, "commander_salt": commander_salt, "cards_with_salt": salt_cards}
    except Exception:  # noqa: BLE001
        return None


def build_payload(r, deck: Mapping) -> dict:
    """Build the prompt payload for Oracle. Reads only cached data from Redis."""
    deck = dict(deck)
    rid = _rid(deck)
    journal_view = resolve_journal(r, deck)

    # 1. Decklist
    decklist_entries = []
    for c in deck.get("cards") or []:
        if isinstance(c, Mapping):
            decklist_entries.append({
                "name": c.get("name"),
                "quantity": c.get("quantity", 1),
                "type_line": c.get("type_line", c.get("type", "")),
            })
        elif isinstance(c, str):
            decklist_entries.append({"name": c, "quantity": 1})

    # 2. Rulebook personality scores & per-signal evidence
    rulebook_scores = ps.score_deck(r, deck).model_dump()

    # 3. Cached CommanderSalt data
    commandersalt_data = _extract_cached_commandersalt(r, rid, deck)

    # 4. Cached EDHREC salt
    edhrec_salt = _extract_cached_edhrec_salt(r, deck)

    # 5. Detected combos and engines
    inputs = ps.load_inputs(r, deck)
    raw_combos = inputs.get("combos") or []
    combos_clean = []
    for cb in raw_combos:
        if isinstance(cb, Mapping):
            combos_clean.append({
                "cards": cb.get("card_names") or [c.get("name") for c in cb.get("cards") or [] if isinstance(c, Mapping)],
                "produces": cb.get("produces") or [],
                "description": cb.get("description") or "",
            })
    raw_engines = inputs.get("engines") or []
    engines_clean = []
    for eg in raw_engines:
        if isinstance(eg, Mapping):
            engines_clean.append({
                "name": eg.get("name"),
                "cards": eg.get("cards") or [],
                "description": eg.get("summary") or eg.get("description") or eg.get("role", ""),
            })

    # 6. Journal fields (Identity thesis/pillars, journal vision/novelties/pets/targets)
    journal_data = journal_view

    # 7. Game Changers present in the deck
    all_gc = inputs.get("game_changers") or []
    deck_card_names = set()
    for c in deck.get("cards") or []:
        c_name = c.get("name") if isinstance(c, Mapping) else str(c)
        if c_name:
            deck_card_names.add(ps._norm(str(c_name)))
    gc_in_deck = [gc for gc in all_gc if ps._norm(str(gc)) in deck_card_names]

    return {
        "deck_id": rid,
        "deck_name": deck.get("name", "Untitled Deck"),
        "commanders": deck.get("commanders") or [],
        "color_identity": deck.get("color") or "",
        "decklist": decklist_entries,
        "rulebook_scores": rulebook_scores,
        "commandersalt": commandersalt_data,
        "edhrec_salt": edhrec_salt,
        "combos": combos_clean,
        "engines": engines_clean,
        "journal": journal_data,
        "game_changers": gc_in_deck,
    }


def request_prompt(payload: Mapping[str, Any]) -> str:
    """Format payload into the user prompt for Oracle."""
    return (
        f"Deck Personality Read request for '{payload.get('deck_name')}' ({payload.get('deck_id')}).\n\n"
        "Here is the deck evidence from Commander Lab (rulebook scores, CommanderSalt, combos, engines, journal, and Game Changers):\n\n"
        f"```json\n{json.dumps(payload, indent=2)}\n```\n\n"
        "Please provide your expert personality read per the `personality-read` skill, ending with exactly ONE fenced ```json block matching personality-read.schema.json."
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def read_key(rid: str) -> str:
    return f"{READ_PREFIX}{rid}"


def store_read(r, rid: str, input_hash: str, read_doc: PersonalityReadDoc, rulebook: Mapping[str, Any]) -> dict:
    """Persist validated read record to Redis."""
    record = {
        "registry_id": rid,
        "input_hash": input_hash,
        "created_at": _now_iso(),
        "status": "ok",
        "read": read_doc.model_dump(),
        "rulebook": rulebook,
    }
    r.set(read_key(rid), json.dumps(record))
    return record


def load_read(r, rid: str) -> Optional[dict]:
    """Retrieve stored read record from Redis."""
    raw = r.get(read_key(rid))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Jobs & Execution
# ---------------------------------------------------------------------------


def job_key(job_id: str) -> str:
    return JOB_PREFIX + job_id


def _save_job(r, job: dict) -> dict:
    job["updated_at"] = _now_iso()
    r.set(job_key(job["job_id"]), json.dumps(job), ex=JOB_TTL_S)
    return job


def load_job(r, job_id: str) -> Optional[dict]:
    raw = r.get(job_key(job_id))
    try:
        return json.loads(raw) if raw else None
    except (json.JSONDecodeError, TypeError):
        return None


def reserve(deck_id: str, job_id: str) -> None:
    """Claim generation slot (one per deck, MAX_INFLIGHT overall) or raise PersonalityReadBusy."""
    with _INFLIGHT_LOCK:
        if deck_id in _INFLIGHT:
            raise PersonalityReadBusy(
                f"a personality read is already in progress for this deck (job {_INFLIGHT[deck_id]})",
                per_deck=True,
            )
        if len(_INFLIGHT) >= MAX_INFLIGHT:
            raise PersonalityReadBusy(
                f"{MAX_INFLIGHT} personality reads are already in flight; try again shortly",
                per_deck=False,
            )
        _INFLIGHT[deck_id] = job_id


def release(deck_id: str, job_id: Optional[str] = None) -> None:
    with _INFLIGHT_LOCK:
        if deck_id in _INFLIGHT and (job_id is None or _INFLIGHT[deck_id] == job_id):
            del _INFLIGHT[deck_id]


def new_job(r, deck: Mapping) -> dict:
    """Validate and queue a new personality read job."""
    rid = _rid(deck)
    inhash = current_input_hash(r, deck)
    job_id = uuid.uuid4().hex[:12]
    job = {
        "job_id": job_id,
        "deck_id": rid,
        "deck_name": deck.get("name"),
        "input_hash": inhash,
        "status": "queued",
        "created_at": _now_iso(),
    }
    reserve(rid, job_id)
    try:
        return _save_job(r, job)
    except Exception:
        release(rid, job_id)
        raise


def run_job(r, job: dict, deck: Mapping, client_factory: Callable[[], Any]) -> dict:
    from .oracle_client import OFFLINE_MESSAGE, OracleError, OracleUnavailable

    rid = job["deck_id"]
    try:
        job.update(status="running", started_at=_now_iso())
        _save_job(r, job)
        payload = build_payload(r, deck)
        client = client_factory()
        prompt = request_prompt(payload)
        reply = client.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ])
        raw_data = parse_reply(reply)
        raw_data["deck_id"] = rid
        doc = validate_doc(raw_data)
        record = store_read(r, rid, job["input_hash"], doc, payload["rulebook_scores"])
        job.update(status="done", read=doc.model_dump(), record=record)
    except OracleUnavailable as exc:
        job.update(status="failed", error=OFFLINE_MESSAGE, error_kind="oracle_offline", detail=exc.detail)
    except (OracleError, PersonalityReadError) as exc:
        job.update(status="failed", error=str(exc)[:500], error_kind=type(exc).__name__)
    except Exception as exc:  # noqa: BLE001
        log.exception("personality read job %s failed", job["job_id"])
        job.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}", error_kind="internal")
    finally:
        release(rid, job["job_id"])
    job["finished_at"] = _now_iso()
    try:
        return _save_job(r, job)
    except Exception:  # noqa: BLE001
        log.exception("personality read job %s: could not persist final state", job["job_id"])
        return job


def start_job(r, job: dict, deck: Mapping, client_factory: Callable[[], Any]) -> threading.Thread:
    t = threading.Thread(
        target=run_job,
        args=(r, job, dict(deck), client_factory),
        name=f"personality-read-{job['job_id']}",
        daemon=True,
    )
    t.start()
    return t
