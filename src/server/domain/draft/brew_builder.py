"""
Brew deck builders.

`BaseBrewBuilder` subclasses reconstruct.BaseReconstructBuilder but brew keeps
its OWN module-level builder registry (get_builder/set_builder): swapping the
reconstruct builder must never swap the brew builder and vice versa.

A brew build returns a list of exactly ONE draft in the documented draft
format (reconstruct.BaseReconstructBuilder.build docstring).

Default is StubBrewBuilder so the /brew flow is demoable end-to-end without
LLM credentials; swap in BrewBuilder (LLM pipeline, reuses
reconstruct_builder.ReconstructBuilder) via brew_builder.set_builder().
"""
from __future__ import annotations

from typing import Any, List

import src.server.domain.inventory.instances as instance_store
import src.server.domain.draft.reconstruct as reconstruct
import src.server.domain.draft.reconstruct_pools as reconstruct_pools
from src.server.domain.draft.reconstruct_pools import norm_name


class BaseBrewBuilder(reconstruct.BaseReconstructBuilder):
    """Pluggable builder interface for brew. build(session, r) -> [one draft]."""

    def build(self, session: dict, r: Any) -> list[dict]:
        raise NotImplementedError("Builder must implement build(session, r)")


class StubBrewBuilder(BaseBrewBuilder):
    """Deterministic stub: commander + donor pool cards, honoring build_mode.

    owned_first: maximize Tier 1 (donor) then Tier 2 (free inventory); Tier 3
    only for staples. best_build: a short owned core plus a wider Tier 3 buy
    list (quality-first, cost reported not minimized).
    """

    def build(self, session: dict, r: Any) -> list[dict]:
        import src.server.domain.draft.brew as brew  # local import: brew imports this module lazily too

        commanders = list(session.get("commanders") or [])
        if not commanders:
            raise ValueError("a commander is required to brew a deck")
        pref = (session.get("preferences") or [{}])[0]
        build_mode = session.get("build_mode", "owned_first")

        pools = brew._build_pools(r, session) if r is not None else None

        donor_cards: List[str] = []
        for sd in session.get("donor_decks") or []:
            for c in sd.get("cards", []):
                n = c.get("name")
                if n and n not in donor_cards:
                    donor_cards.append(n)
            for cmd in sd.get("commanders") or []:
                if cmd and cmd not in donor_cards:
                    donor_cards.append(cmd)

        cards: list[dict] = []
        used = set()

        for cmd in commanders:
            in_donor = any(norm_name(cmd) == norm_name(d) for d in donor_cards)
            cards.append({
                "name": cmd,
                "quantity": 1,
                "tier": 1 if in_donor else 3,
                "tier_label": "previous_deck" if in_donor else "new",
                "cost": 0.0,
                "rationale": "Designated commander",
            })
            used.add(norm_name(cmd))

        def add(name, tier, tier_label, cost, rationale):
            if norm_name(name) in used:
                return False
            cards.append({
                "name": name, "quantity": 1, "tier": tier,
                "tier_label": tier_label, "cost": cost, "rationale": rationale,
            })
            used.add(norm_name(name))
            return True

        if build_mode == "best_build":
            tier1_cap, tier2_cap, tier3_cap = 6, 2, 12
        else:  # owned_first
            tier1_cap, tier2_cap, tier3_cap = 25, 10, 3

        added = 0
        for name in donor_cards:
            if added >= tier1_cap:
                break
            if add(name, 1, "previous_deck", 0.0, "Available from a named donor deck"):
                added += 1

        added = 0
        if pools is not None:
            for pc in pools.tier2:
                if added >= tier2_cap:
                    break
                if add(pc.name, 2, "owned", 0.0, "Available from free inventory"):
                    added += 1
        elif r is not None:
            try:
                for inst in instance_store.list_instances(r, ownership_status="in_collection"):
                    if added >= tier2_cap:
                        break
                    cname = inst.get("card_name")
                    if cname and add(cname, 2, "owned", 0.0, "Available from free inventory"):
                        added += 1
            except Exception:
                pass

        added = 0
        if pools is not None:
            for pc in pools.tier3:
                if added >= tier3_cap:
                    break
                price = pc.unit_price if pc.unit_price is not None else 0.0
                if add(pc.name, 3, "new", float(price), "Buy to complete the brew"):
                    added += 1
        else:
            for cname, price in [("Arcane Signet", 1.25), ("Command Tower", 0.50), ("Sol Ring", 1.50)]:
                if added >= tier3_cap:
                    break
                if add(cname, 3, "new", price, "Buy to complete the brew"):
                    added += 1

        # Re-tier and cost against the pools so provenance/cost stay truthful.
        if pools is not None:
            costed = reconstruct_pools.compute_deck_cost(cards, pools)
            cards = [cc.to_draft_card(rationale=cards[i].get("rationale", "")) for i, cc in enumerate(costed.cards)]
            total_new_cost = costed.total_new_cost
            is_complete = costed.is_complete
            unpriced = list(costed.unpriced_cards)
        else:
            total_new_cost = round(sum(c.get("cost") or 0.0 for c in cards if c.get("tier") == 3), 2)
            is_complete = True
            unpriced = []

        name = pref.get("name") or f"{commanders[0]} brew"
        themes = list(session.get("themes") or pref.get("themes") or [])
        draft = {
            "id": "draft_0",
            "name": name,
            "commanders": commanders,
            "color": session.get("color", ""),
            "themes": themes,
            "strategies": list(pref.get("strategies", [])),
            "notes": session.get("notes", ""),
            "power_level": session.get("power_level", ""),
            "deck_idea": session.get("deck_idea", ""),
            "build_mode": build_mode,
            "cards": cards,
            "total_new_cost": total_new_cost,
            "is_complete": is_complete,
            "unpriced_cards": unpriced,
            "explanation": (
                f"Brew draft ({build_mode}) for {', '.join(commanders)}"
                + (f" — themes: {', '.join(themes)}." if themes else ".")
            ),
        }
        return [draft]


class BrewBuilder(BaseBrewBuilder):
    """LLM-backed brew builder: reuses the reconstruct agent pipeline with the
    brew session (donor snapshots are aliased to source_decks, so pool building
    is identical) and brew-mode guidance folded into the preference notes."""

    def __init__(self, **kwargs: Any):
        from src.server.domain.draft.reconstruct_builder import ReconstructBuilder

        self._inner = ReconstructBuilder(**kwargs)

    def build(self, session: dict, r: Any) -> list[dict]:
        mode = session.get("build_mode", "owned_first")
        note = {
            "owned_first": "Use owned cards (donor decks and free inventory) wherever they fit; buy only what nothing owned can fill.",
            "best_build": "Build the ideal list for the idea; buy what is missing.",
        }.get(mode, "")
        inner_session = dict(session)
        prefs = [dict(p) for p in (session.get("preferences") or [])]
        if prefs:
            prefs[0]["notes"] = " ".join(x for x in [prefs[0].get("notes", ""), note] if x).strip()
        inner_session["preferences"] = prefs
        drafts = self._inner.build(inner_session, r)
        return drafts[:1]


_builder_instance: BaseBrewBuilder = StubBrewBuilder()


def get_builder() -> BaseBrewBuilder:
    """Return the active brew builder implementation."""
    return _builder_instance


def set_builder(builder: BaseBrewBuilder) -> None:
    """Register a custom or agent-based brew builder implementation."""
    global _builder_instance
    if not isinstance(builder, BaseBrewBuilder):
        raise TypeError("builder must inherit from BaseBrewBuilder")
    _builder_instance = builder
