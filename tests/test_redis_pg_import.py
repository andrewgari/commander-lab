"""Tests for redis_pg_import. Postgres tests need DATABASE_URL (skipped otherwise)."""
import os
import unittest
from pathlib import Path

import redis_pg_import as imp

FIX = str(Path(__file__).parent / "fixtures" / "redis_export_small.json")
DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")

try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


class RetryTests(unittest.TestCase):
    def test_transient_retried_then_ok(self):
        calls = []
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("x")
            return "ok"
        sleeps = []
        self.assertEqual(imp.with_retry(fn, 3, sleep=sleeps.append), "ok")
        self.assertEqual((len(calls), len(sleeps)), (3, 2))

    def test_permanent_not_retried(self):
        calls = []
        def fn():
            calls.append(1)
            raise imp.PermanentError("bad")
        with self.assertRaises(imp.PermanentError):
            imp.with_retry(fn, 3, sleep=lambda s: None)
        self.assertEqual(len(calls), 1)

    def test_exhausted(self):
        with self.assertRaises(ConnectionError):
            imp.with_retry(lambda: (_ for _ in ()).throw(ConnectionError()), 2, sleep=lambda s: None)

    def test_dry_run_no_db(self):
        s = imp.run_import(None, imp.records_from_export(FIX), dry_run=True)
        self.assertEqual(s.scanned, 4)


@unittest.skipUnless(DSN and psycopg, "DATABASE_URL / psycopg not available")
class PgTests(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(DSN, autocommit=True)
        self.conn.execute("DROP TABLE IF EXISTS redis_import")
        self.recs = list(imp.records_from_export(FIX))

    def tearDown(self):
        self.conn.execute("DROP TABLE IF EXISTS redis_import")
        self.conn.close()

    def count(self):
        return self.conn.execute("SELECT count(*) FROM redis_import").fetchone()[0]

    def test_import_idempotent_and_verify(self):
        s = imp.run_import(self.conn, self.recs)
        self.assertEqual((s.inserted, s.updated, s.unchanged), (4, 0, 0))
        self.assertEqual(imp.verify(self.conn, self.recs), [])
        s = imp.run_import(self.conn, self.recs)
        self.assertEqual((s.inserted, s.updated, s.unchanged), (0, 0, 4))
        self.assertEqual(self.count(), 4)

    def test_changed_value_updates_one(self):
        imp.run_import(self.conn, self.recs)
        self.recs[0].value = "changed"
        s = imp.run_import(self.conn, self.recs)
        self.assertEqual((s.inserted, s.updated, s.unchanged), (0, 1, 3))

    def test_unicode_and_ttl_preserved(self):
        imp.run_import(self.conn, self.recs)
        v, ttl = self.conn.execute(
            "SELECT value, ttl_ms FROM redis_import WHERE redis_key='tmp:ttl'").fetchone()
        self.assertEqual((v, ttl), ("héllo", 60000))

    def test_batch_rolls_back_on_midbatch_failure(self):
        class Bad(imp.Record):
            @property
            def checksum(self):
                raise psycopg.errors.DataError("boom")  # fails mid-batch, after earlier upserts
        recs = self.recs[:2] + [Bad("z", "string", "v")]
        with self.assertRaises(psycopg.errors.DataError):
            imp.run_import(self.conn, recs, batch_size=10, sleep=lambda s: None)
        self.assertEqual(self.count(), 0)

    def test_transient_failure_retried_no_duplicates(self):
        real = imp._batch
        n = {"c": 0}
        def flaky(conn, batch, now):
            n["c"] += 1
            if n["c"] == 1:
                real(conn, batch, now)  # write, then lose the connection before commit-ack
                raise ConnectionError("lost")
            return real(conn, batch, now)
        imp._batch = flaky
        try:
            s = imp.run_import(self.conn, self.recs, sleep=lambda s: None)
        finally:
            imp._batch = real
        self.assertEqual(s.retries, 1)
        self.assertEqual(self.count(), 4)

    def test_invalid_skipped(self):
        bad = imp.Record("k", "weird", "v")
        s = imp.run_import(self.conn, self.recs + [bad], skip_invalid=True)
        self.assertEqual((len(s.rejected), self.count()), (1, 4))
        with self.assertRaises(imp.PermanentError):
            imp.run_import(self.conn, [bad])


if __name__ == "__main__":
    unittest.main()
