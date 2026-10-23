"""Idempotent, atomic Redis -> PostgreSQL importer.

Redis is only ever read (SCAN/TYPE/GET/LRANGE/...); nothing is written or deleted.

Target: landing table ``redis_import`` keyed by Redis key (no typed Postgres
schema exists in this repo yet). Each row stores type, JSONB value, TTL and a
sha256 checksum of the canonical value.

* Idempotent: ``INSERT .. ON CONFLICT (redis_key) DO UPDATE .. WHERE checksum differs``;
  a re-run reports everything as ``unchanged``.
* Atomic: each batch is one transaction (rollback on any error). Records are
  parsed/validated *before* any write.
* Retry: whole-batch retry with exponential backoff + jitter, only for transient
  errors (connection, serialization, deadlock, Redis connection/timeout).
  Retrying is safe because the upsert is idempotent.

Usage:
    DATABASE_URL=postgresql://... python redis_pg_import.py --from-export dump.json
    DATABASE_URL=... REDIS_URL=redis://localhost:6379/0 python redis_pg_import.py --from-redis
Flags: --dry-run --verify --batch-size N --max-retries N
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

log = logging.getLogger("redis_pg_import")

DDL = """
CREATE TABLE IF NOT EXISTS redis_import (
    redis_key   TEXT PRIMARY KEY,
    redis_type  TEXT NOT NULL,
    value       JSONB NOT NULL,
    ttl_ms      BIGINT,
    expires_at  TIMESTAMPTZ,
    checksum    TEXT NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
)"""

UPSERT = """
INSERT INTO redis_import AS t (redis_key, redis_type, value, ttl_ms, expires_at, checksum)
VALUES (%s, %s, %s::jsonb, %s, %s, %s)
ON CONFLICT (redis_key) DO UPDATE
   SET redis_type = EXCLUDED.redis_type, value = EXCLUDED.value, ttl_ms = EXCLUDED.ttl_ms,
       expires_at = EXCLUDED.expires_at, checksum = EXCLUDED.checksum, imported_at = now()
 WHERE t.checksum IS DISTINCT FROM EXCLUDED.checksum
RETURNING (xmax = 0) AS inserted"""

TRANSIENT_SQLSTATES = {"40001", "40P01", "08000", "08003", "08006", "57P01"}


class PermanentError(Exception):
    """Validation/constraint problem; never retried."""


@dataclass
class Record:
    key: str
    type: str
    value: object
    ttl_ms: int | None = None

    @property
    def checksum(self) -> str:
        blob = json.dumps({"t": self.type, "v": self.value}, sort_keys=True,
                          separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(blob.encode()).hexdigest()


@dataclass
class Summary:
    scanned: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    rejected: list = field(default_factory=list)
    retries: int = 0

    def line(self) -> str:
        return (f"scanned={self.scanned} inserted={self.inserted} updated={self.updated} "
                f"unchanged={self.unchanged} rejected={len(self.rejected)} retries={self.retries}")


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    try:
        import redis
        if isinstance(exc, (redis.ConnectionError, redis.TimeoutError)):
            return True
    except ImportError:
        pass
    try:
        import psycopg
        if isinstance(exc, psycopg.OperationalError):
            return True
        if isinstance(exc, psycopg.Error):
            return (getattr(exc, "sqlstate", None) or "") in TRANSIENT_SQLSTATES
    except ImportError:
        pass
    return False


def with_retry(fn, max_retries=3, base=0.5, sleep=time.sleep, on_retry=None):
    attempt = 0
    while True:
        try:
            return fn()
        except Exception as e:  # noqa: BLE001 - classified below, otherwise re-raised
            if not is_transient(e) or attempt >= max_retries:
                raise
            attempt += 1
            if on_retry:
                on_retry()
            delay = base * (2 ** (attempt - 1)) * (0.5 + random.random() / 2)
            log.warning("transient error (%s); retry %d/%d in %.2fs", e, attempt, max_retries, delay)
            sleep(delay)


# ---------- sources ----------
def _norm(v):
    """Make export values JSON-safe; base64 wrapper dicts from export are kept as-is."""
    return v


def records_from_export(path: str):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    for key, e in data["keys"].items():
        yield Record(key, e["type"], _norm(e["value"]), e.get("pttl"))


def _dec(b):
    if isinstance(b, str):
        return b
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return {"__b64__": base64.b64encode(b).decode()}


def records_from_redis(r):
    """Read-only SCAN of a live Redis (not a point-in-time snapshot)."""
    for k in r.scan_iter(count=500):
        t = _dec(r.type(k))
        ttl = r.pttl(k)
        if t == "string":
            v = _dec(r.get(k))
        elif t == "hash":
            v = {(_dec(a) if isinstance(_dec(a), str) else _dec(a)["__b64__"]): _dec(b)
                 for a, b in r.hgetall(k).items()}
        elif t == "list":
            v = [_dec(x) for x in r.lrange(k, 0, -1)]
        elif t == "set":
            v = sorted((_dec(x) for x in r.smembers(k)), key=str)
        elif t == "zset":
            v = [[_dec(m), s] for m, s in r.zrange(k, 0, -1, withscores=True)]
        else:
            raise PermanentError(f"unsupported redis type {t!r} for key {k!r}")
        key = _dec(k)
        yield Record(key if isinstance(key, str) else key["__b64__"], t, v, ttl if ttl >= 0 else None)


# ---------- load ----------
def validate(rec: Record) -> None:
    if not rec.key or rec.type not in {"string", "hash", "list", "set", "zset", "stream"}:
        raise PermanentError(f"bad record {rec.key!r} type={rec.type!r}")
    try:
        json.dumps(rec.value)
    except (TypeError, ValueError) as e:
        raise PermanentError(f"unserialisable value for {rec.key!r}: {e}") from e


def _batch(conn, batch: list[Record], now: datetime) -> tuple[int, int, int]:
    ins = upd = unch = 0
    with conn.transaction():  # commit on success, rollback on any exception
        with conn.cursor() as cur:
            for rec in batch:
                exp = (now.timestamp() + rec.ttl_ms / 1000) if rec.ttl_ms else None
                cur.execute(UPSERT, (rec.key, rec.type, json.dumps(rec.value, ensure_ascii=False),
                                     rec.ttl_ms,
                                     datetime.fromtimestamp(exp, timezone.utc) if exp else None,
                                     rec.checksum))
                row = cur.fetchone()
                if row is None:
                    unch += 1
                elif row[0]:
                    ins += 1
                else:
                    upd += 1
    return ins, upd, unch


def run_import(conn, records, batch_size=100, max_retries=3, dry_run=False,
               skip_invalid=False, sleep=time.sleep) -> Summary:
    s = Summary()
    good: list[Record] = []
    for rec in records:
        s.scanned += 1
        try:
            validate(rec)
        except PermanentError as e:
            if not skip_invalid:
                raise
            s.rejected.append((rec.key, str(e)))
            continue
        good.append(rec)
    if dry_run:
        s.unchanged = 0
        log.info("dry-run: %d records would be upserted", len(good))
        return s
    with_retry(lambda: _ensure_schema(conn), max_retries, sleep=sleep)
    now = datetime.now(timezone.utc)
    for i in range(0, len(good), batch_size):
        chunk = good[i:i + batch_size]
        ins, upd, unch = with_retry(lambda: _batch(conn, chunk, now), max_retries, sleep=sleep,
                                    on_retry=lambda: setattr(s, "retries", s.retries + 1))
        s.inserted += ins
        s.updated += upd
        s.unchanged += unch
    return s


def _ensure_schema(conn):
    with conn.transaction():
        conn.execute(DDL)


def verify(conn, records) -> list[str]:
    """Return mismatch descriptions comparing source records to Postgres."""
    with conn.cursor() as cur:
        cur.execute("SELECT redis_key, checksum FROM redis_import")
        db = dict(cur.fetchall())
    bad = []
    for rec in records:
        if db.get(rec.key) != rec.checksum:
            bad.append(rec.key)
    return bad


def main(argv=None) -> int:
    import psycopg
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-export", metavar="JSON")
    src.add_argument("--from-redis", action="store_true")
    p.add_argument("--redis-url", default=os.environ.get("REDIS_URL", "redis://localhost:6379/0"))
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    p.add_argument("--batch-size", type=int, default=100)
    p.add_argument("--max-retries", type=int, default=3)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--skip-invalid", action="store_true")
    p.add_argument("--verify", action="store_true")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not a.database_url and not a.dry_run:
        p.error("DATABASE_URL / --database-url required")

    if a.from_export:
        recs = list(records_from_export(a.from_export))
    else:
        import redis
        recs = list(with_retry(lambda: list(records_from_redis(redis.from_url(a.redis_url))),
                               a.max_retries))
    conn = psycopg.connect(a.database_url, autocommit=True) if a.database_url else None
    try:
        s = run_import(conn, recs, a.batch_size, a.max_retries, a.dry_run, a.skip_invalid)
        log.info(s.line())
        for k, why in s.rejected:
            log.error("rejected %s: %s", k, why)
        if a.verify and not a.dry_run:
            bad = verify(conn, [r for r in recs if r.key not in {k for k, _ in s.rejected}])
            if bad:
                log.error("verify FAILED for %d keys: %s", len(bad), bad)
                return 2
            log.info("verify OK")
        return 1 if s.rejected else 0
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
