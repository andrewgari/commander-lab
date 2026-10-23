"""RV-4: Deep Review view-model for the deck page Review tab. Pure functions, no Redis.

Same approach as `theory_view`: each section type has ONE Jinja partial,
`templates/partials/review/<type>.html`. A type with no partial (a future type), or a known type
whose data has the wrong shape, uses `_fallback.html` (markdown for strings, key/value, list or
pretty JSON otherwise) so an old or odd doc never breaks the page.

Nothing here computes a number or label: everything shown is already in the saved doc
(docs/REVIEW_SPEC.md "Core rule").
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional

from . import theory_view

PARTIAL_DIR = "partials/review"
FALLBACK = f"{PARTIAL_DIR}/_fallback.html"
# Types with their own partial. Keep in sync with templates/partials/review/.
RENDERERS = ("genre", "composition", "curve_tempo", "focus", "strong_cards", "stragglers",
             "combos_engines", "oracle_summary")
WIN_LINE_LABELS = {"alternate_wincon": "Alternate win", "commander_damage": "Commander damage"}
STATUS_TONES = {"short": "danger", "over": "signal", "ok": "ok", "on": "ok"}
ORACLE_TONES = {"ok": "ok", "offline": "neutral", "invalid": "danger"}


def _is_list_of_maps(v: Any) -> bool:
    return isinstance(v, list) and all(isinstance(x, Mapping) for x in v)


def _shape_ok(type_: str, data: Any) -> bool:
    """A known partial only runs on the data shape it expects."""
    if not isinstance(data, Mapping):
        return False
    if type_ == "genre":
        return (data.get("primary") is None or isinstance(data.get("primary"), Mapping)) \
            and _is_list_of_maps(data.get("secondary") or []) and _is_list_of_maps(data.get("ranking") or [])
    if type_ == "composition":
        return _is_list_of_maps(data.get("rows") or [])
    if type_ == "curve_tempo":
        mana = data.get("mana")
        return mana is None or (isinstance(mana, Mapping) and _is_list_of_maps(mana.get("per_turn") or []))
    if type_ == "focus":
        return _is_list_of_maps(data.get("win_lines") or []) and isinstance(data.get("claimed_unsupported") or [], list) \
            and isinstance(data.get("supported_unclaimed") or [], list)
    if type_ in ("strong_cards", "stragglers"):
        ok = _is_list_of_maps(data.get("cards") or [])
        if type_ == "stragglers":
            ok = ok and _is_list_of_maps(data.get("protected_pets") or [])
        return ok
    if type_ == "combos_engines":
        return all(_is_list_of_maps(data.get(k) or []) for k in ("combos_in_deck", "combos_one_away", "engines"))
    if type_ == "oracle_summary":
        return isinstance(data.get("text", ""), str)
    return True


def section_template(section: Mapping) -> str:
    """Template path for one section: its own partial, or the generic fallback."""
    type_ = section.get("type") if isinstance(section, Mapping) else None
    if type_ in RENDERERS and _shape_ok(type_, section.get("data")):
        return f"{PARTIAL_DIR}/{type_}.html"
    return FALLBACK


def section_title(section: Mapping) -> str:
    return theory_view.section_title(section)


def sections(doc: Mapping) -> List[Dict[str, Any]]:
    """[{section, template, title, prose}] in the doc's order. Non-dict entries are skipped."""
    out = []
    for s in (doc or {}).get("sections") or []:
        if isinstance(s, Mapping):
            prose = s.get("prose")
            out.append({"section": s, "template": section_template(s), "title": section_title(s),
                        "prose": prose if isinstance(prose, str) and prose.strip() else None})
    return out


def doc_index(items) -> List[Dict[str, str]]:
    return theory_view.doc_index(items)


def doc_meta(doc: Optional[Mapping]) -> Dict[str, Any]:
    """Header facts for the Review tab. `exists` False drives the empty state."""
    if not isinstance(doc, Mapping):
        return {"exists": False}
    summary = next((s.get("data") for s in doc.get("sections") or []
                    if isinstance(s, Mapping) and s.get("type") == "oracle_summary"
                    and isinstance(s.get("data"), Mapping)), None) or {}
    status = summary.get("status")
    return {"exists": True, "doc_id": doc.get("doc_id"), "created": theory_view.when(doc.get("created_at")),
            "created_iso": doc.get("created_at"), "stale": bool(doc.get("stale")),
            "stale_reason": doc.get("stale_reason") or ("the decklist or genre registry changed" if doc.get("stale") else None),
            "oracle_status": status, "oracle_tone": ORACLE_TONES.get(status, "neutral"),
            "oracle_label": {"ok": "Prose by oracle", "offline": "Explanation offline",
                             "invalid": "Explanation unavailable"}.get(status),
            "fingerprint": str(doc.get("fingerprint") or "")[:8] or None,
            "genre_registry_version": doc.get("genre_registry_version")}


# --- small formatters ---------------------------------------------------------------------

def pct(v: Any, nd: int = 0) -> str:
    try:
        return f"{float(v) * 100:.{nd}f}%"
    except (TypeError, ValueError):
        return "—"


def num(v: Any, nd: int = 1) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return str(int(f)) if f == int(f) and nd == 0 else f"{f:.{nd}f}"


def signed(v: Any) -> str:
    try:
        f = int(v)
    except (TypeError, ValueError):
        return "—"
    return f"+{f}" if f > 0 else str(f).replace("-", "−")


def label(key: Any) -> str:
    key = str(key or "")
    return WIN_LINE_LABELS.get(key) or key.replace("_", " ").strip().capitalize()


def role_tag(tag: Any) -> str:
    """'produces:sacrifice' -> 'produces sacrifice' (the registry's tag vocabulary, spaced)."""
    return str(tag or "").replace(":", " ").replace("_", " ")


# --- markdown (prose + unknown string sections) ----------------------------------------------

_BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+(.*)$")
_HEAD = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")


def md_blocks(text: Any) -> List[Dict[str, Any]]:
    """Tiny, safe markdown: headings, bullet/numbered lists, paragraphs. Output is plain data
    (Jinja autoescapes it), so oracle prose can never inject HTML."""
    if not isinstance(text, str) or not text.strip():
        return []
    blocks: List[Dict[str, Any]] = []
    para: List[str] = []
    items: List[str] = []

    def flush() -> None:
        nonlocal para, items
        if para:
            blocks.append({"kind": "p", "text": " ".join(para)})
        if items:
            blocks.append({"kind": "ul", "items": items})
        para, items = [], []

    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            flush()
            continue
        h, b = _HEAD.match(line), _BULLET.match(line)
        if h:
            flush()
            blocks.append({"kind": "h", "text": h.group(1)})
        elif b:
            if para:
                flush()
            items.append(b.group(1).strip())
        else:
            if items:
                flush()
            para.append(line.strip())
    flush()
    return blocks
