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

import json
from typing import Any, List

import src.server.domain.draft.brew_power as brew_power
import src.server.domain.draft.brew_scoring as brew_scoring
from src.server.domain.draft.brew_scoring_config import BrewScoringConfig
import src.server.domain.inventory.instances as instance_store
import src.server.domain.draft.reconstruct as reconstruct
import src.server.domain.draft.reconstruct_pools as reconstruct_pools
from src.server.domain.draft.card_resolution import ResolutionReport, resolve_card
from src.server.domain.draft.reconstruct_pools import norm_name

DECK_SIZE = 100  # commander(s) + the rest; the stub tops up to this


def _resolve_session(session: dict, r: Any):
    """Copy of `session` with every commander / donor card name resolved
    BEFORE pools are built (so colour filter and donor attribution use real
    names). Unresolvable donor cards are dropped; an unresolvable commander
    raises."""
    report = ResolutionReport()
    out = dict(session)
    cmds = []
    for cmd in session.get("commanders") or []:
        rc = resolve_card(cmd, r)
        if rc is None:
            raise ValueError(f"commander {cmd!r} cannot be resolved to a card name")
        cmds.append(rc.name)
    out["commanders"] = cmds

    def fix_deck(sd: dict) -> dict:
        sd = dict(sd)
        cards = []
        for c in sd.get("cards") or []:
            rc = resolve_card(c.get("name"), r)
            if rc is None:
                report.drop(c.get("name"))
                continue
            cards.append({**c, "name": rc.name})
        sd["cards"] = cards
        sc = []
        for cmd in sd.get("commanders") or []:
            rc = resolve_card(cmd, r)
            if rc is None:
                report.drop(cmd)
            else:
                sc.append(rc.name)
        sd["commanders"] = sc
        return sd

    for key in ("donor_decks", "source_decks"):
        if session.get(key):
            out[key] = [fix_deck(sd) for sd in session[key]]
    return out, report


def _cached_edhrec(r: Any, commanders: List[str]) -> dict:
    """EDHREC {name_key: {inclusion, synergy}} from the cache only (never fetches); {} if absent."""
    if r is None or not commanders:
        return {}
    try:
        from src.server.domain.analytics.card_eval.edhrec_data import CACHE_PREFIX
        from src.server.domain.analytics.providers.edhrec import commander_slug

        raw = r.get(CACHE_PREFIX + commander_slug(commanders))
        page = json.loads(raw) if raw else {}
        return {k: {"inclusion": v.get("inclusion"), "synergy": v.get("synergy")}
                for k, v in (page.get("cards") or {}).items()}
    except Exception:
        return {}


def _load_lab_tags(r: Any) -> dict:
    try:
        raw = r.get("lab_tags") if r is not None else None
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _card_record(r: Any, name: str, lab_tags: Any = None) -> dict:
    """Candidate dict for scoring: canonical card record first, then legacy card_meta;
    tags are the synced lab_tags merged with any record tags."""
    rec: dict = {}
    if r is not None:
        try:
            from src.server.domain.inventory import cards as card_store

            rec = card_store.get_card_by_name(r, name) or {}
        except Exception:
            rec = {}
        if not rec:
            try:
                raw = r.get(f"card_meta:{name}")
                rec = json.loads(raw) if raw else {}
            except Exception:
                rec = {}
    tags = list(rec.get("tags") or []) + list((lab_tags or {}).get(name) or [])
    tl = rec.get("type_line") or rec.get("type") or ""
    if isinstance(tl, (list, tuple)):
        tl = " ".join(str(x) for x in tl)
    if not tl and rec.get("types"):
        tl = " ".join(str(x) for x in rec["types"])
    out = {"name": name, "tags": tags, "oracle_text": rec.get("oracle_text") or "", "type_line": tl}
    mv = rec.get("cmc", rec.get("mana_value"))
    if isinstance(mv, (int, float)) and not isinstance(mv, bool):
        out["cmc"] = mv
    return out


class _Scorer:
    """Shared scoring state for one build: category counts persist across tiers."""

    def __init__(self, r: Any, session: dict, config: BrewScoringConfig):
        self.r, self.config = r, config
        self.counts: dict = {}
        self.target_power = session.get("target_power")
        self.mv_sum, self.mv_n = 0.0, 0
        self.records: dict = {}
        self.commander = (session.get("commanders") or [""])[0]
        self.themes = tuple(session.get("themes") or [])
        self.skeleton = config.skeleton()
        self.lab_tags = _load_lab_tags(r)
        self.edhrec = _cached_edhrec(r, list(session.get("commanders") or []))

    def pick(self, names: List[str], cap: int) -> List[str]:
        if cap <= 0:
            return []
        ctx = brew_scoring.ScoreContext(self.commander, self.themes, self.counts, self.skeleton, self.edhrec,
                                        target_power=self.target_power, mv_sum=self.mv_sum, mv_n=self.mv_n)
        recs = [_card_record(self.r, n, self.lab_tags) for n in names]
        chosen = brew_scoring.select_cards(recs, ctx, cap, self.config)
        for c in chosen:
            self.records[c["name"]] = c
            mv = brew_power.mana_value(c)
            if mv is not None and not brew_power.is_land(c):
                self.mv_sum, self.mv_n = self.mv_sum + mv, self.mv_n + 1
            for k in set(brew_scoring._matching(c, self.skeleton)):
                self.counts[k] = self.counts.get(k, 0) + 1
        return [c["name"] for c in chosen]


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

        session, report = _resolve_session(session, r)
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

        for v in (pools.dropped if pools is not None else []):
            report.drop(v)

        def add(name, tier, tier_label, cost, rationale):
            # Single resolution point: an unresolvable id is dropped, never emitted.
            resolved = resolve_card(name, r)
            if resolved is None:
                report.drop(name)
                return False
            name = resolved.name
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

        scorer = _Scorer(r, session, BrewScoringConfig.from_env())

        def fresh(names):
            return [n for n in dict.fromkeys(names) if norm_name(n) not in used]

        for name in scorer.pick(fresh(donor_cards), tier1_cap):
            add(name, 1, "previous_deck", 0.0, "Available from a named donor deck")

        tier2_names: List[str] = []
        if pools is not None:
            tier2_names = [pc.name for pc in pools.tier2]
        elif r is not None:
            try:
                tier2_names = [inst.get("card_name") for inst in
                               instance_store.list_instances(r, ownership_status="in_collection")
                               if inst.get("card_name")]
            except Exception:
                tier2_names = []
        for name in scorer.pick(fresh(tier2_names), tier2_cap):
            add(name, 2, "owned", 0.0, "Available from free inventory")

        if pools is not None:
            prices = {pc.name: (pc.unit_price if pc.unit_price is not None else 0.0) for pc in pools.tier3}
        else:
            prices = {"Arcane Signet": 1.25, "Command Tower": 0.50, "Sol Ring": 1.50}
        for name in scorer.pick(fresh(list(prices)), tier3_cap):
            add(name, 3, "new", float(prices[name]), "Buy to complete the brew")

        # Caps above only set the tier mix. Root cause of truncated decks was
        # stopping there (25+10+3 ~ 39 cards); keep topping up from every pool
        # (donor, owned, then new) until the deck is full or candidates run out.
        if len(cards) < DECK_SIZE:
            extra = [(n, 1, "previous_deck", 0.0, "Available from a named donor deck") for n in donor_cards]
            if pools is not None:
                extra += [(pc.name, 2, "owned", 0.0, "Available from free inventory") for pc in pools.tier2]
                extra += [
                    (pc.name, 3, "new", float(pc.unit_price if pc.unit_price is not None else 0.0),
                     "Buy to complete the brew")
                    for pc in pools.tier3
                ]
            for args in extra:
                if len(cards) >= DECK_SIZE:
                    break
                add(*args)
        self.last_report = report

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

        est_cards = [{**scorer.records.get(c["name"], {"name": c["name"]}), "quantity": c.get("quantity", 1)}
                     for c in cards]
        estimate = brew_power.estimate_power_level(est_cards, scorer.config)

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
            "target_power": session.get("target_power"),
            "estimated_power": estimate["power"],
            "metadata": {"estimated_power": estimate},
            "build_mode": build_mode,
            "cards": cards,
            "total_new_cost": total_new_cost,
            "is_complete": is_complete,
            "unpriced_cards": unpriced,
            "dropped_ids": list(report.dropped),
            "missing_slots": max(DECK_SIZE - len(cards), 0),  # hook for the fill step
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
