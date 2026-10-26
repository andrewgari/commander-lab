"""Slice 1: /api/v2/* routers — thin, typed, session-injected.

The DB session is overridden to in-memory SQLite; the importer's network
fetchers are monkeypatched, so no external calls happen.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.app.api import deps
from src.server.app.main import app
from src.server.infra.db.orm_models import Base
from src.server.workers import importer
from tests.test_slice1_importer import _archidekt_payload, _scryfall_cards


@pytest.fixture()
def db_session():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    session = sessionmaker(bind=eng, expire_on_commit=False)()
    yield session
    session.close()
    eng.dispose()


@pytest.fixture()
def client(db_session, monkeypatch):
    def override_get_db():
        yield db_session
    app.dependency_overrides[deps.get_db] = override_get_db
    monkeypatch.setattr(importer, "fetch_scryfall_cards", lambda _ids: _scryfall_cards())
    monkeypatch.setattr(importer.archidekt, "fetch_deck_raw", lambda _id: _archidekt_payload())
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_decks_empty_then_imported(client):
    res = client.get("/api/v2/decks")
    assert res.status_code == 200 and res.json() == {"decks": []}

    res = client.post("/api/v2/imports/archidekt", json={"identifier": "417707"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deck_id"] and body["snapshot_id"]
    assert body["deck_cards"] == 4

    res = client.get("/api/v2/decks")
    decks = res.json()["decks"]
    assert len(decks) == 1
    assert decks[0]["name"] == "Test Deck"
    assert decks[0]["archidekt_url"] == "https://archidekt.com/decks/417707"


def test_deck_detail_have_buy(client):
    deck_id = client.post("/api/v2/imports/archidekt", json={"identifier": "417707"}).json()["deck_id"]
    res = client.get(f"/api/v2/decks/{deck_id}")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["deck"]["commanders"] == ["Test Commander"]
    assert len(body["cards"]) == 4
    totals = body["have_buy"]["totals"]
    # 1 commander + 1 ring + 10 forests + maybe-board elves is NOT a requirement
    assert totals["required"] == 12
    assert totals["have"] == 0 and totals["buy"] == 12
    names = {c["name"] for c in body["have_buy"]["cards"]}
    assert "Llanowar Elves" not in names  # maybe-board excluded from the split


def test_deck_detail_404(client):
    assert client.get("/api/v2/decks/4242").status_code == 404


def test_profile_latest_snapshot(client):
    assert client.get("/api/v2/profile/latest").status_code == 404
    client.post("/api/v2/imports/archidekt", json={"identifier": "417707"})
    res = client.get("/api/v2/profile/latest")
    assert res.status_code == 200
    snap = res.json()
    assert snap["trigger"] == "import"
    assert "Test Deck" in snap["summary_text"]
    assert snap["metrics"]["curve"]["land_count"] == 10
    res = client.get("/api/v2/profile/snapshots")
    assert len(res.json()["snapshots"]) == 1


def test_instances_endpoint(client, db_session):
    from src.server.infra.db import repositories as repo
    repo.upsert_card(db_session, {"oracle_id": "oid-x", "name": "X"})
    repo.insert_instance(db_session, {"id": "inst-1", "oracle_id": "oid-x",
                                      "status": "in_collection"})
    db_session.flush()
    res = client.get("/api/v2/instances")
    assert res.status_code == 200
    body = res.json()
    assert body["counts"] == {"in_collection": 1}
    assert body["instances"][0]["id"] == "inst-1"


def test_import_endpoint_surfaces_unimportable_deck(client, monkeypatch):
    monkeypatch.setattr(importer.archidekt, "fetch_deck_raw",
                        lambda _id: {"id": 1, "name": "empty", "cards": []})
    res = client.post("/api/v2/imports/archidekt", json={"identifier": "1"})
    assert res.status_code == 422
