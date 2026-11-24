"""Brew's early build stages, explicit and individually testable.

  Stage 1  SKELETON  deterministic, no LLM: the basic-land plan (the template's land recommendation
                     split evenly over the commander's colour identity) and the temporary EDHREC pool
                     for commander + archetype (brew_edhrec_pool).
  Stage 2  CORE      landbase upgrades inside the budget, then ~25 spells: template-category needs
                     (EDHREC card-list tags + Scryfall Tagger tags) and the cards on >=40% of both the
                     commander's AND the archetype's decks. Judgment calls go through the reviewer
                     panel (brew_panel).
  Stage 3  DECK      (brew_builder / brew_panel) notes, playstyle, flavor: scored picks + panel.

Each stage records itself on the session via `StageTracker` (`session["stage"]`, `["progress"]`,
`["build_stages"][stage]` = {label, status, message, summary}) so the UI polls one session read.

Nothing here raises on a missing external source: EDHREC or Scryfall being down only shrinks the
candidate sets (the build falls back to the other pools and the gate's refill) and is recorded as
`degraded` in the stage summary.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from src.server.domain.draft import brew_edhrec_pool as edhrec_pool
from src.server.domain.draft.reconstruct_pools import BASIC_LAND_NAMES, norm_name

log = logging.getLogger(__name__)

STAGE_SKELETON, STAGE_CORE, STAGE_DECK, STAGE_REVIEW = "skeleton", "core", "deck", "review"
STAGE_LABELS = {STAGE_SKELETON: "Skeleton", STAGE_CORE: "Core construction",
                STAGE_DECK: "Deck construction", STAGE_REVIEW: "Final review"}
STAGE_ORDER = (STAGE_SKELETON, STAGE_CORE, STAGE_DECK, STAGE_REVIEW)

CORE_SPELLS = 25          # spells after stage 2 ("~25 or so")
CATEGORY_SHARE = 12       # spells stage 2 spends on template-category needs before the >=40% staples
DEFAULT_LANDS = 38        # Command Zone template; used only when the template has no lands slot
MAX_LAND_SHARE = 0.5      # stage 2 replaces at most half the planned lands ("half-completed manabase")
SCRYFALL_TAG_LIMIT = 40
SCRYFALL_MAX_CALLS = 4

_BASIC_FOR_COLOR = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

class StageTracker:
    """Writes stage progress onto the session dict the build was started with and persists it.

    Never raises (progress is cosmetic) and is a no-op without Redis or a session id, so builders
    stay usable in unit tests that pass a bare dict.
    """

    def __init__(self, r: Any, session: dict):
        self.r, self.session = r, session
        self.records: Dict[str, dict] = {k: dict(v) for k, v in (session.get("build_stages") or {}).items()}
        self.stage = ""

    def _persist(self, message: str) -> None:
        s = self.session
        s["build_stages"] = {k: dict(v) for k, v in self.records.items()}
        s["stage"] = self.stage
        s["progress"] = message
        if self.r is None or not s.get("session_id"):
            return
        try:
            import src.server.domain.draft.brew as brew
            s["updated_at"] = brew._now()
            brew._save_session(self.r, s)
        except Exception as exc:  # noqa: BLE001
            log.warning("brew stage progress not saved: %s", exc)

    def start(self, stage: str, message: str) -> None:
        self.stage = stage
        self.records[stage] = {"label": STAGE_LABELS[stage], "status": "running", "message": message,
                               "summary": {}, "started_at": time.time()}
        self._persist(f"{STAGE_LABELS[stage]}: {message}")

    def note(self, message: str) -> None:
        rec = self.records.get(self.stage)
        if rec is not None:
            rec["message"] = message
        self._persist(f"{STAGE_LABELS.get(self.stage, self.stage)}: {message}")

    def finish(self, stage: str, summary: dict, *, degraded: Optional[str] = None, message: str = "") -> None:
        rec = self.records.setdefault(stage, {"label": STAGE_LABELS[stage], "started_at": time.time()})
        rec.update(status="degraded" if degraded else "done", summary=summary, message=message or rec.get("message", ""),
                   finished_at=time.time())
        if degraded:
            rec["degraded"] = degraded
        self._persist(f"{STAGE_LABELS[stage]}: {message or 'done'}")


# ---------------------------------------------------------------------------
# External dependencies (injectable for tests)
# ---------------------------------------------------------------------------

def _scryfall_search(query: str, timeout: float = 6.0) -> List[str]:
    import requests
    resp = requests.get("https://api.scryfall.com/cards/search",
                        params={"q": query, "order": "edhrec", "unique": "cards"}, timeout=timeout,
                        headers={"User-Agent": "commander-lab/1.0", "Accept": "application/json"})
    if resp.status_code != 200:
        return []
    return [c["name"] for c in (resp.json().get("data") or []) if c.get("name")]


@dataclass
class Deps:
    """Seams for the network. `edhrec_client` None = the real EDHRecClient; `scryfall_search(query)`
    returns card names for a Scryfall query (default: the public search API)."""
    edhrec_client: Any = None
    scryfall_search: Callable[[str], List[str]] = _scryfall_search


_deps = Deps()


def get_deps() -> Deps:
    return _deps


def set_deps(deps: Optional[Deps]) -> None:
    global _deps
    _deps = deps or Deps()


# ---------------------------------------------------------------------------
# Stage 1: skeleton
# ---------------------------------------------------------------------------

def land_target(template: dict) -> int:
    for s in template.get("slots") or []:
        if str(s.get("key")) == "lands" and s.get("target") is not None:
            return max(int(s["target"]), 0)
    return DEFAULT_LANDS


def split_evenly(n: int, colors: Sequence[str]) -> Dict[str, int]:
    """`n` basics split evenly over `colors` (remainder to the earliest of WUBRG); Wastes if colourless."""
    basics = [_BASIC_FOR_COLOR[c] for c in colors if c in _BASIC_FOR_COLOR] or ["Wastes"]
    return {b: n // len(basics) + (1 if i < n % len(basics) else 0) for i, b in enumerate(basics)}


def run_skeleton(r: Any, session: dict, colors: Sequence[str], template: dict, deps: Optional[Deps] = None) -> dict:
    """Stage 1. Returns {"lands_target", "basic_plan", "edhrec": pool, "degraded": str|None}.

    Pure planning: nothing is added to the deck here; the builder realises `basic_plan` at assembly
    (generated basics are free, Tier 3, and must not be priced/tiered like pool cards).
    """
    deps = deps or get_deps()
    target = land_target(template)
    plan = split_evenly(target, colors)
    pool = edhrec_pool.fetch_pool(r, session.get("commanders") or [], session.get("themes") or [],
                                  client=deps.edhrec_client)
    edhrec_pool.store_pool(r, session.get("session_id") or "", pool)
    return {"lands_target": target, "basic_plan": plan, "edhrec": pool, "degraded": pool.get("degraded")}


def skeleton_summary(sk: dict) -> dict:
    pool = sk["edhrec"]
    return {"lands_target": sk["lands_target"], "basic_plan": sk["basic_plan"],
            "edhrec_cards": len(pool.get("cards") or {}), "edhrec_decks_sampled": pool.get("decks_sampled", 0),
            "archetype": pool.get("theme"), "archetypes_skipped": pool.get("themes_skipped") or [],
            "edhrec_stale": bool(pool.get("stale"))}


# ---------------------------------------------------------------------------
# Stage 2: core construction
# ---------------------------------------------------------------------------

# Landbase staples in priority order: (name, min colours in identity). Colour fit is NOT hard-coded
# per name: every land is checked against the card database (colour identity inside the commander's,
# not banned) at selection time, so a pair land only appears for a deck that can play it.
_STAPLE_LANDS: Tuple[Tuple[str, int], ...] = (
    ("Command Tower", 2), ("Exotic Orchard", 2), ("Path of Ancestry", 2),
)
_BATTLEBOND = ("Sea of Clouds", "Morphic Pool", "Luxury Suite", "Spire Garden", "Bountiful Promenade",
               "Vault of Champions", "Training Center", "Undergrowth Stadium", "Spectator Seating")
_CHECKLANDS = ("Glacial Fortress", "Drowned Catacomb", "Dragonskull Summit", "Rootbound Crag", "Sunpetal Grove",
               "Isolated Chapel", "Sulfur Falls", "Woodland Cemetery", "Clifftop Retreat", "Hinterland Harbor")
_SHOCKLANDS = ("Hallowed Fountain", "Watery Grave", "Blood Crypt", "Stomping Ground", "Temple Garden",
               "Godless Shrine", "Steam Vents", "Overgrown Tomb", "Sacred Foundry", "Breeding Pool")
_TRIOMES = ("Indatha Triome", "Ketria Triome", "Raugrin Triome", "Savai Triome", "Zagoth Triome",
            "Jetmir's Garden", "Raffine's Tower", "Spara's Headquarters", "Xander's Lounge", "Ziatora's Proving Ground")
_FETCH_UTILITY: Tuple[Tuple[str, int], ...] = (
    ("Evolving Wilds", 2), ("Terramorphic Expanse", 2), ("Reliquary Tower", 0),
)
LAND_PRIORITY: Tuple[Tuple[str, int], ...] = (
    _STAPLE_LANDS + tuple((n, 2) for n in _BATTLEBOND) + tuple((n, 2) for n in _CHECKLANDS)
    + tuple((n, 2) for n in _SHOCKLANDS) + tuple((n, 3) for n in _TRIOMES) + _FETCH_UTILITY
)

OWNED_FIRST_LAND_BUDGET = 15.0
BEST_BUILD_PER_CARD_CAP = 40.0
BUDGET_LAND_SHARE = 0.25


def land_budget(session: dict) -> Tuple[Optional[float], float]:
    """(total new-land spend cap or None for unlimited, per-card price cap) for stage 2's landbase.

    An explicit session budget (USD, total new-card spend) gives lands a quarter of it. Otherwise
    best_build is uncapped in total (each land still <= $40) and owned_first spends at most $15.
    Owned lands (Tier 1/2) are always free.
    """
    b = session.get("budget")
    if isinstance(b, (int, float)) and not isinstance(b, bool) and b > 0:
        total = float(b) * BUDGET_LAND_SHARE
        return total, min(total, BEST_BUILD_PER_CARD_CAP)
    if session.get("build_mode") == "best_build":
        return None, BEST_BUILD_PER_CARD_CAP
    return OWNED_FIRST_LAND_BUDGET, min(OWNED_FIRST_LAND_BUDGET, 8.0)


def plan_lands(*, colors: Sequence[str], identity: Optional[frozenset], lands_target: int, session: dict,
               card_fact: Callable[[str], Optional[dict]], price_of: Callable[[str], Optional[float]],
               is_owned: Callable[[str], bool], exclude: Iterable[str] = (), extra: Iterable[str] = ()) -> List[dict]:
    """Ordered nonbasic lands to put in place of basics: [{name, price, owned, why}].

    `card_fact(name)` -> {"type_line", "identity": frozenset|None, "legal": bool|None} or None when the
    card database does not know the card (skipped). Unknown price and not owned = skipped (never guess).
    """
    cap = int(lands_target * MAX_LAND_SHARE)
    total_cap, per_card = land_budget(session)
    spent, out = 0.0, []
    skip = {norm_name(n) for n in exclude}
    order = _STAPLE_LANDS + tuple((n, 0) for n in extra) + LAND_PRIORITY[len(_STAPLE_LANDS):]
    chosen: Set[str] = set()
    for name, min_colors in order:
        if len(out) >= cap:
            break
        if norm_name(name) in chosen:
            continue
        if len(colors) < min_colors or norm_name(name) in skip:
            continue
        fact = card_fact(name)
        if not fact or "land" not in (fact.get("type_line") or "").lower() or fact.get("legal") is False:
            continue
        ident = fact.get("identity")
        if identity is not None and ident is not None and not ident <= identity:
            continue
        owned = is_owned(name)
        price = 0.0 if owned else price_of(name)
        if price is None or price > per_card:
            continue
        if total_cap is not None and spent + price > total_cap:
            continue
        spent += price
        chosen.add(norm_name(name))
        out.append({"name": name, "price": price, "owned": owned,
                    "why": "owned" if owned else f"within land budget (${price:.2f})"})
    return out


_CATEGORY_TAGS = (  # (words in the slot key/label, Scryfall Tagger tag, EDHREC card-list tags)
    (("ramp",), "ramp", ("manaartifacts",)),
    (("card advantage", "card_advantage", "draw"), "draw", ()),
    (("targeted", "removal", "spot"), "removal", ()),
    (("mass", "sweeper", "wipe"), "sweeper", ()),
)


def category_sources(skeleton: Dict[str, Any]) -> Dict[str, Tuple[str, Tuple[str, ...]]]:
    """{template category key: (scryfall otag, edhrec tags)} for the categories this template has that
    we know how to source. Unknown (custom) categories are still served by the scorer's tag matching."""
    out = {}
    for key, rng in skeleton.items():
        if getattr(rng, "derived", False) or not getattr(rng, "steer", True):
            continue
        text = f"{key} {getattr(rng, 'label', '')}".lower().replace("_", " ")
        for words, otag, tags in _CATEGORY_TAGS:
            if any(w in text for w in words):
                out[key] = (otag, tags)
                break
    return out


def scryfall_candidates(r: Any, otag: str, colors: Sequence[str], deps: Deps, budget: List[int]) -> List[str]:
    """Names tagged `otag` in Scryfall's Tagger, Commander-legal, inside the colour identity, EDHREC-ordered.
    Cached 24h per (tag, identity); at most SCRYFALL_MAX_CALLS live lookups per build (`budget` is a one-item list)."""
    ident = "".join(colors).lower() or "c"
    key = f"brew:scryfall_tag:{otag}:{ident}"
    try:
        raw = r.get(key) if r is not None else None
        if raw:
            return list(json.loads(raw))
    except Exception:  # noqa: BLE001
        pass
    if budget[0] <= 0:
        return []
    budget[0] -= 1
    try:
        names = list(deps.scryfall_search(f"otag:{otag} f:commander id<={ident}"))[:SCRYFALL_TAG_LIMIT]
    except Exception as exc:  # noqa: BLE001 - Scryfall is context, never fatal
        log.warning("brew scryfall tag %s unavailable: %s", otag, exc)
        return []
    try:
        if r is not None and names:
            try:
                r.set(key, json.dumps(names), ex=24 * 3600)
            except TypeError:
                r.set(key, json.dumps(names))
    except Exception:  # noqa: BLE001
        pass
    return names


@dataclass
class CoreResult:
    lands: List[dict] = field(default_factory=list)
    spells: List[str] = field(default_factory=list)
    by_source: Dict[str, int] = field(default_factory=dict)
    degraded: Optional[str] = None


def run_core(*, r: Any, session: dict, colors: Sequence[str], skeleton_out: dict, scorer: Any,
             add: Callable[..., bool], fresh: Callable[[List[str]], List[str]],
             is_land: Callable[[str], bool], card_fact: Callable[[str], Optional[dict]],
             price_of: Callable[[str], Optional[float]], is_owned: Callable[[str], bool],
             rule_filter: Callable[[List[str]], List[str]] = lambda names: names,
             deps: Optional[Deps] = None, identity: Optional[frozenset] = None) -> CoreResult:
    """Stage 2 (see module doc). `add(name, tier, label, cost, rationale)` is the builder's single
    admission point (resolution, singleton, power gate); a name it refuses simply is not added."""
    deps = deps or get_deps()
    res = CoreResult()
    pool = skeleton_out["edhrec"]

    # 2a. landbase: staples, EDHREC's 40%+ lands for this commander/archetype, then the budgeted families
    edhrec_lands = [c["name"] for c in edhrec_pool.core_cards(pool) if is_land(c["name"])]
    for land in plan_lands(extra=edhrec_lands, colors=colors, identity=identity, lands_target=skeleton_out["lands_target"], session=session,
                           card_fact=card_fact, price_of=price_of, is_owned=is_owned):
        tier, label = (2, "owned") if land["owned"] else (3, "new")
        if add(land["name"], tier, label, float(land["price"] or 0.0), f"Landbase: {land['why']}"):
            res.lands.append(land)

    # 2b. spells
    def spells_now() -> int:
        return len(res.spells)

    def take(names: List[str], cap: int, source: str, rationale: str) -> None:
        cand = fresh(rule_filter([n for n in dict.fromkeys(names) if n and not is_land(n)]))
        got = 0
        for n in scorer.pick(cand, max(cap, 0)):
            if add(n, 3, "new", price_of(n) or 0.0, rationale):
                res.spells.append(n)
                got += 1
        res.by_source[source] = res.by_source.get(source, 0) + got

    sources = category_sources(scorer.skeleton)
    calls = [SCRYFALL_MAX_CALLS]
    cards = pool.get("cards") or {}
    for key, (otag, edhrec_tags) in sources.items():
        need = scorer.skeleton[key].low - scorer.counts.get(key, 0)
        if need <= 0 or spells_now() >= CATEGORY_SHARE:
            continue
        names = [c["name"] for c in edhrec_pool.by_tag(pool, *edhrec_tags)] if edhrec_tags else []
        names += scryfall_candidates(r, otag, colors, deps, calls)
        take(names, min(need, CATEGORY_SHARE - spells_now()), f"category:{key}",
             f"Core: fills the template's {scorer.skeleton[key].label or key} slot")

    core = [c["name"] for c in edhrec_pool.core_cards(pool)]
    take(core, CORE_SPELLS - spells_now(), "edhrec_core",
         "Core: on 40%+ of this commander's decks" + (" and this archetype's" if pool.get("theme") else ""))

    if spells_now() < CORE_SPELLS:  # few 40% cards (new/rare commander): next most-played
        rest = sorted(cards.values(), key=lambda v: (-(v.get("theme_inclusion") or v.get("inclusion") or 0.0), v["name"].casefold()))
        take([v["name"] for v in rest], CORE_SPELLS - spells_now(), "edhrec_next", "Core: next most-played with this commander")

    if not cards and not res.by_source.get("category:ramp") and not any(res.by_source.values()):
        res.degraded = pool.get("degraded") or "no EDHREC or Scryfall candidates"
    return res
