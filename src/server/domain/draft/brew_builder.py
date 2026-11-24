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

import dataclasses
import hashlib
import json
import re
from typing import Any, List, Optional

import src.server.domain.draft.brew_power as brew_power
import src.server.domain.draft.brew_rules as brew_rules
import src.server.domain.draft.brew_scoring as brew_scoring
import src.server.domain.draft.brew_edhrec_pool as edhrec_pool
import src.server.domain.draft.brew_panel as brew_panel
import src.server.domain.draft.brew_stages as stages
import src.server.domain.draft.brew_plan as brew_plan
from src.server.domain.draft.brew_scoring_config import BrewScoringConfig, skeleton_from_template
import src.server.domain.inventory.instances as instance_store
import src.server.domain.draft.reconstruct as reconstruct
import src.server.domain.draft.reconstruct_pools as reconstruct_pools
from src.server.domain.draft.card_resolution import ResolutionReport, resolve_card
from src.server.domain.draft.reconstruct_pools import norm_name

DECK_SIZE = 100  # commander(s) + the rest; the stub tops up to this
PLAN_SLOTS = 30            # plan-driven picks in stage 3 (before the generic top-up)
SECRET_TUTOR_SLOTS = 3     # tutors that can fetch the secret commander, pinned ahead of the generic plan picks
SECRET_POOL_SCAN = 1500    # pool cards scanned for tutor text (the live Scryfall search covers the rest)
PLAN_SCRYFALL_CALLS = 8    # live Scryfall searches per build for the plan (results cached 24h)
EDHREC_PRIOR_SCALE = 0.25  # stage 3 weighs EDHREC inclusion/synergy at this fraction: a weak prior, not the ranking


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


def _secret_of(session: dict) -> str:
    return str((session or {}).get("secret_commander") or "").strip()


def _card_rec(r: Any, name: str) -> Optional[dict]:
    """The card database record for `name` (canonical record, then legacy card_meta), or None."""
    if r is None or not name:
        return None
    try:
        from src.server.domain.inventory import cards as card_store
        rec = card_store.get_card_by_name(r, name)
        if not rec:
            raw = r.get(f"card_meta:{name}")
            rec = json.loads(raw) if raw else None
        return rec or None
    except Exception:
        return None


def validate_secret_commander(r: Any, name: str, commanders: List[str], bracket: Any) -> str:
    """The canonical card name of a valid secret commander. ValueError (plain message -> HTTP 400) when
    the card is unknown, not Commander-legal, outside the real commander's colour identity, or breaks
    the chosen Bracket (the Bracket gate wins over everything)."""
    rec = _card_rec(r, name)
    if not rec:
        raise ValueError(f"secret commander {name!r} is not a card we know; check the spelling")
    canon = str(rec.get("name") or name)
    if any(norm_name(canon) == norm_name(c) for c in commanders):
        raise ValueError(f"secret commander {canon!r} is already your commander; pick a different card")
    if reconstruct_pools.RedisCardCatalog._legal(rec) is False:
        raise ValueError(f"secret commander {canon!r} is not legal in Commander")
    ident = reconstruct_pools.parse_identity(rec.get("color_identity", rec.get("color")))
    allowed = reconstruct_pools.identity_of_cards(commanders, reconstruct_pools.RedisCardCatalog(r))
    if allowed is None:
        raise ValueError("cannot check the secret commander's colours: the real commander is not in the card database")
    if ident is not None and not ident <= allowed:
        raise ValueError(f"secret commander {canon!r} is outside your commander's colour identity")
    b = brew_power.BracketGate(bracket)
    if not b.admit(_card_record(r, canon)):
        raise ValueError(f"secret commander {canon!r} breaks Bracket {b.bracket} "
                         "(Game Changers, tutors, combos or other limits for that Bracket); raise the Bracket or pick another card")
    return canon


_SUPERTYPES = {"legendary", "basic", "snow", "world", "ongoing", "host", "elite"}


def secret_type_words(type_line: str) -> List[str]:
    """Card types and subtypes of the secret commander (Equipment, Vehicle, Creature, ...), most specific first."""
    tl = type_line or ""
    left, _, right = tl.partition("—")
    words = [w for w in right.split() if w] + [w for w in left.split() if w]
    return [w for w in dict.fromkeys(words) if w.lower() not in _SUPERTYPES]


def fetches_secret(oracle_text: str, words: List[str]) -> int:
    """0 = not a tutor for the secret commander, 1 = generic tutor (any card), 2 = type-specific tutor."""
    best = 0
    for seg in re.findall(r"search your library for ([^.;\n]*)", (oracle_text or "").lower()):
        if re.match(r"(?:a|an|any|up to \w+) (?:other )?cards?\b", seg):
            best = max(best, 1)
        elif any(re.search(rf"\b{re.escape(w.lower())}s?\b", seg) for w in words):
            best = max(best, 2)
    return best


def secret_tutor_queries(words: List[str], ident: str) -> List[tuple]:
    """Scryfall searches for ways to find the secret commander: type-specific first, then any-card tutors."""
    suffix = f" f:commander id<={ident or 'c'} -t:land"
    out = [(f"secret_tutor:{w.lower()}", f'o:"search your library for" o:"{w.lower()} card"{suffix}') for w in words[:3]]
    out.append(("secret_tutor:any", f'o:"search your library for a card"{suffix}'))
    return out


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


def candidate_ranker(r: Any, commanders: List[str], use_edhrec: bool = True):
    """Tier 3 ranker (lowest key first): EDHREC inclusion for this commander, then cards the lab
    has tagged, then name. Replaces the alphabetical default that capped the pool at the A/B cards."""
    edhrec = _cached_edhrec(r, commanders) if use_edhrec else {}
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


def _augment_tier3(r: Any, pools: Any, names: List[str], identity: Optional[frozenset], price_lookup: Any) -> int:
    """Put `names` (in the order given) into Tier 3 when the capped catalog pool left them out, so they are
    priced, costed and available to every later stage. Unknown, banned and off-identity cards are skipped.
    Returns how many were added."""
    if pools is None or not names:
        return 0
    have = {norm_name(c.name) for c in pools.all_cards()}
    new = []
    for n in names:
        k = norm_name(n)
        if k in have or k in _BASIC_NAMES:
            continue
        fact = _card_fact(r, n)
        if not fact or fact["legal"] is False:
            continue
        if identity is not None and fact["identity"] is not None and not fact["identity"] <= identity:
            continue
        new.append((n, fact["identity"]))
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


def _estimate(cards: list, rec_of: Any, config: Any) -> float:
    est = [{**rec_of(c["name"]), "quantity": int(c.get("quantity") or 1)} for c in cards]
    return brew_power.estimate_power_level(est, config)["power"]


POWER_NUDGE_GAP = 0.75
POWER_NUDGE_SWAPS = 6


def _power_nudge(r, session, cards, scorer, add, drop_card, fresh, rule_filter, prov, rec_of, is_land, gate, ident, budget) -> dict:
    """When the deck's estimated power is clearly under the target, swap a few non-functional generic cards for
    stronger, efficient ones (tutors, Game Changers, fast mana). Power is only a soft guide: every swap still goes through
    add() and the hard Bracket gate, so the bracket's limits are never exceeded."""
    out = {"before": None, "after": None, "out": [], "in": []}
    target = scorer.target_power
    if target is None or getattr(gate, "restrict_mana", False):  # Bracket 1-2: no fast mana / tutor swaps
        return out
    out["before"] = _estimate(cards, rec_of, scorer.config)
    if out["before"] >= target - POWER_NUDGE_GAP:
        out["after"] = out["before"]
        return out
    deps = stages.get_deps()
    names = list(brew_power.FAST_MANA_NAMES)
    names = [n.title() for n in names]
    for q in ("is:gamechanger", "otag:tutor"):
        full = f"{q} f:commander id<={ident or 'c'} -t:land"
        names += stages.scryfall_query_names(r, "power:" + hashlib.sha1(full.encode()).hexdigest()[:16], full, deps, budget)
    cands = fresh(rule_filter(list(dict.fromkeys(names))))
    victims = [c["name"] for c in cards
               if prov.get(c["name"]) in (stages.PROV_FILL, stages.SCAFFOLD_EDHREC) and not is_land(c["name"])
               and not brew_scoring._matching(rec_of(c["name"]), scorer.skeleton)]
    for _ in range(min(POWER_NUDGE_SWAPS, len(victims))):
        picked = scorer.pick(cands, 1)
        if not picked:
            break
        n = picked[0]
        cands = [c for c in cands if c != n]
        if add(n, 3, "new", 0.0, "Power: swapped in to close the gap to the target power", source="power_swap"):
            gone = victims.pop(0)
            drop_card(gone)
            out["in"].append(n)
            out["out"].append(gone)
    out["after"] = _estimate(cards, rec_of, scorer.config)
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
        self.skeleton = skeleton_from_template(session_template(session))
        self.pool_hits: dict = {}  # category -> candidate names seen matching it
        self.lab_tags = _load_lab_tags(r)
        self.secret = _secret_of(session)
        # A secret commander ignores popular lists for the real commander (no popularity ranking signal).
        self.edhrec = {} if self.secret else _cached_edhrec(r, list(session.get("commanders") or []))
        self.gate: Any = None  # BracketGate; set by the builder so picks are admission-aware
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
        gate = brew_power.BracketGate(session.get("target_bracket"))
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

        prov: dict = {}          # card name -> provenance (see brew_stages: scaffold_* / plan / donor / owned / fill)
        plan_set: set = set()    # normalised names found by the deck plan (stage 3)

        def add(name, tier, tier_label, cost, rationale, source=None):
            # Single resolution point: an unresolvable id is dropped, never emitted.
            resolved = resolve_card(name, r)
            if resolved is None:
                report.drop(name)
                return False
            name = resolved.name
            if norm_name(name) in used:
                return False
            if tier != 1 and over_cap(name):  # template ranges are caps for everything but the user's own donor cards
                return False
            if not gate.admit(_card_record(r, name, scorer.lab_tags)):
                return False
            cards.append({
                "name": name, "quantity": 1, "tier": tier,
                "tier_label": tier_label, "cost": cost, "rationale": rationale,
            })
            used.add(norm_name(name))
            prov[name] = source or (stages.PROV_DONOR if tier == 1 else stages.PROV_OWNED if tier == 2
                                    else stages.PROV_PLAN if norm_name(name) in plan_set else stages.PROV_FILL)
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

        def lands_in_deck() -> int:
            return sum(int(c.get("quantity") or 1) for c in cards if is_land(c["name"]) or norm_name(c["name"]) in _BASIC_NAMES)

        rec_memo: dict = {}

        def rec_of(n):
            if n not in rec_memo:
                rec_memo[n] = scorer.records.get(n) or _card_record(r, n, scorer.lab_tags)
            return rec_memo[n]

        land_slot = scorer.skeleton.get("lands")
        land_high = land_slot.high if land_slot else stages.land_target(session_template(session)) + 1

        def over_cap(n):
            """Would adding `n` push the deck past the template: lands past the lands slot's top, or any
            steered category (ramp, draw, removal, ...) past its top? Basics are exempt (they are the filler)."""
            if norm_name(n) in _BASIC_NAMES:
                return False
            if is_land(n):
                return lands_in_deck() >= land_high
            keys = [k for k in brew_scoring._matching(rec_of(n), scorer.skeleton)
                    if not scorer.skeleton[k].derived and scorer.skeleton[k].steer]
            if not keys:
                return False
            have: dict = {}
            for c in cards:
                if not is_land(c["name"]):
                    for k in set(brew_scoring._matching(rec_of(c["name"]), scorer.skeleton)):
                        have[k] = have.get(k, 0) + 1
            return any(have.get(k, 0) >= scorer.skeleton[k].high for k in keys)

        def eligible(n):
            """Known to the card database, not banned, inside the commander's colour identity."""
            if r is None:
                return True
            f = fact(n)
            return bool(f) and f["legal"] is not False and (f["identity"] is None or f["identity"] <= identity)

        secret = _secret_of(session)
        secret_key = norm_name(secret) if secret else None

        def rule_filter(names):
            names = [n for n in names if eligible(n)]
            return brew_rules.filter_names(rules, names, r, commanders)[0] if rules else names
        def drop_card(name):
            k = norm_name(name)
            for i, c in enumerate(cards):
                if norm_name(c["name"]) == k and norm_name(c["name"]) not in {norm_name(x) for x in commanders} \
                        and k != secret_key:
                    del cards[i]
                    used.discard(k)
                    rejected.add(k)
                    scorer.unpick(c["name"])
                    return True
            return False

        plan_labels: list = []

        def panel_stage(stage, proposals, candidates, protected=()):
            hi = {n for n in proposals if (price_of(n) or 0.0) >= brew_panel.HIGH_IMPACT_PRICE
                  or n.lower() in brew_power.FAST_MANA_NAMES or n.lower() in brew_power.COMBO_PIECE_NAMES}
            ctx = {"commanders": commanders, "secret_commander": secret, "target_power": scorer.target_power, "deck_idea": session.get("deck_idea"),
                   "notes": session.get("notes"), "themes": session.get("themes"), "playstyle": _playstyle(session),
                   "template": [f"{k}: {v.target}" for k, v in scorer.skeleton.items()],
                   "deck": [c["name"] for c in cards][:100],
                   "plan": plan_labels,
                   "scaffold": [n for n in proposals if prov.get(n) == stages.SCAFFOLD_EDHREC]}
            out = brew_panel.review_stage(stage, ctx, proposals, fresh(list(candidates))[:60], high_impact=hi,
                                          protected=protected)
            brew_panel.persist(progress_session, stage, out.record)
            for n in out.cuts:
                drop_card(n)
            added = [n for n in out.adds if n not in out.cuts and add(n, 3, "new", price_of(n) or 0.0, "Panel suggestion", source=stages.PROV_PLAN)] if out.adds else []
            out.record["added"] = added
            return out

        secret_words: List[str] = []
        if secret:  # guaranteed a slot in the 99, pinned like a hard include (the Bracket gate was checked at start)
            srec = _card_record(r, secret, scorer.lab_tags)
            secret = resolve_card(secret, r).name if resolve_card(secret, r) else secret
            secret_key = norm_name(secret)
            secret_words = secret_type_words(srec.get("type_line") or "")
            if secret_key not in used:
                cards.append({"name": secret, "quantity": 1, "tier": 2 if is_owned(secret) else 3,
                              "tier_label": "owned" if is_owned(secret) else "new",
                              "cost": 0.0 if is_owned(secret) else float(price_of(secret) or 0.0),
                              "rationale": "Secret commander: the deck is built around this card"})
                used.add(secret_key)
                prov[secret] = stages.PROV_PLAN
                gate.count(srec)

        # ---- Stage 1: skeleton (deterministic, no LLM) ----
        lands_target = stages.land_target(session_template(session))
        tracker.start(stages.STAGE_SKELETON, f"planning {lands_target} lands and downloading EDHREC data")
        sk = stages.run_skeleton(r, session, colors, session_template(session))
        _augment_tier3(r, pools, [v["name"] for v in sorted((sk["edhrec"].get("cards") or {}).values(), key=lambda v: (
            -(v.get("inclusion") or v.get("theme_inclusion") or 0.0), v["name"].casefold()))],
                       identity if r is not None else None, price_lookup)
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
                             protected=[c["name"] for c in edhrec_pool.core_cards(sk["edhrec"])] + ([secret] if secret else []))
        tracker.finish(stages.STAGE_CORE, {"lands": [l["name"] for l in core.lands], "spells": len(core.spells),
                                           "by_source": core.by_source,
                                           "panel": (progress_session.get("stage_reviews") or {}).get("core", {}).get("status")},
                       degraded=core.degraded, message=f"{len(core.lands)} nonbasic lands, {len(core.spells)} core spells")

        # ---- Stage 3: deck construction. EDHREC was scaffolding; now the deck's own plan drives. ----
        tracker.start(stages.STAGE_DECK, "building the plan from your notes and the commander's text")
        stage3_from = len(cards)
        cmd_rec = _card_record(r, commanders[0], scorer.lab_tags)
        sec_rec = _card_record(r, secret, scorer.lab_tags) if secret else {}
        # Archetype inference starts from the secret commander; the real commander's own text still counts.
        plan_items = brew_plan.build_plan(list(session.get("themes") or []),
                                          f"{session.get('notes') or ''}\n{session.get('deck_idea') or ''}",
                                          f"{sec_rec.get('oracle_text') or ''}\n{cmd_rec.get('oracle_text') or ''}",
                                          (sec_rec.get("type_line") if brew_plan.creature_types(sec_rec.get("type_line") or "")
                                           else cmd_rec.get("type_line")) or "")
        plan_labels.extend(i.label for i in plan_items)
        ident_str = "".join(colors).lower()
        sf_budget = [PLAN_SCRYFALL_CALLS]
        plan_names: List[str] = []
        for item in plan_items:
            q = brew_plan.full_query(item, ident_str)
            plan_names += stages.scryfall_query_names(
                r, "plan:" + hashlib.sha1(q.encode()).hexdigest()[:16], q, stages.get_deps(), sf_budget)
        plan_names = rule_filter(list(dict.fromkeys(plan_names)))
        plan_set.update(norm_name(n) for n in plan_names)
        _augment_tier3(r, pools, plan_names, identity if r is not None else None, price_lookup)
        # EDHREC is a weak prior from here on; the plan's own terms weigh more in the commander-synergy score.
        scorer.config = dataclasses.replace(
            scorer.config, edhrec_inclusion=scorer.config.edhrec_inclusion * EDHREC_PRIOR_SCALE,
            edhrec_synergy=scorer.config.edhrec_synergy * EDHREC_PRIOR_SCALE,
            commander_synergy=scorer.config.commander_synergy * 2)
        scorer.themes = tuple(dict.fromkeys([*scorer.themes, *brew_plan.theme_terms(plan_items)]))

        secret_tutors: List[str] = []
        if secret:  # extra ways to find the secret commander, type-specific tutors first, inside the Bracket gate
            ranked: dict = {}
            for cid, q in secret_tutor_queries(secret_words, ident_str):
                for n in stages.scryfall_query_names(r, "plan:" + hashlib.sha1(q.encode()).hexdigest()[:16], q,
                                                     stages.get_deps(), sf_budget):
                    ranked.setdefault(n, 2 if cid != "secret_tutor:any" else 1)
            pool_names = ([pc.name for pc in pools.tier2] + [pc.name for pc in pools.tier3])[:SECRET_POOL_SCAN] \
                if pools is not None else []
            for n in pool_names:
                lvl = fetches_secret(_card_record(r, n, scorer.lab_tags).get("oracle_text") or "", secret_words)
                if lvl:
                    ranked[n] = max(ranked.get(n, 0), lvl)
            tut = rule_filter([n for n in sorted(ranked, key=lambda n: (-ranked[n], n.casefold())) if norm_name(n) != secret_key])
            _augment_tier3(r, pools, tut, identity if r is not None else None, price_lookup)
            for n in tut:  # ranked order is kept: type-specific tutors first
                if len(secret_tutors) >= SECRET_TUTOR_SLOTS:
                    break
                if n in fresh([n]) and add(n, 2 if is_owned(n) else 3, "owned" if is_owned(n) else "new",
                                           0.0 if is_owned(n) else (price_of(n) or 0.0),
                                           f"Tutor: can fetch your secret commander ({secret})", source=stages.PROV_PLAN):
                    scorer.records[n] = _card_record(r, n, scorer.lab_tags)
                    secret_tutors.append(n)

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

        # Provisional scaffold: EDHREC-sourced cards that serve no template category and are not on the plan are
        # "just popular picks"; make room for plan-driven cards by cutting up to half of them (lowest inclusion first).
        scaffold_cut: List[str] = []
        if plan_names:
            def functional(n):
                return bool(brew_scoring._matching(scorer.records.get(n) or _card_record(r, n, scorer.lab_tags), scorer.skeleton))
            popular_only = [c["name"] for c in cards
                            if prov.get(c["name"]) == stages.SCAFFOLD_EDHREC and norm_name(c["name"]) not in plan_set
                            and not is_owned(c["name"]) and not functional(c["name"])]
            inc = {v["name"]: (v.get("inclusion") or 0.0) for v in (sk["edhrec"].get("cards") or {}).values()}
            popular_only.sort(key=lambda n: (inc.get(n, 0.0), n))
            for n in popular_only[: min(len(popular_only) // 2, len(fresh(plan_names)))]:
                if drop_card(n):
                    scaffold_cut.append(n)

        plan_added = [n for n in scorer.pick(fresh(plan_names), PLAN_SLOTS + len(scaffold_cut))
                      if add(n, 2 if is_owned(n) else 3, "owned" if is_owned(n) else "new", 0.0 if is_owned(n) else (price_of(n) or 0.0),
                             "Plan: found by the deck plan (" + ", ".join(plan_labels[:3]) + ")", source=stages.PROV_PLAN)]

        if pools is not None:
            prices = {pc.name: (pc.unit_price if pc.unit_price is not None else 0.0) for pc in pools.tier3}
        else:
            prices = {"Arcane Signet": 1.25, "Command Tower": 0.50, "Sol Ring": 1.50}
        for name in scorer.pick(fresh(list(prices)), tier3_cap):  # pool order = #527's ranker: a fallback, not the plan
            add(name, 3, "new", float(prices[name]), "Buy to complete the brew")

        # Caps above only set the tier mix. Keep topping up from every pool (donor,
        # owned, then new) until the deck is full or candidates run out, but
        # (a) rank the remaining candidates with the scorer instead of taking pool
        # order (alphabetical when EDHREC data is absent) and (b) reserve the land
        # slots the template asks for (the skeleton's plan) so spells cannot crowd out the mana base.
        land_reserve = sk["lands_target"]

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
        scaffold_left = [c["name"] for c in cards[:stage3_from] if prov.get(c["name"]) == stages.SCAFFOLD_EDHREC
                         and not is_owned(c["name"])]
        proposals = list(dict.fromkeys(scaffold_left + stage3))
        if proposals:
            tracker.note(f"reviewing {len(proposals)} picks ({len(scaffold_left)} provisional scaffold)")
            panel_stage("deck", proposals, plan_names,  # scaffold is NOT protected: the panel may cut popular-only picks
                        protected=[c["name"] for c in cards if c.get("tier") in (1, 2)] + ([secret] if secret else []))
            top_up()  # refill whatever the panel cut
        nudge = _power_nudge(r, session, cards, scorer, add, drop_card, fresh, rule_filter, prov, rec_of, is_land, gate,
                             ident_str, sf_budget)
        if nudge["out"]:
            top_up()
        counts: dict = {}
        for c in cards:
            k = prov.get(c["name"]) or ("commander" if c["name"] in commanders else stages.PROV_FILL)
            counts[k] = counts.get(k, 0) + 1
        tracker.finish(stages.STAGE_DECK, {"added": len(cards) - stage3_from, "plan": plan_labels,
                                           "plan_candidates": len(plan_names), "plan_picks": len(plan_added),
                                           "scaffold_replaced": scaffold_cut, "provenance": counts, "power_nudge": nudge,
                                           "panel": (progress_session.get("stage_reviews") or {}).get("deck", {}).get("status")},
                       degraded=None if plan_names else "no plan candidates (Scryfall unavailable or nothing matched)",
                       message=f"{len(cards)} cards chosen ({len(plan_added)} plan-driven, {len(scaffold_cut)} scaffold replaced)")

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
        cmdr_keys = {norm_name(x) for x in commanders}
        for c in cards:  # provenance survives costing (compute_deck_cost rebuilds the card dicts)
            c["provenance"] = ("commander" if norm_name(c["name"]) in cmdr_keys
                               else stages.PROV_BASIC if norm_name(c["name"]) in _BASIC_NAMES and c["name"] not in prov
                               else prov.get(c["name"]) or stages.PROV_FILL)

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
            "target_bracket": session.get("target_bracket", brew_power.DEFAULT_BRACKET),
            "game_plan": session.get("game_plan"),
            "archetype": session.get("archetype"),
            "secret_commander": secret,
            "secret_tutors": secret_tutors,
            "dials": session.get("dials") or {},
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


def count_template_rows(r: Any, session: dict, draft: dict) -> dict:
    """{"lands": n, "ramp": n, "spells": n, "targets": {...}} for a draft, by quantity (commander excluded
    from spells). Used by the pre-review sanity check and handed to the reviewer."""
    skel = skeleton_from_template(session_template(session))
    lab_tags = _load_lab_tags(r)
    lands = ramp = spells = 0
    ramp_key = next((k for k, v in skel.items() if "ramp" in f"{k} {v.label}".lower() and not v.derived), None)
    cmdrs = {norm_name(c) for c in draft.get("commanders") or []}
    for c in draft.get("cards") or []:
        q = int(c.get("quantity") or 1)
        if norm_name(c["name"]) in cmdrs:
            continue
        rec = _card_record(r, c["name"], lab_tags)
        if norm_name(c["name"]) in _BASIC_NAMES or brew_power.is_land(rec):
            lands += q
            continue
        spells += q
        if ramp_key and ramp_key in brew_scoring._matching(rec, skel):
            ramp += q
    return {"lands": lands, "ramp": ramp, "spells": spells,
            "targets": {"lands": skel["lands"].target if "lands" in skel else None,
                        "ramp": skel[ramp_key].target if ramp_key else None},
            "high": {"lands": skel["lands"].high if "lands" in skel else None,
                     "ramp": skel[ramp_key].high if ramp_key else None}}


def sanity_trim(r: Any, session: dict, draft: dict) -> dict:
    """Deterministic check before the oracle review: lands and ramp must sit inside the template's ranges.
    Surplus basics (then surplus non-owned ramp, last added first) are trimmed rather than failing the brew; the
    release gate then refills the freed slots with legal spells. Records `sanity_trim` on the draft."""
    if draft.get("user_edited"):
        return {}
    before = count_template_rows(r, session, draft)
    removed: list = []
    hi_l, hi_r = before["high"]["lands"], before["high"]["ramp"]
    if hi_l is not None and before["lands"] > hi_l:
        excess = before["lands"] - hi_l
        for c in sorted((c for c in draft["cards"] if norm_name(c["name"]) in _BASIC_NAMES),
                        key=lambda c: -int(c.get("quantity") or 1)):
            while excess and int(c.get("quantity") or 1) > 0:
                c["quantity"] = int(c.get("quantity") or 1) - 1
                removed.append(c["name"])
                excess -= 1
        draft["cards"] = [c for c in draft["cards"] if int(c.get("quantity") or 1) > 0]
    if hi_r is not None and before["ramp"] > hi_r:
        skel = skeleton_from_template(session_template(session))
        key = next(k for k, v in skel.items() if "ramp" in f"{k} {v.label}".lower() and not v.derived)
        lab = _load_lab_tags(r)
        ramps = [c for c in reversed(draft["cards"]) if c.get("tier") != 1 and c.get("provenance") != "commander"
                 and norm_name(c["name"]) not in _BASIC_NAMES
                 and key in brew_scoring._matching(_card_record(r, c["name"], lab), skel)
                 and not brew_power.is_land(_card_record(r, c["name"], lab))]
        for c in ramps[: before["ramp"] - hi_r]:
            draft["cards"].remove(c)
            removed.append(c["name"])
    after = count_template_rows(r, session, draft)
    info = {"before": {k: before[k] for k in ("lands", "ramp", "spells")},
            "after": {k: after[k] for k in ("lands", "ramp", "spells")}, "removed": removed}
    if removed:
        draft["sanity_trim"] = info
    return info


_DIAL_TEXT = {
    "aggressiveness": ("lean aggressive: fast pressure, cheap threats", "lean patient: fewer all-in attackers"),
    "commander_focus": ("build around the commander: more ways to find and protect it",
                        "the commander is one piece; do not over-index on it"),
    "interactiveness": ("more interaction: removal, counters, protection", "less interaction; spend slots on the plan"),
    "political": ("politics welcome: deals, goad, group effects", "avoid political and group-dependent cards"),
}


def guidance_note(session: dict) -> str:
    """Prompt guidance from the bracket (hard), power (soft), game plan, archetype and dials (soft)."""
    parts = []
    bracket = session.get("target_bracket")
    if bracket is not None:
        parts.append(f"HARD LIMIT: build to Commander Bracket {bracket} (1-5); never exceed its Game Changer, "
                     "mass land denial, extra turn and two-card combo limits.")
    tp = session.get("target_power")
    if tp is not None:
        parts.append(f"SOFT GUIDE: target power {tp:g} on the 1-10 scale in the Power scale table; "
                     "it never overrides the bracket.")
    plan = session.get("game_plan")
    if plan:
        parts.append(f"Game plan: {plan}" + (" with a combo finish." if session.get("combo") else "."))
    sec = _secret_of(session)
    if sec:
        parts.append(
            f"SECRET COMMANDER: {sec}. Build the deck around it; it MUST be in the 99 and is never cut. Include extra ways to "
            "find it (tutors that can fetch it, including type-specific ones), still inside the Bracket limit; the Bracket always wins. "
            "Ignore popular guides and EDHREC-style typical lists for the real commander; still value synergy with the real "
            "commander's own text.")
    arch = session.get("archetype")
    if arch and arch != "let the brewer decide":
        parts.append(f"Archetype (guide, not a law): {arch}.")
    elif arch:
        parts.append("Archetype: infer it from the " + ("secret commander first, then the real commander and the deck idea."
                                                       if sec else "commander and the deck idea."))
    for name, v in (session.get("dials") or {}).items():
        if v in ("high", "low") and name in _DIAL_TEXT:
            parts.append(f"{name.replace('_', ' ').capitalize()} {v}: {_DIAL_TEXT[name][0 if v == 'high' else 1]}.")
    return " ".join(parts)


def pin_secret(r: Any, session: dict, draft: dict) -> None:
    """LLM-built drafts: guarantee the secret commander is in the 99 (swap out the last plain spell if absent)."""
    sec = _secret_of(session)
    if not sec:
        return
    draft["secret_commander"] = sec
    cards = draft.get("cards") or []
    if any(norm_name(c.get("name")) == norm_name(sec) for c in cards):
        return
    cmdrs = {norm_name(x) for x in draft.get("commanders") or session.get("commanders") or []}
    for i in range(len(cards) - 1, -1, -1):
        n = cards[i].get("name") or ""
        if norm_name(n) not in cmdrs and norm_name(n) not in _BASIC_NAMES and int(cards[i].get("quantity") or 1) == 1:
            cards[i] = {"name": sec, "quantity": 1, "tier": 3, "tier_label": "new", "cost": 0.0,
                        "rationale": "Secret commander: the deck is built around this card"}
            return
    cards.append({"name": sec, "quantity": 1, "tier": 3, "tier_label": "new", "cost": 0.0,
                  "rationale": "Secret commander: the deck is built around this card"})


def brew_rules_of(session: dict) -> List[dict]:
    return [x for x in (session.get("approved_rules") or []) if session.get("rules_approved")]


class BrewBuilder(BaseBrewBuilder):
    """LLM-backed brew builder: reuses the reconstruct agent pipeline with the
    brew session (donor snapshots are aliased to source_decks, so pool building
    is identical) and brew-mode guidance folded into the preference notes."""

    def __init__(self, **kwargs: Any):
        from src.server.domain.draft.reconstruct_agent import load_guide
        from src.server.domain.draft.reconstruct_builder import ReconstructBuilder
        from src.server.domain.knowledge.render import read_static

        if kwargs.get("guide") is None:  # power table rides along like the other knowledge sections
            kwargs["guide"] = load_guide() + "\n\n" + read_static("power_scale")

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
        if prefs:
            prefs[0]["notes"] = " ".join(x for x in [prefs[0].get("notes", ""), guidance_note(session)] if x).strip()
        drafts = self._inner.build(inner_session, r)[:1]
        cfg = BrewScoringConfig.from_env()
        for d in drafts:
            pin_secret(r, session, d)
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
