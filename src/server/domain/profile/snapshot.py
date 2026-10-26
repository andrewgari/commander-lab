"""Compute a profile_snapshots row from an imported deck + the collection.

Pure, deterministic logic (AGENT_RULES.md §5: numbers and labels are
deterministic code — the plain-language summary is a template over computed
metrics, never LLM output). No FastAPI, no SQLAlchemy, no infra imports.

Slice 1 metrics (ARCHITECTURE.md §4.2: "curve prefs, archetype mix"):
- curve: mana-value histogram over non-land spells (commander + main,
  qty-weighted, buckets 0..6 and 7+), average non-land MV, land count.
  Same bucketing as the legacy deck page (min(floor(cmc), 7)).
- archetype_mix: share of the deck's thematic categories (deck_cards.category,
  commander + main, qty-weighted; cards without a category are
  "Uncategorized").
- ownership: the have/buy totals for the deck (domain.inventory.have_buy).

Cards whose reference facts are missing (no type_line/cmc row) are counted
under metrics["unavailable"] with a reason (AGENT_RULES.md §4) and excluded
from curve math — never treated as MV 0.
"""
from __future__ import annotations

from typing import Optional

UNCATEGORIZED = "Uncategorized"

_COLOR_GROUP_NAMES = {
    (): "Colorless",
    ("W",): "Mono-White", ("U",): "Mono-Blue", ("B",): "Mono-Black",
    ("R",): "Mono-Red", ("G",): "Mono-Green",
    ("W", "U"): "Azorius", ("U", "B"): "Dimir", ("B", "R"): "Rakdos",
    ("R", "G"): "Gruul", ("G", "W"): "Selesnya", ("W", "B"): "Orzhov",
    ("U", "R"): "Izzet", ("B", "G"): "Golgari", ("R", "W"): "Boros",
    ("G", "U"): "Simic",
    ("W", "U", "B"): "Esper", ("U", "B", "R"): "Grixis",
    ("B", "R", "G"): "Jund", ("R", "G", "W"): "Naya",
    ("G", "W", "U"): "Bant", ("W", "B", "R"): "Mardu",
    ("U", "R", "G"): "Temur", ("B", "G", "W"): "Abzan",
    ("R", "W", "U"): "Jeskai", ("G", "U", "B"): "Sultai",
}


def color_group_name(color_identity: list) -> str:
    key = tuple(sorted(c for c in (color_identity or []) if c in "WUBRG"))
    if key in _COLOR_GROUP_NAMES:
        return _COLOR_GROUP_NAMES[key]
    return {4: "Four-color", 5: "Five-color"}.get(len(key), "Colorless")


def _is_land(card: dict) -> bool:
    return "Land" in (card.get("type_line") or "")


def compute_curve(deck_cards: list) -> dict:
    """Mana-value curve over commander + main, qty-weighted."""
    buckets = [0] * 8  # 0..6, 7+
    spells = lands = 0
    mv_total = 0.0
    missing = []
    for card in deck_cards:
        if card.get("board") not in ("commander", "main"):
            continue
        qty = int(card.get("qty") or 1)
        if card.get("type_line") is None or card.get("cmc") is None:
            missing.append(card.get("name") or card.get("oracle_id"))
            continue
        if _is_land(card):
            lands += qty
            continue
        spells += qty
        cmc = float(card.get("cmc") or 0)
        mv_total += cmc * qty
        buckets[min(int(cmc), 7)] += qty
    return {
        "buckets": buckets,  # index 7 = "7+"
        "spells": spells,
        "lands": lands,
        "avg_mv_nonland": round(mv_total / spells, 2) if spells else None,
        "unavailable": {
            "count": len(missing),
            "reason": "card reference data missing" if missing else None,
        },
    }


def compute_archetype_mix(deck_cards: list) -> dict:
    """Thematic category mix over commander + main, qty-weighted."""
    counts: dict = {}
    total = 0
    for card in deck_cards:
        if card.get("board") not in ("commander", "main"):
            continue
        cat = card.get("category") or UNCATEGORIZED
        qty = int(card.get("qty") or 1)
        counts[cat] = counts.get(cat, 0) + qty
        total += qty
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "total_cards": total,
        "categories": [
            {"category": cat, "cards": n, "share": round(n / total, 4) if total else 0}
            for cat, n in ranked
        ],
        "top": [cat for cat, _ in ranked[:5] if cat != UNCATEGORIZED],
    }


def build_power_sentence(power: Optional[dict]) -> str:
    """One plain-language sentence from the power metric (or why it's missing)."""
    if not power:
        return ""
    if not power.get("available"):
        return f"Power level unavailable: {power.get('reason')}."
    text = (
        f"It plays around bracket {power['estimated_bracket']}"
        f" ({power.get('bracket_name')}), estimated power level"
        f" {power['power_level']:g}/10"
    )
    if power.get("game_changers"):
        n = power["game_changers"]
        text += f" with {n} Game Changer{'s' if n != 1 else ''}"
    text += "."
    return text


def build_summary(deck: dict, curve: dict, mix: dict, ownership: dict,
                  power: Optional[dict] = None) -> str:
    """Deterministic plain-language summary from computed metrics."""
    name = deck.get("name") or "This deck"
    colors = color_group_name(deck.get("color_identity") or [])
    commanders = deck.get("commanders") or []
    led_by = f", led by {', '.join(commanders)}" if commanders else ""
    total = (curve.get("spells") or 0) + (curve.get("lands") or 0)
    parts = [f"{name} is a {colors} Commander deck{led_by} with {total} cards"]
    if curve.get("lands"):
        parts[-1] += f" ({curve['lands']} lands)"
    parts[-1] += "."
    avg = curve.get("avg_mv_nonland")
    if avg is not None:
        parts.append(f"Its average mana value is {avg:.2f} (non-land).")
    top = [c for c in mix.get("categories", [])[:3]]
    if top:
        plan = ", ".join(f"{c['category']} ({c['cards']})" for c in top)
        parts.append(f"Its game plan leans on {plan}.")
    totals = (ownership or {}).get("totals") or {}
    need = totals.get("need")
    if need:
        have, pending, buy = totals.get("have", 0), totals.get("pending", 0), totals.get("buy", 0)
        own = f"You own {have} of {need} cards"
        if pending and buy:
            own += f"; {pending} on the way, {buy} left to buy."
        elif pending:
            own += f"; {pending} on the way."
        elif buy:
            own += f"; {buy} left to buy."
        else:
            own += " — the deck is fully assembled."
        parts.append(own)
    power_sentence = build_power_sentence(power)
    if power_sentence:
        parts.append(power_sentence)
    if curve.get("unavailable", {}).get("count"):
        parts.append(
            f"Curve data unavailable for {curve['unavailable']['count']} card(s): "
            f"{curve['unavailable']['reason']}."
        )
    return " ".join(parts)


def build_snapshot(deck: dict, deck_cards: list, have_buy: dict, collection: dict,
                   power: Optional[dict] = None) -> dict:
    """Assemble the (inputs, metrics, summary_text) triple for one snapshot."""
    curve = compute_curve(deck_cards)
    mix = compute_archetype_mix(deck_cards)
    metrics = {
        "curve": curve,
        "archetype_mix": mix,
        "ownership": have_buy.get("totals", {}),
        "power": power or {"available": False, "reason": "power not computed"},
    }
    inputs = {
        "deck_id": deck.get("id"),
        "archidekt_id": deck.get("archidekt_id"),
        "deck_name": deck.get("name"),
        "fingerprint": deck.get("fingerprint"),
        "collection": collection,
    }
    return {
        "inputs": inputs,
        "metrics": metrics,
        "summary_text": build_summary(deck, curve, mix, have_buy, power),
    }
