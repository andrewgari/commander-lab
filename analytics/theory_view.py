"""Theory doc view-model (TH-3). Pure functions, no Redis.

Each section is rendered by one Jinja partial, `templates/partials/theory/<type>.html`.
A type with no partial (a future type, or oracle's free-form `notes`) uses `_fallback.html`,
which shows the section title and its data from `fallback_view()`. Old docs never break: a
known type whose data has the wrong shape also falls back instead of raising.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

PARTIAL_DIR = "partials/theory"
FALLBACK = f"{PARTIAL_DIR}/_fallback.html"
# Types with their own partial. Keep in sync with templates/partials/theory/.
RENDERERS = ("thesis_pillars", "decklist", "cuts", "buy_total", "commander_change")
SOURCE_TONES = {"kept": "neutral", "owned": "ok", "buy": "signal"}
SOURCE_LABELS = {"kept": "kept", "owned": "owned", "buy": "buy"}
SALT_TONES = {"low": "neutral", "medium": "signal", "high": "danger"}


def _shape_ok(type_: str, data: Any) -> bool:
    """A known partial only runs on the data shape it expects."""
    if not isinstance(data, dict):
        return False
    if type_ == "thesis_pillars":
        return isinstance(data.get("thesis", ""), str) and isinstance(data.get("pillars", []), list)
    if type_ == "decklist":
        groups = data.get("groups")
        return isinstance(groups, list) and all(isinstance(g, dict) and isinstance(g.get("cards", []), list)
                                                for g in groups)
    if type_ in ("cuts",):
        return isinstance(data.get("cards", []), list)
    if type_ == "buy_total":
        return isinstance(data.get("total_usd"), (int, float))
    if type_ == "commander_change":
        return isinstance(data.get("new_commanders", []), list)
    return True


def section_template(section: Mapping) -> str:
    """Template path for one section: its own partial, or the generic fallback."""
    type_ = section.get("type") if isinstance(section, Mapping) else None
    if type_ in RENDERERS and _shape_ok(type_, section.get("data")):
        return f"{PARTIAL_DIR}/{type_}.html"
    return FALLBACK


def _label(key: str) -> str:
    return str(key).replace("_", " ").strip().capitalize()


def _scalar(v: Any) -> bool:
    return v is None or isinstance(v, (str, int, float, bool))


def _text(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "yes" if v else "no"
    return str(v)


def fallback_view(data: Any) -> Dict[str, Any]:
    """How the generic renderer shows a section's data.

    kind "text": {"paragraphs": [str]}   (a string; blank lines split paragraphs)
    kind "kv":   {"pairs": [(label, str)]} (a flat dict of scalars, e.g. oracle's `notes`)
    kind "list": {"items": [str]}       (a list of scalars)
    kind "json": {"text": str}          (anything else, pretty-printed in a <pre>)
    kind "empty"
    """
    if data is None or data == "" or data == {} or data == []:
        return {"kind": "empty"}
    if isinstance(data, str):
        paras = [p.strip() for p in data.replace("\r\n", "\n").split("\n\n") if p.strip()]
        return {"kind": "text", "paragraphs": paras or [data]}
    if _scalar(data):
        return {"kind": "text", "paragraphs": [_text(data)]}
    if isinstance(data, Mapping) and all(_scalar(v) for v in data.values()):
        return {"kind": "kv", "pairs": [(_label(k), _text(v)) for k, v in data.items()]}
    if isinstance(data, list) and all(_scalar(v) for v in data):
        return {"kind": "list", "items": [_text(v) for v in data]}
    return {"kind": "json", "text": json.dumps(data, indent=2, ensure_ascii=False, default=str)}


def money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "—"


def when(iso: Optional[str]) -> str:
    """'2026-10-12 22:10 UTC' (the page localises <time> elements client-side)."""
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return iso or "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def commander_line(summary: Mapping) -> str:
    """'Kept' or 'Swapped → New Commander' for the Ideas list."""
    if summary.get("commander_changed"):
        new = " + ".join(summary.get("commanders") or []) or "new commander"
        return f"Swapped → {new}"
    return "Kept"


def group_count(group: Mapping) -> int:
    return sum(int(c.get("qty") or 1) for c in group.get("cards") or [] if isinstance(c, Mapping))


def doc_meta(doc: Mapping) -> Dict[str, Any]:
    """Header facts for the doc page, from the app's verification (never oracle's claims)."""
    v = doc.get("verification") or {}
    counts = v.get("counts") or {}
    return {"theme": doc.get("theme") or "Untitled theory", "created": when(doc.get("created_at")),
            "created_iso": doc.get("created_at"), "mode": doc.get("commander_mode") or "keep",
            "commander_changed": bool(v.get("commander_changed")), "commanders": v.get("commanders") or [],
            "counts": counts, "buy_total": v.get("buy_total_usd"),
            "corrections": [c for c in v.get("corrections") or [] if isinstance(c, Mapping)]}


def sections(doc: Mapping) -> List[Dict[str, Any]]:
    """[{section, template}] in the doc's order. Non-dict entries are skipped."""
    out = []
    for s in doc.get("sections") or []:
        if isinstance(s, Mapping):
            out.append({"section": s, "template": section_template(s)})
    return out
