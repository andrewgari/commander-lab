"""Deterministic review checks: one implementation, two callers.

The eval harness (``tests/review_evals/gates.py``) and the Pass D verifier (``d_verify.py``) both run
THESE functions, so "what counts as a violation" cannot drift between CI and production. Each check is
``check(doc, ctx) -> list[Violation]``; a review passes only when every check returns nothing. Checks
never call an LLM or the network.

``doc`` is any mapping shaped like ``review_document.v1`` (only the keys a check reads are needed:
``swaps``, ``cuts_to_consider``, ``strengths``, ``problems``, ``bracket``, ``meta``, ``charts``).

``Violation.items`` names the document items a violation is about (a swap id, ``cut:<card>``, a finding
id) so the verifier can regenerate or remove exactly those items and nothing else.

``CheckContext`` (alias ``EvalContext``) carries everything a check needs besides the review itself:

* ``deck``      -- ``{"commanders": [name], "cards": [name], "pet_cards": [name],
                      "budget_hard_cap": float|None, "rejected_swaps": [[cut, add], ...],
                      "candidate_pool": [name] | absent, "game_changers": [name] | absent}``.
                   ``cards`` includes commanders (one entry per copy).
* ``scryfall``  -- Scryfall data keyed by exact card name:
                   ``{"color_identity": ["W"], "legalities": {"commander": "legal"}}``.
* ``fresh_fact_pack`` -- the fact pack rebuilt from the same data; the review's
                   ``meta.fact_pack_hash`` and ``charts`` must match it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from src.server.domain.analytics.review_pipeline.names import normalize

BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
          "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
          "Snow-Covered Mountain", "Snow-Covered Forest"}


@dataclass(frozen=True)
class Violation:
    gate: str
    message: str
    items: tuple[str, ...] = ()      # ids of the document items at fault, in document order

    def __str__(self) -> str:
        return f"[{self.gate}] {self.message}"


@dataclass
class CheckContext:
    deck: dict[str, Any]
    scryfall: dict[str, dict[str, Any]]
    fresh_fact_pack: dict[str, Any] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


EvalContext = CheckContext   # the harness's historical name


# ---------------------------------------------------------------- helpers

def _name(ref: Any) -> str | None:
    if isinstance(ref, dict):
        return ref.get("name")
    return ref if isinstance(ref, str) else None


def _swaps(doc: dict) -> list[dict]:
    return doc.get("swaps") or []


def _findings(doc: dict) -> list[dict]:
    return (doc.get("strengths") or []) + (doc.get("problems") or [])


def cut_item_id(card_name: str | None) -> str:
    """Item id of a ``cuts_to_consider`` entry (cuts have no id of their own in the document)."""
    return f"cut:{card_name}"


def _all_card_names(doc: dict) -> list[tuple[str, str, str | None]]:
    """(location, name, item id) for every card reference in the document."""
    out: list[tuple[str, str | None, str | None]] = []
    for c in doc.get("meta", {}).get("commanders", []):
        out.append(("meta.commanders", _name(c), None))
    for s in _swaps(doc):
        for key in ("cut", "add", "alternative"):
            if s.get(key):
                out.append((f"swap {s.get('id')}.{key}", _name(s[key]), s.get("id")))
    for c in doc.get("cuts_to_consider") or []:
        out.append(("cuts_to_consider", _name(c.get("card")), cut_item_id(_name(c.get("card")))))
    for f in _findings(doc):
        for c in (f.get("evidence") or {}).get("cards", []):
            out.append((f"finding {f.get('id')}.evidence", _name(c), f.get("id")))
    return [(loc, n, item) for loc, n, item in out if n]


def _final_deck(doc: dict, ctx: CheckContext) -> list[str]:
    cards = list(ctx.deck["cards"])
    for s in _swaps(doc):
        cut, add = _name(s.get("cut")), _name(s.get("add"))
        if cut in cards:
            cards.remove(cut)
        if add:
            cards.append(add)
    return cards


def _commander_identity(ctx: CheckContext) -> set[str]:
    ident: set[str] = set()
    for c in ctx.deck["commanders"]:
        ident |= set(ctx.scryfall.get(c, {}).get("color_identity", []))
    return ident


def _items(*ids: str | None) -> tuple[str, ...]:
    return tuple(i for i in ids if i)


# ------------------------------------------------------------------ checks

def hallucinated_card_names(doc, ctx):
    return [Violation("hallucinated_card_names", f"{loc}: '{n}' not in Scryfall data", _items(item))
            for loc, n, item in _all_card_names(doc) if n not in ctx.scryfall]


def add_outside_color_identity(doc, ctx):
    allowed, out = _commander_identity(ctx), []
    for s in _swaps(doc):
        n = _name(s.get("add"))
        extra = set(ctx.scryfall.get(n, {}).get("color_identity", [])) - allowed
        if n in ctx.scryfall and extra:
            out.append(Violation("add_outside_color_identity",
                                 f"swap {s.get('id')}: '{n}' has {sorted(extra)} outside commander identity {sorted(allowed)}",
                                 _items(s.get("id"))))
    return out


def add_not_commander_legal(doc, ctx):
    out = []
    for s in _swaps(doc):
        n = _name(s.get("add"))
        if n in ctx.scryfall:
            status = (ctx.scryfall[n].get("legalities") or {}).get("commander")
            if status != "legal":
                out.append(Violation("add_not_commander_legal", f"swap {s.get('id')}: '{n}' commander legality is {status!r}",
                                     _items(s.get("id"))))
    return out


def singleton_violations(doc, ctx):
    """Duplicates after applying every swap. The swap whose add creates the duplicate is the item at fault."""
    counts: dict[str, int] = {}
    for n in ctx.deck["cards"]:
        counts[n] = counts.get(n, 0) + 1
    culprits: dict[str, list[str]] = {}
    for s in _swaps(doc):
        cut, add = _name(s.get("cut")), _name(s.get("add"))
        if cut in counts and counts[cut] > 0:
            counts[cut] -= 1
        if add:
            counts[add] = counts.get(add, 0) + 1
            if counts[add] > 1 and add not in BASICS and s.get("id"):
                culprits.setdefault(add, []).append(str(s["id"]))
    return [Violation("singleton_violations", f"'{n}' appears {k}x after applying all swaps", _items(*culprits.get(n, [])))
            for n, k in sorted(counts.items()) if k > 1 and n not in BASICS]


def add_already_in_deck(doc, ctx):
    in_deck = set(ctx.deck["cards"])
    return [Violation("add_already_in_deck", f"swap {s.get('id')}: '{_name(s.get('add'))}' is already in the deck", _items(s.get("id")))
            for s in _swaps(doc) if _name(s.get("add")) in in_deck]


def cut_is_pet_card(doc, ctx):
    pets = set(ctx.deck.get("pet_cards") or [])
    out = [Violation("cut_is_pet_card", f"swap {s.get('id')}: cuts pet card '{_name(s.get('cut'))}'", _items(s.get("id")))
           for s in _swaps(doc) if _name(s.get("cut")) in pets]
    out += [Violation("cut_is_pet_card", f"cuts_to_consider: pet card '{_name(c.get('card'))}'", _items(cut_item_id(_name(c.get("card")))))
            for c in doc.get("cuts_to_consider") or [] if _name(c.get("card")) in pets]
    return out


def add_above_budget_cap(doc, ctx):
    cap = ctx.deck.get("budget_hard_cap")
    if cap is None:
        return []
    return [Violation("add_above_budget_cap", f"swap {s.get('id')}: '{_name(s.get('add'))}' price ${s['price']} > cap ${cap}", _items(s.get("id")))
            for s in _swaps(doc) if s.get("price") is not None and s["price"] > cap]


def swaps_exceed_target_bracket(doc, ctx):
    """Net bracket delta of the swaps must not push the estimate past the target. The violation names only the
    LOWEST-ranked raising swaps, i.e. the fewest changes that restore the budget (the verifier regenerates those)."""
    b = doc.get("bracket") or {}
    target, est = b.get("target"), b.get("estimated")
    if target is None or est is None:
        return []
    raising = [s.get("id") for s in _swaps(doc) if s.get("bracket_effect") == "raises"]
    lowering = [s for s in _swaps(doc) if s.get("bracket_effect") == "lowers"]
    delta = len(raising) - len(lowering)
    # Any "raises" swap is only acceptable if it lands at or below the target.
    if delta > 0 and est + delta > target:
        room = max(target - est + len(lowering), 0)
        return [Violation("swaps_exceed_target_bracket",
                          f"estimated bracket {est} + {delta} raising swap(s) exceeds target {target}", _items(*raising[room:]))]
    return []


def unresolved_finding_id(doc, ctx):
    ids = {f.get("id") for f in _findings(doc)}
    return [Violation("unresolved_finding_id", f"swap {s.get('id')}: resolves_finding_id '{s.get('resolves_finding_id')}' does not exist",
                      _items(s.get("id")))
            for s in _swaps(doc) if s.get("resolves_finding_id") not in ids]


def empty_evidence(doc, ctx):
    out = []
    for f in _findings(doc):
        ev = f.get("evidence") or {}
        if not ev.get("cards") and not ev.get("fact_keys"):
            out.append(Violation("empty_evidence", f"finding '{f.get('id')}' has empty evidence", _items(f.get("id"))))
    return out


def _rejected(ctx: CheckContext) -> set[tuple[str, str]]:
    return {(normalize(c), normalize(a)) for c, a in (ctx.deck.get("rejected_swaps") or [])}


def previously_rejected_suggestion(doc, ctx):
    """The owner already said no to this exact (cut, add) pair for this deck (``player_profile.previously_rejected``).
    The alternative is checked as well: it is offered as a substitute for the same cut."""
    rejected = _rejected(ctx)
    if not rejected:
        return []
    out = []
    for s in _swaps(doc):
        cut = _name(s.get("cut")) or ""
        for key in ("add", "alternative"):
            n = _name(s.get(key))
            if n and (normalize(cut), normalize(n)) in rejected:
                out.append(Violation("previously_rejected_suggestion",
                                     f"swap {s.get('id')}: cut '{cut}' for {key} '{n}' was already rejected by the owner for this deck",
                                     _items(s.get("id"))))
    return out


def add_not_in_candidate_pool(doc, ctx):
    """CONTRACT §2.3: adds and alternatives come only from the candidate pool. Skipped (no violation) when the
    context carries no pool, e.g. golden-review fixtures that predate Pass C."""
    pool = ctx.deck.get("candidate_pool")
    if pool is None:
        return []
    allowed = {normalize(n) for n in pool}
    out = []
    for s in _swaps(doc):
        for key in ("add", "alternative"):
            n = _name(s.get(key))
            if n and normalize(n) not in allowed:
                out.append(Violation("add_not_in_candidate_pool", f"swap {s.get('id')}: {key} '{n}' is not in the candidate pool", _items(s.get("id"))))
    return out


def cut_not_in_deck(doc, ctx):
    in_deck = {normalize(n) for n in ctx.deck["cards"]}
    commanders = {normalize(n) for n in ctx.deck["commanders"]}
    out = []
    for s in _swaps(doc):
        n = _name(s.get("cut"))
        if n and normalize(n) not in in_deck:
            out.append(Violation("cut_not_in_deck", f"swap {s.get('id')}: cut '{n}' is not in the deck", _items(s.get("id"))))
        elif n and normalize(n) in commanders:
            out.append(Violation("cut_not_in_deck", f"swap {s.get('id')}: cut '{n}' is a commander", _items(s.get("id"))))
    return out


def duplicate_swap_cut(doc, ctx):
    """Two swaps cutting the same card: the second one cannot happen."""
    seen: dict[str, str] = {}
    out = []
    for s in _swaps(doc):
        n = _name(s.get("cut"))
        if not n:
            continue
        k = normalize(n)
        if k in seen:
            out.append(Violation("duplicate_swap_cut", f"swap {s.get('id')}: '{n}' is already cut by swap {seen[k]}", _items(s.get("id"))))
        else:
            seen[k] = str(s.get("id"))
    return out


def top_changes_unresolved(doc, ctx):
    top = doc.get("top_changes")
    if not top:
        return []
    ids = [s.get("id") for s in _swaps(doc)]
    sel = top.get("swap_ids") or []
    out = [Violation("top_changes_unresolved", f"top_changes names swap '{i}', which does not exist", ("top_changes",)) for i in sel if i not in ids]
    if len(set(sel)) != len(sel):
        out.append(Violation("top_changes_unresolved", "top_changes names the same swap twice", ("top_changes",)))
    if len(sel) > 3 or (ids and len(sel) != min(3, len(ids))):
        out.append(Violation("top_changes_unresolved", f"top_changes has {len(sel)} swap(s); expected {min(3, len(ids))} of {len(ids)}", ("top_changes",)))
    return out


def bracket_effect_misstated(doc, ctx):
    """Whether a card is a Game Changer is a fact (Scryfall), not the model's opinion. Adding one the cut is not must be
    declared ``raises``; the reverse ``lowers``. Skipped when the context has no Game Changer list."""
    gcs = ctx.deck.get("game_changers")
    if gcs is None:
        return []
    gc = {normalize(n) for n in gcs}
    out = []
    for s in _swaps(doc):
        add_gc, cut_gc = normalize(_name(s.get("add")) or "") in gc, normalize(_name(s.get("cut")) or "") in gc
        want = "raises" if add_gc and not cut_gc else "lowers" if cut_gc and not add_gc else None
        if want and s.get("bracket_effect") != want:
            out.append(Violation("bracket_effect_misstated",
                                 f"swap {s.get('id')}: adds '{_name(s.get('add'))}' for '{_name(s.get('cut'))}' (Game Changer status "
                                 f"add={add_gc}, cut={cut_gc}) so bracket_effect must be '{want}', not {s.get('bracket_effect')!r}",
                                 _items(s.get("id"))))
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


CHECKS: dict[str, Callable[[dict, CheckContext], list[Violation]]] = {
    g.__name__: g for g in (
        hallucinated_card_names, add_outside_color_identity, add_not_commander_legal,
        singleton_violations, add_already_in_deck, cut_is_pet_card, add_above_budget_cap,
        swaps_exceed_target_bracket, unresolved_finding_id, empty_evidence,
        previously_rejected_suggestion, add_not_in_candidate_pool, cut_not_in_deck, duplicate_swap_cut,
        top_changes_unresolved, bracket_effect_misstated, charts_mismatch_fact_pack,
    )
}

# What the verifier can run before the document is assembled (charts and the fact-pack hash are copied in later).
PIPELINE_CHECKS = tuple(n for n in CHECKS if n != "charts_mismatch_fact_pack")


def run_checks(doc: dict, ctx: CheckContext, names: tuple[str, ...] | None = None) -> list[Violation]:
    out: list[Violation] = []
    for name in names or tuple(CHECKS):
        out.extend(CHECKS[name](doc, ctx))
    return out
