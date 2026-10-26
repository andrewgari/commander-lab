"""Slice 1: Redis→Postgres instance migration script — dry-run plan, apply,
and reconciliation report (scripts/migrate_instances_to_pg.py)."""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.infra.db.orm_models import Base
from src.server.infra.db import repositories as repo

SPEC = importlib.util.spec_from_file_location(
    "migrate_instances_to_pg",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                 "scripts", "migrate_instances_to_pg.py"),
)
mig = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mig
SPEC.loader.exec_module(mig)


class FakeRedis:
    def __init__(self, store):
        self.store = store

    def get(self, key):
        return self.store.get(key)

    def keys(self, pattern):
        prefix = pattern.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def mget(self, keys):
        return [self.store.get(k) for k in keys]


@pytest.fixture()
def session():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    yield s
    s.close()
    eng.dispose()


def _redis_world():
    store = {}
    cards = {"oid-a": "Alpha", "oid-b": "Beta"}
    for oid, name in cards.items():
        store[f"idx:card_name_to_oracle:{name}"] = oid
    instances = [
        {"id": "i1", "card_name": "Alpha", "ownership_status": "in_collection",
         "finish": "foil", "condition": "LP", "price_paid": 3.5,
         "date_acquired": "2025-12-01T00:00:00", "archidekt_uid": "", "notes": ""},
        {"id": "i2", "card_name": "Beta", "ownership_status": "in_deck",
         "deck_id": "archidekt:417707", "finish": "nonfoil", "condition": "NM",
         "date_acquired": "2025-12-02T00:00:00", "archidekt_uid": "", "notes": ""},
        # in_deck for a deck that is NOT imported yet -> demoted, reported loudly
        {"id": "i3", "card_name": "Alpha", "ownership_status": "in_deck",
         "deck_id": "archidekt:999", "finish": "nonfoil", "condition": "NM",
         "date_acquired": "2025-12-03T00:00:00", "archidekt_uid": "", "notes": ""},
        # no oracle id anywhere -> unmappable, reported
        {"id": "i4", "card_name": "Ghost", "ownership_status": "in_collection",
         "finish": "nonfoil", "condition": "NM",
         "date_acquired": "2025-12-04T00:00:00", "archidekt_uid": "", "notes": ""},
    ]
    for inst in instances:
        store[f"instance:{inst['id']}"] = json.dumps(inst)
    return FakeRedis(store)


def _imported_deck(session):
    repo.upsert_imported_deck(
        session, archidekt_id="417707", name="Test Deck", status="have",
        color_identity=[], commanders=[], source="archidekt",
        deck_cards=[{"oracle_id": "oid-b", "board": "main", "qty": 1, "category": None}],
    )


def test_plan_dry_run_reports_every_family(session):
    _imported_deck(session)
    r = _redis_world()
    result = mig.plan(r, session, skip_scryfall=True)
    report = result["report"]
    assert report["key_families"] == {"instance:*": 4}
    assert report["planned_inserts"] == 3
    assert [u["id"] for u in report["unmappable_instances"]] == ["i4"]
    # the copy bound to the un-imported deck is demoted and reported, never silent
    assert [u["id"] for u in report["unmapped_deck_instances"]] == ["i3"]
    demoted = next(p for p in result["planned"] if p["id"] == "i3")
    assert demoted["status"] == "in_collection" and demoted["deck_id"] is None
    bound = next(p for p in result["planned"] if p["id"] == "i2")
    assert bound["status"] == "in_deck" and bound["deck_id"] is not None


def test_apply_and_reconcile(session):
    _imported_deck(session)
    r = _redis_world()
    result = mig.plan(r, session, skip_scryfall=True)
    applied = mig.apply_plan(session, result["planned"])
    assert applied == {"inserted": 3, "skipped_existing": 0}
    session.flush()
    recon = mig.reconcile(session, result["report"])
    assert recon["totals"]["postgres"] == 3
    assert recon["totals"]["redis"] == 4
    assert recon["totals"]["unmappable"] == 1
    assert recon["ok"]

    # idempotent: re-running inserts nothing
    again = mig.apply_plan(session, result["planned"])
    assert again == {"inserted": 0, "skipped_existing": 3}

    # seeding provenance events exist
    from sqlalchemy import select, func
    from src.server.infra.db.models import InstanceEventRow
    n = session.execute(select(func.count()).select_from(InstanceEventRow)).scalar()
    assert n >= 3  # acquired for each; assigned for the in_deck copy
