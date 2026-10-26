"""Card dossier tests (docs/architecture/card-dossier-spec.md): aggregate
endpoint, per-panel queries on real rows, lazy Scryfall printings fetch,
notes PUT, edge persistence sink, Redis lab-tags mirror. Runs on in-memory
SQLite; Scryfall and Redis are stubbed (no network).
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.analytics.interactions.vocab import VOCAB_VERSION
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import Base
from src.server.infra.db.session import session_scope
from src.server.infra.services import dossier_service, interaction_edges


# Stub fetcher for tests that must not touch the network: "no printings data yet".
NO_FETCH = lambda _oid: []  # noqa: E731


@pytest.fixture()
def factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    Base.metadata.drop_all(eng)
    eng.dispose()


def _card(oid, name, **kw):
    return {"oracle_id": oid, "name": name,
            "type_line": kw.get("type_line", "Creature"),
            "oracle_text": kw.get("oracle_text", "Rules text."),
            "mana_cost": kw.get("mana_cost", "{2}{G}"),
            "colors": kw.get("colors", ["G"]),
            "color_identity": kw.get("color_identity", ["G"]),
            "cmc": kw.get("cmc", 3), "keywords": kw.get("keywords", [])}


def _printing(sf, oid, set_code="tst", released="2024-01-01", default=False, **kw):
    import datetime
    return {"scryfall_id": sf, "oracle_id": oid, "set_code": set_code,
            "set_name": kw.get("set_name", "Test Set"),
            "collector_number": kw.get("collector_number", "1"),
            "rarity": kw.get("rarity", "rare"),
            "released_at": datetime.date.fromisoformat(released),
            "is_default": default,
            "image_uri_normal": kw.get("image_uri_normal", f"https://img/{sf}.jpg"),
            "image_uri_art_crop": kw.get("image_uri_art_crop", f"https://img/{sf}-art.jpg"),
            "prices": kw.get("prices", {"usd": "1.00"})}


def _seed(factory):
    """Cards A/B/C, deck (virtual) with A+B, instances, tags, edges A→B, B←C."""
    with session_scope(factory) as s:
        for oid, name in [("o-a", "Card A"), ("o-b", "Card B"), ("o-c", "Card C")]:
            repo.upsert_card(s, _card(oid, name))
        repo.upsert_printing(s, _printing("sf-a1", "o-a", released="2020-01-01"))
        repo.upsert_printing(s, _printing("sf-a2", "o-a", set_code="nu",
                                          released="2024-06-01", default=True))
        repo.upsert_deck(s, {"id": "archidekt:1", "name": "Ghired", "status": "virtual",
                             "color_identity": [], "commanders": [],
                             "source": "archidekt", "fingerprint": "x",
                             "archidekt_id": "1"})
        repo.replace_deck_cards(s, "archidekt:1", [
            {"oracle_id": "o-a", "qty": 1, "board": "main", "category": "Ramp"},
            {"oracle_id": "o-b", "qty": 2, "board": "main", "category": None},
        ])
        repo.insert_instance(s, {"id": "i1", "oracle_id": "o-a", "status": "in_collection",
                                 "finish": "nonfoil"})
        repo.insert_instance(s, {"id": "i2", "oracle_id": "o-a", "status": "in_deck",
                                 "deck_id": "archidekt:1", "finish": "foil"})
        repo.insert_instance(s, {"id": "i3", "oracle_id": "o-a", "status": "not_owned"})
        repo.replace_card_tags(s, [
            {"oracle_id": "o-a", "tag": "ramp", "source": "learned"},
            {"oracle_id": "o-a", "tag": "pet", "source": "manual"},
        ])
        repo.replace_interaction_edges(s, [
            {"src_oracle_id": "o-a", "dst_oracle_id": "o-b",
             "vocab_version": VOCAB_VERSION, "reasons": ["tokens"],
             "reason_labels": ["Tokens"], "reason_text": "A makes tokens; B eats tokens",
             "weight": 3},
            {"src_oracle_id": "o-c", "dst_oracle_id": "o-a",
             "vocab_version": VOCAB_VERSION, "reasons": ["counters"],
             "reason_labels": ["Counters"], "reason_text": "C makes counters; A eats counters",
             "weight": 5},
            # stale vocab version: never read by the dossier
            {"src_oracle_id": "o-b", "dst_oracle_id": "o-a",
             "vocab_version": VOCAB_VERSION - 1, "reasons": ["x"],
             "reason_labels": ["X"], "reason_text": "stale", "weight": 99},
        ])


# --- service: panels -----------------------------------------------------------

def test_dossier_all_panels_real_data(factory):
    _seed(factory)
    with session_scope(factory) as s:
        d = dossier_service.get_dossier(s, "o-a", printing_fetcher=NO_FETCH)
    assert d["card"]["name"] == "Card A"
    assert d["card"]["oracle_text"] == "Rules text."
    assert d["card"]["mana_cost"] == "{2}{G}"
    assert d["printing"]["scryfall_id"] == "sf-a2"  # is_default wins
    assert [p["scryfall_id"] for p in d["printings"]] == ["sf-a2", "sf-a1"]
    assert {t["tag"]: t["source"] for t in d["tags"]} == {"ramp": "learned", "pet": "manual"}
    assert d["decks"] == [{"deck_id": "archidekt:1", "name": "Ghired",
                           "status": "virtual", "board": "main",
                           "category": "Ramp", "qty": 1}]
    # synergy: both directions merged, weight desc, stale vocab excluded
    assert [(n["name"], n["direction"], n["weight"]) for n in d["synergy"]] == [
        ("Card C", "in", 5), ("Card B", "out", 3)]
    assert d["synergy"][0]["reason_text"] == "C makes counters; A eats counters"
    assert d["affinity"] is None
    assert d["collection"] == {"owned": True, "copies": 2,
                               "statuses": ["in_collection", "in_deck"],
                               "finishes": ["foil", "nonfoil"]}


def test_dossier_printing_param_and_fallback(factory):
    _seed(factory)
    with session_scope(factory) as s:
        d = dossier_service.get_dossier(s, "o-a", printing="sf-a1", printing_fetcher=NO_FETCH)
        assert d["printing"]["scryfall_id"] == "sf-a1"
        # unknown printing falls back to the default row
        d2 = dossier_service.get_dossier(s, "o-a", printing="sf-nope", printing_fetcher=NO_FETCH)
        assert d2["printing"]["scryfall_id"] == "sf-a2"


def test_dossier_empty_panels_are_empty_not_fabricated(factory):
    with session_scope(factory) as s:
        repo.upsert_card(s, _card("o-lonely", "Lonely"))
        d = dossier_service.get_dossier(s, "o-lonely", printing_fetcher=NO_FETCH)
    assert d["printings"] == [] and d["printing"] is None
    assert d["tags"] == [] and d["decks"] == [] and d["synergy"] == []
    assert d["affinity"] is None
    assert d["collection"] == {"owned": False, "copies": 0, "statuses": [], "finishes": []}


def test_dossier_unknown_card_is_none(factory):
    with session_scope(factory) as s:
        assert dossier_service.get_dossier(s, "o-nope", printing_fetcher=NO_FETCH) is None


# --- lazy printings fetch -------------------------------------------------------

def _fake_fetcher(oid):
    return [
        {"scryfall_id": "sf-p1", "oracle_id": oid, "set_code": "old",
         "set_name": "Old Set", "collector_number": "1", "rarity": "common",
         "released_at": "2000-01-01", "image_uri_normal": "https://img/p1.jpg",
         "image_uri_art_crop": "", "prices": {}, "promo": False, "lang": "en"},
        {"scryfall_id": "sf-p2", "oracle_id": oid, "set_code": "prm",
         "set_name": "Promo", "collector_number": "1p", "rarity": "mythic",
         "released_at": "2025-01-01", "image_uri_normal": "https://img/p2.jpg",
         "image_uri_art_crop": "", "prices": {}, "promo": True, "lang": "en"},
        {"scryfall_id": "sf-p3", "oracle_id": oid, "set_code": "new",
         "set_name": "New Set", "collector_number": "7", "rarity": "rare",
         "released_at": "2024-01-01", "image_uri_normal": "https://img/p3.jpg",
         "image_uri_art_crop": "", "prices": {}, "promo": False, "lang": "en"},
    ]


def test_lazy_printings_fetch_caches_and_picks_default(factory):
    with session_scope(factory) as s:
        repo.upsert_card(s, _card("o-lazy", "Lazy Card"))
        d = dossier_service.get_dossier(s, "o-lazy", printing_fetcher=_fake_fetcher)
    assert len(d["printings"]) == 3
    # newest non-promo English wins over the newer promo
    assert d["printing"]["scryfall_id"] == "sf-p3"
    assert d["printing"]["is_default"] is True
    # rows persisted: a second read never re-fetches (a failing fetcher is unused)
    def _boom(_oid):
        raise AssertionError("fetcher must not run when rows are cached")
    with session_scope(factory) as s:
        d2 = dossier_service.get_dossier(s, "o-lazy", printing_fetcher=_boom)
    # spec §4: printings ordered released_at DESC (promo 2025 > new 2024 > old 2000)
    assert [p["scryfall_id"] for p in d2["printings"]] == ["sf-p2", "sf-p3", "sf-p1"]


def test_lazy_printings_fetch_failure_degrades_to_empty(factory):
    def _boom(oid):
        raise RuntimeError("scryfall down")
    with session_scope(factory) as s:
        repo.upsert_card(s, _card("o-down", "Down Card"))
        d = dossier_service.get_dossier(s, "o-down", printing_fetcher=_boom)
    assert d["printings"] == [] and d["printing"] is None  # empty state, not a 503


# --- notes ----------------------------------------------------------------------

def test_put_notes_upsert_and_validation(factory):
    with session_scope(factory) as s:
        repo.upsert_card(s, _card("o-n", "Noted"))
        assert dossier_service.put_notes(s, "o-nope", 3, "x") is None
        with pytest.raises(ValueError):
            dossier_service.put_notes(s, "o-n", 6, "too high")
        out = dossier_service.put_notes(s, "o-n", 4, "love it")
        assert out["affinity"] == 4 and out["notes"] == "love it"
        out2 = dossier_service.put_notes(s, "o-n", None, "meh")
        assert out2["affinity"] is None and out2["notes"] == "meh"
        d = dossier_service.get_dossier(s, "o-n", printing_fetcher=NO_FETCH)
        assert d["affinity"]["notes"] == "meh"


# --- edge persistence sink --------------------------------------------------------

def test_collect_and_persist_edges(factory):
    _seed(factory)
    deck_graphs = [{
        "edges": [
            {"source": "Card A", "target": "Card B", "reasons": ["tokens"], "weight": 1},
            {"source": "Ghost", "target": "Card A", "reasons": ["tokens"], "weight": 1},
        ]
    }]
    coll = {"index": {"counters": {"producers": ["Card C"], "consumers": ["Card A"]}}}
    pair_reasons = interaction_edges.collect_edges(deck_graphs, coll)
    assert pair_reasons[("Card A", "Card B")] == ["tokens"]
    assert pair_reasons[("Card C", "Card A")] == ["counters"]
    with session_scope(factory) as s:
        summary = interaction_edges.persist_edges(s, pair_reasons, VOCAB_VERSION)
        assert summary["rows_written"] == 2  # Ghost unresolved -> skipped, counted
        assert summary["pairs_unresolved"] == 1
        neighbors = repo.list_synergy_neighbors(s, "o-a", VOCAB_VERSION)
    by_dir = {(n["name"], n["direction"]) for n in neighbors}
    assert ("Card B", "out") in by_dir and ("Card C", "in") in by_dir
    # replace semantics: a second persist wipes prior rows
    with session_scope(factory) as s:
        interaction_edges.persist_edges(s, {}, VOCAB_VERSION)
        assert repo.list_synergy_neighbors(s, "o-a", VOCAB_VERSION) == []


def test_edge_sink_end_to_end(factory):
    _seed(factory)
    sink = interaction_edges.make_pg_edge_sink(session_factory=factory)
    summary = sink([{"edges": [{"source": "Card A", "target": "Card B",
                                "reasons": ["tokens"], "weight": 1}]}], None, VOCAB_VERSION)
    assert summary["rows_written"] == 1
    with session_scope(factory) as s:
        d = dossier_service.get_dossier(s, "o-a", printing_fetcher=NO_FETCH)
    assert [(n["name"], n["direction"]) for n in d["synergy"]] == [("Card B", "out")]


# --- Redis lab-tags mirror ----------------------------------------------------------

class _FakeRedis:
    def __init__(self, data):
        self._data = data

    def get(self, key):
        return self._data.get(key)


def test_import_card_tags_from_redis(factory):
    with session_scope(factory) as s:
        repo.upsert_card(s, _card("o-a", "Card A"))
        r = _FakeRedis({
            "lab_tags": json.dumps({"Card A": ["ramp", "pet"], "Unknown Card": ["x"]}),
            "lab_tags_source": json.dumps({"Card A": {"pet": "manual"}}),
        })
        out = dossier_service.import_card_tags_from_redis(r, s)
        assert out["tags_written"] == 2
        assert out["names_unresolved"] == ["Unknown Card"]
        tags = {t["tag"]: t["source"] for t in repo.get_card_tags(s, "o-a")}
        assert tags == {"ramp": "learned", "pet": "manual"}


# --- migration module ---------------------------------------------------------------

def test_migration_chains_both_a007_heads():
    from src.server.infra.db.migrations.versions import a008_card_dossier as m
    assert m.revision == "a008_card_dossier"
    assert set(m.down_revision) == {"a007_create_reviews", "a007_derived_vector_indexes"}
    assert m.ALL_DDLS  # DDL module importable without alembic installed


# --- /api/v2 routers ------------------------------------------------------------------

@pytest.fixture()
def client(factory):
    from fastapi.testclient import TestClient

    from src.server.app.api import deps
    from src.server.app.main import app

    def _override():
        s = factory()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[deps.db_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_dossier_round_trip(client, factory, monkeypatch):
    _seed(factory)
    monkeypatch.setattr(dossier_service, "default_printing_fetcher", _fake_fetcher)
    r = client.get("/api/v2/card/o-a/dossier")
    assert r.status_code == 200
    body = r.json()
    assert body["card"]["name"] == "Card A"
    assert body["printing"]["scryfall_id"] == "sf-a2"  # seeded rows: no fetch
    assert body["decks"][0]["name"] == "Ghired"
    assert body["synergy"][0]["name"] == "Card C"
    assert body["collection"]["owned"] is True
    # printing param
    r2 = client.get("/api/v2/card/o-a/dossier?printing=sf-a1")
    assert r2.json()["printing"]["scryfall_id"] == "sf-a1"
    # notes PUT then re-read
    put = client.put("/api/v2/card/o-a/notes", json={"affinity": 5, "notes": "staple"})
    assert put.status_code == 200
    assert put.json()["affinity"]["affinity"] == 5
    assert client.get("/api/v2/card/o-a/dossier").json()["affinity"]["notes"] == "staple"


def test_api_dossier_404_and_notes_validation(client, factory):
    assert client.get("/api/v2/card/o-nope/dossier").status_code == 404
    assert client.put("/api/v2/card/o-nope/notes",
                      json={"affinity": 1, "notes": "x"}).status_code == 404
    with session_scope(factory) as s:
        repo.upsert_card(s, _card("o-a", "Card A"))
    assert client.put("/api/v2/card/o-a/notes",
                      json={"affinity": 0, "notes": "bad"}).status_code == 422
