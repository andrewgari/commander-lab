"""Slice 3 fact pack (ARCHITECTURE.md §6): the deterministic code passes.

Pure module: no FastAPI, no SQLAlchemy, no src.server.infra imports
(AGENT_RULES.md §2). Everything here is a function of plain dicts, so the
same deck + same inputs always yields byte-identical output (canonical form:
``canonical(fact_pack)``).

The heavy deterministic math reuses the ported analytics modules
(``review_metrics.compute_review``: composition, curve/tempo, focus,
strong/stragglers, combos/engines, genre) and ``bracket_check`` (Game
Changers, mass land denial, stax-adjacent tutors, extra turns, two-card
combos). This module adds the passes compute_review does not cover:

- mana_math     land count, color sources, ramp density, curve by color
- bracket_salt  bracket findings (each flagged card carries a reason) + salt
- deck_spirit   pet cards / journal — "unavailable" + reason until the
                journal is migrated (§4: never guessed)
- inventory     have/buy metrics from card_instances
- profile       latest snapshot metrics + active overrides (pipeline inputs)

Interaction tags come from ``oracle_rules`` (deterministic oracle-text
rules) only — no Scryfall Tagger otags, no LLM pass — so the graph is
reproducible offline. Cards the rules leave untagged appear as untagged
graph nodes, exactly like a cache-miss in the old rebuild path.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence

from src.server.domain.analytics import bracket_check
from src.server.domain.analytics import composition as comp
from src.server.domain.analytics import review_metrics
from src.server.domain.analytics.interactions import engines as engines_mod
from src.server.domain.analytics.interactions import graph as graph_mod
from src.server.domain.analytics.interactions import oracle_rules

SCHEMA = "review_fact_pack/1"

GENRE_UNAVAILABLE = "genre registry has not been migrated to Postgres yet"
SPIRIT_UNAVAILABLE = "deck journal (pet cards, spirit notes) has not been migrated to Postgres yet"

BASIC_LAND_TYPES = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}
COLORS = ("W", "U", "B", "R", "G")
BASICS = set(BASIC_LAND_TYPES) | {"Wastes", "Snow-Covered Plains", "Snow-Covered Island",
                                  "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest"}

_ADD_RE = re.compile(r"\{([WUBRGC])\}")
_ANY_COLOR_RE = re.compile(r"add one mana of any (?:type|color)|add mana of any color|any color of mana")
_PIP_RE = re.compile(r"\{([^}]+)\}")


def name_key(name: str) -> str:
    return (name or "").split(" // ")[0].strip().lower()


# ---------------------------------------------------------------------------
# Input shaping
# ---------------------------------------------------------------------------

def deck_for_review(deck_row: Mapping, deck_cards: Sequence[Mapping]) -> dict:
    """The deck dict compute_review expects: commanders + main board only.

    Sideboard/maybe/considering are not part of the reviewed 100 (structural
    categories stay structural, AGENT_RULES.md §9).
    """
    cards = [
        {"name": c["name"], "quantity": int(c.get("qty") or 1)}
        for c in deck_cards
        if c.get("board") == "main" and c.get("name")
    ]
    cards.sort(key=lambda c: c["name"].lower())
    commanders = [c for c in (deck_row.get("commanders") or []) if c]
    return {
        "registry_id": deck_row.get("id"),
        "name": deck_row.get("name"),
        "commanders": commanders,
        "cards": cards,
    }


def texts_for_deck(deck: Mapping, cards_by_oracle: Mapping[str, Mapping],
                   deck_cards: Sequence[Mapping]) -> Dict[str, dict]:
    """{card name: scryfall facts} for every card in the reviewed deck."""
    oracle_by_name = {c["name"]: c["oracle_id"] for c in deck_cards if c.get("name")}
    texts: Dict[str, dict] = {}
    for name in list(oracle_by_name) + list(deck.get("commanders") or []):
        oid = oracle_by_name.get(name)
        # commanders may not have a commander-board row keyed under the same
        # name spelling; fall back to scanning the join for the commander name
        if oid is None:
            oid = next((c["oracle_id"] for c in deck_cards if c.get("name") == name), None)
        card = cards_by_oracle.get(oid) if oid else None
        if card:
            texts[name] = {
                "name": card.get("name"),
                "type_line": card.get("type_line") or "",
                "oracle_text": card.get("oracle_text") or "",
                "mana_cost": card.get("mana_cost") or "",
                "cmc": card.get("cmc") or 0,
                "colors": list(card.get("colors") or []),
                "color_identity": list(card.get("color_identity") or []),
                "keywords": list(card.get("keywords") or []),
            }
    return texts


def build_graph(texts: Mapping[str, Mapping], commanders: Sequence[str]) -> tuple:
    """Interaction graph tagged by oracle-text rules only (deterministic).

    Returns (graph, engines, tag_info). Cards the rules cannot tag are
    untagged nodes; tag_info records coverage so the fact pack can say so.
    """
    tags: Dict[str, dict] = {}
    untagged = []
    for name in sorted(texts):
        produces, consumes = oracle_rules.tag_from_oracle(dict(texts[name]))
        if produces or consumes:
            tags[name] = {"produces": list(produces), "consumes": list(consumes)}
        else:
            untagged.append(name)
    graph = graph_mod.build_graph(sorted(texts), tags, commanders=commanders)
    engines = engines_mod.name_engines(engines_mod.detect_engines(graph), cache={})
    info = {"tag_source": "oracle_rules", "tagged": len(tags), "untagged": len(untagged)}
    return graph, engines, info


# ---------------------------------------------------------------------------
# Mana math pass (land count, color sources, ramp density, curve by color)
# ---------------------------------------------------------------------------

def _colors_produced(text: str, type_line: str) -> set:
    """Colors a land/mana card can produce, from its oracle text + types."""
    out = set()
    tl = type_line.split("//")[0]
    for land_type, color in BASIC_LAND_TYPES.items():
        if land_type in tl:
            out.add(color)
    # only look at activated "add" clauses, not costs
    tx = (text or "").split("\n//")[0]
    if _ANY_COLOR_RE.search(tx):
        out.update(COLORS)
    else:
        for line in tx.split("\n"):
            if "add" not in line.lower():
                continue
            # symbols appearing after "add" on the same line
            idx = line.lower().find("add")
            out.update(_ADD_RE.findall(line[idx:]))
    return out


def _pips(mana_cost: str) -> List[str]:
    cost = (mana_cost or "").split("//")[0]
    return [s.upper() for s in _PIP_RE.findall(cost)]


def mana_math_section(deck: Mapping, texts: Mapping[str, Mapping],
                      composition: Mapping) -> dict:
    """Land count, color sources, ramp density, curve by color."""
    cmdr = set(deck.get("commanders") or [])
    facts = {}  # name -> (is_land, cmc, pips)
    for name, card in texts.items():
        if name in cmdr:
            continue
        tl = (card.get("type_line") or "").split("//")[0].lower()
        is_land = "land" in tl and "creature" not in tl
        mana_cost = card.get("mana_cost") or ""
        facts[name] = (is_land, review_metrics.cmc_of(mana_cost), _pips(mana_cost))

    qty = {c["name"]: int(c.get("quantity") or 1) for c in deck.get("cards") or []}
    lands = sum(qty.get(n, 1) for n, (is_land, _, _) in facts.items() if is_land)

    ramp_names = set()
    by_card = (composition or {}).get("by_card") or {}
    for n, rows in by_card.items():
        if "ramp" in rows:
            ramp_names.add(n)
    ramp = sum(qty.get(n, 1) for n in ramp_names if n in facts)

    color_sources: Dict[str, dict] = {}
    for color in COLORS:
        land_hits, other_hits = [], []
        for name, card in texts.items():
            if name in cmdr:
                continue
            produced = _colors_produced(card.get("oracle_text") or "", card.get("type_line") or "")
            if color not in produced:
                continue
            is_land = facts.get(name, (False, 0, []))[0]
            (land_hits if is_land else other_hits).append(name)
        color_sources[color] = {
            "lands": sum(qty.get(n, 1) for n in land_hits),
            "other": sum(qty.get(n, 1) for n in other_hits),
        }
        color_sources[color]["total"] = color_sources[color]["lands"] + color_sources[color]["other"]

    curve_by_color: Dict[str, Dict[str, int]] = {}
    for name, (is_land, cmc, pips) in facts.items():
        if is_land:
            continue
        needed = sorted({p for p in pips if p in COLORS})
        if not needed:
            needed = ["C"]
        for color in needed:
            bucket = curve_by_color.setdefault(color, {})
            key = str(min(cmc, 7)) + ("+" if cmc >= 7 else "")
            bucket[key] = bucket.get(key, 0) + qty.get(name, 1)

    nonland = sum(qty.get(n, 1) for n, (is_land, _, _) in facts.items() if not is_land)
    total = sum(qty.get(n, 1) for n in facts) or 1
    return {
        "lands": lands,
        "ramp": ramp,
        "ramp_density": round(ramp / max(nonland, 1), 4),
        "land_share": round(lands / total, 4),
        "color_sources": color_sources,
        "curve_by_color": {c: curve_by_color[c] for c in sorted(curve_by_color)},
        "why": ("Lands/ramp from the composition rows over Scryfall type lines; a color source is a "
                "land or mana card whose oracle text can add that color ('add one mana of any color' "
                "counts for all five). Curve by color buckets nonland cards by mana value under each "
                "colored pip they require (C = colorless-only)."),
    }


# ---------------------------------------------------------------------------
# Bracket / salt pass
# ---------------------------------------------------------------------------

def bracket_salt_section(deck: Mapping, texts: Mapping[str, Mapping],
                         combos_raw: Optional[Mapping], edhrec_page: Optional[Mapping]) -> dict:
    """Bracket findings (Game Changers, MLD, extra turns, two-card combos,
    tutors — each flagged with a reason) + EDHREC salt scores."""
    findings = bracket_check.check_deck_brackets(
        deck, combos={"included": list((combos_raw or {}).get("included") or [])}
        if combos_raw and not combos_raw.get("error") else None,
        oracle=texts,
    )
    f = findings.model_dump()

    page = edhrec_page or {}
    cards = page.get("cards") or {}
    in_deck = {name_key(n): n for n in [c["name"] for c in deck.get("cards") or []]}
    in_deck.update({name_key(c): c for c in deck.get("commanders") or []})
    salty = []
    for key, rec in cards.items():
        if key in in_deck and rec.get("salt") is not None:
            salty.append({"name": in_deck[key], "salt": round(float(rec["salt"]), 2)})
    salty.sort(key=lambda r: (-r["salt"], r["name"]))
    edhrec_ok = bool(page) and not page.get("error")
    salt = {
        "available": edhrec_ok,
        "reason": None if edhrec_ok else (f"EDHREC page unavailable: {(page or {}).get('error')}"
                                          if page else "EDHREC page not fetched"),
        "commander_salt": page.get("commander_salt") if edhrec_ok else None,
        "salty_cards": salty[:10],
        "source": "EDHREC salt scores",
    }
    return {
        "minimum_bracket": f.get("minimum_bracket"),
        "minimum_bracket_name": f.get("minimum_bracket_name"),
        "floors": f.get("floors") or [],
        "game_changers": f.get("game_changers"),
        "mass_land_denial": f.get("mass_land_denial"),
        "two_card_combos": f.get("two_card_combos"),
        "extra_turns": f.get("extra_turns"),
        "tutors": f.get("tutors"),
        "notes": f.get("notes") or [],
        "data": f.get("data") or {},
        "salt": salt,
        "why": ("Bracket floors from the checked-in bracket card lists + Commander Spellbook "
                "combos + oracle-text fallbacks (bracket_check); every flagged card keeps the "
                "rule that flagged it. Salt is EDHREC's community salt score."),
    }


# ---------------------------------------------------------------------------
# Smaller passes
# ---------------------------------------------------------------------------

def spirit_section(pets: Optional[Sequence[str]] = None,
                   journal: Optional[Mapping] = None) -> dict:
    """Deck spirit: pet cards are untouchable before candidate retrieval.

    v1: the journal lives in the legacy store, so spirit is explicitly
    unavailable (§4) rather than silently empty.
    """
    if journal:
        return {"available": True, "reason": None, "pets": sorted(pets or []),
                "journal_present": True}
    return {"available": False, "reason": SPIRIT_UNAVAILABLE, "pets": sorted(pets or []),
            "journal_present": False}


def inventory_section(have_buy_report: Mapping) -> dict:
    """Have/buy metrics from card_instances (Slice 1 math, unchanged)."""
    totals = (have_buy_report or {}).get("totals") or {}
    lines = (have_buy_report or {}).get("lines") or []
    buy_list = sorted(l["name"] for l in lines if (l.get("buy") or 0) > 0)
    return {
        "totals": totals,
        "buy_count": len(buy_list),
        "buy_list": buy_list,
        "why": "Copies owned / in mail / to buy from card_instances (Slice 1 have/buy math).",
    }


def profile_section(snapshot: Optional[Mapping], overrides: Optional[Mapping]) -> dict:
    """The intent/profile inputs the analysis pass sees (latest snapshot +
    active overrides; both Postgres, Slice 2)."""
    snap = snapshot or {}
    return {
        "available": bool(snapshot),
        "reason": None if snapshot else "no profile snapshot exists yet",
        "snapshot": {
            "created_at": snap.get("created_at"),
            "trigger": snap.get("trigger"),
            "metrics": snap.get("metrics") or {},
            "summary_text": snap.get("summary_text") or "",
        } if snapshot else None,
        "overrides": {k: (v.get("value") if isinstance(v, Mapping) else v)
                      for k, v in sorted((overrides or {}).items())},
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def build_fact_pack(deck: Mapping, texts: Mapping[str, Mapping], *,
                    combos: Optional[Mapping] = None,
                    combos_raw: Optional[Mapping] = None,
                    edhrec_page: Optional[Mapping] = None,
                    have_buy: Optional[Mapping] = None,
                    snapshot: Optional[Mapping] = None,
                    overrides: Optional[Mapping] = None,
                    pets: Optional[Sequence[str]] = None,
                    journal: Optional[Mapping] = None) -> dict:
    """Run every deterministic pass and assemble the fact pack.

    `combos` is the review-shaped spellbook dict (in_deck / one_away /
    error); `combos_raw` the raw find-my-combos payload for bracket checks.
    """
    commanders = [c for c in deck.get("commanders") or [] if c]
    graph, engines, tag_info = build_graph(texts, commanders)

    metrics_inputs = {
        "deck": deck,
        "texts": texts,
        "graph": graph,
        "engines": engines,
        "combos": combos or {"in_deck": [], "one_away": [], "error": "not fetched"},
        "genres": [],          # registry unmigrated: genre_section yields primary None
        "genre_version": None,
        "journal": journal,
        "pets": list(pets or []),
        "template": None,      # default Command Zone template
        "weak": None,          # computed inside compute_review from the graph
    }
    pack = review_metrics.compute_review(metrics_inputs)

    # Genre: with an empty registry compute_review reports primary None —
    # say WHY instead of rendering a bare null (AGENT_RULES.md §4).
    pack["genre"]["available"] = False
    pack["genre"]["reason"] = GENRE_UNAVAILABLE

    pack["mana_math"] = mana_math_section(deck, texts, pack.get("composition") or {})
    pack["bracket_salt"] = bracket_salt_section(deck, texts, combos_raw, edhrec_page)
    pack["deck_spirit"] = spirit_section(pets, journal)
    pack["inventory"] = inventory_section(have_buy or {})
    pack["profile"] = profile_section(snapshot, overrides)
    pack["fact_pack_schema"] = SCHEMA
    pack["inputs"]["interaction_tags"] = tag_info
    combos = combos or {}
    edhrec_page = edhrec_page or {}
    pack["inputs"]["combos_status"] = (
        "error: " + str(combos["error"]) if combos.get("error") else "ok"
    )
    pack["inputs"]["edhrec_status"] = (
        "error: " + str(edhrec_page["error"]) if edhrec_page.get("error")
        else ("ok" if edhrec_page else "not fetched")
    )
    return pack


def canonical(fact_pack: Mapping) -> str:
    """The serialization the determinism acceptance is checked against."""
    return review_metrics.dumps(fact_pack)
