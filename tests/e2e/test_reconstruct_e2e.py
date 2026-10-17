"""End-to-end Reconstruct flow over the real FastAPI app and a real Redis.

Covers the six scenarios from t_7585f336:
  1. single source deck -> one new deck
  2. two source decks  -> two new decks
  3. partner commander selection
  4. minimal preferences (general idea only, no commander named)
  5. regenerate drafts, then accept the regenerated set
  6. abandon after drafts are generated

Determinism: the LLM is replaced by ScriptedLLM, which returns pre-written
replies (JSON in the agent's schema). Every other stage is the real code:
start -> decommission, candidate pools (tiers), deterministic validation,
costing, accept (dedication) and abandon. Redis is real; the test flushes
database RECONSTRUCT_E2E_DB only.

Run:  RECONSTRUCT_E2E_REDIS_URL=redis://127.0.0.1:6411/15 \
      python -m pytest tests/e2e/test_reconstruct_e2e.py -v

If Redis is unreachable the module is skipped (never falls back to a
production database).

NOTE: this suite drives the REST API, not the browser. The Reconstruct UI
(PR #138) is not on main; see the bug report for the browser-level gap.
"""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

REDIS_URL = os.getenv("RECONSTRUCT_E2E_REDIS_URL", "redis://127.0.0.1:6411/15")
E2E_DB = 15

try:
    import redis as redis_lib

    _probe = redis_lib.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
    _probe.ping()
    _REDIS_OK = True
except Exception:  # pragma: no cover - environment dependent
    _REDIS_OK = False

pytestmark = pytest.mark.skipif(not _REDIS_OK, reason=f"Redis not reachable at {REDIS_URL}")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
import deck_validator as dv  # noqa: E402
import instances as instance_store  # noqa: E402
import reconstruct  # noqa: E402
import registry  # noqa: E402
from reconstruct_builder import ReconstructBuilder  # noqa: E402

# ---------------------------------------------------------------------------
# Fixed card universe (synthetic, so legality and colour identity are known)
# ---------------------------------------------------------------------------

U = frozenset({"U"})
R = frozenset({"R"})
UR = frozenset({"U", "R"})
C = frozenset()

COMMANDER_BLUE = "Ezra Blue"            # U legend, source deck A's commander
COMMANDER_RED = "Rhea Red"              # R legend, source deck B's commander
PARTNER_A = "Partner One"               # U legend w/ Partner
PARTNER_B = "Partner Two"               # R legend w/ Partner

ALPHA = [f"Alpha Card {i:03d}" for i in range(1, 100)]    # source deck A (U/colourless)
BETA = [f"Beta Card {i:03d}" for i in range(1, 100)]      # source deck B (R/colourless)
OWNED = [f"Owned Gem {i:02d}" for i in range(1, 31)]      # tier 2: free, undedicated
DEDICATED = [f"Dedicated Card {i:02d}" for i in range(1, 13)]  # bound to OTHER active deck
SHARED = "Shared Dup"                                     # 1 copy, dedicated to OTHER deck
NEW = [f"New Card {i:03d}" for i in range(1, 201)]        # tier 3: market

OTHER_DECK_NAME = "Other Active Deck"
CONTROL_DECK_NAME = "Control Deck"


def _build_catalog() -> dv.CardCatalog:
    cat = dv.CardCatalog()

    def legend(name, ci, sub=("Human",), kw=()):
        cat.add(dv.CardInfo(name=name, color_identity=ci, types=("Creature",),
                            supertypes=("Legendary",), subtypes=sub, keywords=kw,
                            oracle_text="", commander_legality="legal"))

    legend(COMMANDER_BLUE, U, ("Human", "Wizard"))
    legend(COMMANDER_RED, R, ("Human", "Warrior"))
    legend(PARTNER_A, U, ("Human",), ("Partner",))
    legend(PARTNER_B, R, ("Human",), ("Partner",))

    def card(name, ci, types=("Creature",)):
        cat.add(dv.CardInfo(name=name, color_identity=ci, types=types,
                            commander_legality="legal", oracle_text=""))

    for n in ALPHA:
        card(n, U if int(n.split()[-1]) % 3 else C)
    for n in BETA:
        card(n, R if int(n.split()[-1]) % 3 else C)
    for n in OWNED:
        card(n, C, ("Artifact",))
    for n in DEDICATED:
        card(n, U, ("Sorcery",))
    card(SHARED, C, ("Sorcery",))
    for n in NEW:
        card(n, U if int(n.split()[-1]) % 2 else C, ("Artifact",))
    return cat


CATALOG = _build_catalog()


def _all_card_names() -> List[str]:
    return sorted({info.name for info in CATALOG._by_key.values()})


def _identity(name: str) -> frozenset:
    for info in CATALOG._by_key.values():
        if info.name == name:
            return info.color_identity
    return C


def _price_for(name: str) -> Optional[float]:
    """Deterministic market prices for tier-3 candidates."""
    if name in DEDICATED or name == SHARED:
        return 4.00
    if name in (PARTNER_A, PARTNER_B):
        return 2.00
    if name in NEW:
        return round(0.25 + (int(name.split()[-1]) % 10) * 0.5, 2)
    return None


PRICES = {n: _price_for(n) for n in _all_card_names()}


def _price_lookup(names):
    return {n: PRICES.get(n) for n in names}


# ---------------------------------------------------------------------------
# Scripted LLM
# ---------------------------------------------------------------------------

class ScriptedLLM:
    """Returns pre-written replies in order; records every prompt it saw."""

    def __init__(self, replies: Sequence[str]):
        self._replies = list(replies)
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self._replies:
            raise AssertionError("LLM called more times than scripted")
        return self._replies.pop(0)


def deck_reply(decks: Sequence[Dict[str, Any]]) -> str:
    """Serialize deck plans into the agent's JSON reply.

    Each plan: {"name", "commanders": [...], "cards": [(name, qty), ...]}.
    Commanders are listed in `cards` too (the agent schema says "All 100
    cards, commander(s) included").
    """
    out = []
    for d in decks:
        cards = []
        for name, qty in d["cards"]:
            cards.append({
                "name": name,
                "quantity": qty,
                "tier": _planned_tier(name),
                "why": f"Plays into {d['name']}",
            })
        out.append({
            "name": d["name"],
            "commanders": d["commanders"],
            "cards": cards,
            "explanation": f"Scripted draft {d['name']}.",
        })
    return json.dumps({"decks": out})


def _planned_tier(name: str) -> int:
    """Tier the stub *claims*. The system re-derives tiers from pools; any
    disagreement is ignored, so this only needs to be plausible."""
    if name in ALPHA or name in BETA or name in (COMMANDER_BLUE, COMMANDER_RED):
        return 1
    if name in OWNED:
        return 2
    return 3


def _pad_basics(cards: List[Tuple[str, int]], colour: str) -> List[Tuple[str, int]]:
    """Fill to 100 cards with basic lands (exempt from pools, free)."""
    basic = {"U": "Island", "R": "Mountain", "": "Wastes"}[colour]
    total = sum(q for _, q in cards)
    if total < 100:
        cards = cards + [(basic, 100 - total)]
    return cards


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def r():
    client = redis_lib.from_url(REDIS_URL, decode_responses=True)
    assert client.connection_pool.connection_kwargs.get("db") == E2E_DB, \
        "refusing to flush a Redis DB other than the e2e DB"
    client.flushdb()
    yield client
    client.flushdb()


@pytest.fixture
def api(r, monkeypatch):
    """TestClient over the real app, with the Reconstruct builder pointed at
    scripted LLM replies. Returns a factory: api.llm(replies) installs them."""
    monkeypatch.setattr(app_module, "r", r)
    original_builder = reconstruct.get_builder()

    class Harness:
        def __init__(self):
            self.client = TestClient(app_module.app)
            self.llm: Optional[ScriptedLLM] = None

        def install_llm(self, replies: Sequence[str]) -> ScriptedLLM:
            self.llm = ScriptedLLM(replies)
            reconstruct.set_builder(ReconstructBuilder(
                client=self.llm,
                catalog=CATALOG,
                price_lookup=_price_lookup,
                owned_instances=None,  # read ownership from Redis (real path)
            ))
            return self.llm

    h = Harness()
    yield h
    reconstruct.set_builder(original_builder)


# ---------------------------------------------------------------------------
# Seeding (uses the real registry / instances code paths)
# ---------------------------------------------------------------------------

def _seed_deck(r, name: str, commanders: List[str], cards: List[Tuple[str, int]],
               source_id: str, status: str = "physical") -> dict:
    normalized = {
        "source": "manual",
        "source_id": source_id,
        "name": name,
        "color": "",
        "commanders": commanders,
        "commander_uids": [],
        "folder": "Physical",
        "description": "",
        "cards": [{"name": n, "quantity": q} for n, q in cards],
        "url": "",
    }
    deck = registry.upsert_deck(r, normalized, default_status=status)
    if status == "physical":
        instance_store.auto_bind_physical(
            r,
            deck_registry_id=registry.registry_id_of(deck),
            deck_name=name,
            decklist=normalized["cards"],
        )
    return deck


def seed_world(r) -> Dict[str, Any]:
    """Two physical source decks (A blue, B red), one other active deck that
    holds dedicated copies, a control deck for the decommission comparison,
    and undedicated owned inventory (tier 2)."""
    # Owned, undedicated copies. Created first so the other deck's bind
    # cannot consume them by accident (auto_bind reuses in_collection copies).
    for name in OWNED:
        instance_store.create_instance(r, name, ownership_status="in_collection")
    instance_store.create_instance(r, SHARED, ownership_status="in_collection")

    alpha_cards = [(COMMANDER_BLUE, 1)] + [(n, 1) for n in ALPHA]
    beta_cards = [(COMMANDER_RED, 1)] + [(n, 1) for n in BETA]

    deck_a = _seed_deck(r, "Source Alpha", [COMMANDER_BLUE], alpha_cards, "src-alpha")
    deck_b = _seed_deck(r, "Source Beta", [COMMANDER_RED], beta_cards, "src-beta")
    other = _seed_deck(r, OTHER_DECK_NAME, [COMMANDER_RED],
                       [(COMMANDER_RED, 1)] + [(n, 1) for n in DEDICATED] + [(SHARED, 1)],
                       "other-active")
    control = _seed_deck(r, CONTROL_DECK_NAME, [COMMANDER_BLUE], alpha_cards, "control")

    return {
        "source_a": registry.registry_id_of(deck_a),
        "source_b": registry.registry_id_of(deck_b),
        "other": registry.registry_id_of(other),
        "control": registry.registry_id_of(control),
    }


# ---------------------------------------------------------------------------
# Plan helpers: build exactly-100-card drafts out of the fixed universe
# ---------------------------------------------------------------------------

def plan_blue_single() -> List[Tuple[str, int]]:
    """Blue draft for scenario 1: 1 commander, 60 Alpha (tier 1), 20 Owned Gems
    (tier 2), 9 New (tier 3), 4 Dedicated (tier 3, other deck's copies)."""
    cards = [(COMMANDER_BLUE, 1)]
    cards += [(n, 1) for n in ALPHA[:60]]
    cards += [(n, 1) for n in OWNED[:20]]
    cards += [(n, 1) for n in NEW[:9]]
    cards += [(n, 1) for n in DEDICATED[:4]]
    return cards  # 1+60+20+9+4 = 94 -> pad to 100 with Islands


def plan_blue_single_full() -> List[Tuple[str, int]]:
    return _pad_basics(plan_blue_single(), "U")


def plan_red_second() -> List[Tuple[str, int]]:
    """Red draft for scenario 2 (second deck). Uses Beta (tier 1) and the owned
    gems NOT used by the blue draft (OWNED[20:30]), so no tier-2 copy is
    double-allocated."""
    colourless_new = [n for n in NEW if int(n.split()[-1]) % 2 == 0]  # identity C
    cards = [(COMMANDER_RED, 1)]
    cards += [(n, 1) for n in BETA[:70]]
    cards += [(n, 1) for n in OWNED[20:30]]
    cards += [(n, 1) for n in colourless_new[:10]]
    cards += [(SHARED, 1)]  # dedicated to other deck -> must be tier 3, paid
    return _pad_basics(cards, "R")


def plan_partner_pair() -> List[Tuple[str, int]]:
    """UR partner pair draft. Partner One/Two are not in any source deck, so
    both are Tier 3 (bought). Sources A (U) and B (R) supply Tier 1."""
    cards = [(PARTNER_A, 1), (PARTNER_B, 1)]
    cards += [(n, 1) for n in ALPHA[:40]]
    cards += [(n, 1) for n in BETA[:40]]
    cards += [(n, 1) for n in OWNED[:10]]
    cards += [(n, 1) for n in NEW[:6]]
    return _pad_basics(cards, "U")  # 98 -> 2 Islands


def plan_minimal() -> List[Tuple[str, int]]:
    """Same shape as scenario 1 but for a model given only a general idea."""
    return plan_blue_single_full()


def tier_map() -> Dict[str, int]:
    return {n: _planned_tier(n) for n in _all_card_names()}


# ---------------------------------------------------------------------------
# Invariant helpers
# ---------------------------------------------------------------------------

BASIC_NAMES = {"Island", "Mountain", "Wastes"}
OTHER_DECK_CARDS = set(DEDICATED) | {SHARED}


def free_counts(r) -> Counter:
    from reconstruct_pools import free_owned_counts
    return Counter(free_owned_counts(instance_store.list_instances(r, ownership_status="in_collection")))


def assert_draft_legal(draft: Dict[str, Any], expected_identity: frozenset,
                       tier1_names: Dict[str, int], tier2_names: Dict[str, int]) -> None:
    cards = draft["cards"]
    # 100 cards (commander(s) included)
    total = sum(c["quantity"] for c in cards)
    assert total == 100, f"{draft['name']}: {total} cards, expected 100"

    # singleton (basics exempt)
    for c in cards:
        if c["name"] not in BASIC_NAMES:
            assert c["quantity"] == 1, f"{c['name']} x{c['quantity']} violates singleton"

    # every commander is in the list
    names = {c["name"] for c in cards}
    for cmd in draft["commanders"]:
        assert cmd in names, f"commander {cmd} missing from card list"

    # colour identity: every card within the union of commander identities
    for c in cards:
        ci = _identity(c["name"])
        assert ci <= expected_identity, f"{c['name']} identity {set(ci)} outside {set(expected_identity)}"

    # every card carries a tier and a rationale
    for c in cards:
        assert c["tier"] in (1, 2, 3), f"{c['name']} has no source tier: {c.get('tier')!r}"
        assert c.get("rationale"), f"{c['name']} has no rationale"

    # tier-membership truth: tier 1 only from source snapshots, tier 2 only from free copies
    for c in cards:
        if c["name"] in BASIC_NAMES:
            continue
        if c["tier"] == 1:
            assert c["name"] in tier1_names, f"{c['name']} tagged tier 1 but not in a source deck"
        if c["tier"] == 2:
            assert c["name"] in tier2_names, f"{c['name']} tagged tier 2 but not a free owned copy"
            assert c["name"] not in OTHER_DECK_CARDS, f"{c['name']} dedicated elsewhere but tagged free"

    # cost rules
    for c in cards:
        if c["tier"] in (1, 2):
            assert c["cost"] in (0, 0.0), f"{c['name']} tier {c['tier']} costs {c['cost']}"
    tier3_sum = round(sum(c["cost"] for c in cards if c["tier"] == 3 and c["cost"] is not None), 2)
    assert draft["total_new_cost"] == pytest.approx(tier3_sum, abs=0.005), \
        f"{draft['name']}: total {draft['total_new_cost']} != tier3 sum {tier3_sum}"

    # dedicated-elsewhere cards are never free (a basic land is never in that set)
    for c in cards:
        if c["name"] in OTHER_DECK_CARDS:
            assert c["tier"] == 3 and c["cost"] and c["cost"] > 0, \
                f"{c['name']} is dedicated to {OTHER_DECK_NAME} but was not priced as a purchase"


def assert_no_double_allocation(drafts: Sequence[Dict[str, Any]], free: Counter,
                                tier1_snapshot: Counter) -> None:
    """Across ALL drafts in the set: tier 2 usage <= free copies, tier 1 usage
    <= copies in source decks. Basics exempt."""
    t2, t1 = Counter(), Counter()
    for d in drafts:
        for c in d["cards"]:
            if c["name"] in BASIC_NAMES:
                continue
            if c["tier"] == 2:
                t2[c["name"]] += c["quantity"]
            if c["tier"] == 1:
                t1[c["name"]] += c["quantity"]
    for name, n in t2.items():
        assert n <= free.get(name, 0), f"{name}: {n} free uses > {free.get(name, 0)} owned"
    for name, n in t1.items():
        assert n <= tier1_snapshot.get(name, 0), f"{name}: {n} tier-1 uses > {tier1_snapshot.get(name, 0)} in sources"


def snapshot_counts(session: Dict[str, Any]) -> Counter:
    c = Counter()
    for src in session["source_decks"]:
        for card in src["cards"]:
            c[card["name"]] += card.get("quantity", 1)
    return c


def assert_sources_retired_like_decommission(r, source_ids: Sequence[str]) -> None:
    decks = registry._load_decks(r)
    for sid in source_ids:
        deck = registry._find_in(decks, sid)
        assert deck is not None, f"source {sid} vanished"
        assert deck["status"] == "retired", f"{sid} status {deck['status']!r}"
        assert deck.get("decommissioned_at"), f"{sid} has no decommissioned_at"
        assert registry.DEFAULT_DECOMMISSION_TAG not in (deck.get("tags") or []), f"{sid} kept 'removed' tag"
        bound = [i for i in instance_store.list_instances(r, deck_id=sid) if i.get("ownership_status") == "in_deck"]
        assert not bound, f"{sid} still has {len(bound)} in_deck instances"


def dedicated_map(r) -> Dict[str, str]:
    """{instance_id: deck_id} for every instance currently dedicated (in_deck)."""
    return {i["id"]: str(i.get("deck_id")) for i in instance_store.list_instances(r, ownership_status="in_deck")}


def assert_accepted_deck_dedicated(r, deck_registry_id: str, draft: Dict[str, Any],
                                   before: Dict[str, str]) -> None:
    """After accept: the new deck holds exactly `quantity` dedicated copies of
    each card; every instance dedicated to any OTHER deck is unchanged; and no
    instance is dedicated to two decks (a dict maps each id to one deck)."""
    after = dedicated_map(r)
    mine = {iid for iid, d in after.items() if d == deck_registry_id}
    per_name = Counter(i["card_name"] for i in instance_store.list_instances(r, deck_id=deck_registry_id)
                       if i.get("ownership_status") == "in_deck")
    for c in draft["cards"]:
        assert per_name[c["name"]] == c["quantity"], \
            f"{c['name']}: {per_name[c['name']]} dedicated copies, deck lists {c['quantity']}"
    assert len(mine) == sum(c["quantity"] for c in draft["cards"])
    # other decks' dedications untouched
    for iid, deck in before.items():
        assert after.get(iid) == deck, f"instance {iid} moved from {deck} to {after.get(iid)}"



# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _start(api, deck_ids):
    resp = api.client.post("/api/reconstruct", json={"deck_ids": deck_ids})
    assert resp.status_code == 201, resp.text
    return resp.json()["session_id"]


def _prefs(api, sid, prefs):
    resp = api.client.post(f"/api/reconstruct/{sid}/preferences", json={"preferences": prefs})
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]


def _build(api, sid):
    resp = api.client.post(f"/api/reconstruct/{sid}/build?sync=true")
    assert resp.status_code == 200, resp.text
    return resp.json()["session"]


def _drafts(api, sid):
    resp = api.client.get(f"/api/reconstruct/{sid}/drafts")
    assert resp.status_code == 200, resp.text
    return resp.json()["drafts"]


def _accept(api, sid, indices):
    return api.client.post(f"/api/reconstruct/{sid}/accept", json={"draft_indices": indices})



# ---------------------------------------------------------------------------
# Scenario 1: single deck -> one new deck
# ---------------------------------------------------------------------------

def test_s1_single_deck_to_one_new_deck(r, api):
    ids = seed_world(r)
    api.install_llm([deck_reply([{"name": "Blue Phoenix", "commanders": [COMMANDER_BLUE],
                                  "cards": plan_blue_single_full()}])])

    sid = _start(api, [ids["source_a"]])
    session = api.client.get(f"/api/reconstruct/{sid}").json()["session"]
    assert session["status"] == "pending_preferences"
    assert_sources_retired_like_decommission(r, [ids["source_a"]])

    _prefs(api, sid, [{"commanders": [COMMANDER_BLUE], "themes": ["blue artifacts"]}])
    tier1 = snapshot_counts(session)
    free = free_counts(r)
    session = _build(api, sid)
    assert session["status"] == "drafts_ready"

    drafts = _drafts(api, sid)
    assert len(drafts) == 1
    draft = drafts[0]
    assert_draft_legal(draft, expected_identity=U, tier1_names=tier1, tier2_names=free)
    assert_no_double_allocation(drafts, free, tier1)

    before = dedicated_map(r)
    resp = _accept(api, sid, [0])
    assert resp.status_code == 200, resp.text
    created = resp.json()["created_decks"]
    assert len(created) == 1
    assert_accepted_deck_dedicated(r, registry.registry_id_of(created[0]), draft, before)
    assert_sources_retired_like_decommission(r, [ids["source_a"]])


def test_s1_source_retirement_matches_normal_decommission(r, api):
    """Reconstruct's retirement of a source deck must leave the same state
    as the existing one-deck decommission endpoint."""
    ids = seed_world(r)
    api.install_llm([deck_reply([{"name": "Blue Phoenix", "commanders": [COMMANDER_BLUE],
                                  "cards": plan_blue_single_full()}])])

    # Control deck: normal decommission (retire via the endpoint)
    resp_ctrl = api.client.post(f"/api/decks/{ids['control']}/decommission", json={})
    assert resp_ctrl.status_code == 200, resp_ctrl.text
    ctrl = resp_ctrl.json()

    sid = _start(api, [ids["source_a"]])
    decks = registry._load_decks(r)
    src = registry._find_in(decks, ids["source_a"])
    ctrl_deck = registry._find_in(decks, ids["control"])

    assert src["status"] == ctrl_deck["status"] == ctrl["status"] == "retired"
    assert src.get("tags") == ctrl_deck.get("tags")
    assert registry.DEFAULT_DECOMMISSION_TAG not in (src.get("tags") or [])
    assert bool(src.get("decommissioned_at")) and bool(ctrl_deck.get("decommissioned_at"))
    session = api.client.get(f"/api/reconstruct/{sid}").json()["session"]
    result = session["decommission_results"][0]
    assert result["unassigned_count"] == ctrl["unassigned_count"] == 100
    assert result["tag_removed"] == ctrl["tag_removed"]
    assert result["status"] == "retired"


# ---------------------------------------------------------------------------
# Scenario 2: two source decks -> two new decks
# ---------------------------------------------------------------------------

def test_s2_two_decks_to_two_new_decks(r, api):
    ids = seed_world(r)
    api.install_llm([deck_reply([
        {"name": "Blue Phoenix", "commanders": [COMMANDER_BLUE], "cards": plan_blue_single_full()},
        {"name": "Red Hammer", "commanders": [COMMANDER_RED], "cards": plan_red_second()},
    ])])

    sid = _start(api, [ids["source_a"], ids["source_b"]])
    session0 = api.client.get(f"/api/reconstruct/{sid}").json()["session"]
    assert_sources_retired_like_decommission(r, [ids["source_a"], ids["source_b"]])

    _prefs(api, sid, [
        {"commanders": [COMMANDER_BLUE], "themes": ["blue artifacts"]},
        {"commanders": [COMMANDER_RED], "themes": ["red sorceries"]},
    ])
    tier1 = snapshot_counts(session0)
    free = free_counts(r)
    session = _build(api, sid)
    assert session["status"] == "drafts_ready"

    drafts = _drafts(api, sid)
    assert len(drafts) == 2
    assert_draft_legal(drafts[0], expected_identity=U, tier1_names=tier1, tier2_names=free)
    assert_draft_legal(drafts[1], expected_identity=R, tier1_names=tier1, tier2_names=free)
    # Shared pool: the two drafts together must not use more than the owned copies.
    assert_no_double_allocation(drafts, free, tier1)

    before = dedicated_map(r)
    resp = _accept(api, sid, [0, 1])
    assert resp.status_code == 200, resp.text
    created = resp.json()["created_decks"]
    assert len(created) == 2
    for deck, draft in zip(created, drafts):
        assert_accepted_deck_dedicated(r, registry.registry_id_of(deck), draft, before)
    assert_sources_retired_like_decommission(r, [ids["source_a"], ids["source_b"]])


# ---------------------------------------------------------------------------
# Scenario 3: partner commander selection
# ---------------------------------------------------------------------------

def test_s3_partner_commander_pair(r, api):
    ids = seed_world(r)
    api.install_llm([deck_reply([{"name": "Partner Storm",
                                  "commanders": [PARTNER_A, PARTNER_B],
                                  "cards": plan_partner_pair()}])])
    sid = _start(api, [ids["source_a"], ids["source_b"]])
    session0 = api.client.get(f"/api/reconstruct/{sid}").json()["session"]
    _prefs(api, sid, [{"commanders": [PARTNER_A, PARTNER_B], "themes": ["partners"]}])
    tier1 = snapshot_counts(session0)
    free = free_counts(r)
    session = _build(api, sid)
    assert session["status"] == "drafts_ready"

    draft = _drafts(api, sid)[0]
    assert sorted(draft["commanders"]) == sorted([PARTNER_A, PARTNER_B])
    assert_draft_legal(draft, expected_identity=UR, tier1_names=tier1, tier2_names=free)
    # Partners are not in any source deck or owned pool -> both are purchases.
    by_name = {c["name"]: c for c in draft["cards"]}
    for p in (PARTNER_A, PARTNER_B):
        assert by_name[p]["tier"] == 3 and by_name[p]["cost"] == 2.0
    assert draft["is_complete"] is True
    assert draft["unpriced_cards"] == []

    before = dedicated_map(r)
    created = _accept(api, sid, [0]).json()["created_decks"]
    assert sorted(created[0]["commanders"]) == sorted([PARTNER_A, PARTNER_B])
    assert_accepted_deck_dedicated(r, registry.registry_id_of(created[0]), draft, before)


# ---------------------------------------------------------------------------
# Scenario 4: minimal preferences (general idea only, no commander)
# ---------------------------------------------------------------------------

def test_s4_minimal_preferences_general_idea_only(r, api):
    ids = seed_world(r)
    api.install_llm([deck_reply([{"name": "Idea Deck", "commanders": [COMMANDER_BLUE],
                                  "cards": plan_minimal()}])])
    sid = _start(api, [ids["source_a"]])
    session0 = api.client.get(f"/api/reconstruct/{sid}").json()["session"]

    session = _prefs(api, sid, [{"notes": "something fun with blue artifacts"}])["preferences"]
    assert session[0]["commanders"] == []
    _build(api, sid)

    drafts = _drafts(api, sid)
    assert len(drafts) == 1
    assert_draft_legal(drafts[0], expected_identity=U,
                       tier1_names=snapshot_counts(session0),
                       tier2_names=free_counts(r))


# ---------------------------------------------------------------------------
# Scenario 5: regenerate, then accept
# ---------------------------------------------------------------------------

def test_s5_regenerate_then_accept(r, api):
    ids = seed_world(r)
    first = plan_blue_single_full()
    # Second draft: swap 9 New Cards for 9 different ones (still legal, still 100).
    second = [(n, q) for n, q in first if n not in NEW[:9]] + [(n, 1) for n in NEW[100:109]]
    assert sum(q for _, q in second) == 100
    llm = api.install_llm([
        deck_reply([{"name": "Draft v1", "commanders": [COMMANDER_BLUE], "cards": first}]),
        deck_reply([{"name": "Draft v2", "commanders": [COMMANDER_BLUE], "cards": second}]),
    ])
    sid = _start(api, [ids["source_a"]])
    session0 = api.client.get(f"/api/reconstruct/{sid}").json()["session"]
    _prefs(api, sid, [{"commanders": [COMMANDER_BLUE], "themes": ["blue artifacts"]}])
    tier1 = snapshot_counts(session0)
    free = free_counts(r)

    _build(api, sid)
    assert _drafts(api, sid)[0]["name"] == "Draft v1"

    # Regenerate: same preferences, new build replaces the drafts.
    session = _build(api, sid)
    assert session["status"] == "drafts_ready"
    assert len(llm.calls) == 2
    drafts = _drafts(api, sid)
    assert len(drafts) == 1 and drafts[0]["name"] == "Draft v2"
    assert_draft_legal(drafts[0], expected_identity=U, tier1_names=tier1, tier2_names=free)

    before = dedicated_map(r)
    created = _accept(api, sid, [0]).json()["created_decks"]
    assert created[0]["name"] == "Draft v2"
    assert_accepted_deck_dedicated(r, registry.registry_id_of(created[0]), drafts[0], before)
    # The accepted deck contains v2's New cards, not v1's.
    names = {c["name"] for c in drafts[0]["cards"]}
    assert not (names & set(NEW[:9]))


# ---------------------------------------------------------------------------
# Scenario 6: abandon after drafts are generated
# ---------------------------------------------------------------------------

def test_s6_abandon_after_drafts(r, api):
    ids = seed_world(r)
    api.install_llm([deck_reply([{"name": "Abandoned", "commanders": [COMMANDER_BLUE],
                                  "cards": plan_blue_single_full()}])])
    sid = _start(api, [ids["source_a"]])
    _prefs(api, sid, [{"commanders": [COMMANDER_BLUE]}])
    _build(api, sid)
    assert _drafts(api, sid)

    decks_before = len(registry._load_decks(r))
    free_before = free_counts(r)

    resp = api.client.post(f"/api/reconstruct/{sid}/abandon", json={"reason": "changed my mind"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["session"]["status"] == "abandoned"
    assert resp.json()["session"]["abandon_reason"] == "changed my mind"

    # No new deck, source stays retired, owned copies are still undedicated (not bound).
    assert len(registry._load_decks(r)) == decks_before
    assert_sources_retired_like_decommission(r, [ids["source_a"]])
    assert free_counts(r) == free_before

    # Accept and rebuild are refused after abandon.
    assert _accept(api, sid, [0]).status_code == 400
    assert api.client.post(f"/api/reconstruct/{sid}/build?sync=true").status_code == 400


# ---------------------------------------------------------------------------
# Known bug: spare owned copy of a source-deck card is hidden (issue #157)
# ---------------------------------------------------------------------------

SHARED_CARD = "Alpha Card 003"  # colourless, in source A


def test_s2_shared_card_extra_owned_copy_is_free(r, api):
    ids = seed_world(r)
    # The user owns one extra undedicated copy of a card that is also in source A.
    instance_store.create_instance(r, SHARED_CARD, ownership_status="in_collection")

    red_with_shared = _pad_basics(
        [(COMMANDER_RED, 1)] + [(n, 1) for n in BETA[:70]] + [(n, 1) for n in OWNED[20:30]]
        + [(n, 1) for n in [x for x in NEW if int(x.split()[-1]) % 2 == 0][:10]]
        + [(SHARED_CARD, 1)],
        "R",
    )
    reply = deck_reply([
        {"name": "Blue Phoenix", "commanders": [COMMANDER_BLUE], "cards": plan_blue_single_full()},
        {"name": "Red Hammer", "commanders": [COMMANDER_RED], "cards": red_with_shared},
    ])
    # Same reply on every retry (max_retries=3 -> 4 calls): the model is told the
    # error and still returns a legal set, so only the pool can make it fail.
    api.install_llm([reply] * 4)
    sid = _start(api, [ids["source_a"], ids["source_b"]])
    _prefs(api, sid, [{"commanders": [COMMANDER_BLUE]}, {"commanders": [COMMANDER_RED]}])
    session = _build(api, sid)
    assert session["status"] == "drafts_ready", session.get("error")

    red = _drafts(api, sid)[1]
    shared = [c for c in red["cards"] if c["name"] == SHARED_CARD][0]
    assert shared["tier"] == 2 and shared["cost"] == 0


# ---------------------------------------------------------------------------
# Input validation on the Reconstruct entry points
# ---------------------------------------------------------------------------

def test_rejects_more_than_two_source_decks_without_retiring_any(r, api):
    ids = seed_world(r)
    extra = _seed_deck(r, "Source Gamma", [COMMANDER_BLUE], [(COMMANDER_BLUE, 1)], "src-gamma")
    gamma = registry.registry_id_of(extra)
    resp = api.client.post("/api/reconstruct", json={"deck_ids": [ids["source_a"], ids["source_b"], gamma]})
    assert resp.status_code == 400
    decks = registry._load_decks(r)
    for did in (ids["source_a"], ids["source_b"], gamma):
        assert registry._find_in(decks, did)["status"] == "physical"


def test_rejects_non_physical_source_without_retiring_others(r, api):
    ids = seed_world(r)
    testing = _seed_deck(r, "Not Physical", [COMMANDER_BLUE], [(COMMANDER_BLUE, 1)],
                         "not-physical", status="testing")
    resp = api.client.post("/api/reconstruct", json={"deck_ids": [ids["source_a"], registry.registry_id_of(testing)]})
    assert resp.status_code == 400
    decks = registry._load_decks(r)
    assert registry._find_in(decks, ids["source_a"])["status"] == "physical"


def test_rejects_three_preferences_and_accept_before_build(r, api):
    ids = seed_world(r)
    sid = _start(api, [ids["source_a"]])
    three = [{"commanders": [COMMANDER_BLUE]}] * 3
    resp = api.client.post(f"/api/reconstruct/{sid}/preferences", json={"preferences": three})
    assert resp.status_code == 400
    assert _accept(api, sid, [0]).status_code == 400
