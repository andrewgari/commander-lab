"""Known-staple-card -> theme vocabulary registry (Vocab cleanup PR5).

``KNOWN_STAPLE_THEMES`` (analytics/engine.py) used to be a hardcoded dict
mapping a handful of staple card names to the theme/mechanic tags they
should credit during offline heuristic theme alignment (e.g. "Hardened
Scales" -> "counters"). It is now a Redis-backed, user-editable registry:
Andrew can add, edit, and delete entries from the UI, with no hardcoded,
undeletable rows.

Design notes (mirrors autotag/tag_registry.py's seeding contract):

* BUILTIN_STAPLE_THEMES is the original hardcoded mapping. It is used ONLY
  to seed the registry the first time it's read, so existing deck scoring
  doesn't regress on day one. After that first seed this module never
  reseeds -- a user-deleted entry stays deleted, forever, even across
  restarts.
* analytics/engine.py keeps a module-level ``KNOWN_STAPLE_THEMES`` dict that
  defaults to ``BUILTIN_STAPLE_THEMES`` (so importing/using the engine
  without a Redis client, e.g. in unit tests, behaves exactly as before)
  and gets refreshed in place by ``engine.load_staple_theme_vocab(r)``,
  which callers with a Redis client (app.py) invoke before running the
  engine against a live deck.

Redis layout:
    staple_themes          JSON dict {card_name: [theme, ...]}
    staple_themes:seeded   presence flag; set once, never cleared
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

REGISTRY_KEY = "staple_themes"
SEEDED_KEY = "staple_themes:seeded"
MAX_CARD_NAME = 200
MAX_THEME = 60
MAX_THEMES_PER_CARD = 20

# The original hardcoded mapping (analytics/engine.py, pre-PR5). Used only to
# seed the registry the first time it's read; never consulted again afterwards.
BUILTIN_STAPLE_THEMES: Dict[str, List[str]] = {
    "Hardened Scales": ["counters", "+1/+1 counters", "midrange"],
    "Evolution Sage": ["counters", "+1/+1 counters", "proliferate", "landfall", "midrange"],
    "Doubling Season": ["counters", "+1/+1 counters", "tokens", "proliferate", "midrange"],
    "Conclave Mentor": ["counters", "+1/+1 counters", "lifegain", "midrange"],
    "The Ozolith": ["counters", "+1/+1 counters", "midrange"],
    "Inexorable Tide": ["counters", "+1/+1 counters", "proliferate", "spellslinger"],
    "Deepglow Skate": ["counters", "+1/+1 counters", "proliferate", "midrange"],
    "Atraxa, Praetors' Voice": ["counters", "+1/+1 counters", "proliferate", "midrange"],
    "Viscera Seer": ["aristocrats", "sacrifice"],
    "Blood Artist": ["aristocrats", "sacrifice", "drain"],
    "Zulaport Cutthroat": ["aristocrats", "sacrifice", "drain"],
    "Carrion Feeder": ["aristocrats", "sacrifice"],
    "Young Pyromancer": ["tokens", "spellslinger"],
    "Lingering Souls": ["tokens", "graveyard"],
    "Goblin Chieftain": ["tribal", "aggro"],
    "Goblin Warchief": ["tribal", "aggro"],
    "Goblin King": ["tribal", "aggro"],
    "Goblin Piledriver": ["tribal", "aggro"],
    "Dragon Fodder": ["tokens", "tribal"],
    "Krenko's Command": ["tokens", "tribal"],
    "Hordeling Outburst": ["tokens", "tribal"],
    "Purphoros, God of the Forge": ["tokens", "aggro"],
    "Impact Tremors": ["tokens", "aggro"],
    "Winter Orb": ["stax"],
    "Static Orb": ["stax"],
    "Armageddon": ["stax", "mass land destruction"],
    "Stasis": ["stax"],
    "Drannith Magistrate": ["stax"],
    "Opposition Agent": ["stax"],
    "Thassa's Oracle": ["combo"],
    "Demonic Consultation": ["combo"],
    "Isochron Scepter": ["combo", "spellslinger"],
    "Dramatic Reversal": ["combo", "spellslinger"],
    "Swords to Plowshares": ["control", "removal", "midrange"],
    "Counterspell": ["control", "counterspell", "midrange"],
    "Cyclonic Rift": ["control", "board wipe", "midrange"],
    "Rhystic Study": ["control", "card draw", "midrange"],
    "Beast Within": ["control", "removal", "midrange"],
}


class StapleThemeError(ValueError):
    """Invalid staple-theme payload (HTTP 400)."""


class StapleThemeNotFound(KeyError):
    """No registry entry for that card name (HTTP 404)."""


_WRITE_LOCK = threading.RLock()


def _locked(fn):
    def wrapper(*a, **kw):
        with _WRITE_LOCK:
            return fn(*a, **kw)
    return wrapper


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_seeded(r) -> None:
    """Seed the registry from BUILTIN_STAPLE_THEMES exactly once; never again."""
    if r.get(SEEDED_KEY) is not None:
        return
    with _WRITE_LOCK:
        if r.get(SEEDED_KEY) is not None:
            return
        if r.get(REGISTRY_KEY) is None:
            r.set(REGISTRY_KEY, json.dumps(BUILTIN_STAPLE_THEMES, ensure_ascii=False))
        r.set(SEEDED_KEY, "1")


def _load(r) -> Dict[str, List[str]]:
    ensure_seeded(r)
    try:
        data = json.loads(r.get(REGISTRY_KEY) or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out: Dict[str, List[str]] = {}
    for card, themes in data.items():
        if isinstance(card, str) and isinstance(themes, list):
            out[card] = [str(t) for t in themes if isinstance(t, (str, int, float))]
    return out


def _save(r, data: Dict[str, List[str]]) -> None:
    r.set(REGISTRY_KEY, json.dumps(data, ensure_ascii=False))


def get_map(r) -> Dict[str, List[str]]:
    """{card_name: [theme, ...]} for every registry entry."""
    return _load(r)


def list_entries(r) -> List[dict]:
    """All entries, sorted by card name, as {card, themes, updated_at}."""
    data = _load(r)
    meta = _meta(r)
    out = [{"card": card, "themes": themes, "updated_at": meta.get(card)} for card, themes in data.items()]
    return sorted(out, key=lambda e: e["card"].lower())


def _meta_key() -> str:
    return "staple_themes:meta"


def _meta(r) -> Dict[str, str]:
    try:
        data = json.loads(r.get(_meta_key()) or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_meta(r, meta: Dict[str, str]) -> None:
    r.set(_meta_key(), json.dumps(meta, ensure_ascii=False))


def _clean_card(card: Any) -> str:
    if not isinstance(card, str) or not card.strip():
        raise StapleThemeError("A staple theme entry needs a card name.")
    card = " ".join(card.split())
    if len(card) > MAX_CARD_NAME:
        raise StapleThemeError(f"Card name is longer than {MAX_CARD_NAME} characters.")
    return card


def _clean_themes(themes: Any) -> List[str]:
    if not isinstance(themes, list) or not themes:
        raise StapleThemeError("themes must be a non-empty list of strings.")
    if len(themes) > MAX_THEMES_PER_CARD:
        raise StapleThemeError(f"A card can have at most {MAX_THEMES_PER_CARD} themes.")
    out, seen = [], set()
    for t in themes:
        if not isinstance(t, str) or not t.strip():
            raise StapleThemeError("Each theme must be non-empty text.")
        clean = " ".join(t.split())
        if len(clean) > MAX_THEME:
            raise StapleThemeError(f"Theme is longer than {MAX_THEME} characters.")
        key = clean.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(clean)
    return out


@_locked
def set_entry(r, card: Any, themes: Any) -> dict:
    """Create or replace the theme list for *card*."""
    clean_card = _clean_card(card)
    clean_themes = _clean_themes(themes)
    data = _load(r)
    data[clean_card] = clean_themes
    _save(r, data)
    meta = _meta(r)
    meta[clean_card] = _now()
    _save_meta(r, meta)
    return {"card": clean_card, "themes": clean_themes, "updated_at": meta[clean_card]}


@_locked
def delete_entry(r, card: str) -> dict:
    """Removes *card* from the registry. A deleted entry stays deleted (never reseeded)."""
    data = _load(r)
    if card not in data:
        raise StapleThemeNotFound(card)
    themes = data.pop(card)
    _save(r, data)
    meta = _meta(r)
    meta.pop(card, None)
    _save_meta(r, meta)
    return {"card": card, "themes": themes}
