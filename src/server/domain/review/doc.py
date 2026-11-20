"""Slice 3 stored review doc (ARCHITECTURE.md §4.2/§6): typed sections,
the Theory-doc pattern.

The doc is what both renderers read: the styled document and the dashboard
panels render from the same stored row's ``fact_pack`` + ``doc``, so they
can never disagree. Each fact section's ``data`` IS the corresponding fact
pack subsection (same numbers by construction) plus an ``explanation``
block written by the LLM — or the explicit "explanation offline" marker
when the LLM is down (REVIEW_SPEC core rule).

Pure module: pydantic models + assembly from already-verified data.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"
OFFLINE_TEXT = "explanation offline"

# Section order in the rendered document. Every key maps 1:1 to a fact pack
# subsection except `overview` (deck meta) and `recommendations` (analysis).
SECTION_ORDER = (
    "overview", "mana_math", "composition", "curve_tempo", "focus",
    "strong_cards", "stragglers", "combos_engines", "bracket_salt",
    "genre", "deck_spirit", "inventory", "recommendations", "profile",
)
SECTION_TITLES = {
    "overview": "Overview",
    "mana_math": "Mana",
    "composition": "Composition",
    "curve_tempo": "Curve & tempo",
    "focus": "How this deck wins",
    "strong_cards": "Strong cards",
    "stragglers": "Stragglers",
    "combos_engines": "Combos & engines",
    "bracket_salt": "Bracket & salt",
    "genre": "Genre",
    "deck_spirit": "Deck spirit",
    "inventory": "Have / buy",
    "recommendations": "Recommendations",
    "profile": "Profile inputs",
}
# Sections the LLM writer explains (the rest are facts-only).
WRITER_SECTIONS = (
    "overview", "mana_math", "composition", "curve_tempo", "focus",
    "strong_cards", "stragglers", "combos_engines", "bracket_salt",
    "recommendations",
)


class ExplanationBlock(BaseModel):
    """One section's LLM prose. `cards` = every card the prose references
    (validated against Scryfall + the verified input set by the verifier)."""
    model_config = ConfigDict(extra="allow")

    status: str = "offline"  # ok | offline
    text: str = OFFLINE_TEXT
    cards: List[str] = Field(default_factory=list)
    reason: Optional[str] = None


class ReviewSection(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1)
    title: str = ""
    data: Any = None


class ReviewDoc(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = SCHEMA_VERSION
    deck_id: str
    fingerprint: str
    sections: List[ReviewSection] = Field(default_factory=list)
    verification: Dict[str, Any] = Field(default_factory=dict)

    def section(self, type_: str) -> Optional[ReviewSection]:
        return next((s for s in self.sections if s.type == type_), None)


def offline_explanation(reason: str) -> dict:
    return ExplanationBlock(status="offline", text=OFFLINE_TEXT, reason=reason).model_dump()


def build_doc(deck_id: str, fingerprint: str, fact_pack: Mapping, analysis: Mapping,
              explanations: Optional[Mapping[str, Mapping]] = None,
              verification: Optional[Mapping] = None) -> dict:
    """Assemble the typed-section doc. Deterministic in its inputs.

    `explanations`: {section key: ExplanationBlock-shaped dict}; missing keys
    get the explicit offline marker so an absent LLM never renders as empty.
    """
    explanations = explanations or {}
    sections: List[ReviewSection] = []
    for key in SECTION_ORDER:
        if key == "overview":
            deck = fact_pack.get("deck") or {}
            data: Any = {
                "name": deck.get("name"),
                "commanders": list(deck.get("commanders") or []),
                "fingerprint": fact_pack.get("fingerprint"),
            }
        elif key == "recommendations":
            data = {
                "adds": list(analysis.get("adds") or []),
                "cuts": list(analysis.get("cuts") or []),
                "protected_pets": list(analysis.get("protected_pets") or []),
                "why": analysis.get("why"),
            }
        else:
            data = fact_pack.get(key)
            if data is None:
                continue
        if isinstance(data, Mapping):
            data = dict(data)
            if key in WRITER_SECTIONS:
                data["explanation"] = dict(
                    explanations.get(key) or offline_explanation("writer not run")
                )
        sections.append(ReviewSection(
            type=key, title=SECTION_TITLES.get(key, key.replace("_", " ").title()), data=data,
        ))
    return ReviewDoc(
        deck_id=deck_id, fingerprint=fingerprint, sections=sections,
        verification=dict(verification or {}),
    ).model_dump()


def dashboard_panels(fact_pack: Mapping, analysis: Mapping) -> List[dict]:
    """The dashboard's stat panels — computed from the SAME fact pack the
    document renders, so the numbers can never disagree. Pure formatting;
    no new math beyond reshaping existing numbers."""
    mana = fact_pack.get("mana_math") or {}
    tempo = fact_pack.get("curve_tempo") or {}
    bracket = fact_pack.get("bracket_salt") or {}
    inv = fact_pack.get("inventory") or {}
    focus = fact_pack.get("focus") or {}
    strong = fact_pack.get("strong_cards") or {}
    strag = fact_pack.get("stragglers") or {}
    combos = fact_pack.get("combos_engines") or {}
    cmdr_curve = tempo.get("commander_on_curve") or {}
    totals = inv.get("totals") or {}
    return [
        {"key": "lands", "label": "Lands", "value": mana.get("lands")},
        {"key": "ramp", "label": "Ramp", "value": mana.get("ramp")},
        {"key": "commander_on_curve", "label": "Commander on curve",
         "value": cmdr_curve.get("p_with_ramp"),
         "unavailable": None if cmdr_curve else "no commander mana value"},
        {"key": "unspent_avg", "label": "Avg unspent mana T2–T6",
         "value": (tempo.get("mana") or {}).get("unspent_avg_t2_t6")},
        {"key": "active_share", "label": "Active cards",
         "value": (tempo.get("active_share") or {}).get("value")},
        {"key": "durdle_tax", "label": "Durdle tax",
         "value": (tempo.get("durdle_tax") or {}).get("value")},
        {"key": "min_bracket", "label": "Minimum bracket",
         "value": bracket.get("minimum_bracket"),
         "note": bracket.get("minimum_bracket_name")},
        {"key": "win_lines", "label": "Win lines",
         "value": len(focus.get("win_lines") or [])},
        {"key": "strong", "label": "Strong cards", "value": strong.get("count")},
        {"key": "stragglers", "label": "Stragglers", "value": strag.get("count")},
        {"key": "combos", "label": "Combos in deck",
         "value": len(combos.get("combos_in_deck") or []),
         "unavailable": None if combos.get("combos_available") else combos.get("combos_error")},
        {"key": "have_pct", "label": "Owned",
         "value": totals.get("have_pct"), "note": f"{totals.get('buy', 0)} to buy"},
        {"key": "adds", "label": "Suggested adds", "value": len(analysis.get("adds") or [])},
        {"key": "cuts", "label": "Suggested cuts", "value": len(analysis.get("cuts") or [])},
    ]
