"""Tests for reconstruct_pools.py (tiered pools, tagging, costing).

Fixtures and factories only: no live price API, no network. Redis is an in-memory fake.

Run: python -m unittest tests.test_reconstruct_pools (also collected by pytest).
Written as fixture-style checks; the shim below runs them under unittest so CI
(`python -m unittest discover -s tests`, no pytest installed) executes them.
"""
import inspect
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ---------------------------------------------------------------------------
# Minimal fixture shim so these run under plain unittest (CI has no pytest).
# ---------------------------------------------------------------------------

_FIXTURES = {}


class pytest:  # noqa: N801 - stands in for the two pytest names used below
    @staticmethod
    def fixture(fn):
        _FIXTURES[fn.__name__] = fn
        return fn

    @staticmethod
    def approx(expected, rel=1e-6, abs=1e-12):
        class _Approx:
            def __eq__(self, other):
                return abs_(other - expected) <= max(abs, rel * abs_(expected))
        return _Approx()


abs_ = abs


import instances
import reconstruct_pools as rp
from reconstruct_pools import (
    CatalogCard,
    InMemoryCatalog,
    PoolConfig,
    RedisCardCatalog,
    Tier,
    build_candidate_pools,
    compute_card_costs,
    compute_deck_cost,
    compute_deck_costs,
    norm_name,
    redis_price_lookup,
    tag_cards,
    to_llm_payload,
)


# ---------------------------------------------------------------------------
# Factories / fixtures
# ---------------------------------------------------------------------------


class FakePipe:
    def __init__(self, r):
        self.r, self.ops = r, []

    def __getattr__(self, name):
        def queue(*a):
            self.ops.append((name, a))
            return self

        return queue

    def execute(self):
        for name, a in self.ops:
            getattr(self.r, name)(*a)
        self.ops = []


class FakeRedis:
    def __init__(self):
        self.store, self.sets = {}, {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v

    def mget(self, ks):
        return [self.store.get(k) for k in ks]

    def sadd(self, k, v):
        self.sets.setdefault(k, set()).add(v)

    def srem(self, k, v):
        self.sets.get(k, set()).discard(v)

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    def sinter(self, *ks):
        out = None
        for k in ks:
            s = self.sets.get(k, set())
            out = set(s) if out is None else out & s
        return out or set()

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def pipeline(self):
        return FakePipe(self)


def make_card(name, identity="", legal=True, **kw):
    return CatalogCard(name=name, color_identity=rp.parse_identity(identity), legal=legal, **kw)


def make_instance(name, status="in_collection", deck_id=None):
    return {"id": f"i-{name}-{status}-{deck_id}", "card_name": name, "ownership_status": status, "deck_id": deck_id}


def make_source_deck(name, cards, commanders=()):
    return {"name": name, "commanders": list(commanders), "cards": [{"name": n, "quantity": q} for n, q in cards]}


def make_session(source_decks, n_targets=2):
    return {
        "session_id": "recon_test",
        "source_decks": source_decks,
        "preferences": [{"name": f"Target {i}"} for i in range(n_targets)],
    }


def price_fn(table):
    """Fake price source; records what it was asked for."""
    calls = []

    def lookup(names):
        calls.append(list(names))
        return {n: table.get(n) for n in names}

    lookup.calls = calls
    return lookup


@pytest.fixture
def catalog():
    return InMemoryCatalog([
        make_card("Sol Ring"),
        make_card("Arcane Signet"),
        make_card("Cultivate", "G"),
        make_card("Lightning Bolt", "R"),
        make_card("Counterspell", "U"),
        make_card("Path to Exile", "W"),
        make_card("Atraxa, Praetors' Voice", "WUBG"),
        make_card("Mana Crypt", legal=False),
        make_card("Mystery Card", "B", legal=None),
        make_card("Forest", "G"),
        make_card("Rhystic Study", "U"),
    ])


@pytest.fixture
def prices():
    return {
        "Sol Ring": 1.5, "Arcane Signet": 1.25, "Cultivate": 0.4, "Lightning Bolt": 0.75,
        "Counterspell": 1.0, "Path to Exile": 1.25, "Atraxa, Praetors' Voice": 15.0,
        "Rhystic Study": 30.0, "Mystery Card": None,
    }


@pytest.fixture
def session():
    return make_session([
        make_source_deck("Deck A", [("Sol Ring", 1), ("Cultivate", 1), ("Forest", 10)], commanders=["Atraxa, Praetors' Voice"]),
        make_source_deck("Deck B", [("Sol Ring", 1), ("Lightning Bolt", 1)]),
    ])


@pytest.fixture
def owned():
    return [
        make_instance("Sol Ring"),                       # also Tier 1 -> precedence
        make_instance("Counterspell"),
        make_instance("Counterspell"),
        make_instance("Path to Exile", "in_deck", deck_id="archidekt:9"),  # dedicated elsewhere
        make_instance("Rhystic Study", "in_mail"),       # not in hand
        make_instance("Arcane Signet", "not_owned"),     # wishlist placeholder
        make_instance("Arcane Signet"),
        make_instance("Arcane Signet", "in_deck", deck_id="archidekt:9"),
    ]


@pytest.fixture
def pools(session, catalog, owned, prices):
    return build_candidate_pools(session, catalog=catalog, owned_instances=owned, price_lookup=price_fn(prices))


def names(cards):
    return [c.name for c in cards]


# ---------------------------------------------------------------------------
# Tier assignment and precedence
# ---------------------------------------------------------------------------


def check_tier1_is_source_snapshot_cards_with_summed_quantities(pools):
    t1 = {c.name: c for c in pools.tier1}
    assert set(t1) == {"Atraxa, Praetors' Voice", "Sol Ring", "Cultivate", "Forest", "Lightning Bolt"}
    assert t1["Sol Ring"].available_qty == 2  # one copy from each source deck
    assert t1["Forest"].available_qty == 10
    assert dict(t1["Sol Ring"].source_decks) == {"Deck A": 1, "Deck B": 1}
    assert all(c.tier == Tier.PREVIOUS_DECK for c in pools.tier1)


def check_commander_missing_from_snapshot_cards_is_added_once(pools):
    atraxa = next(c for c in pools.tier1 if c.name == "Atraxa, Praetors' Voice")
    assert atraxa.available_qty == 1 and atraxa.is_commander


def check_commander_already_in_cards_is_not_double_counted():
    sd = make_source_deck("A", [("Atraxa, Praetors' Voice", 1)], commanders=["Atraxa, Praetors' Voice"])
    [c] = rp.tier1_from_snapshots([sd])
    assert c.available_qty == 1 and c.is_commander


def check_tier_precedence_card_in_every_tier_lands_in_tier1_only(pools):
    # Sol Ring: in source decks, owned in collection AND in the catalog.
    assert pools.tier_of("Sol Ring") == Tier.PREVIOUS_DECK
    assert "Sol Ring" not in names(pools.tier2)
    assert "Sol Ring" not in names(pools.tier3)


def check_owned_card_not_in_source_is_tier2_not_tier3(pools):
    assert pools.tier_of("Counterspell") == Tier.OWNED
    assert "Counterspell" not in names(pools.tier3)


def check_no_card_appears_in_more_than_one_tier(pools):
    seen = [norm_name(c.name) for c in pools.all_cards()]
    assert len(seen) == len(set(seen))


def check_spare_owned_copy_of_source_card_stays_free_in_tier2(catalog):
    # #157: 2 source copies of Sol Ring + 3 owned undedicated copies -> 1 spare.
    sess = make_session([
        make_source_deck("A", [("Sol Ring", 1)]),
        make_source_deck("B", [("Sol Ring", 1)]),
    ])
    owned_ = [make_instance("Sol Ring") for _ in range(3)]
    p = build_candidate_pools(sess, catalog=catalog, owned_instances=owned_, price_lookup=price_fn({}))
    assert {c.name: c.available_qty for c in p.tier1}["Sol Ring"] == 2
    assert {c.name: c.available_qty for c in p.tier2} == {"Sol Ring": 1}
    assert "Sol Ring" not in names(p.tier3)
    # A deck taking the Tier 1 copies leaves the spare as a free Tier 2 copy.
    left = rp.consume_pools(p, [("Sol Ring", 2)])
    assert [t.tier for t in tag_cards(["Sol Ring"], left)] == [Tier.OWNED]


def check_owned_copies_equal_to_source_copies_leave_no_tier2(catalog):
    sess = make_session([make_source_deck("A", [("Sol Ring", 2)])])
    owned_ = [make_instance("Sol Ring"), make_instance("Sol Ring")]
    p = build_candidate_pools(sess, catalog=catalog, owned_instances=owned_, price_lookup=price_fn({}))
    assert "Sol Ring" not in names(p.tier2)


def check_precedence_is_case_and_double_face_insensitive(catalog):
    sess = make_session([make_source_deck("A", [("sol ring", 1), ("Delver of Secrets", 1)])])
    cat = InMemoryCatalog([*catalog.iter_cards(), make_card("Delver of Secrets // Insectile Aberration", "U")])
    p = build_candidate_pools(sess, catalog=cat, owned_instances=[make_instance("SOL RING")], price_lookup=price_fn({}))
    assert p.tier_of("Sol Ring") == Tier.PREVIOUS_DECK
    assert p.tier_of("Delver of Secrets") == Tier.PREVIOUS_DECK
    assert "Delver of Secrets // Insectile Aberration" not in names(p.tier3)
    assert names(p.tier2) == []


def check_tier3_is_other_legal_catalog_cards(pools):
    # Everything legal, non-basic and not already Tier 1/2.
    assert set(names(pools.tier3)) == {"Path to Exile", "Rhystic Study", "Mystery Card"}


def check_tier3_excludes_banned_and_basics_by_default(pools, catalog):
    assert "Mana Crypt" not in names(pools.tier3)  # legal=False
    p = build_candidate_pools(make_session([]), catalog=catalog, owned_instances=[], price_lookup=price_fn({}))
    assert "Forest" not in names(p.tier3)
    p = build_candidate_pools(
        make_session([]), catalog=catalog, owned_instances=[], price_lookup=price_fn({}),
        config=PoolConfig(include_basics=True),
    )
    assert "Forest" in names(p.tier3)


def check_unknown_legality_allowed_unless_strict(catalog):
    sess = make_session([])
    loose = build_candidate_pools(sess, catalog=catalog, owned_instances=[], price_lookup=price_fn({}))
    strict = build_candidate_pools(
        sess, catalog=catalog, owned_instances=[], price_lookup=price_fn({}),
        config=PoolConfig(require_known_legal=True),
    )
    assert "Mystery Card" in names(loose.tier3)
    assert "Mystery Card" not in names(strict.tier3)
    assert "Mana Crypt" not in names(strict.tier3)


def check_excluded_names_never_offered_in_tier3(catalog):
    p = build_candidate_pools(
        make_session([]), catalog=catalog, owned_instances=[], price_lookup=price_fn({}),
        config=PoolConfig(excluded_names=frozenset({"Rhystic Study"})),
    )
    assert "Rhystic Study" not in names(p.tier3)


# ---------------------------------------------------------------------------
# Tier 2: ownership / dedication
# ---------------------------------------------------------------------------


def check_tier2_excludes_cards_dedicated_to_other_active_decks(pools):
    # Path to Exile: its only copy is in_deck elsewhere -> not owned-and-free -> Tier 3.
    assert pools.tier_of("Path to Exile") == Tier.NEW


def check_tier2_excludes_in_mail_and_not_owned(pools):
    assert pools.tier_of("Rhystic Study") == Tier.NEW  # in_mail only


def check_tier2_quantity_counts_only_free_copies(pools):
    # Arcane Signet: 1 free, 1 not_owned placeholder, 1 in_deck elsewhere
    sig = pools.get("Arcane Signet")
    assert sig.tier == Tier.OWNED and sig.available_qty == 1
    assert pools.get("Counterspell").available_qty == 2


def check_free_owned_counts_ignores_instances_with_deck_id():
    inst = [make_instance("A"), {**make_instance("A"), "deck_id": "x"}, make_instance("B", "in_deck", "d")]
    assert rp.free_owned_counts(inst) == {"A": 1}


def check_decommissioned_source_cards_are_free_in_model_but_stay_tier1(session, catalog, prices):
    # After start_reconstruct, the source decks' instances were moved to in_collection.
    freed = [make_instance("Sol Ring"), make_instance("Sol Ring"), make_instance("Cultivate"), make_instance("Counterspell")]
    p = build_candidate_pools(session, catalog=catalog, owned_instances=freed, price_lookup=price_fn(prices))
    assert p.tier_of("Cultivate") == Tier.PREVIOUS_DECK  # not double counted in Tier 2
    assert names(p.tier2) == ["Counterspell"]


def check_tier2_reads_instance_store_by_default(session, catalog, prices):
    r = FakeRedis()
    instances.create_instance(r, "Counterspell", ownership_status="in_collection")
    instances.create_instance(r, "Counterspell", ownership_status="in_collection")
    instances.create_instance(r, "Cultivate", ownership_status="in_collection")
    instances.create_instance(r, "Path to Exile", ownership_status="in_collection")
    instances.create_instance(r, "Path to Exile", ownership_status="in_deck", deck_id="archidekt:9")
    p = build_candidate_pools(session, r, catalog=catalog, price_lookup=price_fn(prices))
    assert p.get("Counterspell").available_qty == 2
    assert p.get("Path to Exile").available_qty == 1  # the in_deck copy is not counted
    assert p.tier_of("Cultivate") == Tier.PREVIOUS_DECK


# ---------------------------------------------------------------------------
# Quantity tracking
# ---------------------------------------------------------------------------


def check_available_quantities_cover_tier1_and_tier2_only(pools):
    av = pools.available_quantities()
    assert av[norm_name("Sol Ring")].quantity == 2
    assert av[norm_name("Sol Ring")].tier == Tier.PREVIOUS_DECK
    assert av[norm_name("Counterspell")].quantity == 2
    assert av[norm_name("Counterspell")].tier == Tier.OWNED
    assert norm_name("Rhystic Study") not in av  # Tier 3 is unlimited
    assert all(a.quantity > 0 for a in av.values())


def check_tier3_has_no_quantity_limit(pools):
    assert all(c.available_qty is None for c in pools.tier3)


def check_shared_pool_conflict_is_detectable_from_available_quantities(pools):
    av = pools.available_quantities()
    deck1 = [("Counterspell", 2)]
    deck2 = [("Counterspell", 1)]
    used = {}
    for deck in (deck1, deck2):
        for t in tag_cards(deck, pools):
            used[norm_name(t.name)] = used.get(norm_name(t.name), 0) + t.quantity
    over = {k for k, n in used.items() if k in av and n > av[k].quantity}
    assert over == {norm_name("Counterspell")}


def check_target_deck_count_from_preferences_and_capped(session, catalog):
    kw = dict(catalog=catalog, owned_instances=[], price_lookup=price_fn({}))
    assert build_candidate_pools(session, **kw).target_deck_count == 2
    assert build_candidate_pools(make_session([], n_targets=1), **kw).target_deck_count == 1
    assert build_candidate_pools({"source_decks": []}, **kw).target_deck_count == 1
    assert build_candidate_pools(session, target_deck_count=5, **kw).target_deck_count == 2


# ---------------------------------------------------------------------------
# Tier 3 filter / cap / efficiency
# ---------------------------------------------------------------------------


def check_tier3_colour_identity_filter_is_subset_match(catalog, prices):
    p = build_candidate_pools(
        make_session([]), catalog=catalog, owned_instances=[], price_lookup=price_fn(prices),
        config=PoolConfig(color_identity="UG"),
    )
    got = set(names(p.tier3))
    assert "Counterspell" in got and "Cultivate" in got and "Sol Ring" in got  # U, G, colorless
    assert "Lightning Bolt" not in got and "Atraxa, Praetors' Voice" not in got
    assert "Mystery Card" not in got  # B


def check_tier3_colorless_filter(catalog):
    p = build_candidate_pools(
        make_session([]), catalog=catalog, owned_instances=[], price_lookup=price_fn({}),
        config=PoolConfig(color_identity="C"),
    )
    assert set(names(p.tier3)) == {"Sol Ring", "Arcane Signet"}


def check_tier3_cap_and_truncation_flag(catalog):
    p = build_candidate_pools(
        make_session([]), catalog=catalog, owned_instances=[], price_lookup=price_fn({}),
        config=PoolConfig(tier3_cap=3),
    )
    assert len(p.tier3) == 3 and p.tier3_truncated
    assert p.tier3_total_candidates > 3
    assert names(p.tier3) == sorted(names(p.tier3), key=str.casefold)  # deterministic default order


def check_tier3_cap_keeps_ranked_best_and_prices_only_survivors(catalog, prices):
    lookup = price_fn(prices)
    rank = {"Rhystic Study": 0, "Cultivate": 1}
    p = build_candidate_pools(
        make_session([]), catalog=catalog, owned_instances=[], price_lookup=lookup,
        config=PoolConfig(tier3_cap=2, ranker=lambda c: rank.get(c.name, 99)),
    )
    assert names(p.tier3) == ["Rhystic Study", "Cultivate"]
    assert sorted(lookup.calls[0]) == ["Cultivate", "Rhystic Study"]  # no wasted price lookups


def check_tier3_cap_zero_and_none(catalog):
    kw = dict(catalog=catalog, owned_instances=[], price_lookup=price_fn({}))
    assert build_candidate_pools(make_session([]), config=PoolConfig(tier3_cap=0), **kw).tier3 == []
    big = build_candidate_pools(make_session([]), config=PoolConfig(tier3_cap=None), **kw)
    assert not big.tier3_truncated


def check_color_identity_config_accepts_strings_lists_and_words(catalog):
    for spec in ("UG", ["U", "G"], ["Blue", "Green"], "blue green"):
        p = build_candidate_pools(
            make_session([]), catalog=catalog, owned_instances=[], price_lookup=price_fn({}),
            config=PoolConfig(color_identity=spec),
        )
        assert p.config.color_identity == frozenset("UG")


def check_available_quantities_unaffected_by_tier3_filter(session, catalog, owned):
    p = build_candidate_pools(
        session, catalog=catalog, owned_instances=owned, price_lookup=price_fn({}),
        config=PoolConfig(color_identity="W", tier3_cap=1),
    )
    assert norm_name("Counterspell") in p.available_quantities()  # off-colour owned card still tracked


def check_llm_payload_is_compact_and_narrowable(pools):
    pl = to_llm_payload(pools)
    json.dumps(pl)  # serialisable
    assert ["Sol Ring", 2] in pl["tier1"]
    assert ["Counterspell", 2] in pl["tier2"]
    assert ["Rhystic Study", 30.0] in pl["tier3"]
    assert ["Mystery Card", None] in pl["tier3"]
    narrow = to_llm_payload(pools, color_identity="U", tier3_cap=1)
    assert len(narrow["tier3"]) == 1 and narrow["tier3_truncated"]
    only_u = to_llm_payload(pools, color_identity="U")
    assert "Path to Exile" not in [n for n, _ in only_u["tier3"]]
    assert "Rhystic Study" in [n for n, _ in only_u["tier3"]]


def check_refine_tier3_drops_unknown_identity_when_filtering():
    cards = [
        rp.PoolCard("A", Tier.NEW, color_identity=frozenset("U")),
        rp.PoolCard("B", Tier.NEW, color_identity=None),
    ]
    assert names(rp.refine_tier3(cards, "U")) == ["A"]
    assert names(rp.refine_tier3(cards, None)) == ["A", "B"]


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------


def check_tag_cards_assigns_tiers_for_all_input_shapes(pools):
    tagged = tag_cards(
        ["Sol Ring", ("Counterspell", 1), {"name": "Rhystic Study", "quantity": 1}, {"name": "Not In Any Pool"}],
        pools,
    )
    by = {t.name: t for t in tagged}
    assert by["Sol Ring"].tier == Tier.PREVIOUS_DECK and by["Sol Ring"].tier_label == "previous_deck"
    assert by["Counterspell"].tier == Tier.OWNED and by["Counterspell"].tier_label == "owned"
    assert by["Rhystic Study"].tier == Tier.NEW and by["Rhystic Study"].tier_label == "new"
    assert by["Rhystic Study"].in_pool
    assert by["Not In Any Pool"].tier == Tier.NEW and not by["Not In Any Pool"].in_pool


def check_tag_cards_merges_duplicate_names_and_flags_over_availability(pools):
    tagged = tag_cards([("Counterspell", 2), ("counterspell", 1), "Sol Ring"], pools)
    cs = next(t for t in tagged if t.name == "Counterspell")
    assert cs.quantity == 3 and cs.available_qty == 2 and cs.exceeds_available
    sr = next(t for t in tagged if t.name == "Sol Ring")
    assert not sr.exceeds_available


def check_tag_cards_ignores_blank_and_non_positive(pools):
    assert tag_cards(["", ("Sol Ring", 0), {"name": None}], pools) == []


def check_tagged_card_to_dict_matches_builder_vocabulary(pools):
    d = tag_cards(["Cultivate"], pools)[0].to_dict()
    assert d["tier"] == 1 and d["tier_label"] == "previous_deck"
    json.dumps(d)


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------


def check_per_card_cost_by_tier(pools):
    costs = {c.name: c for c in compute_card_costs(
        [("Sol Ring", 1), ("Counterspell", 2), ("Rhystic Study", 1), ("Atraxa, Praetors' Voice", 1)], pools)}
    assert costs["Sol Ring"].cost == 0.0 and costs["Sol Ring"].price_known
    assert costs["Counterspell"].cost == 0.0 and costs["Counterspell"].unit_price == 0.0
    rs = costs["Rhystic Study"]
    assert rs.tier == Tier.NEW and rs.unit_price == 30.0 and rs.cost == 30.0
    assert costs["Atraxa, Praetors' Voice"].cost == 0.0  # Tier 1 (source commander), though priced $15


def check_tier3_cost_multiplies_by_quantity(pools):
    [c] = compute_card_costs([("Arcane Signet", 1)], pools)  # owned -> 0
    assert c.cost == 0.0
    [c] = compute_card_costs([("Rhystic Study", 3)], pools)
    assert c.cost == 90.0 and c.unit_price == 30.0


def check_deck_total_new_card_cost(pools):
    deck = [("Sol Ring", 1), ("Counterspell", 1), ("Rhystic Study", 1), ("Path to Exile", 2)]
    dc = compute_deck_cost(deck, pools)
    assert dc.total_new_cost == pytest.approx(30.0 + 2 * 1.25)
    assert dc.is_complete and dc.unpriced_cards == ()
    assert [c.name for c in dc.cards] == ["Sol Ring", "Counterspell", "Rhystic Study", "Path to Exile"]


def check_deck_total_for_each_of_two_decks(pools):
    a, b = compute_deck_costs([[("Rhystic Study", 1)], [("Path to Exile", 1), "Sol Ring"]], pools)
    assert (a.total_new_cost, b.total_new_cost) == (30.0, 1.25)


def check_all_owned_deck_costs_nothing(pools):
    dc = compute_deck_cost(["Sol Ring", "Cultivate", "Counterspell"], pools)
    assert dc.total_new_cost == 0.0 and dc.is_complete


def check_missing_price_is_unknown_not_zero(pools):
    [c] = compute_card_costs([("Mystery Card", 1)], pools)
    assert c.tier == Tier.NEW
    assert c.price_known is False and c.cost is None and c.unit_price is None


def check_deck_total_flags_unpriced_cards_explicitly(pools):
    dc = compute_deck_cost([("Rhystic Study", 1), ("Mystery Card", 2)], pools)
    assert dc.total_new_cost == 30.0  # lower bound
    assert not dc.is_complete
    assert dc.unpriced_cards == ("Mystery Card",) and dc.unpriced_copies == 2
    d = dc.to_dict()
    assert d["is_complete"] is False and d["cards"][1]["cost"] is None
    json.dumps(d)


def check_card_outside_pool_uses_explicit_prices_then_lookup_then_unknown(pools):
    deck = [("Off Pool A", 1), ("Off Pool B", 1), ("Off Pool C", 1)]
    lookup = price_fn({"Off Pool B": 4.0})
    costs = {c.name: c for c in compute_card_costs(deck, pools, prices={"Off Pool A": 2.5}, price_lookup=lookup)}
    assert costs["Off Pool A"].cost == 2.5
    assert costs["Off Pool B"].cost == 4.0
    assert costs["Off Pool C"].cost is None and not costs["Off Pool C"].price_known
    assert lookup.calls == [["Off Pool B", "Off Pool C"]]  # only the still-unpriced ones


def check_pool_price_wins_over_explicit_prices(pools):
    [c] = compute_card_costs([("Rhystic Study", 1)], pools, prices={"Rhystic Study": 1.0})
    assert c.cost == 30.0


def check_free_price_zero_is_known_not_missing(session, catalog):
    p = build_candidate_pools(session, catalog=catalog, owned_instances=[], price_lookup=price_fn({"Rhystic Study": 0.0}))
    [c] = compute_card_costs(["Rhystic Study"], p)
    assert c.price_known and c.cost == 0.0


def check_basic_lands_free_by_default(pools):
    [c] = compute_card_costs([("Island", 30)], pools)
    assert c.cost == 0.0 and c.price_known
    [c] = compute_card_costs([("Island", 30)], pools, free_basics=False)
    assert c.cost is None


def check_card_cost_to_draft_card_matches_builder_format(pools):
    [c] = compute_card_costs([("Rhystic Study", 1)], pools)
    d = c.to_draft_card("draw engine")
    assert d == {"name": "Rhystic Study", "quantity": 1, "tier": 3, "tier_label": "new",
                 "cost": 30.0, "price_known": True, "rationale": "draw engine"}


# ---------------------------------------------------------------------------
# Redis adapters (card database and price source), still no network
# ---------------------------------------------------------------------------


OID = "11111111-2222-3333-4444-555555555555"
OID2 = "11111111-2222-3333-4444-555555555556"


def check_redis_catalog_reads_card_orm_and_legacy_meta():
    r = FakeRedis()
    r.set(f"card:{OID}", json.dumps({"oracle_id": OID, "name": "Cultivate", "color": "G", "cmc": 3}))
    r.set(f"card:{OID2}", json.dumps({"oracle_id": OID2, "name": "Mana Crypt", "color": "C", "legal": False}))
    r.set("card:Sol Ring", json.dumps({"copies": []}))  # legacy inventory blob, ignored
    r.set("card_meta:Cultivate", json.dumps({"color": "G"}))  # shadowed by the ORM record
    r.set("card_meta:Rhystic Study", json.dumps({"color": "U", "type": "Enchantment"}))
    cards = {c.name: c for c in RedisCardCatalog(r).iter_cards()}
    assert set(cards) == {"Cultivate", "Mana Crypt", "Rhystic Study"}
    assert cards["Cultivate"].color_identity == frozenset("G") and cards["Cultivate"].legal is None
    assert cards["Mana Crypt"].legal is False
    assert cards["Rhystic Study"].color_identity == frozenset("U")


def check_redis_catalog_honours_scryfall_legalities_and_identity_list():
    r = FakeRedis()
    r.set(f"card:{OID}", json.dumps({"oracle_id": OID, "name": "X", "color_identity": ["W", "U"],
                                      "legalities": {"commander": "banned"}}))
    [c] = list(RedisCardCatalog(r).iter_cards())
    assert c.legal is False and c.color_identity == frozenset("WU")


def check_redis_price_lookup_cached_only_never_hits_network():
    r = FakeRedis()
    r.set(rp.PRICE_CACHE_PREFIX + "Sol Ring", json.dumps({"usd": 1.5, "at": 1}))
    r.set(rp.PRICE_CACHE_PREFIX + "Dud", json.dumps({"usd": None, "at": 1}))
    got = redis_price_lookup(r, refresh=False)(["Sol Ring", "Dud", "Never Cached"])
    assert got == {"Sol Ring": 1.5, "Dud": None, "Never Cached": None}


def check_full_build_from_redis_defaults(session):
    r = FakeRedis()
    for oid, nm, col in [(OID, "Counterspell", "U"), (OID2, "Rhystic Study", "U")]:
        r.set(f"card:{oid}", json.dumps({"oracle_id": oid, "name": nm, "color": col}))
    r.set(rp.PRICE_CACHE_PREFIX + "Rhystic Study", json.dumps({"usd": 30.0, "at": 1}))
    instances.create_instance(r, "Counterspell", ownership_status="in_collection")
    p = build_candidate_pools(session, r, price_lookup=redis_price_lookup(r, refresh=False))
    assert p.tier_of("Counterspell") == Tier.OWNED
    assert p.get("Rhystic Study").unit_price == 30.0
    assert compute_deck_cost(["Rhystic Study"], p).total_new_cost == 30.0


def check_no_redis_and_no_overrides_gives_tier1_only(session):
    p = build_candidate_pools(session)
    assert p.tier2 == [] and p.tier3 == [] and len(p.tier1) == 5


# ---------------------------------------------------------------------------
# unittest entry point: one test per check_*, fixtures resolved per test.
# ---------------------------------------------------------------------------


def _resolve(name, cache):
    if name not in cache:
        fn = _FIXTURES[name]
        cache[name] = fn(*[_resolve(a, cache) for a in inspect.signature(fn).parameters])
    return cache[name]


def _make_test(fn):
    def test(self):
        cache = {}
        fn(*[_resolve(a, cache) for a in inspect.signature(fn).parameters])
    test.__doc__ = fn.__doc__
    return test


def _dry_session(src_ids=("archidekt:42",)):
    sess = make_session([
        {**make_source_deck("A", [("Sol Ring", 2), ("Cultivate", 1)]), "id": 42, "registry_id": "archidekt:42"},
    ])
    sess["source_deck_ids"] = list(src_ids)
    return sess


def check_source_deck_keys_collects_ids_and_registry_ids():
    sess = _dry_session()
    sess["source_decks"][0]["id"] = 42  # non-string ignored
    assert rp._source_deck_keys(sess) == {"archidekt:42"}
    assert rp._source_deck_keys({}) == set()


def check_free_owned_counts_counts_source_in_deck_only_with_keys():
    inst = [
        make_instance("Sol Ring"),
        make_instance("Sol Ring", "in_deck", deck_id="archidekt:42"),
        make_instance("Sol Ring", "in_deck", deck_id="archidekt:9"),
        make_instance("Sol Ring", "in_mail"),
    ]
    assert rp.free_owned_counts(inst) == {"Sol Ring": 1}
    assert rp.free_owned_counts(inst, source_deck_keys=["archidekt:42"]) == {"Sol Ring": 2}


def check_dry_run_pools_match_decommissioned_pools(catalog):
    # Decommissioned: source copies moved to in_collection (+1 spare). Dry run: still in_deck.
    real = _dry_session()
    after_decom = [make_instance("Sol Ring") for _ in range(3)] + [make_instance("Cultivate")]
    p_real = build_candidate_pools(real, catalog=catalog, owned_instances=after_decom, price_lookup=price_fn({}))

    dry = _dry_session()
    dry["dry_run"] = True
    still_in_deck = (
        [make_instance("Sol Ring", "in_deck", deck_id="archidekt:42") for _ in range(2)]
        + [make_instance("Sol Ring")]
        + [make_instance("Cultivate", "in_deck", deck_id="archidekt:42")]
    )
    # unique ids are needed only for the Redis path; the list path counts directly
    p_dry = build_candidate_pools(dry, catalog=catalog, owned_instances=still_in_deck, price_lookup=price_fn({}))
    for tier in (1, 2, 3):
        assert p_dry.tier_cards(tier) == p_real.tier_cards(tier), tier
    assert p_dry.available_quantities() == p_real.available_quantities()
    assert {c.name: c.available_qty for c in p_dry.tier2} == {"Sol Ring": 1}


def check_dry_run_loads_source_in_deck_instances_from_redis(catalog):
    import instances as inst_store
    r = FakeRedis()
    for _ in range(2):
        inst_store.create_instance(r, "Sol Ring", ownership_status="in_deck", deck_id="archidekt:42", deck_name="A")
    inst_store.create_instance(r, "Sol Ring", ownership_status="in_collection")
    inst_store.create_instance(r, "Cultivate", ownership_status="in_deck", deck_id="archidekt:9", deck_name="Other")
    dry = _dry_session()
    dry["dry_run"] = True
    p = build_candidate_pools(dry, r, catalog=catalog, price_lookup=price_fn({}))
    assert {c.name: c.available_qty for c in p.tier2} == {"Sol Ring": 1}
    real = _dry_session()
    p2 = build_candidate_pools(real, r, catalog=catalog, price_lookup=price_fn({}))
    # non-dry run does not look at in_deck instances: only the in_collection copy, fully absorbed by Tier 1
    assert {c.name: c.available_qty for c in p2.tier2} == {}


class TestReconstructPools(unittest.TestCase):
    pass


for _name, _fn in list(globals().items()):
    if _name.startswith("check_") and callable(_fn):
        setattr(TestReconstructPools, "test_" + _name[len("check_"):], _make_test(_fn))
del _name, _fn  # leaked loop vars would be collected as a second copy of the class


if __name__ == "__main__":
    unittest.main()
