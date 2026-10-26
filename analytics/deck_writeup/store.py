"""Persistence for the primer builder (Redis), all keyed by registry id:

    primer:draft:{rid}        {"answers", "updated_at"}   wizard autosave
    primer:{rid}              {"html", "markdown", "answers", "sections", "version", "updated_at"}
    primer:prev:{rid}         previous saved primer (one Undo)
    primer:staged_desc:{rid}  {"description", "updated_at"}   description staged by the builder's Step 5

and the deck's own `description` / `description_src` / `description_prev` on the `decks` registry record."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from .sanitize import sanitize_primer_html
from .service import DESCRIPTION_MAX_CHARS, WriteupError

MAX_JSON_BYTES = 200_000
DESCRIPTION_SOURCES = ("draft", "yours")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def k_draft(rid: str) -> str:
    return f"primer:draft:{rid}"


def k_primer(rid: str) -> str:
    return f"primer:{rid}"


def k_prev(rid: str) -> str:
    return f"primer:prev:{rid}"


def k_staged(rid: str) -> str:
    return f"primer:staged_desc:{rid}"


def _get(r, key: str) -> Optional[dict]:
    raw = r.get(key)
    if not raw:
        return None
    try:
        val = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return val if isinstance(val, dict) else None


def _set(r, key: str, val: Mapping[str, Any]) -> None:
    raw = json.dumps(val)
    if len(raw) > MAX_JSON_BYTES:
        raise WriteupError("That is too large to save.", "too_large")
    r.set(key, raw)


# ---- draft ---------------------------------------------------------------------------------------------

def load_draft(r, rid: str) -> Optional[dict]:
    return _get(r, k_draft(rid))


def save_draft(r, rid: str, answers: Any) -> dict:
    if not isinstance(answers, Mapping):
        raise WriteupError("answers must be an object", "invalid")
    rec = {"answers": dict(answers), "updated_at": _now()}
    _set(r, k_draft(rid), rec)
    return rec


# ---- primer --------------------------------------------------------------------------------------------

def load_primer(r, rid: str) -> Optional[dict]:
    return _get(r, k_primer(rid))


def has_undo(r, rid: str) -> bool:
    return bool(r.exists(k_prev(rid)))


def save_primer(r, rid: str, html_text: str, known_cards, answers: Optional[Mapping] = None,
                markdown: str = "", sections: Optional[list] = None) -> dict:
    """Sanitize + save. The current primer (if any) becomes the single Undo version."""
    if not isinstance(html_text, str) or not html_text.strip():
        raise WriteupError("The primer is empty.", "empty")
    clean = sanitize_primer_html(html_text, known_cards)
    if not clean.strip():
        raise WriteupError("The primer is empty.", "empty")
    cur = load_primer(r, rid)
    rec = {"html": clean, "markdown": markdown if isinstance(markdown, str) else "",
           "answers": dict(answers) if isinstance(answers, Mapping) else (cur or {}).get("answers") or {},
           "sections": [s for s in (sections or []) if isinstance(s, Mapping)],
           "version": int((cur or {}).get("version") or 0) + 1, "updated_at": _now()}
    _set(r, k_primer(rid), rec)
    if cur is not None:
        _set(r, k_prev(rid), cur)
    else:
        r.delete(k_prev(rid))
    return rec


def undo_primer(r, rid: str) -> Optional[dict]:
    """Restore the previous version. The one-step undo is consumed (no redo)."""
    prev = _get(r, k_prev(rid))
    if prev is None:
        return None
    _set(r, k_primer(rid), prev)
    r.delete(k_prev(rid))
    return prev


# ---- staged description ----------------------------------------------------------------------------------

def load_staged(r, rid: str) -> Optional[dict]:
    return _get(r, k_staged(rid))


def stage_description(r, rid: str, text: str) -> None:
    _set(r, k_staged(rid), {"description": text, "updated_at": _now()})


def clear_staged(r, rid: str) -> None:
    r.delete(k_staged(rid))


# ---- deck description -------------------------------------------------------------------------------------

def clean_description(text: Any) -> str:
    if not isinstance(text, str) or not " ".join(text.split()):
        raise WriteupError("The description is empty.", "empty")
    t = " ".join(text.split())
    if len(t) > DESCRIPTION_MAX_CHARS:
        raise WriteupError(f"The description is {len(t)} characters; the limit is {DESCRIPTION_MAX_CHARS}.", "too_long")
    return t


def save_description(r, rid: str, text: Any, src: Any) -> dict:
    """Write the description onto the deck record; the previous one is kept for a single Undo."""
    import registry
    if src not in DESCRIPTION_SOURCES:
        raise WriteupError("src must be 'draft' or 'yours'", "invalid")
    t = clean_description(text)
    decks = registry._load_decks(r)
    for d in decks:
        if registry.registry_id_of(d) == rid:
            d["description_prev"] = {"description": d.get("description") or "", "src": d.get("description_src") or ""}
            d["description"], d["description_src"] = t, src
            registry._save_decks(r, decks)
            clear_staged(r, rid)
            return {"description": t, "src": src, "has_undo": True}
    raise WriteupError("deck not found", "not_found")


def undo_description(r, rid: str) -> Optional[dict]:
    import registry
    decks = registry._load_decks(r)
    for d in decks:
        if registry.registry_id_of(d) == rid:
            prev = d.pop("description_prev", None)
            if not isinstance(prev, Mapping):
                return None
            d["description"] = prev.get("description") or ""
            if prev.get("src"):
                d["description_src"] = prev["src"]
            else:
                d.pop("description_src", None)
            registry._save_decks(r, decks)
            return {"description": d["description"], "src": d.get("description_src") or "", "has_undo": False}
    return None
