"""Prometheus metrics for data transfers (Redis -> PostgreSQL importer).

Counters are updated as the transfer runs (per record read, per batch after the commit
outcome is known), so a scrape mid-run sees live numbers. Error rate is derived in
PromQL, not stored:

    sum(rate(transfer_records_failed_total[5m])) / clamp_min(sum(rate(transfer_records_total[5m])), 1e-9)

Labels are bounded (``pipeline``, ``reason``); never key names or IDs.
Metric calls never raise into the transfer path.
"""
from __future__ import annotations

import logging
import os
import time

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Gauge, Histogram

log = logging.getLogger("transfer_metrics")
PIPELINE = "redis_postgres"
REASONS = ("hash_mismatch", "validation", "db_error", "source_error")


class TransferMetrics:
    def __init__(self, registry: CollectorRegistry | None = REGISTRY, pipeline: str = PIPELINE):
        r = registry
        self.pipeline = pipeline
        lp = ["pipeline"]
        self.processed = Counter("transfer_records_total", "Records read for transfer", lp, registry=r)
        self.succeeded = Counter("transfer_records_succeeded_total",
                                 "Records committed (and verified) in PostgreSQL", lp, registry=r)
        self.skipped = Counter("transfer_records_unchanged_total",
                               "Records committed that were already up to date (idempotent no-op; "
                               "also counted in succeeded)", lp, registry=r)
        self.failed = Counter("transfer_records_failed_total", "Records that ended in terminal failure",
                              lp + ["reason"], registry=r)
        self.retries = Counter("transfer_retries_total", "Transient-error batch retries", lp, registry=r)
        self.batches = Counter("transfer_batches_total", "Batches finished", lp + ["status"], registry=r)
        self.duration = Histogram("transfer_batch_duration_seconds", "Batch wall time", lp, registry=r)
        self.in_progress = Gauge("transfer_in_progress", "1 while a transfer is running", lp, registry=r)
        self.last_success = Gauge("transfer_last_success_timestamp_seconds",
                                  "Unix time of last fully successful run", lp, registry=r)
        for rs in REASONS:
            self.failed.labels(pipeline, rs)
        for c in (self.processed, self.succeeded, self.skipped, self.retries):
            c.labels(pipeline)

    def _safe(self, fn, *a):
        try:
            fn(*a)
        except Exception:
            log.debug("metrics update failed", exc_info=True)

    def record_read(self, n=1):
        self._safe(lambda: self.processed.labels(self.pipeline).inc(n))

    def record_success(self, n, unchanged=0):
        def f():
            self.succeeded.labels(self.pipeline).inc(n)
            self.skipped.labels(self.pipeline).inc(unchanged)
            self.batches.labels(self.pipeline, "success").inc()
        self._safe(f)

    def record_failure(self, n, reason, batch=False):
        def f():
            self.failed.labels(self.pipeline, reason).inc(n)
            if batch:
                self.batches.labels(self.pipeline, "failed").inc()
        self._safe(f)

    def record_retry(self):
        self._safe(lambda: self.retries.labels(self.pipeline).inc())

    def observe_batch(self, seconds):
        self._safe(lambda: self.duration.labels(self.pipeline).observe(seconds))

    def run_started(self):
        self._safe(lambda: self.in_progress.labels(self.pipeline).set(1))

    def run_finished(self, ok):
        def f():
            self.in_progress.labels(self.pipeline).set(0)
            if ok:
                self.last_success.labels(self.pipeline).set(time.time())
        self._safe(f)


_default: TransferMetrics | None = None


def get_metrics() -> TransferMetrics:
    global _default
    if _default is None:
        _default = TransferMetrics()
    return _default


def start_exporter(port: int | None = None) -> int | None:
    """Serve /metrics on ``port`` (or $TRANSFER_METRICS_PORT) for the life of the process.
    No-op when unset/0."""
    port = port if port is not None else int(os.environ.get("TRANSFER_METRICS_PORT") or 0)
    if not port:
        return None
    from prometheus_client import start_http_server
    start_http_server(port)
    log.info("metrics exporter listening on :%d", port)
    return port


def push_if_configured(registry: CollectorRegistry = REGISTRY) -> bool:
    """Push to a Pushgateway ($PUSHGATEWAY_URL) at end of a short-lived run. No-op when unset."""
    url = os.environ.get("PUSHGATEWAY_URL")
    if not url:
        return False
    try:
        from prometheus_client import push_to_gateway
        push_to_gateway(url, job="redis_pg_import", registry=registry)
        return True
    except Exception:
        log.warning("pushgateway push failed", exc_info=True)
        return False
