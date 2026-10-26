"""Slice 1 tests: Archidekt import -> Postgres, have/buy math, profile snapshot,
/api/v2 routers. Runs on in-memory SQLite (no Postgres needed); the import
fetcher is stubbed (no network).
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.decks import deck_import
from src.server.domain.inventory import have_buy as hb
from src.server.domain.profile import snapshot as snap
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import Base
from src.server.infra.db.session import session_scope
from src.server.infra.services import import_service


def _entry(name, oracle_id, qty=1, categories=None, types=None, cmc=3.0,
           colors=None, color_identity=None, scryfall_id=""):
    return {
        "quantity": qty,
        "categories": categories or [],
        "card": {
            "uid": scryfall_id or f"sf-{oracle_id}",
            "collectorNumber": "1",
            "rarity": "rare",
            "edition": {"editioncode": "tst"},
            "prices": {"usd": 1.0},
            "oracleCard": {
                "name": name,
                "uid": oracle_id,
                "types": types or ["Creature"],
                "superTypes": [],
                "subTypes": [],
                "cmc": cmc,
                "manaCost": "{2}{G}",
                "colors": colors or ["Green"],
                "colorIdentity": color_identity or ["Green"],
                "text": "",
                "keywords": [],
            },
        },
    }


FAKE_PAYLOAD = {
    "id": 5054882,
    "name": "The Return of the King",
    "parentFolderId": 588380,
    "cards": [
        _entry("Aragorn, King Elessar", "o-cmd", categories=["Commander"],
               types=["Legendary", "Creature"], cmc=7.0,
               color_identity=["Red", "Green", "White"]),
        _entry("Forest", "o-forest", qty=10, categories=["Land"], types=["Land"], cmc=0),
        _entry("Llanowar Elves", "o-elves", qty=4, categories=["Ramp"], cmc=1.0),
        _entry("Sol Ring", "o-ring", qty=1, categories=["Ramp"], types=["Artifact"], cmc=1.0,
               colors=[], color_identity=[]),
        _entry("Cultivate", "o-cult", qty=1, categories=["Ramp"], cmc=3.0),
        _entry("Swords to Plowshares", "o-stp", qty=1, categories=["Removal"], cmc=1.0,
               colors=["White"]),
        _entry("Maybe Card", "o-maybe", qty=1, categories=["Maybeboard"], cmc=2.0),
    ],
}


@pytest.fixture()
def factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield sessionmaker(bind=eng, expire_on_commit=False)
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def fetcher():
    return lambda identifier: FAKE_PAYLOAD


# --- domain: deck_import -----------------------------------------------------

def test_normalize_boards_and_status():
    d = deck_import.normalize_archidekt_deck(FAKE_PAYLOAD)
    assert d["id"] == "archidekt:5054882"
    assert d["status"] == "have"  # physical folder 588380
    assert d["commanders"] == ["Aragorn, King Elessar"]
    assert d["color_identity"] == ["W", "R", "G"]
    boards = {c["name"]: c["board"] for c in d["cards"]}
    assert boards["Aragorn, King Elessar"] == "commander"
    assert boards["Forest"] == "main"
    assert boards["Maybe Card"] == "maybe"
    cats = {c["name"]: c["category"] for c in d["cards"]}
    assert cats["Llanowar Elves"] == "Ramp"
    assert cats["Aragorn, King Elessar"] is None


def test_normalize_virtual_when_other_folder():
    payload = {**FAKE_PAYLOAD, "parentFolderId": 1}
    assert deck_import.normalize_archidekt_deck(payload)["status"] == "virtual"


def test_fingerprint_stable_and_content_sensitive():
    a = deck_import.normalize_archidekt_deck(FAKE_PAYLOAD)
    b = deck_import.normalize_archidekt_deck({**FAKE_PAYLOAD, "name": "renamed"})
    assert a["fingerprint"] == b["fingerprint"]  # name excluded
    c = {**FAKE_PAYLOAD, "cards": FAKE_PAYLOAD["cards"][:-1]}
    assert deck_import.normalize_archidekt_deck(c)["fingerprint"] != a["fingerprint"]


def test_normalize_rejects_garbage():
    with pytest.raises(deck_import.DeckImportError):
        deck_import.normalize_archidekt_deck({"cards": []})
    with pytest.raises(deck_import.DeckImportError):
        deck_import.normalize_archidekt_deck({"id": 1, "cards": []})


# --- domain: have_buy ---------------------------------------------------------

def _inst(oracle_id, status, deck_id=None):
    return {"oracle_id": oracle_id, "status": status, "deck_id": deck_id}


def test_have_buy_math():
    deck_cards = [
        {"oracle_id": "a", "name": "A", "qty": 4, "board": "main", "category": None},
        {"oracle_id": "b", "name": "B", "qty": 2, "board": "main", "category": None},
        {"oracle_id": "c", "name": "C", "qty": 1, "board": "maybe", "category": None},
    ]
    instances = [
        _inst("a", "in_deck", "deck1"), _inst("a", "in_deck", "deck1"),
        _inst("a", "in_collection"), _inst("a", "in_mail"),
        _inst("b", "in_deck", "other"),  # elsewhere: not counted
        _inst("b", "in_mail"),
        _inst("x", "not_owned"),
    ]
    rep = hb.compute_have_buy(deck_cards, instances, "deck1")
    lines = {l["oracle_id"]: l for l in rep["lines"]}
    assert "c" not in lines  # maybe board out of scope
    assert lines["a"] == {**lines["a"], "need": 4, "have": 3, "pending": 1, "buy": 0}
    assert lines["b"]["have"] == 0 and lines["b"]["pending"] == 1 and lines["b"]["buy"] == 1
    assert lines["b"]["elsewhere"] == 1
    assert rep["totals"] == {"need": 6, "have": 3, "pending": 2, "buy": 1,
                             "complete": False, "have_pct": 50}


def test_have_buy_complete():
    deck_cards = [{"oracle_id": "a", "name": "A", "qty": 1, "board": "commander"}]
    rep = hb.compute_have_buy(deck_cards, [_inst("a", "in_collection")], "d")
    assert rep["totals"]["complete"] is True
    assert rep["totals"]["have_pct"] == 100


# --- domain: snapshot ----------------------------------------------------------

def test_curve_and_mix():
    deck_cards = [
        {"oracle_id": "1", "name": "Land", "qty": 10, "board": "main",
         "type_line": "Land", "cmc": 0, "category": "Land"},
        {"oracle_id": "2", "name": "One", "qty": 4, "board": "main",
         "type_line": "Creature", "cmc": 1, "category": "Ramp"},
        {"oracle_id": "3", "name": "Seven", "qty": 1, "board": "main",
         "type_line": "Creature", "cmc": 8, "category": "Bomb"},
    ]
    curve = snap.compute_curve(deck_cards)
    assert curve["lands"] == 10 and curve["spells"] == 5
    assert curve["buckets"][1] == 4 and curve["buckets"][7] == 1
    assert curve["avg_mv_nonland"] == round((4 * 1 + 1 * 8) / 5, 2)
    mix = snap.compute_archetype_mix(deck_cards)
    assert mix["total_cards"] == 15
    assert mix["categories"][0] == {"category": "Land", "cards": 10, "share": round(10 / 15, 4)}


def test_summary_text_mentions_ownership():
    deck = {"name": "Test Deck", "color_identity": ["G"], "commanders": ["Cmdr"]}
    curve = {"spells": 60, "lands": 37, "avg_mv_nonland": 3.0, "unavailable": {"count": 0}}
    mix = {"categories": [{"category": "Ramp", "cards": 12}]}
    ownership = {"totals": {"need": 100, "have": 90, "pending": 4, "buy": 6}}
    text = snap.build_summary(deck, curve, mix, ownership)
    assert "Mono-Green" in text and "Cmdr" in text and "90 of 100" in text
    assert "4 on the way" in text and "6 left to buy" in text


# --- repositories + service -----------------------------------------------------

def _seed_instances(factory, deck_id):
    with session_scope(factory) as s:
        # need card rows for FK
        for oid in ["o-cmd", "o-elves"]:
            repo.upsert_card(s, {"oracle_id": oid, "name": oid, "type_line": "Creature",
                                 "oracle_text": "", "mana_cost": "", "colors": [],
                                 "color_identity": [], "cmc": 1, "keywords": []})
        repo.insert_instance(s, {"id": "i1", "oracle_id": "o-elves", "status": "in_collection"})
        repo.insert_instance(s, {"id": "i2", "oracle_id": "o-elves", "status": "in_deck",
                                 "deck_id": deck_id})
        repo.insert_instance(s, {"id": "i3", "oracle_id": "o-elves", "status": "in_mail"})


def test_import_end_to_end(factory, fetcher):
    _seed_instances(factory, "archidekt:5054882")
    out = import_service.import_archidekt_deck("5054882", session_factory=factory,
                                               fetcher=fetcher)
    assert out["created"] is True and out["changed"] is True
    assert out["snapshot_id"] is not None
    assert out["card_qty"] == 18  # 1+10+4+1+1+1 (maybe excluded? see below)
    with session_scope(factory) as s:
        deck = repo.get_deck(s, "archidekt:5054882")
        assert deck["status"] == "have"
        cards = repo.get_deck_cards(s, "archidekt:5054882")
        assert len(cards) == 7
        snaps = repo.list_snapshots(s)
        assert len(snaps) == 1
        assert "curve" in snaps[0]["metrics"] and "archetype_mix" in snaps[0]["metrics"]
        assert "The Return of the King" in snaps[0]["summary_text"]


def test_import_idempotent_snapshot(factory, fetcher):
    import_service.import_archidekt_deck("5054882", session_factory=factory, fetcher=fetcher)
    out2 = import_service.import_archidekt_deck("5054882", session_factory=factory, fetcher=fetcher)
    assert out2["created"] is False and out2["changed"] is False
    assert out2["snapshot_id"] is None
    with session_scope(factory) as s:
        assert len(repo.list_snapshots(s)) == 1


def test_have_buy_against_imported_deck(factory, fetcher):
    _seed_instances(factory, "archidekt:5054882")
    import_service.import_archidekt_deck("5054882", session_factory=factory, fetcher=fetcher)
    with session_scope(factory) as s:
        cards = repo.get_deck_cards(s, "archidekt:5054882")
        inst = repo.list_instances(s)
    rep = hb.compute_have_buy(cards, inst, "archidekt:5054882")
    elves = next(l for l in rep["lines"] if l["oracle_id"] == "o-elves")
    assert (elves["need"], elves["have"], elves["pending"], elves["buy"]) == (4, 2, 1, 1)
    assert rep["totals"]["need"] == 17  # commander + main only (maybe excluded)


# --- /api/v2 routers -------------------------------------------------------------

@pytest.fixture()
def client(factory, fetcher, monkeypatch):
    from fastapi.testclient import TestClient

    from src.server.app.api import deps
    from src.server.app.main import app

    def _override():
        s = factory()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[deps.db_session] = _override
    monkeypatch.setattr("src.server.infra.providers.archidekt.fetch_deck_raw", fetcher)
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_api_import_decks_detail_profile(client):
    r = client.post("/api/v2/imports/archidekt", json={"identifier": "5054882"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deck_id"] == "archidekt:5054882"
    assert body["snapshot_id"] is not None
    assert body["have_buy"]["need"] == 17

    r = client.get("/api/v2/decks")
    assert r.status_code == 200
    decks = r.json()["decks"]
    assert len(decks) == 1 and decks[0]["name"] == "The Return of the King"
    assert decks[0]["have_buy"]["need"] == 17

    r = client.get("/api/v2/decks/archidekt:5054882")
    assert r.status_code == 200, r.text
    detail = r.json()
    assert set(detail["boards"]) >= {"commander", "main", "maybe"}
    assert detail["have_buy"]["totals"]["need"] == 17
    assert detail["snapshot"]["metrics"]["curve"]["spells"] > 0
    assert "The Return of the King" in detail["snapshot"]["summary_text"]

    r = client.get("/api/v2/profile/latest")
    assert r.status_code == 200
    assert r.json()["available"] is True

    r = client.get("/api/v2/decks/archidekt:9999999")
    assert r.status_code == 404
