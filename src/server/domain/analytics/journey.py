"""
Deck journey timeline (DT-8, docs/architecture/deck-tuner-overlay-spec.md §3.4).

A timeline of a deck's major changes and milestones. Every entry carries a
short editable description of how the deck played at that point; a redraft
regenerates that description from the entry's structured data.

Redis keys (all JSON, no TTL):
    journey:entry:{id}               one entry
    journey:entries:{registry_id}    LIST of entry ids, newest first (max HISTORY_MAX)

Entry: {id, deck_id, schema_version, kind, title, description,
        diff: {added: [...], cut: [...]}, source_draft_id,
        created_at, updated_at, redrafted_from}

Kinds: milestone (user-marked point), tune_applied (written by the tuner
apply path with the card diff, DT-2), sync (provider sync diff), manual
(user-written note).

Redraft versioning: a redraft REPLACES the entry with a new id at the same
position in the timeline. The replacement keeps title/diff/created_at, gets a
freshly drafted description, a bumped updated_at, and redrafted_from = the id
it was regenerated from. The old entry key is deleted, so redrafted_from is
provenance, not a resolvable pointer.

Everything here is deterministic. No LLM.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

log = logging.getLogger("commander_lab.journey")

ENTRY_PREFIX = "journey:entry:"
ENTRIES_PREFIX = "journey:entries:"
SCHEMA_VERSION = 1
HISTORY_MAX = 50          # journal:reviews precedent (analytics/journal.py)
TITLE_MAX = 200
DESCRIPTION_MAX = 2000
DIFF_MAX = 200            # card names per side of a diff
DIFF_SHOW = 8             # names quoted in a drafted description

KINDS = ("milestone", "tune_applied", "sync", "manual")
KIND_LABELS = {"milestone": "Milestone", "tune_applied": "Tune-up applied",
               "sync": "Synced", "manual": "Note"}


class JourneyError(ValueError):
    def __init__(self, message: str, errors: Optional[List[str]] = None):
        super().__init__(message)
        self.errors = errors or [message]


class EntryNotFound(JourneyError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _registry_id(deck: Mapping) -> str:
    if deck.get("registry_id"):
        return deck["registry_id"]
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


def _entries_key(deck: Mapping) -> str:
    return ENTRIES_PREFIX + _registry_id(deck)


def _get_json(r, key: str):
    raw = r.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _clean_text(value: Any, field: str, limit: int, required: bool = False) -> str:
    if value is None:
        if required:
            raise JourneyError(f"{field} is required.")
        return ""
    if not isinstance(value, str):
        raise JourneyError(f"{field} must be a string.")
    text = value.strip()
    if required and not text:
        raise JourneyError(f"{field} is required.")
    if len(text) > limit:
        raise JourneyError(f"{field} must be {limit} characters or fewer.")
    return text


def _clean_names(value: Any, side: str) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise JourneyError(f"diff.{side} must be a list of card names.")
    seen, out = set(), []
    for v in value:
        if not isinstance(v, str) or not v.strip():
            raise JourneyError(f"diff.{side} must be a list of card names.")
        name = v.strip()
        if name not in seen:
            seen.add(name)
            out.append(name)
        if len(out) >= DIFF_MAX:
            break
    return out


def _clean_diff(diff: Any) -> Dict[str, List[str]]:
    if diff is None:
        return {"added": [], "cut": []}
    if not isinstance(diff, Mapping):
        raise JourneyError("diff must be an object with added/cut lists.")
    return {"added": _clean_names(diff.get("added"), "added"),
            "cut": _clean_names(diff.get("cut"), "cut")}


def _quote(names: List[str]) -> str:
    shown = names[:DIFF_SHOW]
    rest = len(names) - len(shown)
    return ", ".join(shown) + (f", …+{rest} more" if rest > 0 else "")


def default_title(kind: str, diff: Mapping) -> str:
    """The title an appended entry gets when the caller doesn't write one
    (spec §3.4 example: \"Swapped 6 cards after tune-up\")."""
    added, cut = len(diff.get("added") or []), len(diff.get("cut") or [])
    n = added + cut
    if kind == "tune_applied":
        return f"Swapped {n} card{'s' if n != 1 else ''} after tune-up" if n else "Tune-up applied"
    if kind == "sync":
        return f"Synced deck changes (+{added} −{cut})" if n else "Synced with no card changes"
    return KIND_LABELS[kind]


def draft_description(kind: str, title: str, diff: Mapping) -> str:
    """The deterministic auto-written note for an entry: what changed, plus a
    prompt to record how the deck played. Redrafting regenerates exactly this."""
    added, cut = (diff or {}).get("added") or [], (diff or {}).get("cut") or []
    moves = []
    if added:
        moves.append("added " + _quote(added))
    if cut:
        moves.append("cut " + _quote(cut))
    change = "; ".join(moves)
    if kind == "tune_applied":
        base = "Applied a tune-up" + (f": {change}." if change else " with no card changes.")
    elif kind == "sync":
        base = "Synced the deck from its provider" + (f": {change}." if change else " with no card changes.")
    elif kind == "milestone":
        base = f"Milestone: {title}" + (f" — {change}." if change else ".")
    else:
        base = title + (f" — {change}." if change else "")
    return base + " How did the deck play at this point? Edit this note to record it."


def _public(entry: Mapping) -> dict:
    return dict(entry)


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


def append_entry(r, deck: Mapping, kind: str = "manual", title: Optional[str] = None,
                 description: Optional[str] = None, diff: Any = None,
                 source_draft_id: Optional[str] = None, now: Optional[str] = None) -> dict:
    """Append an entry to the deck's timeline. The tuner apply path (DT-2)
    calls this with kind=\"tune_applied\" and the card diff; the UI uses
    \"manual\"/\"milestone\". A blank title falls back to default_title(); a
    blank description is auto-drafted."""
    if kind not in KINDS:
        raise JourneyError(f"Unknown journey entry kind '{kind}'. Allowed: {list(KINDS)}")
    now = now or _now()
    clean_diff = _clean_diff(diff)
    title = _clean_text(title, "title", TITLE_MAX) or default_title(kind, clean_diff)
    description = _clean_text(description, "description", DESCRIPTION_MAX) \
        or draft_description(kind, title, clean_diff)
    entry = {"id": uuid.uuid4().hex[:12], "deck_id": _registry_id(deck),
             "schema_version": SCHEMA_VERSION, "kind": kind, "title": title,
             "description": description, "diff": clean_diff,
             "source_draft_id": source_draft_id or None,
             "created_at": now, "updated_at": now, "redrafted_from": None}
    key = _entries_key(deck)
    overflow = r.lrange(key, HISTORY_MAX - 1, -1) or []
    r.lpush(key, entry["id"])
    r.ltrim(key, 0, HISTORY_MAX - 1)
    for old_id in overflow:
        r.delete(ENTRY_PREFIX + old_id)
    r.set(ENTRY_PREFIX + entry["id"], json.dumps(entry))
    return entry


def list_entries(r, deck: Mapping, limit: int = HISTORY_MAX) -> List[dict]:
    out = []
    ids = r.lrange(_entries_key(deck), 0, limit - 1) or []
    for raw in r.mget([ENTRY_PREFIX + i for i in ids]) if ids else []:
        try:
            entry = json.loads(raw) if raw else None
        except (json.JSONDecodeError, TypeError):
            entry = None
        if entry:
            out.append(_public(entry))
    return out


def get_entry(r, deck: Mapping, entry_id: str) -> dict:
    entry = _get_json(r, ENTRY_PREFIX + (entry_id or ""))
    if not entry or entry.get("deck_id") != _registry_id(deck):
        raise EntryNotFound(f"Journey entry '{entry_id}' not found for this deck.")
    return entry


def update_entry(r, deck: Mapping, entry_id: str, title: Optional[str] = None,
                 description: Optional[str] = None, now: Optional[str] = None) -> dict:
    """Edit an entry's title and/or description in place (same id)."""
    entry = get_entry(r, deck, entry_id)
    if title is None and description is None:
        raise JourneyError("Nothing to update: pass title and/or description.")
    if title is not None:
        entry["title"] = _clean_text(title, "title", TITLE_MAX, required=True)
    if description is not None:
        entry["description"] = _clean_text(description, "description", DESCRIPTION_MAX)
    entry["updated_at"] = now or _now()
    r.set(ENTRY_PREFIX + entry["id"], json.dumps(entry))
    return _public(entry)


def redraft_entry(r, deck: Mapping, entry_id: str, now: Optional[str] = None) -> dict:
    """Regenerate the entry's description. The redrafted version gets a new id,
    takes the old entry's slot in the timeline, and points redrafted_from at
    the id it was regenerated from (see module docstring)."""
    old = get_entry(r, deck, entry_id)
    now = now or _now()
    entry = dict(old, id=uuid.uuid4().hex[:12],
                 description=draft_description(old["kind"], old["title"], old.get("diff") or {}),
                 updated_at=now, redrafted_from=old["id"])
    key = _entries_key(deck)
    ids = r.lrange(key, 0, -1) or []
    if old["id"] in ids:
        ids[ids.index(old["id"])] = entry["id"]
        r.delete(key)
        for existing in reversed(ids):   # re-lpush oldest first: order preserved
            r.lpush(key, existing)
    r.delete(ENTRY_PREFIX + old["id"])
    r.set(ENTRY_PREFIX + entry["id"], json.dumps(entry))
    return _public(entry)
