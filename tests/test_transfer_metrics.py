"""Metrics semantics for the Redis->PG importer. Postgres tests need DATABASE_URL."""
import os
import unittest
import urllib.request
from pathlib import Path

from prometheus_client import CollectorRegistry

import src.server.workers.redis_pg_import as imp
import src.server.workers.transfer_metrics as tm

FIX = str(Path(__file__).parent / "fixtures" / "redis_export_small.json")
DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None


def val(reg, name, **labels):
    v = reg.get_sample_value(name, {"pipeline": tm.PIPELINE, **labels})
    return v or 0


class MetricsUnit(unittest.TestCase):
    def setUp(self):
        self.reg = CollectorRegistry()
        self.m = tm.TransferMetrics(self.reg)

    def test_counters_and_bounded_labels(self):
        self.m.record_read(3)
        self.m.record_success(2, unchanged=1)
        self.m.record_failure(1, "hash_mismatch", batch=True)
        self.assertEqual(val(self.reg, "transfer_records_total"), 3)
        self.assertEqual(val(self.reg, "transfer_records_succeeded_total"), 2)
        self.assertEqual(val(self.reg, "transfer_records_unchanged_total"), 1)
        self.assertEqual(val(self.reg, "transfer_records_failed_total", reason="hash_mismatch"), 1)

    def test_never_raises(self):
        self.m.processed = None  # break it
        self.m.record_read()

    def test_dry_run_counts_nothing(self):
        imp.run_import(None, imp.records_from_export(FIX), dry_run=True, metrics=self.m)
        self.assertEqual(val(self.reg, "transfer_records_total"), 0)

    def test_exporter_noop_when_unset(self):
        os.environ.pop("TRANSFER_METRICS_PORT", None)
        self.assertIsNone(tm.start_exporter())
        os.environ.pop("PUSHGATEWAY_URL", None)
        self.assertFalse(tm.push_if_configured(self.reg))

    def test_validation_failure_counted(self):
        bad = imp.Record("k", "weird", "v")
        with self.assertRaises(imp.PermanentError):
            imp.run_import(None, [bad], metrics=self.m)
        self.assertEqual(val(self.reg, "transfer_records_failed_total", reason="validation"), 1)
        self.assertEqual(val(self.reg, "transfer_in_progress"), 0)


@unittest.skipUnless(DSN and psycopg, "needs DATABASE_URL")
class MetricsPg(unittest.TestCase):
    def setUp(self):
        self.reg = CollectorRegistry()
        self.m = tm.TransferMetrics(self.reg)
        self.conn = psycopg.connect(DSN, autocommit=True)
        self.conn.execute("DROP TABLE IF EXISTS redis_import")
        self.recs = list(imp.records_from_export(FIX))

    def tearDown(self):
        self.conn.execute("DROP TABLE IF EXISTS redis_import")
        self.conn.close()

    def test_success_then_idempotent_rerun(self):
        imp.run_import(self.conn, self.recs, metrics=self.m)
        self.assertEqual(val(self.reg, "transfer_records_total"), 4)
        self.assertEqual(val(self.reg, "transfer_records_succeeded_total"), 4)
        self.assertEqual(val(self.reg, "transfer_records_unchanged_total"), 0)
        self.assertGreater(val(self.reg, "transfer_last_success_timestamp_seconds"), 0)
        imp.run_import(self.conn, self.recs, metrics=self.m)
        self.assertEqual(val(self.reg, "transfer_records_unchanged_total"), 4)

    def test_hash_mismatch_batch_all_failed_none_succeeded(self):
        real = imp.UPSERT
        imp.UPSERT = real.replace("%s::jsonb", "CASE WHEN %s::text IS NOT NULL THEN '\"corrupt\"'::jsonb END")
        try:
            with self.assertRaises(imp.IntegrityError):
                imp.run_import(self.conn, self.recs, metrics=self.m, sleep=lambda s: None)
        finally:
            imp.UPSERT = real
        self.assertEqual(val(self.reg, "transfer_records_succeeded_total"), 0)
        self.assertEqual(val(self.reg, "transfer_records_failed_total", reason="hash_mismatch"), 4)
        self.assertEqual(val(self.reg, "transfer_retries_total"), 0)
        self.assertEqual(val(self.reg, "transfer_in_progress"), 0)

    def test_retry_counted_not_failed(self):
        real = imp._batch
        n = {"c": 0}

        def flaky(conn, batch, now):
            n["c"] += 1
            if n["c"] == 1:
                raise ConnectionError("lost")
            return real(conn, batch, now)
        imp._batch = flaky
        try:
            imp.run_import(self.conn, self.recs, metrics=self.m, sleep=lambda s: None)
        finally:
            imp._batch = real
        self.assertEqual(val(self.reg, "transfer_retries_total"), 1)
        self.assertEqual(val(self.reg, "transfer_records_succeeded_total"), 4)
        self.assertEqual(sum(val(self.reg, "transfer_records_failed_total", reason=r) for r in tm.REASONS), 0)


class Exporter(unittest.TestCase):
    def test_http_exporter_serves_metrics(self):
        import socket
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        tm.get_metrics().record_read(0)
        self.assertEqual(tm.start_exporter(port), port)
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5).read().decode()
        self.assertIn("transfer_records_total", body)


if __name__ == "__main__":
    unittest.main()
