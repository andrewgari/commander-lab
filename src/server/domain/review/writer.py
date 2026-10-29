"""Slice 3 LLM writer handoff (ARCHITECTURE.md §6): pure halves.

`build_writer_payload` trims the verified facts into the JSON the LLM sees
(numbers and names only — the LLM never computes, never invents).
`parse_reply` extracts + schema-checks the fenced JSON reply. `apply_reply`
turns a validated reply into explanation blocks. The network call itself
lives in src/server/infra/llm (boundary rule 3: domain/review receives LLM
output as data and validates it).
"""
from __future__ import annotations

import json
import re
from typing import Dict, List, Mapping, Optional, Tuple

from .doc import OFFLINE_TEXT, WRITER_SECTIONS, ExplanationBlock

MAX_SECTION_CHARS = 1200


class WriterReplyError(ValueError):
    """The LLM answered, but not with a parseable, schema-shaped reply."""


def _slim_adds_cuts(rows: List[Mapping], limit: int = 15) -> List[dict]:
    return [{"name": r.get("name"), "score": r.get("score"), "why": r.get("why")}
            for r in rows[:limit]]


def build_writer_payload(fact_pack: Mapping, analysis: Mapping) -> dict:
    """The verified facts the writer may reference — nothing else reaches it."""
    tempo = fact_pack.get("curve_tempo") or {}
    bracket = fact_pack.get("bracket_salt") or {}
    focus = fact_pack.get("focus") or {}
    payload = {
        "deck": fact_pack.get("deck") or {},
        "mana_math": {k: v for k, v in (fact_pack.get("mana_math") or {}).items() if k != "why"},
        "composition": {"rows": (fact_pack.get("composition") or {}).get("rows") or []},
        "curve_tempo": {
            "commander_on_curve": tempo.get("commander_on_curve"),
            "mana": {k: v for k, v in (tempo.get("mana") or {}).items()
                     if k in ("lands", "ramp", "nonland", "unspent_total_t2_t6",
                              "unspent_avg_t2_t6", "per_turn")},
            "active_share": tempo.get("active_share"),
            "durdle_tax": tempo.get("durdle_tax"),
            "flex": tempo.get("flex"),
        },
        "focus": {"win_lines": [{"type": l.get("type"), "cards": l.get("cards")}
                                for l in focus.get("win_lines") or []]},
        "strong_cards": [{"name": c.get("name"), "why": c.get("why")}
                         for c in (fact_pack.get("strong_cards") or {}).get("cards") or []],
        "stragglers": [{"name": c.get("name"), "why": c.get("why")}
                       for c in (fact_pack.get("stragglers") or {}).get("cards") or []],
        "combos_engines": {
            "combos_in_deck": [{"cards": c.get("cards"), "produces": c.get("produces")}
                               for c in (fact_pack.get("combos_engines") or {}).get("combos_in_deck") or []],
            "engines": [{"signature": e.get("signature"), "cards": e.get("cards")}
                        for e in (fact_pack.get("combos_engines") or {}).get("engines") or []],
        },
        "bracket_salt": {
            "minimum_bracket": bracket.get("minimum_bracket"),
            "minimum_bracket_name": bracket.get("minimum_bracket_name"),
            "floors": bracket.get("floors") or [],
            "salt": bracket.get("salt") or {},
        },
        "inventory": (fact_pack.get("inventory") or {}).get("totals") or {},
        "recommendations": {
            "adds": _slim_adds_cuts(list(analysis.get("adds") or [])),
            "cuts": _slim_adds_cuts(list(analysis.get("cuts") or [])),
        },
    }
    return payload


def allowed_card_names(fact_pack: Mapping, analysis: Mapping) -> List[str]:
    """Every card name the LLM may reference: deck cards + verified candidates."""
    names = set()
    deck = fact_pack.get("deck") or {}
    names.update(deck.get("commanders") or [])
    for section in (fact_pack.get("strong_cards") or {}).get("cards") or []:
        names.add(section.get("name"))
    for section in (fact_pack.get("stragglers") or {}).get("cards") or []:
        names.add(section.get("name"))
    for line in (fact_pack.get("focus") or {}).get("win_lines") or []:
        names.update(line.get("cards") or [])
    for combo in (fact_pack.get("combos_engines") or {}).get("combos_in_deck") or []:
        names.update(combo.get("cards") or [])
    for engine in (fact_pack.get("combos_engines") or {}).get("engines") or []:
        names.update(engine.get("cards") or [])
    for key in ("adds", "cuts", "candidates"):
        for row in analysis.get(key) or []:
            names.add(row.get("name"))
    for flag in fact_pack.get("card_flags") or []:
        names.add(flag.get("name"))
    names.discard(None)
    return sorted(names)


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def parse_reply(text: str) -> Dict[str, ExplanationBlock]:
    """Extract {section: ExplanationBlock} from the LLM's fenced JSON reply."""
    if not isinstance(text, str):
        raise WriterReplyError("writer reply is not text")
    m = _JSON_BLOCK.search(text)
    raw = m.group(1) if m else text.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WriterReplyError(f"writer reply is not valid JSON: {exc}") from exc
    sections = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections, dict):
        raise WriterReplyError("writer reply has no 'sections' object")
    out: Dict[str, ExplanationBlock] = {}
    for key, value in sections.items():
        if key not in WRITER_SECTIONS or not isinstance(value, dict):
            continue  # unknown sections are ignored, never stored
        block = ExplanationBlock(
            status="ok",
            text=str(value.get("text") or "")[:MAX_SECTION_CHARS],
            cards=[str(n) for n in value.get("cards") or [] if n],
        )
        if block.text.strip():
            out[key] = block
    if not out:
        raise WriterReplyError("writer reply has no usable sections")
    return out


def apply_reply(reply: Mapping[str, ExplanationBlock],
                verified: Mapping[str, Mapping]) -> Dict[str, dict]:
    """Merge a parsed reply with the verifier's per-section audit output."""
    out: Dict[str, dict] = {}
    for key, block in reply.items():
        extra = verified.get(key) or {}
        out[key] = {**block.model_dump(),
                    "cards": list(extra.get("cards", block.cards)),
                    "rejected_cards": list(extra.get("rejected_cards") or [])}
    return out


def offline_blocks(reason: str) -> Dict[str, dict]:
    """Every writer section, explicitly offline (REVIEW_SPEC core rule)."""
    return {key: ExplanationBlock(status="offline", text=OFFLINE_TEXT, reason=reason).model_dump()
            for key in WRITER_SECTIONS}
