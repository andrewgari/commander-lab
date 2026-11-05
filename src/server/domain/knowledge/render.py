"""Generated knowledge-pack sections. Pure: no I/O beyond reading the packaged static files.

Sections that already exist as code tables (brackets, Karsten land math, the deck template) are
rendered from that code so the prompt can never drift from the app. Numbers are copied, never
computed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.server.domain.analytics import brackets as br
from src.server.domain.analytics import composition as comp
from src.server.domain.analytics import fact_sections as fs

_HERE = Path(__file__).resolve().parent
STATIC_DIR = _HERE / "static"
TASKS_DIR = _HERE / "tasks"
SCHEMAS_DIR = _HERE / "schemas"


def read_static(name: str) -> str:
    return (STATIC_DIR / f"{name}.md").read_text(encoding="utf-8").strip()


def read_task(name: str) -> str | None:
    p = TASKS_DIR / f"{name}.md"
    return p.read_text(encoding="utf-8").strip() if p.is_file() else None


def render_brackets() -> str:
    lines = [f"## Brackets", f"Official Commander Brackets as of {br.RULES_AS_OF}. Hard rules decide placement; "
             "advisory rows are shown, never used to place a deck."]
    for b in br.BRACKETS:
        gc = "unlimited" if b.max_game_changers is None else str(b.max_game_changers)
        lines.append(f"### Bracket {b.number}: {b.name}")
        lines.append(f"- Intent: {b.intent}")
        for e in b.expectations:
            lines.append(f"- Expect: {e}")
        lines.append(f"- Game Changers allowed: {gc}")
        lines.append(f"- Mass land denial: {b.mass_land_denial}")
        lines.append(f"- Extra turns: {b.extra_turns}")
        lines.append(f"- Two-card combos: {b.two_card_combos}")
        lines.append(f"- Tutors (advisory): {b.tutors}")
        lines.append("- Expected turns before a win: "
                     + ("any" if b.min_turns is None else f"at least {b.min_turns}"))
    lines.append("### Constraint kinds")
    for c in br.CONSTRAINTS:
        lines.append(f"- {c.label} [{c.kind}]: {c.check}")
    lines.append("Sources: " + "; ".join(f"{k}: {v}" for k, v in br.SOURCES.items() if k != "commandersalt"))
    return "\n".join(lines)


def render_game_changers(gc: Any) -> str:
    """Dynamic part. ``gc`` is a GameChangerList-like object, a dict with the same keys, or None."""
    if gc is None:
        return "## Game Changers\nunavailable: the Game Changers list was not provided."
    get = (lambda k: gc.get(k)) if isinstance(gc, dict) else (lambda k: getattr(gc, k, None))
    cards = list(get("cards") or [])
    if not cards:
        return "## Game Changers\nunavailable: the Game Changers list was empty."
    return (f"## Game Changers\nSource: {get('source')}; as of {get('fetched_at')}; count {len(cards)}.\n"
            + "\n".join(f"- {c}" for c in cards))


def render_mana_math() -> str:
    lines = ["## Mana math",
             f"Deck template (99 cards plus commander): {comp.TEMPLATE_SOURCE}"]
    labels = {"lands": "Lands", "ramp": "Ramp", "card_advantage": "Card advantage",
              "targeted_disruption": "Targeted disruption", "mass_disruption": "Mass disruption",
              "plan": "Plan cards"}
    for k in comp.ROW_KEYS:
        lines.append(f"- {labels.get(k, k)}: {comp.DEFAULT_TEMPLATE[k]} (ok band: target +/-1)")
    lines.append("Colored sources needed (99-card deck), by (generic mana before the pip, colored pips of that color):")
    for (gen, pips), n in sorted(fs.KARSTEN_99.items()):
        lines.append(f"- generic {gen}, {pips} colored pip{'s' if pips != 1 else ''}: {n} sources")
    lines.append(f"Source: {fs.KARSTEN_SOURCE}")
    lines.append("Land count cross-checks (use the values given in FACTS, never recompute): "
                 "Burgess = 31 + number of colors in the commander's identity + commander mana value; "
                 "Karsten = 31.42 + 3.13 x average non-land mana value - 0.28 x cheap ramp/draw pieces.")
    return "\n".join(lines)
