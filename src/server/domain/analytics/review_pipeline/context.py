"""Everything a review run needs, built once and shared (read-only) by all passes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.server.domain.analytics.fact_pack import FactPack
from src.server.domain.analytics.review_pipeline import facts as fx
from src.server.domain.analytics.review_pipeline.names import CardResolver
from src.server.domain.intake.user_intent import UserDeckIntent
from src.server.domain.profile import player_profile as pp

NO_INTENT = ("No deck intent recorded for this deck. Treat every point that depends on the owner's goals "
             "(target power, budget, win condition, what is deliberate) as unknown.")
_DEFAULT_TIER = "optimized"      # UserDeckIntent cannot tell "unset" from these defaults (see player_profile.resolve_with_intent)
_DEFAULT_PRIMARY_WIN = "combat"
_DEFAULT_VISION = "tune_up"


def intent_lines(intent: UserDeckIntent | None) -> list[str]:
    """Only what the owner actually set. Fields still at their dataclass default are treated as unset."""
    if intent is None:
        return []
    out: list[str] = []
    dv = intent.deck_vision
    if dv.description.strip():
        out.append(f"Vision: {dv.description.strip()}")
    if dv.intent and dv.intent != _DEFAULT_VISION:
        out.append(f"Intent: {dv.intent}")
    if dv.target_turn_win:
        out.append(f"Target win turn: {dv.target_turn_win}")
    if intent.target_bracket is not None:
        out.append(f"Target bracket: {intent.target_bracket}")
    if intent.target_power_level.tier != _DEFAULT_TIER:
        out.append(f"Target power: {intent.target_power_level.tier} (scale {intent.target_power_level.scale})")
    bc = intent.budget_constraints
    if bc.no_budget:
        out.append("Budget: none (no limit)")
    else:
        bits = [f"{k} {v:g}" for k, v in (("total budget", bc.total_budget), ("per-card hard cap", bc.hard_cap),
                                          ("per-card soft cap", bc.soft_cap)) if v is not None]
        if bits:
            out.append(f"Budget ({bc.currency}): " + ", ".join(bits))
    wc = intent.preferred_win_conditions
    if wc.primary != _DEFAULT_PRIMARY_WIN or wc.secondary or wc.custom_win_conditions:
        out.append("Preferred win conditions: " + "; ".join([wc.primary, *wc.secondary, *wc.custom_win_conditions]))
    if wc.disliked_or_excluded:
        out.append("Excluded win conditions: " + ", ".join(wc.disliked_or_excluded))
    ap = intent.archetype_preferences
    for label, v in (("Primary archetype", [ap.primary_archetype] if ap.primary_archetype else []),
                     ("Secondary archetypes", ap.secondary_archetypes), ("Excluded archetypes", ap.excluded_archetypes),
                     ("Custom themes", ap.custom_themes)):
        if v:
            out.append(f"{label}: {', '.join(v)}")
    for label, v in (("Genres", intent.deck_genre.genres), ("Kindred", intent.deck_genre.kindred), ("Pet cards", intent.pet_cards)):
        if v:
            out.append(f"{label}: {', '.join(v)}")
    if intent.novelties.strip():
        out.append(f"Novelties (deliberately odd inclusions): {intent.novelties.strip()}")
    if intent.freeform_notes.strip():
        out.append(f"Notes: {intent.freeform_notes.strip()}")
    return out


@dataclass(frozen=True)
class ReviewContext:
    deck_id: str
    deck_name: str
    commanders: tuple[str, ...]
    deck_cards: tuple[tuple[str, int], ...]
    index: fx.FactIndex
    resolver: CardResolver
    card_table: str
    profile_block: str
    intent_text: str
    has_stated_intent: bool
    has_stated_power: bool
    fact_pack_hash: str
    foreign_names: frozenset[str] = field(default_factory=frozenset)   # cards known NOT to be in the deck

    @classmethod
    def build(cls, *, deck_id: str, deck_name: str, fact_pack: Mapping[str, Any], deck_cards: Sequence[tuple[str, int]],
              commanders: Sequence[str], profile: pp.PlayerProfile, intent: UserDeckIntent | None,
              owned: Mapping[str, Mapping] | None = None, caps: fx.BudgetCaps | None = None,
              extra_foreign_names: Sequence[str] = ()) -> "ReviewContext":
        """``owned``: ``candidates.owned_from_rows`` output or None (inventory unavailable).
        ``caps``: explicit budget caps; by default derived from the profile with the deck intent applied.
        ``extra_foreign_names``: card names known not to be in the deck (the candidate pool), used to
        reject findings whose prose recommends them."""
        eff, _ = pp.resolve_with_intent(profile, intent)
        caps = caps or fx.BudgetCaps(soft=eff.budget_norms.soft_cap, hard=eff.budget_norms.hard_cap)
        index = fx.FactIndex.build(fact_pack, deck_cards=deck_cards, caps=caps, owned=owned)
        by_name = {c.get("name"): c for c in fact_pack.get("cards", []) if isinstance(c, Mapping)}
        ids: dict[str, str | None] = {}
        for name, _q in deck_cards:
            oid = (by_name.get(name) or {}).get("oracle_id")
            ids[name] = oid if isinstance(oid, str) else None
        lines = intent_lines(intent)
        deck_set = {n for n, _ in deck_cards}
        foreign = set(extra_foreign_names)
        ed = fact_pack.get("edhrec")
        if isinstance(ed, Mapping):
            foreign.update(r.get("name") for r in ed.get("high_synergy_absent") or [] if isinstance(r, Mapping))
        combos = fact_pack.get("combos")
        if isinstance(combos, Mapping):
            for c in combos.get("near_misses") or []:
                foreign.update(n for n in c.get("card_names") or [] if n not in deck_set)
        resolver = CardResolver(ids)
        # A name that resolves to a deck card (incl. one face of a double-faced card) is NOT foreign.
        foreign = {n for n in foreign if isinstance(n, str) and n and n not in deck_set and resolver.resolve(n) is None}
        pack_hash = fact_pack.hash if isinstance(fact_pack, FactPack) else FactPack(dict(fact_pack)).hash
        return cls(
            deck_id=deck_id, deck_name=deck_name, commanders=tuple(commanders), deck_cards=tuple((n, q) for n, q in deck_cards),
            index=index, resolver=resolver,
            card_table=fx.render_card_table(fact_pack, deck_cards, owned=owned, commanders=commanders),
            profile_block=pp.render_profile_for_prompt(profile, intent),
            intent_text="\n".join(lines) if lines else NO_INTENT, has_stated_intent=bool(lines),
            has_stated_power=bool((intent and (intent.target_bracket is not None or intent.target_power_level.tier != _DEFAULT_TIER))
                                  or eff.power_tendencies.tier),
            fact_pack_hash=pack_hash,
            foreign_names=frozenset(foreign))
