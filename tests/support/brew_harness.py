"""Brew integration harness: run Brew generation end-to-end (start_brew ->
trigger_build -> drafts) with controllable deck sources and a fault-injecting
EDHREC/analytics layer, no network, no real Redis.

    result = run_brew(["Atraxa, Praetors' Voice"], "mid",
                      sources=True, edhrec=EdhrecState.DEGRADED)
    result.deck             # draft dict (commanders inside cards)
    result.estimated_power  # float 1-10 from analytics.bracket_eval
    assert_valid_brew_deck(result.deck, result.catalog)

* power: "low" | "mid" | "high" (stored on the session as power_level).
* sources: True = donor decks registered and named; False = empty collection.
* edhrec: EdhrecState.AVAILABLE | UNAVAILABLE (ConnectionError) | DEGRADED
  (degraded_kind: "slow" = timeouts then success via retry, "timeout" = every
  attempt times out, "partial" = truncated payload). Faults are injected at the
  HTTP session of the REAL EDHRecClient (retry/backoff code stays exercised;
  sleeps are no-ops) by patching the client factory used by
  card_eval.edhrec_data.get_page, so any builder reading EDHREC gets them.
* analytics (Commander Salt power) follows the same state: when not AVAILABLE
  the estimate falls back to the bracket band, as production would.
* builder: default `ReferenceBrewBuilder` (deterministic, EDHREC-aware, pads to
  100 from the fixture catalog). Pass the real builder to test it instead; the
  fixture catalog/EDHREC/sources still apply.
"""
from __future__ import annotations

import enum
import json
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from unittest import mock

import requests

import src.server.domain.analytics.bracket_eval as bracket_eval
import src.server.domain.analytics.card_eval.edhrec_data as edhrec_data
import src.server.domain.draft.brew as brew
import src.server.domain.draft.brew_builder as brew_builder
import src.server.domain.draft.reconstruct_pools as reconstruct_pools
from src.server.domain.analytics.providers.edhrec import EDHRecClient, EDHRecError
from src.server.domain.draft.deck_validator import (
    COMMANDER_BANNED, CardCatalog, CardInfo, normalize_name,
)
from tests.test_brew import FakeRedis

POWER_LEVELS = ("low", "mid", "high")
_GAME_CHANGERS_BY_POWER = {"low": 0, "mid": 2, "high": 4}
_COLORS = "WUBRG"


# --------------------------------------------------------------------------
# Fixture catalog
# --------------------------------------------------------------------------

def _info(name, ident="", types=("Creature",), supers=(), legality="legal", text=""):
    return CardInfo(name=name, color_identity=frozenset(ident), types=tuple(types),
                    supertypes=tuple(supers), commander_legality=legality, oracle_text=text)


def seed_catalog(r, catalog: CardCatalog) -> None:
    """Write catalog cards into (fake) Redis as card_meta:{name} so production
    pool building / validation (catalog_from_redis) sees the same cards."""
    for info in {id(c): c for c in catalog._by_key.values()}.values():
        r.set(f"card_meta:{info.name}", json.dumps({
            "name": info.name, "color": "".join(sorted(info.color_identity)),
            "types": list(info.types), "super_types": list(info.supertypes),
            "oracle_text": info.oracle_text, "commander_legality": info.commander_legality,
        }))


def build_fixture_catalog() -> CardCatalog:
    cat = CardCatalog()
    legend = ("Legendary",)
    for c in [
        _info("Atraxa, Praetors' Voice", "WUBG", supers=legend),
        _info("Tymna the Weaver", "WB", supers=legend, text="Partner"),
        _info("Thrasios, Triton Hero", "UG", supers=legend, text="Partner"),
        _info("Krenko, Mob Boss", "R", supers=legend),
        _info("Sol Ring", "", ("Artifact",)),
        _info("Arcane Signet", "", ("Artifact",)),
        _info("Command Tower", "", ("Land",)),
        _info("Rhystic Study", "U", ("Enchantment",)),
        _info("Smothering Tithe", "W", ("Enchantment",)),
        _info("Cyclonic Rift", "U", ("Instant",)),
        _info("Demonic Tutor", "B", ("Sorcery",)),
        _info("Relentless Rats", "B", text="A deck can have any number of cards named Relentless Rats."),
        # Traps a correct builder must never pick:
        _info("Mana Crypt", "", ("Artifact",), legality="banned"),
        _info("Black Lotus", "", ("Artifact",), legality="banned"),
        _info("Lightning Bolt (off-colour trap)", "R", ("Instant",)),
    ]:
        cat.add(c)
    # Deterministic filler: 12 per colour combination of single colours + colourless.
    for col in _COLORS:
        for i in range(1, 31):
            cat.add(_info(f"Fixture {col} Card {i:02d}", col))
    for i in range(1, 31):
        cat.add(_info(f"Fixture C Card {i:02d}", "", ("Artifact",)))
    return cat


GAME_CHANGER_NAMES = ["Rhystic Study", "Smothering Tithe", "Cyclonic Rift", "Demonic Tutor"]


# --------------------------------------------------------------------------
# EDHREC fault injection
# --------------------------------------------------------------------------

class EdhrecState(str, enum.Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"


def edhrec_payload(card_names: Sequence[str], partial: bool = False) -> dict:
    views = [{"name": n, "num_decks": 900 - i, "potential_decks": 1000, "synergy": 0.3}
             for i, n in enumerate(card_names)]
    if partial:
        views = views[: max(1, len(views) // 5)]
        views.append({"name": "Broken Row", "num_decks": "n/a", "potential_decks": None})
    return {"container": {"json_dict": {"card": {"num_decks": 1000},
                                        "cardlists": [{"tag": "topcards", "cardviews": views}]}}}


class _Resp:
    def __init__(self, status=200, body=None):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FaultSession:
    """requests.Session stand-in with scripted faults. Records every call."""

    def __init__(self, state: EdhrecState, payload: dict, degraded_kind: str = "slow",
                 slow_failures: int = 2):
        self.state, self.payload = state, payload
        self.kind, self.slow_failures = degraded_kind, slow_failures
        self.calls: List[str] = []

    def get(self, url, timeout=None, headers=None):
        self.calls.append(url)
        if self.state is EdhrecState.UNAVAILABLE:
            raise requests.exceptions.ConnectionError("injected: EDHREC unreachable")
        if self.state is EdhrecState.DEGRADED:
            if self.kind == "timeout":
                raise requests.exceptions.Timeout("injected: EDHREC timed out")
            if self.kind == "slow" and len(self.calls) <= self.slow_failures:
                raise requests.exceptions.Timeout("injected: EDHREC slow")
            if self.kind == "partial":
                return _Resp(200, self.payload["partial"])
        return _Resp(200, self.payload["full"])


@contextmanager
def inject_edhrec(session: FaultSession):
    """Make edhrec_data.get_page (and anything using EDHRecClient() there) talk
    to `session`, with retry sleeps disabled."""
    def factory(*a, **kw):
        return EDHRecClient(session=session, sleep_fn=lambda s: None, min_request_interval=0.0)
    with mock.patch.object(edhrec_data, "EDHRecClient", factory):
        yield


# --------------------------------------------------------------------------
# Reference builder
# --------------------------------------------------------------------------

class ReferenceBrewBuilder(brew_builder.BaseBrewBuilder):
    """Deterministic, EDHREC-aware builder over the fixture catalog. Priority:
    commanders, donor-deck cards, EDHREC picks (if reachable), power-level
    picks, fixture filler, then basics. Never exceeds identity / banlist."""

    def __init__(self, catalog: CardCatalog):
        self.catalog = catalog
        self.edhrec_error: Optional[str] = None
        self.edhrec_used = 0

    def build(self, session: dict, r: Any) -> list[dict]:
        commanders = list(session["commanders"])
        ident = frozenset().union(*(self.catalog.get(c).color_identity for c in commanders))
        used: set = set()
        cards: List[dict] = []

        def ok(name):
            info = self.catalog.get(name)
            return (info is not None and info.color_identity <= ident
                    and info.commander_legality == "legal" and name not in COMMANDER_BANNED)

        def add(name, tier=3, label="new"):
            k = normalize_name(name)
            if len(cards) >= 100 or k in used or not ok(name):
                return
            used.add(k)
            cards.append({"name": name, "quantity": 1, "tier": tier, "tier_label": label})

        for c in commanders:
            add(c)
        for sd in session.get("donor_decks") or []:
            for c in sd.get("cards", []):
                add(c["name"], 1, "previous_deck")
        try:
            page = edhrec_data.get_page(r, commanders)
            self.edhrec_error = None
            for entry in page["cards"].values():
                add(entry["name"])
                self.edhrec_used += 1
        except EDHRecError as exc:
            self.edhrec_error = str(exc)
        want = _GAME_CHANGERS_BY_POWER.get(str(session.get("power_level")), 0)
        for name in GAME_CHANGER_NAMES:
            if want <= 0:
                break
            before = len(cards)
            add(name)
            want -= len(cards) - before
        for name in ("Sol Ring", "Arcane Signet", "Command Tower"):
            add(name)
        for col in _COLORS:
            for i in range(1, 31):
                add(f"Fixture {col} Card {i:02d}")
        for i in range(1, 31):
            add(f"Fixture C Card {i:02d}")
        basics = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
        pool = [basics[c] for c in _COLORS if c in ident] or ["Wastes"]
        i = 0
        while len(cards) < 100:  # basics repeat (singleton exempt)
            name = pool[i % len(pool)]
            cards.append({"name": name, "quantity": 1, "tier": 3, "tier_label": "new"})
            i += 1
        return [{
            "id": "draft_0", "name": f"{commanders[0]} brew", "commanders": commanders,
            "power_level": session.get("power_level", ""), "cards": cards,
            "total_new_cost": 0.0, "is_complete": True, "unpriced_cards": [],
        }]


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------

@dataclass
class BrewResult:
    deck: dict
    estimated_power: float
    estimated_bracket: int
    power_source: str
    session: dict
    catalog: CardCatalog
    edhrec_calls: List[str] = field(default_factory=list)
    edhrec_error: Optional[str] = None
    builder: Any = None


def _donor_decks(commanders: Sequence[str], catalog: CardCatalog) -> List[dict]:
    ident = frozenset().union(*(catalog.get(c).color_identity for c in commanders))
    names = [f"Fixture {c} Card {i:02d}" for c in _COLORS if c in ident for i in range(1, 11)]
    names += [f"Fixture C Card {i:02d}" for i in range(1, 6)]
    return [{
        "id": 901, "name": "Harness Donor", "registry_id": "archidekt:901", "source": "archidekt",
        "source_id": "901", "status": "physical", "tags": [], "commanders": list(commanders),
        "cards": [{"name": n, "quantity": 1} for n in names],
    }]


def run_brew(commanders: Sequence[str] | str, power: str = "mid", *, sources: bool = True,
             edhrec: EdhrecState = EdhrecState.AVAILABLE, degraded_kind: str = "slow",
             build_mode: str = "owned_first", builder: Any = None,
             catalog: Optional[CardCatalog] = None) -> BrewResult:
    if power not in POWER_LEVELS:
        raise ValueError(f"power must be one of {POWER_LEVELS}")
    cmds = [commanders] if isinstance(commanders, str) else list(commanders)
    catalog = catalog or build_fixture_catalog()
    for c in cmds:
        if catalog.get(c) is None:
            raise ValueError(f"commander not in fixture catalog: {c}")

    r = FakeRedis()
    donors = _donor_decks(cmds, catalog) if sources else []
    r.set("decks", json.dumps(donors))
    seed_catalog(r, catalog)
    full = [n for n in (f"Fixture {c} Card {i:02d}" for c in _COLORS for i in range(11, 31))]
    payload = {"full": edhrec_payload(full), "partial": edhrec_payload(full, partial=True)}
    session_http = FaultSession(edhrec, payload, degraded_kind)
    builder = builder or ReferenceBrewBuilder(catalog)

    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(
            reconstruct_pools, "redis_price_lookup", lambda r, refresh=True: (lambda names: {})))
        stack.enter_context(inject_edhrec(session_http))
        orig = brew_builder.get_builder()
        brew_builder.set_builder(builder)
        stack.callback(brew_builder.set_builder, orig)

        s = brew.start_brew(r, cmds, [d["registry_id"] for d in donors], build_mode,
                            power_level=power)
        brew.trigger_build(r, s["session_id"], sync=True)
        session = brew.get_session(r, s["session_id"])
        deck = brew.get_drafts(r, s["session_id"])[0]

    # Analytics: Commander Salt-style power only when "analytics" is reachable.
    meta: Dict[str, Any] = {}
    if edhrec is EdhrecState.AVAILABLE:
        meta["commandersalt"] = {"power": {"low": 3.0, "mid": 6.5, "high": 8.5}[power]}
    ev = bracket_eval.evaluate_bracket(
        {"commanders": deck.get("commanders", cmds), "cards": deck["cards"]}, None, meta)
    return BrewResult(
        deck=deck, estimated_power=ev.estimated_power_level,
        estimated_bracket=ev.estimated_bracket, power_source=ev.power_source,
        session=session, catalog=catalog, edhrec_calls=session_http.calls,
        edhrec_error=getattr(builder, "edhrec_error", None), builder=builder)
