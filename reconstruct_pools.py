"""
Tiered candidate pools and costing for the Reconstruct deck builder.

Pure data-in / data-out: no LLM, no validator. The builder (t_c5954272) imports
the typed interfaces here; Redis and the price source are injected, so tests run
on fixtures.

Tiers (a card appears in exactly ONE tier: the lowest-numbered it qualifies for)
    Tier 1  "previous_deck"  cards in the session's source decklist snapshots,
                             quantity = copies summed across the source decks.
    Tier 2  "owned"          owned copies not dedicated to any active deck.
    Tier 3  "new"            every other Commander-legal card in the card
                             database, priced from the existing price source.

Ownership rule used for Tier 2 (see docs/RECONSTRUCT_POOLS.md)
    The ownership source of truth is instances.py. A copy is *free* iff its
    instance has ownership_status == "in_collection" (so deck_id is null).
      * in_deck   -> dedicated to an active deck -> excluded.
      * in_mail / not_owned -> not physically in hand -> excluded.
    Cards of decks being reconstructed: the existing decommission step
    (registry.decommission_decks, run by reconstruct.start_reconstruct) already
    moves their instances in_deck -> in_collection, so the model itself treats
    them as free. We add no extra rule on top; those cards are Tier 1 anyway
    (precedence), so they are never double counted in Tier 2.

Cost rule
    Tier 1 = 0, Tier 2 = 0, Tier 3 = market price x quantity. A Tier 3 card with
    no price is `price_known=False` / `cost=None`; it is NEVER silently 0. The
    deck total is the sum of the known prices and carries `is_complete=False`
    plus the list of unpriced cards.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, replace
from enum import IntEnum
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Union,
)

import cards as card_store
import instances as instance_store

log = logging.getLogger("commander_lab.reconstruct_pools")

WUBRG = "WUBRG"
_COLOR_WORDS = {"white": "W", "blue": "U", "black": "B", "red": "R", "green": "G"}

_BASICS = ("Plains", "Island", "Swamp", "Mountain", "Forest")
BASIC_LAND_NAMES = frozenset(set(_BASICS) | {"Wastes"} | {f"Snow-Covered {b}" for b in _BASICS} | {"Snow-Covered Wastes"})

DEFAULT_TIER3_CAP = 300

PRICE_CACHE_PREFIX = "price:card:"  # written by analytics/prices.py


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class Tier(IntEnum):
    PREVIOUS_DECK = 1
    OWNED = 2
    NEW = 3

    @property
    def label(self) -> str:
        return TIER_LABELS[int(self)]


# Same vocabulary as reconstruct.BaseReconstructBuilder's draft card format.
TIER_LABELS = {1: "previous_deck", 2: "owned", 3: "new"}

PriceLookup = Callable[[Sequence[str]], Mapping[str, Optional[float]]]
CardInput = Union[str, Tuple[str, int], Mapping[str, Any]]


@dataclass(frozen=True)
class CatalogCard:
    """One row of the card database, as far as pool building needs it."""

    name: str
    color_identity: frozenset = frozenset()
    # None = the database has no legality data for this card (see PoolConfig.require_known_legal).
    legal: Optional[bool] = None
    cmc: float = 0.0
    type_line: str = ""


class CardCatalog(Protocol):
    def iter_cards(self) -> Iterable[CatalogCard]: ...


@dataclass(frozen=True)
class PoolConfig:
    """Knobs for pool size / content. All optional."""

    # Tier 3 colour-identity filter. A card passes iff its identity is a subset
    # of this set. None = no filter. "C"/empty set = colorless only.
    color_identity: Optional[frozenset] = None
    # Max Tier 3 cards kept (after filtering and ranking). None = unlimited.
    tier3_cap: Optional[int] = DEFAULT_TIER3_CAP
    # Ranks Tier 3 candidates; lowest key first (kept first when capped).
    # Default is alphabetical, which is deterministic but not "best": pass a
    # ranker (e.g. EDHREC inclusion) from the orchestration layer for relevance.
    ranker: Optional[Callable[[CatalogCard], Any]] = None
    # The card database carries no legality field today, so legal=None is
    # treated as legal; legal=False is always excluded. Set True to be strict.
    require_known_legal: bool = False
    # Basic lands are unlimited and free to the builder, so they are left out of
    # Tier 3 by default. (Basics in Tier 1/2 are still reported there.)
    include_basics: bool = False
    # Names never offered in Tier 3 (e.g. a banlist the caller maintains).
    excluded_names: frozenset = frozenset()


@dataclass(frozen=True)
class PoolCard:
    name: str
    tier: Tier
    # Copies available to the builder. None = unlimited (Tier 3).
    available_qty: Optional[int] = None
    unit_price: Optional[float] = None  # Tier 3 only; None = unknown
    color_identity: Optional[frozenset] = None  # None = not known to the catalog
    is_commander: bool = False
    # Tier 1 only: {source deck name: copies}
    source_decks: Tuple[Tuple[str, int], ...] = ()

    @property
    def tier_label(self) -> str:
        return self.tier.label

    @property
    def price_known(self) -> bool:
        return self.tier != Tier.NEW or self.unit_price is not None


@dataclass(frozen=True)
class Availability:
    """Shared-pool limit for one Tier 1/2 card, for the validator."""

    name: str
    tier: Tier
    quantity: int


@dataclass
class CandidatePools:
    tier1: List[PoolCard] = field(default_factory=list)
    tier2: List[PoolCard] = field(default_factory=list)
    tier3: List[PoolCard] = field(default_factory=list)
    target_deck_count: int = 1
    config: PoolConfig = field(default_factory=PoolConfig)
    # Tier 3 candidates that passed the filters before the cap was applied.
    tier3_total_candidates: int = 0

    @property
    def tier3_truncated(self) -> bool:
        return self.tier3_total_candidates > len(self.tier3)

    def tier_cards(self, tier: Union[int, Tier]) -> List[PoolCard]:
        return {1: self.tier1, 2: self.tier2, 3: self.tier3}[int(tier)]

    def all_cards(self) -> List[PoolCard]:
        return [*self.tier1, *self.tier2, *self.tier3]

    def get(self, name: str) -> Optional[PoolCard]:
        return self._index().get(norm_name(name))

    def tier_of(self, name: str) -> Optional[Tier]:
        card = self.get(name)
        return card.tier if card else None

    def available_quantities(self) -> Dict[str, Availability]:
        """{normalised name: Availability} for every Tier 1 and Tier 2 card.

        These are copies shared by BOTH target decks: if two drafts together use
        more than `quantity` copies of a card, they conflict. Tier 3 is
        unlimited and is not listed. Use `norm_name(name)` to look cards up.
        """
        out: Dict[str, Availability] = {}
        for c in (*self.tier1, *self.tier2):
            out[norm_name(c.name)] = Availability(c.name, c.tier, int(c.available_qty or 0))
        return out

    def _index(self) -> Dict[str, PoolCard]:
        # Rebuilt when the pool sizes change; pools are treated as immutable after build.
        sizes = (len(self.tier1), len(self.tier2), len(self.tier3))
        cache = self.__dict__.get("_idx_cache")
        if cache is None or cache[0] != sizes:
            idx: Dict[str, PoolCard] = {}
            for c in self.all_cards():
                idx.setdefault(norm_name(c.name), c)
            cache = (sizes, idx)
            self.__dict__["_idx_cache"] = cache
        return cache[1]


@dataclass(frozen=True)
class TaggedCard:
    name: str
    quantity: int
    tier: Tier
    # False when the card is in no pool (e.g. outside the capped Tier 3 list). It is
    # still Tier 3: anything not owned and not from a source deck must be bought.
    in_pool: bool
    available_qty: Optional[int]  # None = unlimited
    exceeds_available: bool

    @property
    def tier_label(self) -> str:
        return self.tier.label

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "tier": int(self.tier),
            "tier_label": self.tier_label,
            "in_pool": self.in_pool,
            "available_qty": self.available_qty,
            "exceeds_available": self.exceeds_available,
        }


@dataclass(frozen=True)
class CardCost:
    name: str
    quantity: int
    tier: Tier
    unit_price: Optional[float]  # Tier 3 market price; 0.0 for tiers 1-2; None = unknown
    cost: Optional[float]  # unit_price x quantity; None = unknown price
    price_known: bool

    @property
    def tier_label(self) -> str:
        return self.tier.label

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "quantity": self.quantity,
            "tier": int(self.tier),
            "tier_label": self.tier_label,
            "unit_price": self.unit_price,
            "cost": self.cost,
            "price_known": self.price_known,
        }

    def to_draft_card(self, rationale: str = "") -> dict:
        """Shape of one entry in reconstruct.BaseReconstructBuilder drafts (`cost` may be None)."""
        d = self.to_dict()
        d.pop("unit_price")
        d["rationale"] = rationale
        return d


@dataclass(frozen=True)
class DeckCost:
    cards: Tuple[CardCost, ...]
    total_new_cost: float  # sum of KNOWN Tier 3 costs
    unpriced_cards: Tuple[str, ...]  # Tier 3 cards with no price
    unpriced_copies: int

    @property
    def is_complete(self) -> bool:
        """False when total_new_cost excludes unpriced Tier 3 cards (it is a lower bound)."""
        return not self.unpriced_cards

    def to_dict(self) -> dict:
        return {
            "total_new_cost": self.total_new_cost,
            "is_complete": self.is_complete,
            "unpriced_cards": list(self.unpriced_cards),
            "unpriced_copies": self.unpriced_copies,
            "cards": [c.to_dict() for c in self.cards],
        }


# ---------------------------------------------------------------------------
# Name / colour helpers
# ---------------------------------------------------------------------------


def norm_name(name: str) -> str:
    """Case-insensitive key; double-faced 'A // B' matches 'A'."""
    return (name or "").split(" // ")[0].strip().casefold()


def parse_identity(value: Any) -> Optional[frozenset]:
    """Accept 'WUB', ['W','U'], ['White'], 'C'/'colorless'/'' or None -> frozenset of WUBRG (None stays None)."""
    if value is None:
        return None
    if isinstance(value, (set, frozenset, list, tuple)):
        parts = [str(v) for v in value]
    else:
        text = str(value).strip()
        parts = list(text) if re.fullmatch(r"[WUBRGCwubrgc]*", text) else re.split(r"[\s,/]+", text)
    out = set()
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if p.lower() in _COLOR_WORDS:
            out.add(_COLOR_WORDS[p.lower()])
        elif p.upper() in tuple(WUBRG):
            out.add(p.upper())
        # "C", "colorless" and anything unrecognised contribute no colour
    return frozenset(out)


def identity_of_cards(names: Iterable[str], catalog: CardCatalog) -> frozenset:
    """Union of the colour identities of `names` (e.g. the target deck's commanders)."""
    by_name = {norm_name(c.name): c for c in catalog.iter_cards()}
    out: set = set()
    for n in names:
        c = by_name.get(norm_name(n))
        if c:
            out |= set(c.color_identity)
    return frozenset(out)


# ---------------------------------------------------------------------------
# Card database + price source adapters
# ---------------------------------------------------------------------------


class InMemoryCatalog:
    """Catalog over a list of CatalogCard; used by tests and callers that already hold the data."""

    def __init__(self, cards: Iterable[CatalogCard]):
        self._cards = list(cards)

    def iter_cards(self) -> Iterable[CatalogCard]:
        return iter(self._cards)


class RedisCardCatalog:
    """The existing card database: `card:{oracle_id}` records (cards.py), plus any
    legacy `card_meta:{name}` records not covered by them.

    Records store colour identity as a letter string ("WUB", "C") under `color`.
    They carry no legality yet; a `legal` bool or `legalities.commander` is honoured
    when present (sync/Scryfall enrichment may add it later).
    """

    def __init__(self, r: Any):
        self.r = r

    @staticmethod
    def _legal(rec: Mapping) -> Optional[bool]:
        if rec.get("legal") is not None:
            return bool(rec["legal"])
        leg = (rec.get("legalities") or {}).get("commander")
        if leg is not None:
            return leg == "legal"
        return None

    def _to_card(self, name: str, rec: Mapping) -> CatalogCard:
        ident = rec.get("color_identity", rec.get("color"))
        return CatalogCard(
            name=name,
            color_identity=parse_identity(ident) or frozenset(),
            legal=self._legal(rec),
            cmc=float(rec.get("cmc") or 0.0),
            type_line=rec.get("type_line") or rec.get("type") or "",
        )

    def iter_cards(self) -> Iterable[CatalogCard]:
        seen = set()
        for key in self.r.keys("card:*"):
            key = key.decode() if isinstance(key, bytes) else key
            if not card_store.is_oracle_id(key[len("card:"):]):
                continue  # legacy card:{name} inventory blob
            raw = self.r.get(key)
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if not rec.get("name"):
                continue
            seen.add(norm_name(rec["name"]))
            yield self._to_card(rec["name"], rec)
        for key in self.r.keys("card_meta:*"):
            key = key.decode() if isinstance(key, bytes) else key
            name = key[len("card_meta:"):]
            if norm_name(name) in seen:
                continue
            raw = self.r.get(key)
            try:
                rec = json.loads(raw) if raw else None
            except (ValueError, TypeError):
                rec = None
            if rec:
                yield self._to_card(name, rec)


def redis_price_lookup(r: Any, refresh: bool = True) -> PriceLookup:
    """Adapter over the existing price source (analytics/prices.py, `price:card:{name}`).

    refresh=True uses analytics.prices.card_prices (cache + Scryfall refresh of stale
    entries) when that module is importable; otherwise, or with refresh=False, only
    the cached values are read and anything uncached is reported as unknown (None).
    """

    def lookup(names: Sequence[str]) -> Dict[str, Optional[float]]:
        wanted = sorted({n for n in names if n})
        if not wanted:
            return {}
        if refresh:
            try:
                from analytics.prices import card_prices

                return dict(card_prices(r, wanted))
            except ImportError:
                log.warning("analytics.prices not importable; using cached prices only")
            except Exception as exc:  # noqa: BLE001 - prices are context, never fatal
                log.warning("price refresh failed (%s); using cached prices only", exc)
        out: Dict[str, Optional[float]] = {}
        for n in wanted:
            raw = r.get(PRICE_CACHE_PREFIX + n)
            usd = None
            if raw:
                try:
                    usd = json.loads(raw).get("usd")
                except (ValueError, TypeError, AttributeError):
                    usd = None
            out[n] = float(usd) if usd is not None else None
        return out

    return lookup


# ---------------------------------------------------------------------------
# Tier 1 / Tier 2
# ---------------------------------------------------------------------------


def tier1_from_snapshots(source_decks: Iterable[Mapping]) -> List[PoolCard]:
    """Tier 1 from the session's source decklist snapshots.

    Quantity is summed across source decks (two source decks each running Sol Ring
    give 2 copies for the two target decks to share). A commander missing from a
    snapshot's `cards` is added once for that deck.
    """
    qty: Dict[str, int] = {}
    display: Dict[str, str] = {}
    by_deck: Dict[str, Dict[str, int]] = {}
    commanders: set = set()

    def add(name: str, n: int, deck: str):
        k = norm_name(name)
        display.setdefault(k, name)
        qty[k] = qty.get(k, 0) + n
        by_deck.setdefault(k, {})
        by_deck[k][deck] = by_deck[k].get(deck, 0) + n

    for i, sd in enumerate(source_decks or []):
        deck = sd.get("name") or f"Source deck {i + 1}"
        listed = set()
        for c in sd.get("cards") or []:
            name = c.get("name")
            n = int(c.get("quantity", 1) or 0)
            if name and n > 0:
                add(name, n, deck)
                listed.add(norm_name(name))
        for cmd in sd.get("commanders") or []:
            if not cmd:
                continue
            commanders.add(norm_name(cmd))
            if norm_name(cmd) not in listed:
                add(cmd, 1, deck)
    return [
        PoolCard(
            name=display[k],
            tier=Tier.PREVIOUS_DECK,
            available_qty=n,
            is_commander=k in commanders,
            source_decks=tuple(sorted(by_deck[k].items())),
        )
        for k, n in sorted(qty.items(), key=lambda kv: display[kv[0]].casefold())
    ]


def free_owned_counts(instances: Iterable[Mapping]) -> Dict[str, int]:
    """{name: copies} of owned copies not dedicated to any deck (ownership_status == in_collection)."""
    counts: Dict[str, int] = {}
    for inst in instances:
        if inst.get("ownership_status") != "in_collection" or inst.get("deck_id"):
            continue
        name = inst.get("card_name")
        if name:
            counts[name] = counts.get(name, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Pool construction
# ---------------------------------------------------------------------------


def _passes_identity(card_identity: frozenset, allowed: Optional[frozenset]) -> bool:
    return allowed is None or card_identity <= allowed


def refine_tier3(
    cards: Iterable[PoolCard],
    color_identity: Any = None,
    cap: Optional[int] = None,
) -> List[PoolCard]:
    """Narrow an existing Tier 3 list (e.g. per target deck) by colour identity and size.
    Cards whose identity is unknown are dropped when a filter is given. Order is preserved."""
    allowed = parse_identity(color_identity)
    out = [c for c in cards if allowed is None or (c.color_identity is not None and c.color_identity <= allowed)]
    return out if cap is None else out[: max(cap, 0)]


def build_candidate_pools(
    session: Mapping,
    r: Any = None,
    *,
    catalog: Optional[CardCatalog] = None,
    owned_instances: Optional[Iterable[Mapping]] = None,
    price_lookup: Optional[PriceLookup] = None,
    config: Optional[PoolConfig] = None,
    target_deck_count: Optional[int] = None,
) -> CandidatePools:
    """Build the three tiered pools for a Reconstruct session.

    Args:
        session: reconstruct session dict (uses `source_decks` snapshots and,
            for the deck count, `preferences`).
        r: Redis client. Used only for defaults not passed explicitly: owned
            instances (instances.py), catalog (RedisCardCatalog) and price_lookup
            (redis_price_lookup). With no r and no override, that part is empty/unknown.
        catalog / owned_instances / price_lookup: overrides (tests, other sources).
        config: PoolConfig (Tier 3 colour filter, cap, ranker, ...).
        target_deck_count: defaults to len(session["preferences"]) or 1; capped 1..2.
    """
    config = config or PoolConfig()
    if config.color_identity is not None:
        config = replace(config, color_identity=parse_identity(config.color_identity))
    if target_deck_count is None:
        target_deck_count = len(session.get("preferences") or []) or 1
    target_deck_count = max(1, min(2, int(target_deck_count)))

    if catalog is None and r is not None:
        catalog = RedisCardCatalog(r)
    ident_by_name: Dict[str, CatalogCard] = {}
    catalog_cards: List[CatalogCard] = list(catalog.iter_cards()) if catalog is not None else []
    for cc in catalog_cards:
        ident_by_name.setdefault(norm_name(cc.name), cc)

    def ident(name: str) -> Optional[frozenset]:
        cc = ident_by_name.get(norm_name(name))
        return cc.color_identity if cc else None

    # Tier 1
    tier1 = [
        replace(c, color_identity=ident(c.name))
        for c in tier1_from_snapshots(session.get("source_decks") or [])
    ]
    taken = {norm_name(c.name) for c in tier1}

    # Tier 2: lowest-tier precedence means any Tier 1 name is dropped here.
    if owned_instances is None and r is not None:
        owned_instances = instance_store.list_instances(r, ownership_status="in_collection")
    owned = free_owned_counts(owned_instances or [])
    tier2: List[PoolCard] = []
    merged: Dict[str, Tuple[str, int]] = {}
    for name, n in owned.items():
        k = norm_name(name)
        if k in taken:
            continue
        prev = merged.get(k)
        merged[k] = (prev[0] if prev else name, (prev[1] if prev else 0) + n)
    for k, (name, n) in sorted(merged.items(), key=lambda kv: kv[1][0].casefold()):
        tier2.append(PoolCard(name=name, tier=Tier.OWNED, available_qty=n, color_identity=ident(name)))
    taken |= set(merged)

    # Tier 3: filter, rank, cap, then price only the survivors.
    excluded = {norm_name(n) for n in config.excluded_names}
    candidates: Dict[str, CatalogCard] = {}
    for cc in catalog_cards:
        k = norm_name(cc.name)
        if k in taken or k in excluded or k in candidates:
            continue
        if cc.legal is False or (cc.legal is None and config.require_known_legal):
            continue
        if not config.include_basics and cc.name in BASIC_LAND_NAMES:
            continue
        if not _passes_identity(cc.color_identity, config.color_identity):
            continue
        candidates[k] = cc
    ranker = config.ranker
    ranked = sorted(
        candidates.values(),
        key=(lambda c: (ranker(c), c.name.casefold())) if ranker else (lambda c: c.name.casefold()),
    )
    kept = ranked if config.tier3_cap is None else ranked[: max(config.tier3_cap, 0)]

    if price_lookup is None and r is not None:
        price_lookup = redis_price_lookup(r)
    prices: Mapping[str, Optional[float]] = {}
    if price_lookup is not None and kept:
        prices = price_lookup([c.name for c in kept]) or {}
    tier3 = [
        PoolCard(
            name=c.name,
            tier=Tier.NEW,
            available_qty=None,
            unit_price=_clean_price(prices.get(c.name)),
            color_identity=c.color_identity,
        )
        for c in kept
    ]

    return CandidatePools(
        tier1=tier1,
        tier2=tier2,
        tier3=tier3,
        target_deck_count=target_deck_count,
        config=config,
        tier3_total_candidates=len(ranked),
    )


def _clean_price(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Tagging and costing
# ---------------------------------------------------------------------------


def _normalise_cards(cards: Iterable[CardInput]) -> List[Tuple[str, int]]:
    """-> [(name, qty)] with duplicate names merged, order kept."""
    order: List[str] = []
    names: Dict[str, str] = {}
    qty: Dict[str, int] = {}
    for c in cards or []:
        if isinstance(c, str):
            name, n = c, 1
        elif isinstance(c, Mapping):
            name, n = c.get("name"), c.get("quantity", c.get("qty", 1))
        else:
            name, n = c
        if not name:
            continue
        n = int(n if n is not None else 1)
        if n <= 0:
            continue
        k = norm_name(name)
        if k not in qty:
            order.append(k)
            names[k] = name
            qty[k] = 0
        qty[k] += n
    return [(names[k], qty[k]) for k in order]


def tag_cards(cards: Iterable[CardInput], pools: CandidatePools) -> List[TaggedCard]:
    """Tag any card list with its source tier.

    `cards`: names, (name, qty) pairs, or dicts with name/quantity. A card in no pool
    is tagged Tier 3 with in_pool=False. `exceeds_available` flags Tier 1/2 cards listed
    in more copies than the pool holds (the cross-deck check is the validator's job).
    """
    out: List[TaggedCard] = []
    for name, n in _normalise_cards(cards):
        pc = pools.get(name)
        if pc is None:
            out.append(TaggedCard(name, n, Tier.NEW, False, None, False))
        else:
            avail = pc.available_qty
            out.append(TaggedCard(name, n, pc.tier, True, avail, avail is not None and n > avail))
    return out


def compute_card_costs(
    cards: Iterable[CardInput],
    pools: CandidatePools,
    *,
    prices: Optional[Mapping[str, Optional[float]]] = None,
    price_lookup: Optional[PriceLookup] = None,
    free_basics: bool = True,
) -> List[CardCost]:
    """Per-card cost: Tier 1 = 0, Tier 2 = 0, Tier 3 = market price x quantity.

    Tier 3 price precedence: the pool entry's price, then `prices`, then `price_lookup`
    (only for cards still unpriced). Still unknown -> price_known=False, cost=None.
    `free_basics`: a Tier 3 basic land costs 0 (it is not bought as a "card").
    """
    tagged = tag_cards(cards, pools)
    prices = prices or {}
    unit: Dict[str, Optional[float]] = {}
    need_lookup: List[str] = []
    for t in tagged:
        if t.tier != Tier.NEW:
            continue
        if free_basics and t.name in BASIC_LAND_NAMES:
            unit[t.name] = 0.0
            continue
        pc = pools.get(t.name)
        price = pc.unit_price if pc is not None else None
        if price is None:
            price = _clean_price(prices.get(t.name))
        unit[t.name] = price
        if price is None:
            need_lookup.append(t.name)
    if need_lookup and price_lookup is not None:
        got = price_lookup(need_lookup) or {}
        for n in need_lookup:
            unit[n] = _clean_price(got.get(n))

    out: List[CardCost] = []
    for t in tagged:
        if t.tier != Tier.NEW:
            out.append(CardCost(t.name, t.quantity, t.tier, 0.0, 0.0, True))
            continue
        p = unit.get(t.name)
        if p is None:
            out.append(CardCost(t.name, t.quantity, t.tier, None, None, False))
        else:
            out.append(CardCost(t.name, t.quantity, t.tier, round(p, 2), round(p * t.quantity, 2), True))
    return out


def compute_deck_cost(cards: Iterable[CardInput], pools: CandidatePools, **kwargs: Any) -> DeckCost:
    """Per-card costs plus the total new-card cost for one deck (see compute_card_costs for kwargs)."""
    costs = compute_card_costs(cards, pools, **kwargs)
    total = round(sum(c.cost for c in costs if c.cost is not None), 2)
    unpriced = [c for c in costs if not c.price_known]
    return DeckCost(
        cards=tuple(costs),
        total_new_cost=total,
        unpriced_cards=tuple(c.name for c in unpriced),
        unpriced_copies=sum(c.quantity for c in unpriced),
    )


def compute_deck_costs(decks: Sequence[Iterable[CardInput]], pools: CandidatePools, **kwargs: Any) -> List[DeckCost]:
    """compute_deck_cost for each target deck (up to 2)."""
    return [compute_deck_cost(d, pools, **kwargs) for d in decks]


# ---------------------------------------------------------------------------
# LLM payload
# ---------------------------------------------------------------------------


def to_llm_payload(
    pools: CandidatePools,
    *,
    color_identity: Any = None,
    tier2_cap: Optional[int] = None,
    tier3_cap: Optional[int] = None,
) -> dict:
    """Compact, JSON-serialisable pools for the prompt.

    tier1 / tier2: [name, available_qty]; tier3: [name, unit_price|null].
    Optional per-call narrowing: `color_identity` (Tier 2 and 3; cards with unknown
    identity are kept in Tier 2, dropped in Tier 3) and per-tier caps. Nothing here
    changes `pools.available_quantities()`, which stays complete for the validator.
    """
    allowed = parse_identity(color_identity)

    def keep2(c: PoolCard) -> bool:
        return allowed is None or c.color_identity is None or c.color_identity <= allowed

    t2 = [c for c in pools.tier2 if keep2(c)]
    if tier2_cap is not None:
        t2 = t2[: max(tier2_cap, 0)]
    t3 = refine_tier3(pools.tier3, allowed, tier3_cap)
    return {
        "target_deck_count": pools.target_deck_count,
        "tier1": [[c.name, c.available_qty] for c in pools.tier1],
        "tier2": [[c.name, c.available_qty] for c in t2],
        "tier3": [[c.name, c.unit_price] for c in t3],
        "tier3_truncated": pools.tier3_truncated or len(t3) < len(pools.tier3),
    }
