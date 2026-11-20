"""The guided primer form: field definitions (the UI renders these) and server-side cleaning."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping

MAX_FIELD = 1500
MAX_LIST_ITEMS = 12

# kind: "text" | "list"; required fields must be filled before a primer can be generated.
PRIMER_FIELDS: List[dict] = [
    {"key": "theme", "label": "What is the deck about?", "kind": "text", "required": True,
     "help": "One or two sentences: the idea, the fantasy, the flavor."},
    {"key": "game_plan", "label": "How does it win?", "kind": "text", "required": True,
     "help": "The turn-by-turn plan: early setup, midgame engine, how the game ends."},
    {"key": "key_cards", "label": "Key cards", "kind": "list", "required": False,
     "help": "Cards the deck leans on. Names only."},
    {"key": "combos", "label": "Combos and win lines", "kind": "text", "required": False, "help": ""},
    {"key": "mulligan", "label": "Mulligan and opening hands", "kind": "text", "required": False, "help": ""},
    {"key": "play_tips", "label": "Piloting tips", "kind": "text", "required": False, "help": ""},
    {"key": "weaknesses", "label": "Weaknesses and bad matchups", "kind": "text", "required": False, "help": ""},
    {"key": "budget_notes", "label": "Budget, upgrades and swaps", "kind": "text", "required": False, "help": ""},
    {"key": "tone", "label": "Tone", "kind": "text", "required": False,
     "help": "e.g. casual and funny, or tight and technical."},
]
_BY_KEY = {f["key"]: f for f in PRIMER_FIELDS}


class FormError(ValueError):
    pass


def _collapse(s: str) -> str:
    return " ".join(s.split())


def clean_form(raw: Any) -> Dict[str, Any]:
    """Drop unknown keys, trim, bound lengths, drop empties. Raises FormError on wrong types."""
    if not isinstance(raw, Mapping):
        raise FormError("form must be an object")
    out: Dict[str, Any] = {}
    for key, spec in _BY_KEY.items():
        val = raw.get(key)
        if val is None:
            continue
        if spec["kind"] == "list":
            if isinstance(val, str):
                val = [p for p in val.replace("\n", ",").split(",")]
            if not isinstance(val, (list, tuple)) or not all(isinstance(x, str) for x in val):
                raise FormError(f"{key} must be a list of strings")
            items = [_collapse(x)[:120] for x in val if _collapse(x)]
            if items:
                out[key] = items[:MAX_LIST_ITEMS]
        else:
            if not isinstance(val, str):
                raise FormError(f"{key} must be text")
            # keep paragraph breaks, collapse inline whitespace
            text = "\n".join(_collapse(line) for line in val.strip().splitlines()).strip()
            if text:
                out[key] = text[:MAX_FIELD]
    return out


def missing_required(form: Mapping[str, Any]) -> List[str]:
    return [f["key"] for f in PRIMER_FIELDS if f["required"] and not form.get(f["key"])]
