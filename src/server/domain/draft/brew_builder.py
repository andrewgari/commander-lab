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
from typing import Any, List, Optional

import src.server.domain.draft.brew_power as brew_power
import src.server.domain.draft.brew_rules as brew_rules
import src.server.domain.draft.brew_scoring as brew_scoring
import src.server.domain.draft.brew_edhrec_pool as edhrec_pool
import src.server.domain.draft.brew_panel as brew_panel
import src.server.domain.draft.brew_stages as stages
from src.server.domain.draft.brew_scoring_config import BrewScoringConfig, skeleton_from_template
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


_edhrec_fetch_enabled = False


def enable_edhrec_fetch(enabled: bool = True) -> None:
    """Let a build fetch a missing EDHREC commander page (wired on by the app; tests stay offline)."""
    global _edhrec_fetch_enabled
    _edhrec_fetch_enabled = bool(enabled)


def prefetch_edhrec(r: Any, commanders: List[str]) -> None:
    """Best-effort warm of the EDHREC cache for this commander so candidate ranking has signal.
    Never raises: a failed fetch leaves the scorer on its no-EDHREC path."""
    if not _edhrec_fetch_enabled or r is None or not commanders:
        return
    try:
        from src.server.domain.analytics.card_eval import edhrec_data

        edhrec_data.get_page(r, list(commanders))
    except Exception as exc:  # noqa: BLE001 - EDHREC is a ranking hint, never fatal
        import logging

        logging.getLogger(__name__).warning("brew: EDHREC prefetch failed for %s: %s", commanders, exc)


def candidate_ranker(r: Any, commanders: List[str]):
    """Tier 3 ranker (lowest key first): EDHREC inclusion for this commander, then cards the lab
    has tagged, then name. Replaces the alphabetical default that capped the pool at the A/B cards."""
    edhrec = _cached_edhrec(r, commanders)
    tagged = {k.casefold() for k in _load_lab_tags(r)}

    def key(card: Any):
        nk = card.name.casefold()
        inc = (edhrec.get(nk) or edhrec.get(norm_name(card.name)) or {}).get("inclusion") or 0.0
        return (-inc, 0 if nk in tagged else 1, card.name.casefold())

    return key


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
    out = {"name": name, "tags": tags, "oracle_id": rec.get("oracle_id") or "", "oracle_text": rec.get("oracle_text") or "", "type_line": tl}
    mv = rec.get("cmc", rec.get("mana_value"))
    if isinstance(mv, (int, float)) and not isinstance(mv, bool):
        out["cmc"] = mv
    return out


def session_template(session: dict) -> dict:
    """The composition template this brew builds to: the one stored on the session at start
    (form > profile > app default), else the app default (sessions that predate templates)."""
    tpl = session.get("composition_template")
    if isinstance(tpl, dict) and tpl.get("slots"):
        return tpl
    from src.server.domain.analytics import composition_service
    return composition_service.app_default_template()


def _reason(row: dict, available: Optional[int]) -> str:
    """Short why a category missed its target (composition is soft: this explains, never blocks)."""
    k, target, actual = row["category"], row["target"], row["actual"]
    if row.get("derived"):
        return "Plan is whatever the other categories leave"
    if actual < target:
        if available is not None and available < target:
            return f"Only {available} matching card(s) in the available pools"
        return "Higher-scored picks, or power/legality limits, left fewer matches than the target"
    return "Top-scored picks also matched this category (categories overlap)"


_BASIC_TYPE = "Basic Land"


def attach_composition(r: Any, session: dict, draft: dict, lab_tags: Any = None,
                       pool_hits: Optional[dict] = None, records: Optional[dict] = None) -> None:
    """Record the template used and target vs actual per category on `draft`.

    Soft: every row whose actual differs from its target carries a `reason`; nothing is rejected.
    `pool_hits` = {category: candidate names seen}; without it the counts from a previous report are kept.
    """
    tpl = session_template(session)
    skeleton = skeleton_from_template(tpl)
    previous = {row["category"]: row.get("available") for row in (draft.get("composition") or {}).get("rows", [])}
    lab_tags = lab_tags if lab_tags is not None else _load_lab_tags(r)
    cards = []
    for c in draft.get("cards") or []:
        name = c.get("name")
        rec = dict((records or {}).get(name) or _card_record(r, name, lab_tags))
        if not rec.get("type_line") and norm_name(name) in _BASIC_NAMES:
            rec["type_line"] = _BASIC_TYPE
        cards.append({**rec, "quantity": c.get("quantity", 1)})
    rows = brew_scoring.composition_report(cards, skeleton)
    for row in rows:
        k = row["category"]
        row["derived"] = skeleton[k].derived
        avail = len(pool_hits[k]) if pool_hits is not None and k in pool_hits else previous.get(k)
        if avail is not None:
            row["available"] = avail
        row["met"] = row["actual"] == row["target"]
        if not row["met"]:
            row["reason"] = _reason(row, avail)
    draft["composition_template"] = {"source": tpl.get("source"), "preset_id": tpl.get("preset_id"),
                                     "name": tpl.get("name")}
    draft["composition"] = {"template": draft["composition_template"], "rows": rows}


def apply_power_estimate(r: Any, session: dict, draft: dict, config: BrewScoringConfig,
                         records: Any = None) -> None:
    """Set target_power / estimated_power / metadata.estimated_power on a draft from its
    CURRENT cards. Cards without a scoring record (commanders, edits, LLM picks) are hydrated."""
    records = dict(records or {})
    lab_tags = None
    est_cards = []
    for c in draft.get("cards") or []:
        name = c.get("name")
        rec = records.get(name)
        if rec is None:
            if lab_tags is None:
                lab_tags = _load_lab_tags(r)
            rec = _card_record(r, name, lab_tags)
        q = c.get("quantity", 1)
        est_cards.append({**rec, "quantity": q if isinstance(q, int) else 1})
    estimate = brew_power.estimate_power_level(est_cards, config)
    draft["target_power"] = session.get("target_power")
    draft["estimated_power"] = estimate["power"]
    draft.setdefault("metadata", {})["estimated_power"] = estimate


_BASIC_FOR_COLOR = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
_BASIC_NAMES = frozenset(norm_name(n) for n in (*_BASIC_FOR_COLOR.values(), "Wastes"))


def _land_target(session: dict) -> int:
    """Lands the session's composition template wants (its `lands` slot, low bound); 36 if unset."""
    try:
        for slot in session_template(session).get("slots") or []:
            if slot.get("key") == "lands":
                return int(slot.get("low") or slot.get("target") or 36)
    except Exception:
        pass
    return 36


def _commander_identity(r: Any, commanders: List[str]) -> List[str]:
    """Ordered WUBRG colours of the commanders' identity ([] = colourless or unknown)."""
    if r is None:
        return []
    try:
        ident = reconstruct_pools.identity_of_cards(commanders, reconstruct_pools.RedisCardCatalog(r)) or frozenset()
    except Exception:
        return []
    return [c for c in "WUBRG" if c in ident]


def _fill_basics(cards: list, need: int, colors: List[str]) -> None:
    """Append `need` generated basic lands (Tier 3, free), split evenly over `colors` (Wastes if none).

    Runs AFTER costing so generated copies never inherit a donor/inventory entry's tier or
    availability: a basic already supplied by a finite Tier 1/2 pool is skipped when another
    colour is free (else the copies go in a separate Tier 3 entry); a Tier 3 basic is stacked.
    """
    basics = [_BASIC_FOR_COLOR[c] for c in colors] or ["Wastes"]
    pooled = {norm_name(c["name"]) for c in cards if c.get("tier") in (1, 2)}
    free = [b for b in basics if norm_name(b) not in pooled]
    targets = free or basics
    per = {b: need // len(targets) + (1 if i < need % len(targets) else 0) for i, b in enumerate(targets)}
    for b, n in per.items():
        if not n:
            continue
        for c in cards:
            if norm_name(c["name"]) == norm_name(b) and c.get("tier") not in (1, 2):
                c["quantity"] = int(c.get("quantity") or 1) + n
                break
        else:
            cards.append({"name": b, "quantity": n, "tier": 3, "tier_label": "new", "cost": 0.0,
                          "rationale": "Basic land to complete the 100-card deck"})


def _card_fact(r: Any, name: str) -> Optional[dict]:
    """{type_line, identity, legal} from the card database, or None when it does not know the card."""
    if r is None:
        return None
    rec = None
    try:
        from src.server.domain.inventory import cards as card_store
        rec = card_store.get_card_by_name(r, name)
        if not rec:
            raw = r.get(f"card_meta:{name}")
            rec = json.loads(raw) if raw else None
    except Exception:
        rec = None
    if not rec:
        return None
    tl = rec.get("type_line") or rec.get("type") or ""
    if isinstance(tl, (list, tuple)):
        tl = " ".join(str(x) for x in tl)
    legal = reconstruct_pools.RedisCardCatalog._legal(rec)
    return {"type_line": tl, "legal": legal,
            "identity": reconstruct_pools.parse_identity(rec.get("color_identity", rec.get("color")))}


def _augment_tier3(r: Any, pools: Any, pool: dict, identity: Optional[frozenset], price_lookup: Any) -> int:
    """Put the EDHREC pool's cards (best first) into Tier 3 when the capped, alphabetical catalog pool
    left them out, so they are priced, costed and available to every later stage. Returns how many."""
    if pools is None or not pool.get("cards"):
        return 0
    have = {norm_name(c.name) for c in pools.all_cards()}
    new = []
    for v in sorted(pool["cards"].values(), key=lambda v: (-(v.get("inclusion") or v.get("theme_inclusion") or 0.0), v["name"].casefold())):
        k = norm_name(v["name"])
        if k in have or k in _BASIC_NAMES:
            continue
        fact = _card_fact(r, v["name"])
        if not fact or fact["legal"] is False:
            continue
        if identity is not None and fact["identity"] is not None and not fact["identity"] <= identity:
            continue
        new.append((v["name"], fact["identity"]))
        have.add(k)
    if not new:
        return 0
    try:
        prices = price_lookup([n for n, _ in new]) or {}
    except Exception:
        prices = {}
    for n, ident in new:
        pools.tier3.append(reconstruct_pools.PoolCard(
            name=n, tier=reconstruct_pools.Tier.NEW, available_qty=None,
            unit_price=reconstruct_pools._clean_price(prices.get(n)), color_identity=ident))
    return len(new)


def _playstyle(session: dict) -> str:
    """One line for the panel: what this player enjoys, from the profile snapshot taken at session start."""
    snap = session.get("profile_snapshot") or {}
    parts = []
    if snap.get("builder_identity"):
        parts.append(str(snap["builder_identity"])[:200])
    if snap.get("power_tier"):
        parts.append(f"power tendency: {snap['power_tier']}")
    if snap.get("power_notes"):
        parts.append(str(snap["power_notes"])[:160])
    if snap.get("welcome_generic_staples") is not None:
        parts.append("welcomes generic staples" if snap["welcome_generic_staples"] else "prefers quirky/unique cards over generic staples")
    dis = [*(snap.get("dislikes_strategies") or []), *(snap.get("dislikes_play_patterns") or [])]
    if dis:
        parts.append("dislikes: " + ", ".join(map(str, dis[:8])))
    return "; ".join(parts)


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
        self.skeleton = skeleton_from_template(session_template(session))
        self.pool_hits: dict = {}  # category -> candidate names seen matching it
        self.lab_tags = _load_lab_tags(r)
        self.edhrec = _cached_edhrec(r, list(session.get("commanders") or []))
        self.gate: Any = None  # TierGate; set by the builder so picks are admission-aware
        self.source_names: frozenset = frozenset()
        self.source_oracle_ids: frozenset = frozenset()

    def set_sources(self, names: List[str]) -> None:
        """Register the user's source cards (donor decks + owned inventory) for the source bonus."""
        self.source_names, self.source_oracle_ids = brew_scoring.build_source_index(
            [_card_record(self.r, n) for n in names if n])

    def unpick(self, name: str) -> None:
        """Undo a pick's effect on scoring state (a reviewer panel cut it)."""
        c = self.records.pop(name, None)
        if c is None:
            return
        for k in set(brew_scoring._matching(c, self.skeleton)):
            self.counts[k] = max(self.counts.get(k, 0) - 1, 0)
        mv = brew_power.mana_value(c)
        if mv is not None and not brew_power.is_land(c) and self.mv_n:
            self.mv_sum, self.mv_n = self.mv_sum - mv, self.mv_n - 1

    def pick(self, names: List[str], cap: int, step: int = 1) -> List[str]:
        """Greedy scored pick. `step` = admitted cards per re-score (1 = exact; larger trades exactness for speed)."""
        if cap <= 0:
            return []
        recs = [_card_record(self.r, n, self.lab_tags) for n in names]
        for rec in recs:
            for k in brew_scoring._matching(rec, self.skeleton):
                self.pool_hits.setdefault(k, set()).add(rec["name"])
        chosen: list = []
        # Admission-aware greedy: only admitted cards consume slots or update scoring state.
        while len(chosen) < cap and recs:
            ctx = brew_scoring.ScoreContext(self.commander, self.themes, self.counts, self.skeleton, self.edhrec,
                                            source_names=self.source_names, source_oracle_ids=self.source_oracle_ids,
                                            target_power=self.target_power, mv_sum=self.mv_sum, mv_n=self.mv_n)
            batch = brew_scoring.select_cards(recs, ctx, cap - len(chosen), self.config)
            if not batch:
                break
            progressed = False
            taken = 0
            for c in batch:
                recs = [x for x in recs if x["name"] != c["name"]]
                if self.gate is not None and not self.gate.admit(c):
                    continue
                progressed = True
                chosen.append(c)
                self.records[c["name"]] = c
                mv = brew_power.mana_value(c)
                if mv is not None and not brew_power.is_land(c):
                    self.mv_sum, self.mv_n = self.mv_sum + mv, self.mv_n + 1
                for k in set(brew_scoring._matching(c, self.skeleton)):
                    self.counts[k] = self.counts.get(k, 0) + 1
                taken += 1
                if taken >= step:
                    break  # re-score every `step` admitted cards (1 = after each, exact)
            if not progressed and not recs:
                break
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

        progress_session = session  # stage progress / panel records are written onto the caller's session
        session, report = _resolve_session(session, r)
        commanders = list(session.get("commanders") or [])
        if not commanders:
            raise ValueError("a commander is required to brew a deck")
        tracker = stages.StageTracker(r, progress_session)
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

        # Approved hard rules: violating donor cards are removed like every other pool's.
        rules = brew._session_rules(session)
        rule_removed: List[dict] = list(getattr(pools, "rule_removed", None) or [])
        if rules:
            donor_cards, gone = brew_rules.filter_names(rules, donor_cards, r, commanders)
            rule_removed += gone

        # Donor cards outside the commander's colour identity never enter the deck (their identity
        # is known from the pool's catalog lookup; unknown identity is left to the review gate).
        if pools is not None and donor_cards:
            allowed = reconstruct_pools.parse_identity(pools.config.color_identity)
            if allowed is not None:
                ci = {norm_name(c.name): c.color_identity for c in pools.tier1}
                off = [n for n in donor_cards if ci.get(norm_name(n)) is not None and not ci[norm_name(n)] <= allowed]
                if off:
                    donor_cards = [n for n in donor_cards if n not in off]

        cards: list[dict] = []
        used = set()
        scorer = _Scorer(r, session, BrewScoringConfig.from_env())
        gate = brew_power.TierGate(scorer.target_power, scorer.config.power)
        scorer.gate = gate

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
            gate.count(_card_record(r, cmd, scorer.lab_tags))  # commanders can't be rejected but use up the tier budget

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
            if not gate.admit(_card_record(r, name, scorer.lab_tags)):
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


        snap = session.get("profile_snapshot") or {}
        rejected = {norm_name(n) for n in (snap.get("dislikes_cards") or [])}  # panel cuts + profile dislikes

        def fresh(names):
            return [n for n in dict.fromkeys(names) if norm_name(n) not in used and norm_name(n) not in rejected]

        tier2_all: List[str] = []
        if pools is not None:
            tier2_all = [pc.name for pc in pools.tier2]
        elif r is not None:
            try:
                tier2_all = [inst.get("card_name") for inst in
                             instance_store.list_instances(r, ownership_status="in_collection")
                             if inst.get("card_name")]
            except Exception:
                tier2_all = []
        scorer.set_sources(donor_cards + tier2_all)

        colors = _commander_identity(r, commanders)
        identity = frozenset(colors)
        price_lookup = reconstruct_pools.redis_price_lookup(r) if r is not None else (lambda names: {})
        price_memo: dict = {}

        def price_of(n):
            if n in price_memo:
                return price_memo[n]
            pc = pools.get(n) if pools is not None else None
            p = pc.unit_price if pc is not None else None
            if p is None and pc is None:
                try:
                    p = reconstruct_pools._clean_price((price_lookup([n]) or {}).get(n))
                except Exception:
                    p = None
            price_memo[n] = p
            return p

        def is_owned(n):
            t = pools.tier_of(n) if pools is not None else None
            return t is not None and int(t) in (1, 2)

        fact_memo: dict = {}

        def fact(n):
            if n not in fact_memo:
                fact_memo[n] = _card_fact(r, n)
            return fact_memo[n]

        def is_land(n):
            f = fact(n)
            return bool(f and "land" in (f["type_line"] or "").lower())

        def eligible(n):
            """Known to the card database, not banned, inside the commander's colour identity."""
            if r is None:
                return True
            f = fact(n)
            return bool(f) and f["legal"] is not False and (f["identity"] is None or f["identity"] <= identity)

        def rule_filter(names):
            names = [n for n in names if eligible(n)]
            return brew_rules.filter_names(rules, names, r, commanders)[0] if rules else names
        def drop_card(name):
            k = norm_name(name)
            for i, c in enumerate(cards):
                if norm_name(c["name"]) == k and norm_name(c["name"]) not in {norm_name(x) for x in commanders}:
                    del cards[i]
                    used.discard(k)
                    rejected.add(k)
                    scorer.unpick(c["name"])
                    return True
            return False

        def panel_stage(stage, proposals, candidates, protected=()):
            hi = {n for n in proposals if (price_of(n) or 0.0) >= brew_panel.HIGH_IMPACT_PRICE
                  or n.lower() in brew_power.FAST_MANA_NAMES or n.lower() in brew_power.COMBO_PIECE_NAMES}
            ctx = {"commanders": commanders, "target_power": scorer.target_power, "deck_idea": session.get("deck_idea"),
                   "notes": session.get("notes"), "themes": session.get("themes"), "playstyle": _playstyle(session),
                   "template": [f"{k}: {v.target}" for k, v in scorer.skeleton.items()],
                   "deck": [c["name"] for c in cards][:100]}
            out = brew_panel.review_stage(stage, ctx, proposals, fresh(list(candidates))[:60], high_impact=hi,
                                          protected=protected)
            brew_panel.persist(progress_session, stage, out.record)
            for n in out.cuts:
                drop_card(n)
            added = [n for n in out.adds if n not in out.cuts and add(n, 3, "new", price_of(n) or 0.0, "Panel suggestion")] if out.adds else []
            out.record["added"] = added
            return out

        # ---- Stage 1: skeleton (deterministic, no LLM) ----
        lands_target = stages.land_target(session_template(session))
        tracker.start(stages.STAGE_SKELETON, f"planning {lands_target} lands and downloading EDHREC data")
        sk = stages.run_skeleton(r, session, colors, session_template(session))
        _augment_tier3(r, pools, sk["edhrec"], identity if r is not None else None, price_lookup)
        tracker.finish(stages.STAGE_SKELETON, stages.skeleton_summary(sk), degraded=sk.get("degraded"),
                       message=(f"{sk['lands_target']} lands planned, {len(sk['edhrec'].get('cards') or {})} EDHREC cards"
                                if not sk.get("degraded") else f"{sk['lands_target']} lands planned; {sk['degraded']}"))

        # ---- Stage 2: core construction ----
        tracker.start(stages.STAGE_CORE, "landbase and core spells")
        core = stages.run_core(r=r, session=session, colors=colors, skeleton_out=sk, scorer=scorer, add=add, fresh=fresh,
                               is_land=is_land, card_fact=fact, price_of=price_of, is_owned=is_owned,
                               rule_filter=rule_filter, identity=identity if r is not None else None)
        core_names = [l["name"] for l in core.lands] + list(core.spells)
        if core_names:
            tracker.note(f"reviewing {len(core_names)} core picks")
            edhrec_rest = [v["name"] for v in sorted((sk["edhrec"].get("cards") or {}).values(),
                                                     key=lambda v: -(v.get("inclusion") or 0.0))]
            pr = panel_stage("core", core_names, edhrec_rest,
                             protected=[c["name"] for c in edhrec_pool.core_cards(sk["edhrec"])])
        tracker.finish(stages.STAGE_CORE, {"lands": [l["name"] for l in core.lands], "spells": len(core.spells),
                                           "by_source": core.by_source,
                                           "panel": (progress_session.get("stage_reviews") or {}).get("core", {}).get("status")},
                       degraded=core.degraded, message=f"{len(core.lands)} nonbasic lands, {len(core.spells)} core spells")

        # ---- Stage 3: deck construction (donors, owned, then the ranked new pool) ----
        tracker.start(stages.STAGE_DECK, "choosing cards for the notes and playstyle")
        stage3_from = len(cards)
        edhrec_order = [v["name"] for v in sorted((sk["edhrec"].get("cards") or {}).values(),
                                                  key=lambda v: (-(v.get("synergy") or 0.0) - (v.get("inclusion") or 0.0), v["name"].casefold()))]

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
        edhrec_first = [n for n in edhrec_order if n in prices] + [n for n in prices if n not in set(edhrec_order)]
        for name in scorer.pick(fresh(edhrec_first), tier3_cap):
            add(name, 3, "new", float(prices[name]), "Buy to complete the brew")

        # Caps above only set the tier mix. Keep topping up from every pool (donor,
        # owned, then new) until the deck is full or candidates run out, but
        # (a) rank the remaining candidates with the scorer instead of taking pool
        # order (alphabetical when EDHREC data is absent) and (b) reserve the land
        # slots the template asks for (the skeleton's plan) so spells cannot crowd out the mana base.
        land_reserve = sk["lands_target"]

        def lands_in_deck() -> int:
            return sum(int(c.get("quantity") or 1) for c in cards if is_land(c["name"]) or norm_name(c["name"]) in _BASIC_NAMES)

        def room_for_spells() -> int:
            return DECK_SIZE - len(cards) - max(land_reserve - lands_in_deck(), 0)

        def top_up():
            if len(cards) >= DECK_SIZE:
                return
            tiers: list = [(donor_cards, 1, "previous_deck", {}, "Available from a named donor deck")]
            if pools is not None:
                tiers.append(([pc.name for pc in pools.tier2], 2, "owned", {}, "Available from free inventory"))
                tiers.append(([pc.name for pc in pools.tier3], 3, "new", dict(prices), "Buy to complete the brew"))
            for names, tier, label, cost_of, why in tiers:
                while room_for_spells() > 0:
                    cands = fresh(names)
                    if not cands:
                        break
                    picked = scorer.pick(cands, room_for_spells(), step=8)
                    added = [n for n in picked if add(n, tier, label, float(cost_of.get(n, 0.0) or 0.0), why)]
                    if not added:
                        break
            # Still short of spells (small pools): spend any remaining slots in pool order,
            # still leaving room for the land reserve.
            for names, tier, label, cost_of, why in tiers:
                for n in fresh(names):
                    if room_for_spells() <= 0:
                        break
                    add(n, tier, label, float(cost_of.get(n, 0.0) or 0.0), why)

        top_up()

        stage3 = [c["name"] for c in cards[stage3_from:] if norm_name(c["name"]) not in _BASIC_NAMES]
        if stage3:
            tracker.note(f"reviewing {len(stage3)} picks")
            panel_stage("deck", stage3, edhrec_order,
                        protected=[c["name"] for c in cards if c.get("tier") in (1, 2)])
            top_up()  # refill whatever the panel cut
        tracker.finish(stages.STAGE_DECK, {"added": len(cards) - stage3_from,
                                           "panel": (progress_session.get("stage_reviews") or {}).get("deck", {}).get("status")},
                       message=f"{len(cards)} cards chosen")

        self.last_report = report

        if rules and rule_removed and sum(1 for c in cards if norm_name(c["name"]) not in _BASIC_NAMES) \
                < brew.MIN_BUILD_CARDS:
            # Hard rules emptied the pools: say so, never pad the deck with basic lands.
            kept = sum(1 for c in cards if norm_name(c["name"]) not in _BASIC_NAMES)
            raise brew_rules.RulesStarvedError(brew_rules.starved_message(rule_removed, kept, brew.MIN_BUILD_CARDS))

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

        # Pools exhausted (small in-identity pool): fill the remainder with basics
        # matching the commander identity (Wastes if colourless).
        short = DECK_SIZE - sum(int(c.get("quantity") or 1) for c in cards)
        if short > 0:
            _fill_basics(cards, short, _commander_identity(r, commanders))

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
            "build_mode": build_mode,
            "cards": cards,
            "total_new_cost": total_new_cost,
            "is_complete": is_complete,
            "unpriced_cards": unpriced,
            "dropped_ids": list(report.dropped),
            "rule_removed": [{"card": x["card"], "rule": x["rule"]} for x in
                             {(x["card"], x["rule"]): x for x in rule_removed}.values()],
            "missing_slots": max(DECK_SIZE - sum(int(c.get("quantity") or 1) for c in cards), 0),  # hook for the fill step
            "explanation": (
                f"Brew draft ({build_mode}) for {', '.join(commanders)}"
                + (f" — themes: {', '.join(themes)}." if themes else ".")
            ),
        }
        apply_power_estimate(r, session, draft, scorer.config, scorer.records)
        attach_composition(r, session, draft, scorer.lab_tags, scorer.pool_hits, scorer.records)
        return [draft]


def brew_rules_of(session: dict) -> List[dict]:
    return [x for x in (session.get("approved_rules") or []) if session.get("rules_approved")]


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
        rule_note = "; ".join(r_["label"] for r_ in brew_rules_of(session))
        if rule_note:
            note = (note + " " if note else "") + f"HARD RULES (never include a violating card): {rule_note}."
        if prefs:
            prefs[0]["notes"] = " ".join(x for x in [prefs[0].get("notes", ""), note] if x).strip()
        inner_session["preferences"] = prefs
        tp = session.get("target_power")
        if prefs and tp is not None:
            prefs[0]["notes"] = (prefs[0].get("notes", "") + f" Target power level {tp:g} on a 1-10 scale: "
                                 "use fast mana, tutors, combo pieces and Game Changers only if that is high "
                                 "(7+), avoid them if low (4 or less), and keep a modest average mana value.").strip()
        drafts = self._inner.build(inner_session, r)[:1]
        cfg = BrewScoringConfig.from_env()
        for d in drafts:
            apply_power_estimate(r, session, d, cfg)
            attach_composition(r, session, d)
        return drafts


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
