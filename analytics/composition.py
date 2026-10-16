"""
Composition vs template (AN-3).

Classifies every card of a deck into the Command Zone template rows and
compares the counts with the deck's template:

    Lands · Ramp · Card advantage · Targeted disruption · Mass disruption · Plan

Default template: The Command Zone ep. 658, "Commander Deckbuilding Template
for the New Era" (2025-02-18): 38 / 10 / 12 / 12 / 6 / 22 (totals 100).
ANALYTICS_SPEC said "plan ~25"; the episode's numbers leave 22 for the plan
once the 78 template slots are filled, so 22 it is.

Classification is deterministic (type line + oracle text). A card can count
in two rows (Vilis draws and removes). Plan = every card in none of the
five functional rows. Counts are by quantity, commander included.

A per-deck override lives in Redis at composition:template:{registry_id}
(JSON, only the overridden rows; the rest fall back to the default).
"""
from __future__ import annotations

import json
import re
from typing import Dict, Iterable, List, Mapping, Optional

TEMPLATE_NAME = "Command Zone 2025"
TEMPLATE_SOURCE = ("The Command Zone ep. 658, Commander Deckbuilding Template for the New Era (2025-02-18): "
                   "https://edhrec.com/articles/the-command-zone-commander-deckbuilding-template-for-the-new-era-"
                   "the-command-zone-658-mtg-edh-magic-gathering")
ROW_KEYS = ("lands", "ramp", "card_advantage", "targeted_disruption", "mass_disruption", "plan")
ROW_LABELS = {
    "lands": "Lands",
    "ramp": "Ramp",
    "card_advantage": "Card advantage",
    "targeted_disruption": "Targeted disruption",
    "mass_disruption": "Mass disruption",
    "plan": "Plan",
}
FUNCTIONAL_ROWS = ROW_KEYS[:-1]
DEFAULT_TEMPLATE: Dict[str, int] = {
    "lands": 38, "ramp": 10, "card_advantage": 12,
    "targeted_disruption": 12, "mass_disruption": 6, "plan": 22,
}
OVERRIDE_PREFIX = "composition:template:"
MAX_ROW = 100


class CompositionError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Template (default + per-deck override)
# ---------------------------------------------------------------------------


def _registry_id(deck: dict) -> str:
    if deck.get("registry_id"):
        return deck["registry_id"]
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


def override_key(deck: dict) -> str:
    return OVERRIDE_PREFIX + _registry_id(deck)


def _load_override(r, deck: dict) -> dict:
    raw = r.get(override_key(deck))
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return {k: int(v) for k, v in data.items() if k in DEFAULT_TEMPLATE}


def get_template(r, deck: dict) -> dict:
    override = _load_override(r, deck)
    out = {"name": TEMPLATE_NAME if not override else f"{TEMPLATE_NAME} (custom)",
           "source": TEMPLATE_SOURCE, **DEFAULT_TEMPLATE, **override,
           "overridden": bool(override), "overridden_rows": sorted(override),
           "default": dict(DEFAULT_TEMPLATE)}
    out["total"] = sum(out[k] for k in ROW_KEYS)
    return out


def validate_override(values: Mapping) -> Dict[str, int]:
    if not isinstance(values, Mapping) or not values:
        raise CompositionError("send an object with at least one row, e.g. {\"lands\": 37}")
    clean: Dict[str, int] = {}
    for k, v in values.items():
        if k not in DEFAULT_TEMPLATE:
            raise CompositionError(f"unknown row '{k}'; rows are {', '.join(ROW_KEYS)}")
        if isinstance(v, bool) or not isinstance(v, (int, float)) or int(v) != v:
            raise CompositionError(f"{k} must be a whole number")
        if not 0 <= int(v) <= MAX_ROW:
            raise CompositionError(f"{k} must be between 0 and {MAX_ROW}")
        clean[k] = int(v)
    return clean


def set_template_override(r, deck: dict, values: Mapping) -> dict:
    """Merge `values` into the deck's override. Rows equal to the default are
    dropped from the override, so saving the defaults is the same as a reset."""
    clean = validate_override(values)
    merged = {**_load_override(r, deck), **clean}
    merged = {k: v for k, v in merged.items() if DEFAULT_TEMPLATE[k] != v}
    if merged:
        r.set(override_key(deck), json.dumps(merged, sort_keys=True))
    else:
        r.delete(override_key(deck))
    return get_template(r, deck)


def reset_template(r, deck: dict) -> dict:
    r.delete(override_key(deck))
    return get_template(r, deck)


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------

_REMINDER = re.compile(r"\([^()]*\)")

_PERMANENT_WORDS = r"(?:creature|creatures|permanent|permanents|artifact|artifacts|enchantment|enchantments|" \
                   r"planeswalker|planeswalkers|nonland permanent|nonland permanents)"

RAMP_PATTERNS = [
    # mana abilities on non-land permanents ({T}: Add ..., {1}, {T}: Add ...)
    re.compile(r"(?:^|\n|\. )[^.\n]*\{t\}[^.\n:]*:\s*add\b"),
    re.compile(r"\badd (?:\{[wubrgcx0-9]\}|one mana|two mana|three mana|x mana|mana of any|an amount of)"),
    # land ramp that targets YOUR library ("search your library for ... land ... onto the battlefield")
    re.compile(r"search your library for [^.]*\blands?\b[^.]*onto the battlefield"),
    re.compile(r"you may put (?:a|an additional|up to \w+) lands? cards? [^.]*onto the battlefield"),
    re.compile(r"lands you control have \"\{t\}: add"),
    re.compile(r"\bcreate (?:a|an|\w+) treasure tokens?\b"),
]

CARD_ADVANTAGE_PATTERNS = [
    re.compile(r"\bdraws? (?:a card|two cards|three cards|\w+ cards|x cards|that many cards|cards equal)"),
    re.compile(r"\bdraw cards equal\b"),
    # impulse / Necropotence style: exile top card(s), you may play/put them into your hand
    re.compile(r"exile the top [^.]*card[^.]*\. (?:you may (?:play|cast)|put that card into your hand)"),
    re.compile(r"exile the top card of your library face down\. put that card into your hand"),
    re.compile(r"\bput (?:that card|those cards|them) into your hand at the beginning\b"),
    re.compile(r"\breturn (?:target|up to \w+ target) [^.]*cards? from your graveyard to your hand\b"),
]

TARGETED_PATTERNS = [
    re.compile(r"\b(?:destroy|exile) (?:target|up to (?:one|two|three|\w+) (?:other )?target)\b"),
    re.compile(r"\bcounter target\b"),
    re.compile(r"\breturn target (?:nonland )?(?:creature|permanent|artifact|enchantment|planeswalker)[^.]*"
               r"to its owner'?s hand\b"),
    re.compile(r"\bdeals? (?:\d+|x|that much) damage to (?:any target|target creature|target planeswalker|"
               r"target player or planeswalker|up to)"),
    re.compile(r"\btarget (?:creature|permanent) gets -\d+/-\d+|\btarget creature gets -x/-x"),
    re.compile(r"\b(?:each opponent|target player|target opponent) sacrifices (?:a|an|one|two)\b"),
    re.compile(r"\bgain control of target\b"),
    re.compile(r"\bfights? (?:target|up to one target)\b"),
]

MASS_PATTERNS = [
    re.compile(r"\b(?:destroy|exile) all (?:other )?(?:nonland |non-\w+ |\w+ )?" + _PERMANENT_WORDS),
    re.compile(r"\b(?:destroy|exile) each (?:other )?(?:nonland |\w+ )?(?:creature|permanent|artifact|enchantment)"),
    re.compile(r"\breturn all (?:nonland )?(?:creatures|permanents)[^.]* to their owners'? hands\b"),
    re.compile(r"\ball creatures get -\d+/-\d+|\beach creature gets -\d+/-\d+"),
    re.compile(r"\bdeals? (?:\d+|x|that much) damage to each (?:other )?creature\b"),
    re.compile(r"\bdeals? (?:\d+|x|that much) damage to each creature (?:that player|your opponents|an opponent)"),
    re.compile(r"\bthen sacrifices the rest\b"),
    re.compile(r"\beach (?:player|opponent) sacrifices (?:all|half|x)\b"),
]


def _norm(text: str) -> str:
    text = _REMINDER.sub("", text or "")
    text = text.replace("\u2014", "-").replace("\u2022", "\n").replace("’", "'")
    return text.lower()


def classify_card(name: str, card: Optional[dict]) -> List[str]:
    """Composition rows for one card (excluding `plan`). [] = plan card."""
    if not card:
        return []
    type_line = (card.get("type_line") or "").lower()
    front_type = type_line.split("//")[0]
    text = _norm(card.get("oracle_text") or "")
    if "land" in front_type and "creature" not in front_type:
        return ["lands"]
    rows = []
    if any(p.search(text) for p in RAMP_PATTERNS):
        rows.append("ramp")
    if any(p.search(text) for p in CARD_ADVANTAGE_PATTERNS):
        rows.append("card_advantage")
    if any(p.search(text) for p in TARGETED_PATTERNS):
        rows.append("targeted_disruption")
    if any(p.search(text) for p in MASS_PATTERNS):
        rows.append("mass_disruption")
    return rows


def delta_status(delta: int) -> str:
    """UX_SPEC D8: within ±1 ok; short by >= 2 short (amber); over by >= 2 over (neutral)."""
    if delta <= -2:
        return "short"
    if delta >= 2:
        return "over"
    return "ok"


def _deck_entries(deck: dict) -> List[tuple]:
    qty: Dict[str, int] = {}
    for c in deck.get("cards") or []:
        n = c.get("name")
        if n:
            qty[n] = qty.get(n, 0) + int(c.get("quantity") or 1)
    for n in deck.get("commanders") or []:
        qty.setdefault(n, 1)
    return sorted(qty.items())


def compute_composition(deck: dict, card_texts: Mapping[str, dict], template: Optional[dict] = None) -> dict:
    """Pure: deck + {name: {type_line, oracle_text, mana_cost}} -> composition."""
    tpl = template or {"name": TEMPLATE_NAME, "source": TEMPLATE_SOURCE, **DEFAULT_TEMPLATE,
                       "overridden": False, "overridden_rows": [], "default": dict(DEFAULT_TEMPLATE),
                       "total": sum(DEFAULT_TEMPLATE.values())}
    actual = {k: 0 for k in ROW_KEYS}
    by_card: Dict[str, List[str]] = {}
    cards_by_row: Dict[str, List[str]] = {k: [] for k in ROW_KEYS}
    unknown = []
    total = 0
    for name, q in _deck_entries(deck):
        c = card_texts.get(name)
        if not c:
            unknown.append(name)
        rows = classify_card(name, c) or ["plan"]
        by_card[name] = rows
        total += q
        for row in rows:
            actual[row] += q
            cards_by_row[row].append(name)
    rows_out = []
    for k in ROW_KEYS:
        delta = actual[k] - int(tpl[k])
        rows_out.append({"key": k, "label": ROW_LABELS[k], "actual": actual[k], "target": int(tpl[k]),
                         "delta": delta, "status": delta_status(delta), "cards": len(cards_by_row[k])})
    return {
        "template": tpl,
        "actual": actual,
        "rows": rows_out,
        "by_card": by_card,
        "cards_by_row": cards_by_row,
        "total_cards": total,
        "unknown_cards": unknown,
        "note": "A card can count in two rows. Plan = everything else.",
    }
