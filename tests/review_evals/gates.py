"""Deterministic gates for review_document v1 (eval harness, card 03).

Each gate is ``gate(doc, ctx) -> list[Violation]``; a review passes only when
every gate returns zero violations. Gates never call an LLM or the network.

``EvalContext`` carries everything a gate needs besides the review itself:

* ``deck``      -- ``{"commanders": [name], "cards": [name], "pet_cards": [name],
                      "budget_hard_cap": float|None}``. ``cards`` includes commanders.
* ``scryfall``  -- recorded Scryfall data keyed by exact card name:
                   ``{"color_identity": ["W"], "legalities": {"commander": "legal"}}``.
* ``fresh_fact_pack`` -- output of ``build_fact_pack`` rebuilt from the same
                   recorded data (see ``fresh_fact_pack``). The review's
                   ``meta.fact_pack_hash`` and ``charts`` must match it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Violation:
    gate: str
    message: str

    def __str__(self) -> str:
        return f"[{self.gate}] {self.message}"


@dataclass
class EvalContext:
    deck: dict[str, Any]
    scryfall: dict[str, dict[str, Any]]
    fresh_fact_pack: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------- helpers

def fact_pack_hash(fact_pack: Any) -> str:
    """The fact pack's own hash (``FactPack.hash``), which is what ``meta.fact_pack_hash`` carries."""
    return fact_pack.hash


def fresh_fact_pack(deck: dict[str, Any], scryfall: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Rebuild a fact pack offline from recorded Scryfall data."""
    from src.server.domain.analytics.fact_pack import build_fact_pack

    def _scry(name: str):
        return scryfall.get(name)

    return build_fact_pack(
        {"commanders": deck["commanders"], "cards": deck["cards"]},
        {"scryfall": _scry},
    )


def _name(ref: Any) -> str | None:
    if isinstance(ref, dict):
        return ref.get("name")
    return ref if isinstance(ref, str) else None


def _swaps(doc: dict) -> list[dict]:
    return doc.get("swaps") or []


def _findings(doc: dict) -> list[dict]:
    return (doc.get("strengths") or []) + (doc.get("problems") or [])


def _all_card_names(doc: dict) -> list[tuple[str, str]]:
    """(location, name) for every card reference in the document."""
    out: list[tuple[str, str]] = []
    for c in doc.get("meta", {}).get("commanders", []):
        out.append(("meta.commanders", _name(c)))
    for s in _swaps(doc):
        for key in ("cut", "add", "alternative"):
            if s.get(key):
                out.append((f"swap {s.get('id')}.{key}", _name(s[key])))
    for c in doc.get("cuts_to_consider") or []:
        out.append(("cuts_to_consider", _name(c.get("card"))))
    for f in _findings(doc):
        for c in (f.get("evidence") or {}).get("cards", []):
            out.append((f"finding {f.get('id')}.evidence", _name(c)))
    return [(loc, n) for loc, n in out if n]


def _final_deck(doc: dict, ctx: EvalContext) -> list[str]:
    cards = list(ctx.deck["cards"])
    for s in _swaps(doc):
        cut, add = _name(s.get("cut")), _name(s.get("add"))
        if cut in cards:
            cards.remove(cut)
        if add:
            cards.append(add)
    return cards


def _commander_identity(ctx: EvalContext) -> set[str]:
    ident: set[str] = set()
    for c in ctx.deck["commanders"]:
        ident |= set(ctx.scryfall.get(c, {}).get("color_identity", []))
    return ident


# ------------------------------------------------------------------ gates

def hallucinated_card_names(doc, ctx):
    return [Violation("hallucinated_card_names", f"{loc}: '{n}' not in Scryfall data")
            for loc, n in _all_card_names(doc) if n not in ctx.scryfall]


def add_outside_color_identity(doc, ctx):
    allowed, out = _commander_identity(ctx), []
    for s in _swaps(doc):
        n = _name(s.get("add"))
        extra = set(ctx.scryfall.get(n, {}).get("color_identity", [])) - allowed
        if n in ctx.scryfall and extra:
            out.append(Violation("add_outside_color_identity",
                                 f"swap {s.get('id')}: '{n}' has {sorted(extra)} outside commander identity {sorted(allowed)}"))
    return out


def add_not_commander_legal(doc, ctx):
    out = []
    for s in _swaps(doc):
        n = _name(s.get("add"))
        if n in ctx.scryfall:
            status = (ctx.scryfall[n].get("legalities") or {}).get("commander")
            if status != "legal":
                out.append(Violation("add_not_commander_legal", f"swap {s.get('id')}: '{n}' commander legality is {status!r}"))
    return out


def singleton_violations(doc, ctx):
    basics = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
              "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
              "Snow-Covered Mountain", "Snow-Covered Forest"}
    counts: dict[str, int] = {}
    for n in _final_deck(doc, ctx):
        counts[n] = counts.get(n, 0) + 1
    return [Violation("singleton_violations", f"'{n}' appears {k}x after applying all swaps")
            for n, k in sorted(counts.items()) if k > 1 and n not in basics]


def add_already_in_deck(doc, ctx):
    in_deck = set(ctx.deck["cards"])
    return [Violation("add_already_in_deck", f"swap {s.get('id')}: '{_name(s.get('add'))}' is already in the deck")
            for s in _swaps(doc) if _name(s.get("add")) in in_deck]


def cut_is_pet_card(doc, ctx):
    pets = set(ctx.deck.get("pet_cards") or [])
    out = [Violation("cut_is_pet_card", f"swap {s.get('id')}: cuts pet card '{_name(s.get('cut'))}'")
           for s in _swaps(doc) if _name(s.get("cut")) in pets]
    out += [Violation("cut_is_pet_card", f"cuts_to_consider: pet card '{_name(c.get('card'))}'")
            for c in doc.get("cuts_to_consider") or [] if _name(c.get("card")) in pets]
    return out


def add_above_budget_cap(doc, ctx):
    cap = ctx.deck.get("budget_hard_cap")
    if cap is None:
        return []
    return [Violation("add_above_budget_cap", f"swap {s.get('id')}: '{_name(s.get('add'))}' price ${s['price']} > cap ${cap}")
            for s in _swaps(doc) if s.get("price") is not None and s["price"] > cap]


def swaps_exceed_target_bracket(doc, ctx):
    b = doc.get("bracket") or {}
    target, est = b.get("target"), b.get("estimated")
    if target is None or est is None:
        return []
    delta = sum(1 for s in _swaps(doc) if s.get("bracket_effect") == "raises") \
        - sum(1 for s in _swaps(doc) if s.get("bracket_effect") == "lowers")
    # Any "raises" swap is only acceptable if it lands at or below the target.
    if delta > 0 and est + delta > target:
        return [Violation("swaps_exceed_target_bracket",
                          f"estimated bracket {est} + {delta} raising swap(s) exceeds target {target}")]
    return []


def unresolved_finding_id(doc, ctx):
    ids = {f.get("id") for f in _findings(doc)}
    return [Violation("unresolved_finding_id", f"swap {s.get('id')}: resolves_finding_id '{s.get('resolves_finding_id')}' does not exist")
            for s in _swaps(doc) if s.get("resolves_finding_id") not in ids]


def empty_evidence(doc, ctx):
    out = []
    for f in _findings(doc):
        ev = f.get("evidence") or {}
        if not ev.get("cards") and not ev.get("fact_keys"):
            out.append(Violation("empty_evidence", f"finding '{f.get('id')}' has empty evidence"))
    return out


def charts_mismatch_fact_pack(doc, ctx):
    fresh = ctx.fresh_fact_pack
    if fresh is None:
        return [Violation("charts_mismatch_fact_pack", "no fresh fact pack supplied; cannot verify")]
    out = []
    want = fresh.hash
    got = (doc.get("meta") or {}).get("fact_pack_hash")
    if got != want:
        out.append(Violation("charts_mismatch_fact_pack", f"meta.fact_pack_hash {str(got)[:12]}... != fresh {want[:12]}..."))
    if doc.get("charts") != fresh.get("charts"):
        out.append(Violation("charts_mismatch_fact_pack", "charts differ from fresh fact pack charts"))
    return out


GATES: dict[str, Callable[[dict, EvalContext], list[Violation]]] = {
    g.__name__: g for g in (
        hallucinated_card_names, add_outside_color_identity, add_not_commander_legal,
        singleton_violations, add_already_in_deck, cut_is_pet_card, add_above_budget_cap,
        swaps_exceed_target_bracket, unresolved_finding_id, empty_evidence,
        charts_mismatch_fact_pack,
    )
}


def run_gates(doc: dict, ctx: EvalContext) -> list[Violation]:
    out: list[Violation] = []
    for gate in GATES.values():
        out.extend(gate(doc, ctx))
    return out
