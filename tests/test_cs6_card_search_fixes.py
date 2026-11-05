"""CS-6 QA round-1 fixes: stub-card hydration (owned filters), solo commander
pick excludes Backgrounds, UUID card_meta keys never become buy-list names."""
from __future__ import annotations

import importlib.util
import json
import pathlib

from tests.test_cards_search import (  # noqa: F401  (fixtures + helpers)
    _card, client, factory, get, redis, scry, seed, sf, printing, mark_eligible,
    q, parse_filters, build_scryfall_query,
)
from src.server.domain.draft.reconstruct_pools import RedisCardCatalog
from src.server.infra.db import repositories as repo
from src.server.infra.db.session import session_scope

_spec = importlib.util.spec_from_file_location(
    "hydrate_stub_cards",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "hydrate_stub_cards.py")
hydrate_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(hydrate_mod)


def _stub(oid, name):
    return {"oracle_id": oid, "name": name}      # what migrate_instances_to_pg writes


def _bulk():
    return [
        sf("Hinterland Harbor", "o-hh", "Land", ci=("G", "U"), cmc=0, rarity="rare", mana=""),
        sf("Llanowar Elves", "o-elf", "Creature — Elf Druid", ci=("G",), cmc=1, rarity="common"),
    ]


def _seed_stubs(factory):
    seed(factory, [_stub("o-hh", "Hinterland Harbor"), _stub("o-elf", "Llanowar Elves")],
         instances=[("o-hh", "in_collection"), ("o-elf", "in_collection")])


def test_owned_filters_are_noops_on_stub_rows(client, factory):
    """The reported defect: stubs make type/ci filters miss or no-op."""
    _seed_stubs(factory)
    assert get(client, owned="owned", type="land").json()["results"] == []


def test_hydration_makes_every_owned_filter_change_results(client, factory):
    _seed_stubs(factory)
    with session_scope(factory) as s:
        stats = hydrate_mod.hydrate(s, _bulk())
    assert stats["hydrated"] == 2 and stats["unmatched"] == [] and stats["printings"] == 2

    def names(**p):
        return sorted(h["name"] for h in get(client, owned="owned", **p).json()["results"])

    assert names() == ["Hinterland Harbor", "Llanowar Elves"]
    assert names(type="land") == ["Hinterland Harbor"]
    assert names(type="creature") == ["Llanowar Elves"]
    assert names(rarity="common") == ["Llanowar Elves"]
    assert names(mv="0") == ["Hinterland Harbor"]
    assert names(ci="g", ci_mode="exact") == ["Llanowar Elves"]
    assert names(ci="gu", type="land") == ["Hinterland Harbor"]
    hit = get(client, owned="owned", type="land").json()["results"][0]
    assert hit["type_line"] == "Land" and hit["rarity"] == "rare" and hit["color_identity"] == ["G", "U"]


def test_hydration_is_idempotent_and_reports_unmatched(factory):
    seed(factory, [_stub("o-hh", "Hinterland Harbor"), _stub("o-gone", "Mystery Card")])
    with session_scope(factory) as s:
        first = hydrate_mod.hydrate(s, _bulk())
    with session_scope(factory) as s:
        second = hydrate_mod.hydrate(s, _bulk())
    assert first["hydrated"] == 1 and first["unmatched"] == ["Mystery Card"]
    assert second["stubs"] == 1 and second["hydrated"] == 0


def test_solo_adds_negated_background_to_query_but_pair_does_not():
    assert q(cmdr=True, solo=True).endswith("is:commander -t:background")
    assert "-t:background" not in q(cmdr=True)
    assert "-t:background" not in q(solo=True)


def test_solo_owned_commander_pick_excludes_backgrounds(client, factory, redis):
    seed(factory, [
        _card("o-bg", "Agent of the Iron Throne", "Legendary Enchantment — Background", "", ("B",), 3),
        _card("o-atr", "Atraxa", "Legendary Creature — Angel", "", ("G",), 4),
    ], instances=[("o-bg", "in_collection"), ("o-atr", "in_collection")])
    mark_eligible(redis, "o-bg", "o-atr")
    both = get(client, owned="owned", cmdr=1).json()["results"]
    solo = get(client, owned="owned", cmdr=1, solo=1).json()["results"]
    assert {h["name"] for h in both} == {"Agent of the Iron Throne", "Atraxa"}
    assert [h["name"] for h in solo] == ["Atraxa"]


def test_solo_scryfall_query_carries_negation(client, scry):
    scry.pages[1] = ([sf("Atraxa", "o-atraxa", "Legendary Creature — Angel")], False, 1)
    body = get(client, cmdr=1, solo=1).json()
    assert body["query"].endswith("is:commander -t:background")


def test_catalog_never_yields_uuid_names_from_oracle_keyed_card_meta():
    from tests.test_reconstruct_pools import FakeRedis
    oid = "11111111-2222-3333-4444-555555555555"
    oid2 = "11111111-2222-3333-4444-555555555556"
    r = FakeRedis()
    r.set(f"card_meta:{oid}", json.dumps({"name": "Sol Ring", "color": "C"}))
    r.set(f"card_meta:{oid2}", json.dumps({"color": "G"}))          # no name: skipped
    r.set("card_meta:Rhystic Study", json.dumps({"color": "U"}))
    names = sorted(c.name for c in RedisCardCatalog(r).iter_cards())
    assert names == ["Rhystic Study", "Sol Ring"]
