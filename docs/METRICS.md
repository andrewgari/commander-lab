# Transfer metrics (Redis -> PostgreSQL importer)

Prometheus metrics from `transfer_metrics.py`, updated live while `redis_pg_import.py` runs
(read count per record; success/failure per batch, only after the batch transaction outcome is known).

| Metric | Type | Labels |
|---|---|---|
| `transfer_records_total` | counter | pipeline |
| `transfer_records_succeeded_total` | counter | pipeline (committed + hash-verified) |
| `transfer_records_unchanged_total` | counter | pipeline (subset of succeeded: idempotent no-ops) |
| `transfer_records_failed_total` | counter | pipeline, reason=`hash_mismatch\|validation\|db_error\|source_error` |
| `transfer_retries_total` | counter | pipeline |
| `transfer_batches_total` | counter | pipeline, status=`success\|failed` |
| `transfer_batch_duration_seconds` | histogram | pipeline |
| `transfer_in_progress` | gauge | pipeline |
| `transfer_last_success_timestamp_seconds` | gauge | pipeline |

A rolled-back batch counts all its records as failed; retries are counted separately and never double-count records.
Dry runs record nothing.

## Exposure
The importer is a short-lived CLI, so the app's `/metrics` would not see it. Either:
- `--metrics-port 9108` (or `TRANSFER_METRICS_PORT=9108`): serves `/metrics` for the life of the process; or
- `PUSHGATEWAY_URL=http://pushgateway:9091`: pushes once at exit (job `redis_pg_import`).
Both are no-ops when unset. Don't publish the port publicly.

## PromQL
Error rate: `sum(rate(transfer_records_failed_total[5m])) / clamp_min(sum(rate(transfer_records_total[5m])), 1e-9)`
Total transferred: `transfer_records_succeeded_total`. Time since last success: `time() - transfer_last_success_timestamp_seconds`.

`docs/monitoring/` has a sample `prometheus.yml` and a Grafana dashboard (not wired into compose).
