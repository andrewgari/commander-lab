"""Deck plan for Brew's deck-construction stage: what the deck is trying to DO, from the user's notes,
themes and deck idea and the commander's own rules text, expressed as Scryfall searches.

EDHREC is only the scaffold of stages 1-2 (what is already known to work). The plan is how stage 3 finds
cards that serve THIS deck: each item is a Scryfall query (oracle text, creature type, Tagger tag) whose
results feed the scorer. Deterministic (no LLM) so it is testable and works with EDHREC down; the reviewer
panel then judges the result. Retrieval order is Scryfall's `edhrec` order only to decide WHICH <=175 hits
come back for a broad query; the scorer, not that order, ranks them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence

MAX_ITEMS = 6

# Mechanic vocabulary: words found in the commander text / notes / idea -> (label, oracle-text search).
# A query is only a candidate source; the scorer and panel decide what is kept.
_MECHANICS = (
    (r"\+1/\+1 counter|counters? on", "+1/+1 counters", 'o:"+1/+1 counter"'),
    (r"\btoken", "tokens", 'o:"create" o:"token"'),
    (r"sacrific|aristocrat", "sacrifice", 'o:"sacrifice"'),
    (r"graveyard|reanimat|self-?mill|\bmill\b", "graveyard", "(o:graveyard or o:reanimate)"),
    (r"treasure", "treasure", "o:treasure"),
    (r"life ?gain|gain life|lifelink", "lifegain", 'o:"gain life"'),
    (r"landfall|land.*enters|ramp", "landfall", "o:landfall"),
    (r"artifact", "artifacts", "t:artifact o:artifact"),
    (r"enchantment|aura|constellation", "enchantments", "t:enchantment"),
    (r"equipment|equip\b|voltron", "equipment", "t:equipment"),
    (r"instant|sorcery|spellslinger|cast.*spell|magecraft", "spells matter", '(o:"instant or sorcery" or o:"whenever you cast")'),
    (r"draw a card|card draw|wheel", "card flow", 'o:"draw a card"'),
    (r"exile|blink|flicker", "blink", '(o:"exile" o:"return it to the battlefield")'),
    (r"combat|attack|haste", "attackers", 'o:"whenever" o:"attacks"'),
    (r"\+1/\+1 ?anthem|go wide|overrun", "go wide", 'o:"creatures you control get"'),
)
_TYPE_WORDS = re.compile(r"[A-Z][a-z]+")


@dataclass(frozen=True)
class PlanItem:
    label: str
    query: str   # full Scryfall query minus the colour/format suffix added at fetch time
    kind: str    # "theme" | "mechanic" | "creature_type"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def creature_types(type_line: str) -> List[str]:
    """Subtypes of a creature type line ("Legendary Creature — Elf Druid" -> ["Elf", "Druid"])."""
    if "—" not in (type_line or "") or "creature" not in type_line.lower():
        return []
    return _TYPE_WORDS.findall(type_line.split("—", 1)[1])


def build_plan(themes: Sequence[str], text: str, commander_text: str = "", commander_type_line: str = "",
               limit: int = MAX_ITEMS) -> List[PlanItem]:
    """Plan items, user's themes first, then mechanics the commander text/notes mention, then the
    commander's creature types. `text` = notes + deck idea."""
    items: List[PlanItem] = []
    seen = set()

    def add(item: PlanItem) -> None:
        if item.query not in seen and len(items) < limit:
            seen.add(item.query)
            items.append(item)

    for t in themes or []:
        t = str(t).strip()
        if t:
            add(PlanItem(t, f'o:"{t.lower()}"', "theme"))
            add(PlanItem(f"{t} (tagged)", f"otag:{_slug(t)}", "theme"))
    hay = f"{text}\n{commander_text}".lower()
    for pattern, label, query in _MECHANICS:
        if re.search(pattern, hay):
            add(PlanItem(label, query, "mechanic"))
    for ct in creature_types(commander_type_line)[:2]:
        add(PlanItem(f"{ct} creatures", f"t:{ct.lower()}", "creature_type"))
    return items


def full_query(item: PlanItem, ident: str) -> str:
    """The Scryfall search for a plan item: Commander-legal, inside the colour identity, no lands
    (the manabase is stage 2's)."""
    return f"{item.query} f:commander id<={ident or 'c'} -t:land"


def theme_terms(items: Iterable[PlanItem]) -> List[str]:
    """Words the scorer's commander-synergy term should look for (themes and mechanic labels)."""
    out = []
    for i in items:
        if i.kind != "creature_type" and not i.label.endswith("(tagged)"):
            out.append(i.label.split(" matter")[0])
    return out


__all__ = ["PlanItem", "build_plan", "full_query", "theme_terms", "creature_types", "MAX_ITEMS"]
