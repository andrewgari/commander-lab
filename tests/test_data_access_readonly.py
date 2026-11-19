"""Readonly guard regressions. Optional integration URLs must point to test databases."""
from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, text
from sqlalchemy.orm import registry, sessionmaker

from src.server.infra.cache.datastore import GuardedRedis, WritesDisabledError
from src.server.infra.db.config import get_settings
from src.server.infra.db.engine import make_engine


@pytest.fixture
def readonly(monkeypatch):
    monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")


@pytest.mark.parametrize("command", [
    ("SET", "key", "value"), (b"DeLeTe", "key"), ("GETEX", "key"),
    ("SORT", "key", "STORE", "other"), ("EVAL", "return 1", 0),
    ("FUNCTION LOAD", "code"), ("SET key value",), (b"\xff",), (),
])
def test_raw_redis_commands_fail_closed(readonly, command):
    client = MagicMock()
    guarded = GuardedRedis(client)
    with pytest.raises(WritesDisabledError):
        guarded.execute_command(*command)
    client.execute_command.assert_not_called()


@pytest.mark.parametrize("method", [
    "getex", "zunionstore", "eval_ro", "register_script", "client", "pubsub",
    "config_set", "foobar",
])
def test_unknown_and_mutating_redis_methods_fail_closed(readonly, method):
    client = MagicMock()
    with pytest.raises(WritesDisabledError):
        getattr(GuardedRedis(client), method)("key")
    getattr(client, method).assert_not_called()


def test_redis_safe_reads_and_client_escape(readonly):
    client = MagicMock()
    client.connection_pool = object()
    guarded = GuardedRedis(client)
    guarded.get("k")
    guarded.execute_command(b"gEt", "k")
    client.get.assert_called_once_with("k")
    client.execute_command.assert_called_once_with(b"gEt", "k")
    with pytest.raises(WritesDisabledError):
        _ = guarded.connection_pool


def test_pipeline_chaining_raw_commands_and_mode_flip(monkeypatch):
    client = MagicMock()
    pipe = client.pipeline.return_value
    pipe.get.return_value = pipe
    pipe.set.return_value = pipe
    pipe.execute_command.return_value = pipe
    guarded = GuardedRedis(client)
    monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
    p = guarded.pipeline()
    assert p.get("k") is p
    assert p.execute_command("GET", "k") is p
    with pytest.raises(WritesDisabledError):
        p.get("k").set("k", "v")
    with pytest.raises(WritesDisabledError):
        p.execute_command("SET", "k", "v")
    pipe.set.assert_not_called()
    p.execute()
    pipe.execute.assert_called_once()

    monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
    p.set("k", "v")
    monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
    with pytest.raises(WritesDisabledError):
        p.execute()
    assert pipe.execute.call_count == 1
    p.reset()
    p.get("k").execute()
    assert pipe.execute.call_count == 2


def test_redis_cache_injected_and_lazy_clients_are_guarded(monkeypatch):
    from src.server.infra.cache.redis_cache import RedisCache
    from tests.test_redis_cache import FakeRedis

    raw = FakeRedis()
    monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
    cache = RedisCache(raw, namespace="test")
    assert cache.set("existing", "value")
    monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
    assert cache.get("existing") == "value"
    assert cache.get_or_set("new", lambda: "loaded") == "loaded"
    assert not cache.set("existing", "changed")
    assert cache.get("existing") == "value"
    assert cache.get("new") is None
    with pytest.raises(WritesDisabledError):
        RedisCache(raw, namespace="test", fail_open=False).delete("existing")

    monkeypatch.setattr("src.server.infra.cache.redis_cache.redis.from_url", lambda *a, **kw: raw)
    lazy = RedisCache(namespace="lazy")
    assert lazy.get_or_set("new", lambda: "loaded") == "loaded"
    assert lazy.get("new") is None


def test_readwrite_redis_raw_and_new_commands_remain_available(monkeypatch):
    monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
    client = MagicMock()
    guarded = GuardedRedis(client)
    guarded.execute_command("SET", "k", "v")
    guarded.some_new_command("k")
    client.execute_command.assert_called_once_with("SET", "k", "v")
    client.some_new_command.assert_called_once_with("k")


@pytest.fixture
def pg_table(monkeypatch):
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("requires isolated PostgreSQL TEST_DATABASE_URL")
    monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
    engine = make_engine(get_settings(url))
    table = "readonly_test_" + uuid.uuid4().hex
    seq = table + "_seq"
    with engine.begin() as conn:
        conn.exec_driver_sql(f"CREATE TABLE {table} (id integer primary key)")
        conn.exec_driver_sql(f"INSERT INTO {table} VALUES (1)")
        conn.exec_driver_sql(f"CREATE SEQUENCE {seq}")
    try:
        yield engine, table, seq
    finally:
        monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
        with engine.begin() as conn:
            conn.exec_driver_sql(f"DROP TABLE {table}")
            conn.exec_driver_sql(f"DROP SEQUENCE {seq}")
        engine.dispose()


@pytest.mark.parametrize("sql", [
    "INSERT INTO {table} VALUES (2)",
    "UPDATE {table} SET id = 3 WHERE id = 1",
    "DELETE FROM {table} WHERE id = 1",
    "WITH d AS (DELETE FROM {table} RETURNING id) SELECT * FROM d",
    "CREATE TABLE {table}_other (id integer)",
    "SELECT nextval('{seq}')",
])
def test_pg_transaction_blocks_writes_including_raw_sql(pg_table, monkeypatch, sql):
    engine, table, seq = pg_table
    monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT id FROM {table}")).scalars().all() == [1]
        assert conn.execute(text("SHOW transaction_read_only")).scalar() == "on"
        with pytest.raises(WritesDisabledError):
            conn.execute(text(sql.format(table=table, seq=seq)))
        conn.rollback()
    # Pool reuse and rollback must not change the stored data or sequence.
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT id FROM {table}")).scalars().all() == [1]
        assert conn.execute(text(f"SELECT is_called FROM {seq}")).scalar() is False


def test_pg_orm_flush_and_autocommit_are_blocked(pg_table, monkeypatch):
    engine, table, _ = pg_table
    mapper = registry()
    item_table = Table(table, MetaData(), Column("id", Integer, primary_key=True))

    class Item:
        pass

    mapper.map_imperatively(Item, item_table)
    monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
    with sessionmaker(bind=engine)() as session:
        session.add(Item(id=2))
        with pytest.raises(WritesDisabledError):
            session.commit()
        session.rollback()
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        with pytest.raises(WritesDisabledError):
            conn.execute(text(f"INSERT INTO {table} VALUES (2)"))


def test_pg_rechecks_mode_each_transaction_and_pool_reuse(pg_table, monkeypatch):
    engine, table, _ = pg_table
    with engine.connect() as conn:
        conn.execute(text(f"INSERT INTO {table} VALUES (2)"))
        conn.commit()
        monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
        assert conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 2
        with pytest.raises(WritesDisabledError):
            conn.execute(text(f"INSERT INTO {table} VALUES (3)"))
        conn.rollback()
        monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
        conn.execute(text(f"INSERT INTO {table} VALUES (4)"))
        conn.commit()
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT id FROM {table} ORDER BY id")).scalars().all() == [1, 2, 4]


def test_real_redis_guard(readonly, monkeypatch):
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("requires isolated Redis TEST_REDIS_URL")
    import redis

    client = redis.from_url(url, decode_responses=True)
    key = "readonly_test_" + uuid.uuid4().hex
    client.set(key, "before")
    try:
        guarded = GuardedRedis(client)
        assert guarded.get(key) == "before"
        assert guarded.execute_command("GET", key) == "before"
        assert guarded.pipeline().get(key).execute() == ["before"]
        with pytest.raises(WritesDisabledError):
            guarded.execute_command("SET", key, "after")
        with pytest.raises(WritesDisabledError):
            guarded.pipeline().get(key).execute_command("SET", key, "after")
        monkeypatch.setenv("DATA_ACCESS_MODE", "readwrite")
        pipe = guarded.pipeline().set(key, "after")
        monkeypatch.setenv("DATA_ACCESS_MODE", "readonly")
        with pytest.raises(WritesDisabledError):
            pipe.execute()
        pipe.reset()
        assert client.get(key) == "before"
    finally:
        client.delete(key)
        client.close()
