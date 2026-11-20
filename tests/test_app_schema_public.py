"""App objects must live in ``public``, never ag_catalog (decision-review §5).

Runs on real PostgreSQL only (TEST_DATABASE_URL); everything happens in a transaction that is rolled back.
"""
import os

import pytest

sa = pytest.importorskip("sqlalchemy")

pytestmark = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="needs TEST_DATABASE_URL")


def _engine():
    from src.server.infra.db.config import get_settings
    from src.server.infra.db.engine import make_engine

    return make_engine(get_settings(os.environ["TEST_DATABASE_URL"]))


def _where(conn, name):
    return [r[0] for r in conn.execute(sa.text(
        "SELECT table_schema FROM information_schema.tables WHERE table_name = :n"), {"n": name})]


def test_a012_moves_ag_catalog_objects_to_public_and_is_idempotent():
    from src.server.infra.db import app_schema as m
    eng = _engine()
    with eng.connect() as conn:
        trans = conn.begin()
        try:
            conn.execute(sa.text("CREATE SCHEMA IF NOT EXISTS ag_catalog"))  # absent on plain CI postgres
            conn.execute(sa.text("CREATE TYPE ag_catalog.zz_mood AS ENUM ('a','b')"))
            conn.execute(sa.text("CREATE TABLE ag_catalog.zz_probe (id SERIAL PRIMARY KEY, mood ag_catalog.zz_mood)"))
            conn.execute(sa.text("CREATE SEQUENCE ag_catalog.zz_seq"))
            assert _where(conn, "zz_probe") == ["ag_catalog"]
            for _ in range(2):  # second pass proves idempotence
                conn.execute(sa.text(m.MOVE_SQL))
                assert _where(conn, "zz_probe") == ["public"]
            assert conn.execute(sa.text("SELECT to_regclass('public.zz_seq') IS NOT NULL")).scalar()
            assert conn.execute(sa.text("SELECT to_regtype('public.zz_mood') IS NOT NULL")).scalar()
            # AGE's own catalog tables stay put when the extension is installed.
            assert conn.execute(sa.text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name IN ('ag_graph','ag_label') AND table_schema = 'public'")).scalar() == 0
        finally:
            trans.rollback()
    eng.dispose()


def test_app_engine_pins_search_path_ahead_of_ag_catalog():
    eng = _engine()
    with eng.connect() as conn:
        assert "ag_catalog" not in conn.execute(sa.text("SHOW search_path")).scalar()
    eng.dispose()
