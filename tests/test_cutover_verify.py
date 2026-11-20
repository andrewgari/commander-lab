"""Tests for the Phase 1b cutover: PG_CUTOVER flag, deck_links repositories,
and scripts/verify_cutover.py.

Postgres-backed tests need TEST_DATABASE_URL / DATABASE_URL (skipped otherwise);
each test class builds its own throwaway database derived from that DSN, so the
DSN may point at any scratch Postgres where the user can CREATE DATABASE.
"""
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

import src.server.workers.redis_pg_import as imp
from src.server.infra.db.config import cutover_enabled
from src.server.domain.analytics.deck_links_repository import (
    PgDeckLinksRepository,
    RedisDeckLinksRepository,
    ReadOnlyError,
    deck_links_repository,
)

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

REPO_ROOT = Path(__file__).resolve().parent.parent
DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

_migration_spec = importlib.util.spec_from_file_location(
    "a001_create_users_deck_links",
    REPO_ROOT / "src" / "server" / "infra" / "db" / "migrations" / "versions" / "a001_create_users_deck_links.py",
)
_migration = importlib.util.module_from_spec(_migration_spec)
_migration_spec.loader.exec_module(_migration)

import scripts.verify_cutover as vc  # noqa: E402

_db_counter = 0


def _admin_dsn() -> str:
    """DSN pointed at the 'postgres' maintenance DB (for CREATE DATABASE)."""
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    parts = conninfo_to_dict(DSN)
    parts["dbname"] = "postgres"
    return make_conninfo(**parts)


def fresh_db(testcase: unittest.TestCase) -> str:
    """Create a uniquely named scratch DB with the migrated schema; return its DSN."""
    global _db_counter
    _db_counter += 1
    name = f"t_cutover_{os.getpid()}_{_db_counter}"
    with psycopg.connect(_admin_dsn(), autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    parts = conninfo_to_dict(DSN)
    parts["dbname"] = name
    dsn = make_conninfo(**parts)
    with psycopg.connect(dsn, autocommit=True) as conn:
        for ddl in (_migration.USERS_DDL, _migration.DECK_LINKS_DDL):
            conn.execute(ddl)
    return dsn


def make_export(decks, extra_keys=None) -> str:
    """Write a synthetic export file (with correct checksums) and return its path."""
    keys = {"decks": _rec("string", json.dumps(decks))}
    for k, v in (extra_keys or {}).items():
        keys[k] = _rec("string", v)
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump({"keys": keys}, f)
    f.close()
    return f.name


def _rec(rtype, value):
    return {"type": rtype, "value": value, "pttl": None,
            "checksum": imp.content_hash(rtype, value)}


DECKS = [
    {"registry_id": "reg-1", "name": "SameName", "commanders": ["Atraxa"],
     "parent_folder_id": 588380, "url": "https://archidekt.com/decks/arch-1",
     "links": {"archidekt": {"deck_id": "arch-1", "identifier": "https://archidekt.com/decks/arch-1"},
               "moxfield": {"deck_id": "mox-1"}}},
    # name collision with reg-1: identity must be the link, never the name
    {"registry_id": "reg-2", "name": "SameName", "commanders": ["Krenko"],
     "parent_folder_id": 999, "url": "https://archidekt.com/decks/arch-2",
     "links": {"archidekt": {"deck_id": "arch-2"}}},
    # physical folder, moxfield-only link (any provider link row counts)
    {"registry_id": "reg-3", "name": "MoxOnly", "commanders": [],
     "parent_folder_id": 588380, "url": "https://moxfield.com/decks/mox-3",
     "links": {"moxfield": {"identifier": "https://moxfield.com/decks/mox-3"}}},
    # no registry_id -> deck_id NULL, still a registry_link
    {"name": "Unregistered", "commanders": [], "parent_folder_id": 333, "url": "",
     "links": {"archidekt": {"deck_id": "arch-6"}}},
    # unknown provider + malformed link entry -> skipped
    {"registry_id": "reg-5", "name": "TappedOut", "commanders": [], "parent_folder_id": 222,
     "url": "", "links": {"tappedout": {"deck_id": "to-1"}, "archidekt": "bogus"}},
]

EXTRA_KEYS = {"foo:bar": "hello", "noColonKey": "world"}


class FlagTests(unittest.TestCase):
    def test_truth_table(self):
        for v, expected in [("1", True), ("true", True), ("TRUE", True), ("yes", True),
                            ("0", False), ("", False), ("2", False), ("no", False)]:
            self.assertIs(cutover_enabled({"PG_CUTOVER": v}), expected, v)
        self.assertIs(cutover_enabled({}), False)


class StubRedis:
    """Minimal stand-in for a redis client; only GET is needed by the repo."""

    def __init__(self, decks_json):
        self.data = {"decks": decks_json}

    def get(self, key):
        return self.data.get(key)


class RedisFallbackRepoTests(unittest.TestCase):
    def setUp(self):
        self.redis = StubRedis(json.dumps(DECKS))

    def test_factory_off_returns_redis_repo(self):
        repo = deck_links_repository(redis_client=self.redis, env={"PG_CUTOVER": "0"})
        self.assertIsInstance(repo, RedisDeckLinksRepository)

    def test_factory_off_without_client_raises(self):
        with self.assertRaises(RuntimeError):
            deck_links_repository(env={"PG_CUTOVER": "0"})

    def test_factory_on_without_session_raises(self):
        with self.assertRaises(RuntimeError):
            deck_links_repository(env={"PG_CUTOVER": "1"})

    def test_get_and_list(self):
        repo = RedisDeckLinksRepository(self.redis)
        # NOTE: the Redis fallback does NOT filter to LINKABLE_PROVIDERS (unlike
        # the PG importer): the tappedout link is returned too. Only the
        # non-dict "bogus" entry is skipped.
        self.assertEqual(len(repo.list()), 6)
        link = repo.get(("archidekt", "arch-1"))
        self.assertEqual(link["deck_id"], "reg-1")
        self.assertEqual(link["folder_id"], 588380)
        self.assertIsNone(repo.get(("archidekt", "missing")))

    def test_identity_is_link_not_name(self):
        repo = RedisDeckLinksRepository(self.redis)
        same_name = [l for l in repo.list() if l["name_at_source"] == "SameName"]
        self.assertEqual({l["external_id"] for l in same_name}, {"arch-1", "mox-1", "arch-2"})

    def test_writes_raise_readonly_and_do_not_mutate(self):
        repo = RedisDeckLinksRepository(self.redis)
        before = dict(self.redis.data)
        with self.assertRaises(ReadOnlyError):
            repo.create({"provider": "x", "external_id": "y"})
        with self.assertRaises(ReadOnlyError):
            repo.update(("archidekt", "arch-1"), {"url": "z"})
        with self.assertRaises(ReadOnlyError):
            repo.delete(("archidekt", "arch-1"))
        self.assertEqual(self.redis.data, before)  # nothing written, nothing deleted


@unittest.skipUnless(psycopg and DSN, "DATABASE_URL / psycopg not available")
class PgTestBase(unittest.TestCase):
    def setUp(self):
        self.dsn = fresh_db(self)
        self.conn = psycopg.connect(self.dsn, autocommit=True)
        self.export_path = make_export(DECKS, EXTRA_KEYS)
        self.records = list(imp.records_from_export(self.export_path))
        imp.run_import(self.conn, list(self.records))
        imp.import_decks_from_records(self.conn, list(self.records), "lab")

    def tearDown(self):
        self.conn.close()
        os.unlink(self.export_path)

    def scalar(self, sql, params=None):
        with self.conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchone()[0]

    def run_verify(self):
        return vc.run_checks(self.conn, list(self.records), "lab", 100, 20261024)


class ImporterDeckLinksTests(PgTestBase):
    def test_users_single_anchor_row(self):
        self.assertEqual(self.scalar("SELECT count(*) FROM users"), 1)
        self.assertEqual(self.scalar("SELECT id FROM users"), "lab")

    def test_registry_links_imported(self):
        n = self.scalar("SELECT count(*) FROM deck_links WHERE match_method='registry_link'")
        self.assertEqual(n, 5)

    def test_name_collision_distinct_rows(self):
        n = self.scalar("SELECT count(*) FROM deck_links WHERE name_at_source='SameName'")
        self.assertEqual(n, 3)  # arch-1, mox-1 (reg-1) + arch-2 (reg-2)

    def test_deck_id_nullable(self):
        self.assertIsNone(self.scalar(
            "SELECT deck_id FROM deck_links WHERE provider='archidekt' AND external_id='arch-6'"))
        self.assertEqual(self.scalar(
            "SELECT match_method FROM deck_links WHERE provider='archidekt' AND external_id='arch-6'"),
            "registry_link")

    def test_orphan_stubs(self):
        rows = self.scalar("SELECT count(*) FROM deck_links WHERE match_method='orphan_stub'")
        self.assertEqual(rows, 2)
        self.assertEqual(self.scalar(
            "SELECT count(*) FROM deck_links WHERE match_method='orphan_stub' AND deck_id IS NULL "
            "AND raw->>'key' = external_id"), 2)

    def test_physical_counts_any_provider(self):
        self.assertEqual(self.scalar(
            "SELECT count(DISTINCT deck_id) FROM deck_links "
            "WHERE folder_id=588380 AND deck_id IS NOT NULL"), 2)  # reg-1 (arch+mox), reg-3 (mox only)

    def test_unique_provider_external_id(self):
        import psycopg.errors
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.conn.transaction():
                self.conn.execute(
                    "INSERT INTO deck_links (user_id, provider, external_id, url, name_at_source,"
                    " match_method, raw) VALUES ('lab','archidekt','arch-1','x','x','registry_link','{}')")

    def test_second_import_adds_zero_rows(self):
        before = self.scalar("SELECT count(*) FROM deck_links")
        imp.import_decks_from_records(self.conn, list(self.records), "lab")
        self.assertEqual(self.scalar("SELECT count(*) FROM deck_links"), before)
        self.assertEqual(self.scalar("SELECT count(*) FROM users"), 1)


class VerifyCutoverTests(PgTestBase):
    def test_happy_path_all_checks_pass(self):
        checks = self.run_verify()
        failed = [c.name for c in checks if not c.ok]
        self.assertEqual(failed, [], f"unexpected failures: {failed}")

    def test_detects_registry_link_url_tamper(self):
        self.conn.execute("UPDATE deck_links SET url='https://evil.example' "
                          "WHERE provider='archidekt' AND external_id='arch-1'")
        checks = self.run_verify()
        self.assertFalse(next(c for c in checks if "byte equality" in c.name).ok)

    def test_detects_orphan_stub_deletion(self):
        self.conn.execute("DELETE FROM deck_links WHERE match_method='orphan_stub' "
                          "AND external_id='foo:bar'")
        checks = self.run_verify()
        self.assertFalse(next(c for c in checks if "orphan" in c.name).ok)

    def test_detects_orphan_stub_raw_value_tamper(self):
        """Contract: orphan stub raw JSONB holds the original key/value, and any
        mismatch must fail verification. Currently the verifier only checks
        raw->>'key', so corrupted stub content passes — this is the repro."""
        self.conn.execute("UPDATE deck_links SET raw=jsonb_set(raw,'{value}','\"CORRUPTED\"') "
                          "WHERE match_method='orphan_stub' AND external_id='foo:bar'")
        checks = self.run_verify()
        self.assertFalse(next(c for c in checks if "orphan" in c.name).ok)

    def test_detects_orphan_stub_url_tamper(self):
        """Orphan stub url must equal the source key; tampering must fail verification."""
        self.conn.execute("UPDATE deck_links SET url='https://evil.example' "
                          "WHERE match_method='orphan_stub' AND external_id='foo:bar'")
        checks = self.run_verify()
        self.assertFalse(next(c for c in checks if "orphan" in c.name).ok)

    def test_redis_import_value_tamper_fails_cleanly(self):
        """A tampered redis_import value must surface as a failed check (exit
        non-zero), not an unhandled IntegrityError crash from the check-9
        re-import."""
        self.conn.execute("UPDATE redis_import SET value='\"TAMPERED\"' WHERE redis_key='foo:bar'")
        try:
            checks = self.run_verify()
        except imp.IntegrityError as e:
            self.fail(f"verifier crashed instead of reporting a failed check: {e}")
        self.assertFalse(next(c for c in checks if "redis_import" in c.name).ok)

    def test_main_exit_codes(self):
        self.assertEqual(vc.main(["--export", self.export_path, "--database-url", self.dsn,
                                  "--user-id", "lab"]), 0)
        self.conn.execute("UPDATE deck_links SET url='x' WHERE provider='moxfield' "
                          "AND external_id='mox-1'")
        self.assertEqual(vc.main(["--export", self.export_path, "--database-url", self.dsn,
                                  "--user-id", "lab"]), 1)


if __name__ == "__main__":
    unittest.main()
