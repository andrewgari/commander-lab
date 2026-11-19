"""Real PostgreSQL transaction races; requires an isolated TEST_DATABASE_URL.

Each test creates and drops its own schema, never touching application tables.
SQLite does not implement the row locks these tests exercise.
"""
from concurrent.futures import ThreadPoolExecutor
import os
from queue import Queue
from threading import Event
import time
import uuid

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

from src.server.domain.draft.draft_mode import DraftError
from src.server.infra.db import repositories as repo
from src.server.infra.db.config import normalize_url
from src.server.infra.db.orm_models import (
    Base, CardInstanceRow, CardRow, DeckCardRow, DeckRow, DraftCardRow,
    DraftInstanceMoveRow, DraftRow, InstanceEventRow,
)


@pytest.fixture
def pg_factory():
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires TEST_DATABASE_URL for PostgreSQL row-lock tests")
    admin = create_engine(normalize_url(url))
    if admin.dialect.name != "postgresql":
        admin.dispose()
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")
    schema = "draft_race_" + uuid.uuid4().hex
    engine = None
    created = False
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        engine = create_engine(normalize_url(url), connect_args={"options": f"-csearch_path={schema}"})
        tables = [m.__table__ for m in (
            CardRow, DeckRow, DeckCardRow, CardInstanceRow, InstanceEventRow,
            DraftRow, DraftCardRow, DraftInstanceMoveRow,
        )]
        Base.metadata.create_all(engine, tables=tables)
        yield sessionmaker(engine, expire_on_commit=False), engine
    finally:
        if engine is not None:
            engine.dispose()
        try:
            if created:
                with admin.begin() as conn:
                    conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            admin.dispose()


def _seed(factory, action="assign"):
    with factory.begin() as s:
        repo.upsert_card(s, {"oracle_id": "o1", "name": "Card"})
        repo.upsert_deck(s, {"id": "source", "name": "Source"})
        s.flush()
        repo.insert_instance(s, {"id": "i1", "oracle_id": "o1",
                                 "status": "in_deck" if action == "unassign" else "in_collection",
                                 "deck_id": "source" if action == "unassign" else None})
        s.flush()
        for draft_id in ("draft-a", "draft-b"):
            repo.create_draft(s, {"id": draft_id, "kind": "dismantle", "name": draft_id,
                                  "target_name": draft_id})
            repo.upsert_draft_card(s, draft_id, {"oracle_id": "o1", "qty": 1, "board": "main"})
            repo.stage_move(s, draft_id, "i1", action)


def _wait_until_blocked(engine, pid):
    deadline = time.monotonic() + 5
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            if conn.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                return
            time.sleep(0.01)
    pytest.fail(f"PostgreSQL backend {pid} did not wait for a lock")


def _worker(factory, draft_id, operation, ready, go):
    with factory() as s:
        s.execute(text("SET LOCAL lock_timeout = '8s'"))
        s.execute(text("SET LOCAL statement_timeout = '12s'"))
        # Retain previously read ORM objects across the lock wait; the locking
        # read must refresh these, not reuse the identity map's stale values.
        draft = s.get(DraftRow, draft_id)
        instance = s.get(CardInstanceRow, "i1")
        pid = s.scalar(text("SELECT pg_backend_pid()"))
        ready.put(pid)
        assert go.wait(10)
        try:
            if operation == "commit":
                result = repo.commit_draft(s, draft_id)
            elif operation == "abandon":
                result = repo.abandon_draft(s, draft_id)
            else:
                result = repo.upsert_draft_card(
                    s, draft_id, {"oracle_id": "o1", "qty": 2, "board": "main"})
            s.commit()
            return result
        except DraftError as exc:
            s.rollback()
            return str(exc)
        finally:
            # Keep both objects strongly referenced until the query completes.
            assert draft is not None and instance is not None


def _compete(factory, engine, first_draft, second_draft, operation="commit"):
    ready, go = Queue(), Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_worker, factory, second_draft, operation, ready, go)
        try:
            pid = ready.get(timeout=10)
            with factory() as first:
                try:
                    first_result = repo.commit_draft(first, first_draft)
                    go.set()
                    _wait_until_blocked(engine, pid)
                    first.commit()
                finally:
                    first.rollback()
            return first_result, future.result(timeout=15)
        finally:
            go.set()


def test_same_draft_commit_serializes_and_refreshes_cached_status(pg_factory):
    factory, engine = pg_factory
    _seed(factory)
    result, second = _compete(factory, engine, "draft-a", "draft-a")
    assert "committed, not open" in second
    with factory() as s:
        events = repo.list_instance_events(s, "i1")
        assert len(events) == 1
        assert events[0]["to_deck"] == result["deck_id"]
        assert repo.get_draft(s, "draft-a")["result_deck_id"] == result["deck_id"]


@pytest.mark.parametrize("operation", ["stage", "abandon"])
def test_commit_blocks_staging_and_abandon_on_same_draft(pg_factory, operation):
    factory, engine = pg_factory
    _seed(factory)
    result, second = _compete(factory, engine, "draft-a", "draft-a", operation)
    assert "committed, not open" in second
    with factory() as s:
        assert repo.get_deck_cards(s, result["deck_id"])[0]["qty"] == 1
        assert repo.get_draft(s, "draft-a")["status"] == "committed"


@pytest.mark.parametrize("action", ["assign", "unassign"])
def test_shared_instance_serializes_and_revalidates_current_state(pg_factory, action):
    factory, engine = pg_factory
    _seed(factory, action)
    first, second = _compete(factory, engine, "draft-a", "draft-b")
    with factory() as s:
        events = repo.list_instance_events(s, "i1")
        instance = s.get(CardInstanceRow, "i1")
        if action == "assign":
            assert isinstance(second, dict)
            assert len(events) == 2
            assert events[0]["from_deck"] is None
            assert events[0]["to_deck"] == first["deck_id"]
            assert events[1]["from_deck"] == first["deck_id"]
            assert events[1]["to_deck"] == second["deck_id"] == instance.deck_id
        else:
            assert "cannot unassign" in second
            assert len(events) == 1
            assert events[0]["from_deck"] == "source" and events[0]["to_deck"] is None
            assert instance.status == "in_collection" and instance.deck_id is None
            assert repo.get_draft(s, "draft-b")["status"] == "open"
            assert repo.get_deck(s, "draft:draft-b") is None


def test_commit_reads_staging_after_waiting_for_edit(pg_factory):
    factory, engine = pg_factory
    _seed(factory)
    ready, go = Queue(), Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_worker, factory, "draft-a", "commit", ready, go)
        try:
            pid = ready.get(timeout=10)
            with factory() as edit:
                try:
                    repo.upsert_draft_card(edit, "draft-a", {"oracle_id": "o1", "qty": 3, "board": "main"})
                    edit.flush()
                    go.set()
                    _wait_until_blocked(engine, pid)
                    edit.commit()
                finally:
                    edit.rollback()
            result = future.result(timeout=15)
        finally:
            go.set()
    assert isinstance(result, dict)
    with factory() as s:
        assert repo.get_deck_cards(s, result["deck_id"])[0]["qty"] == 3


def test_waiting_commit_proceeds_after_first_rolls_back(pg_factory):
    factory, engine = pg_factory
    _seed(factory)
    ready, go = Queue(), Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_worker, factory, "draft-a", "commit", ready, go)
        try:
            pid = ready.get(timeout=10)
            with factory() as first:
                try:
                    repo.commit_draft(first, "draft-a")
                    go.set()
                    _wait_until_blocked(engine, pid)
                finally:
                    first.rollback()
            result = future.result(timeout=15)
        finally:
            go.set()
    assert isinstance(result, dict)
    with factory() as s:
        assert len(repo.list_instance_events(s, "i1")) == 1
        assert s.get(CardInstanceRow, "i1").deck_id == result["deck_id"]


def test_instance_locks_are_acquired_in_stable_order(pg_factory):
    factory, engine = pg_factory
    _seed(factory)
    with factory.begin() as s:
        repo.insert_instance(s, {"id": "i0", "oracle_id": "o1", "status": "in_collection"})
        s.flush()
        repo.stage_move(s, "draft-a", "i0", "assign")
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if "FOR NO KEY UPDATE" in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with factory.begin() as s:
            repo.commit_draft(s, "draft-a")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "ORDER BY card_instances.id FOR NO KEY UPDATE" in " ".join(statements[0].split())
