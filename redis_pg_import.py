"""Idempotent, atomic Redis -> PostgreSQL importer.

Redis is only ever read (SCAN/TYPE/GET/LRANGE/...); nothing is written or deleted.

Target: landing table ``redis_import`` keyed by Redis key (no typed Postgres
schema exists in this repo yet). Each row stores type, JSONB value, TTL and a
sha256 checksum of the canonical value.

* Idempotent: ``INSERT .. ON CONFLICT (redis_key) DO UPDATE .. WHERE checksum differs``;
  a re-run reports everything as ``unchanged``.
* Atomic: each batch is one transaction (rollback on any error). Records are
  parsed/validated *before* any write.
* Integrity: sha256 of canonical JSON {type, value} (TTL excluded). A source-supplied
  hash (export ``checksum``) must match the recomputed one. After each batch's writes,
  rows are re-read and the hash recomputed from the stored JSONB value; any mismatch
  (incl. a tampered value behind an unchanged checksum column) raises IntegrityError,
  rolls back the batch, is never retried, and exits 3. ``--verify`` does the same
  against all rows (exit 2).
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
import hmac
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime

import transfer_metrics

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

# --- deck_links typed import ---------------------------------------------
DECKS_KEY = "decks"
# Providers whose per-deck links are imported into the typed deck_links table.
LINKABLE_PROVIDERS = ("archidekt", "moxfield")

UPSERT_DECK_LINK = """
INSERT INTO deck_links
  (user_id, deck_id, provider, external_id, url, folder_id,
   name_at_source, commander_at_source, match_method,
   last_synced_at, upstream_missing_at, raw)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
ON CONFLICT (provider, external_id) DO UPDATE
SET user_id = EXCLUDED.user_id,
    deck_id = EXCLUDED.deck_id,
    url = EXCLUDED.url,
    folder_id = EXCLUDED.folder_id,
    name_at_source = EXCLUDED.name_at_source,
    commander_at_source = EXCLUDED.commander_at_source,
    match_method = EXCLUDED.match_method,
    last_synced_at = EXCLUDED.last_synced_at,
    upstream_missing_at = EXCLUDED.upstream_missing_at,
    raw = EXCLUDED.raw
"""

INSERT_USER = """
INSERT INTO users (id, name, archidekt_user_id)
VALUES (%s, %s, %s)
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name,
    archidekt_user_id = EXCLUDED.archidekt_user_id
"""

TRANSIENT_SQLSTATES = {"40001", "40P01", "08000", "08003", "08006", "57P01"}


class PermanentError(Exception):
    """Validation/constraint problem; never retried."""


class IntegrityError(PermanentError):
    """Content hash mismatch (corruption); deterministic, never retried."""


def content_hash(rtype: str, value) -> str:
    """sha256 hex of canonical JSON {"t": type, "v": value}. TTL is excluded (it drifts)."""
    blob = json.dumps({"t": rtype, "v": value}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class Record:
    key: str
    type: str
    value: object
    ttl_ms: int | None = None
    source_hash: str | None = None  # hash supplied by the source/exporter, if any

    @property
    def checksum(self) -> str:
        return content_hash(self.type, self.value)


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
        except Exception as e:
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
        yield Record(key, e["type"], _norm(e["value"]), e.get("pttl"),
                     e.get("checksum") or e.get("sha256"))


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


# ---------- deck_links import ----------

def _now() -> datetime:
    return datetime.now(UTC)


def _deck_link_row(deck: dict, provider: str, entry: dict, user_id: str, now: datetime) -> dict:
    """Build one deck_links row from a registry deck and one of its provider links."""
    return {
        "user_id": user_id,
        # registry id of the local deck; nullable for links whose local deck isn't registered yet
        "deck_id": deck.get("registry_id"),
        "provider": provider,
        "external_id": entry.get("deck_id") or entry.get("identifier"),
        "url": entry.get("identifier") or deck.get("url", ""),
        "folder_id": deck.get("parent_folder_id"),
        "name_at_source": deck.get("name", ""),
        "commander_at_source": ", ".join(deck.get("commanders") or []),
        "match_method": "registry_link",
        "last_synced_at": now,
        "upstream_missing_at": None,
        "raw": entry,
    }


def ensure_user(conn, user_id: str, name: str = "Commander Lab", archidekt_user_id: str = "") -> None:
    """Make sure the single lab user row exists (the cutover's anchor row)."""
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(INSERT_USER, (user_id, name, archidekt_user_id))


def import_decks_from_records(conn, records, user_id: str | None,
                              name: str = "Commander Lab",
                              archidekt_user_id: str = "",
                              now: datetime | None = None) -> int:
    """Import the ``decks`` Redis key into ``deck_links`` (plus ``users``).

    Every deck (any lifecycle status) is imported; each of its provider links
    (archidekt / moxfield) becomes one deck_links row keyed by
    ``(provider, external_id)``. Records other than ``decks`` become stub
    rows (``deck_id`` NULL) so no key is lost (zero loss).
    """
    if now is None:
        now = _now()
    if not user_id:
        return 0
    decks = []
    for rec in records:
        if rec.key == DECKS_KEY:
            try:
                decks = json.loads(rec.value)
            except (TypeError, ValueError):
                decks = []
            break
    if not isinstance(decks, list):
        decks = []
    ensure_user(conn, user_id, name, archidekt_user_id)
    rows = []
    for deck in decks:
        if not isinstance(deck, dict):
            continue
        links = deck.get("links") or {}
        for provider, entry in links.items():
            if provider not in LINKABLE_PROVIDERS:
                continue
            if not isinstance(entry, dict):
                continue
            rows.append(_deck_link_row(deck, provider, entry, user_id, now))
    # Orphan keys: every non-deck key becomes a stub link row (deck_id NULL).
    for rec in records:
        if rec.key == DECKS_KEY:
            continue
        rows.append({
            "user_id": user_id,
            "deck_id": None,
            "provider": rec.key.split(":")[0] if ":" in rec.key else "orphan",
            "external_id": rec.key,
            "url": rec.key,
            "folder_id": None,
            "name_at_source": rec.key,
            "commander_at_source": None,
            "match_method": "orphan_stub",
            "last_synced_at": now,
            "upstream_missing_at": None,
            "raw": {"key": rec.key, "type": rec.type, "value": rec.value},
        })
    with conn.transaction():
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(UPSERT_DECK_LINK, (
                    row["user_id"], row["deck_id"], row["provider"], row["external_id"],
                    row["url"], row["folder_id"], row["name_at_source"],
                    row["commander_at_source"], row["match_method"],
                    row["last_synced_at"], row["upstream_missing_at"],
                    json.dumps(row["raw"], ensure_ascii=False),
                ))
    return len(rows)


# ---------- load ----------

def validate(rec: Record) -> None:
    if not rec.key or rec.type not in {"string", "hash", "list", "set", "zset", "stream"}:
        raise PermanentError(f"bad record {rec.key!r} type={rec.type!r}")
    try:
        json.dumps(rec.value)
    except (TypeError, ValueError) as e:
        raise PermanentError(f"unserialisable value for {rec.key!r}: {e}") from e
    if rec.source_hash is not None:
        sh = str(rec.source_hash).lower().removeprefix("sha256:")
        if not hmac.compare_digest(sh, rec.checksum):
            raise IntegrityError(f"source hash mismatch for {rec.key!r}: "
                                 f"supplied {sh[:12]} != computed {rec.checksum[:12]}")


def _verify_batch(cur, batch: list[Record]) -> None:
    """Re-read persisted rows and recompute hashes from the stored value (not the
    checksum column), so tampered/corrupted values are caught even when the upsert skipped."""
    cur.execute("SELECT redis_key, redis_type, value, checksum FROM redis_import "
                "WHERE redis_key = ANY(%s)", ([r.key for r in batch],))
    stored = {k: (t, v, c) for k, t, v, c in cur.fetchall()}
    for rec in batch:
        row = stored.get(rec.key)
        if row is None:
            raise IntegrityError(f"{rec.key!r} missing after write")
        t, v, c = row
        actual = content_hash(t, v)
        if not (hmac.compare_digest(actual, rec.checksum) and hmac.compare_digest(c, rec.checksum)):
            raise IntegrityError(f"post-write hash mismatch for {rec.key!r}: expected "
                                 f"{rec.checksum[:12]}, stored value {actual[:12]}, column {c[:12]}")


def _batch(conn, batch: list[Record], now: datetime) -> tuple[int, int, int]:
    ins = upd = unch = 0
    with conn.transaction():  # commit on success, rollback on any exception
        with conn.cursor() as cur:
            for rec in batch:
                exp = (now.timestamp() + rec.ttl_ms / 1000) if rec.ttl_ms else None
                cur.execute(UPSERT, (rec.key, rec.type, json.dumps(rec.value, ensure_ascii=False),
                                     rec.ttl_ms,
                                     datetime.fromtimestamp(exp, UTC) if exp else None,
                                     rec.checksum))
                row = cur.fetchone()
                if row is None:
                    unch += 1
                elif row[0]:
                    ins += 1
                else:
                    upd += 1
            _verify_batch(cur, batch)  # raises IntegrityError -> transaction rolls back
    return ins, upd, unch


def run_import(conn, records, batch_size=100, max_retries=3, dry_run=False,
               skip_invalid=False, sleep=time.sleep, metrics=None) -> Summary:
    m = metrics or transfer_metrics.get_metrics()
    s = Summary()
    good: list[Record] = []
    ok = False
    m.run_started()
    try:
        for rec in records:
            s.scanned += 1
            if not dry_run:
                m.record_read()
            try:
                validate(rec)
            except PermanentError as e:
                if not dry_run:
                    m.record_failure(1, "hash_mismatch" if isinstance(e, IntegrityError) else "validation")
                if not skip_invalid:
                    raise
                s.rejected.append((rec.key, str(e)))
                continue
            good.append(rec)
        if dry_run:
            s.unchanged = 0
            log.info("dry-run: %d records would be upserted", len(good))
            return s
        with_retry(lambda: _ensure_schema(conn), max_retries, sleep=sleep, on_retry=m.record_retry)

        def on_retry():
            s.retries += 1
            m.record_retry()

        now = datetime.now(UTC)
        for i in range(0, len(good), batch_size):
            chunk = good[i:i + batch_size]
            t0 = time.monotonic()
            try:
                ins, upd, unch = with_retry(lambda chunk=chunk: _batch(conn, chunk, now), max_retries,
                                            sleep=sleep, on_retry=on_retry)
            except Exception as e:
                m.record_failure(len(chunk), "hash_mismatch" if isinstance(e, IntegrityError)
                                 else "validation" if isinstance(e, PermanentError) else "db_error",
                                 batch=True)
                m.observe_batch(time.monotonic() - t0)
                raise
            m.observe_batch(time.monotonic() - t0)
            m.record_success(len(chunk), unch)  # only after the batch committed
            s.inserted += ins
            s.updated += upd
            s.unchanged += unch
        ok = not s.rejected
        return s
    finally:
        m.run_finished(ok)


def _ensure_schema(conn):
    with conn.transaction():
        conn.execute(DDL)


def verify(conn, records) -> list[str]:
    """Return mismatch descriptions comparing source records to Postgres."""
    with conn.cursor() as cur:
        cur.execute("SELECT redis_key, redis_type, value, checksum FROM redis_import")
        db = {k: (t, v, c) for k, t, v, c in cur.fetchall()}
    bad = []
    for rec in records:
        row = db.get(rec.key)
        if (row is None or row[2] != rec.checksum or content_hash(row[0], row[1]) != rec.checksum):
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
    p.add_argument("--metrics-port", type=int, default=None,
                   help="serve Prometheus /metrics on this port (or $TRANSFER_METRICS_PORT)")
    # Cutover / deck import flags.
    p.add_argument("--user-id", default=None, help="id of the single lab user (anchors deck_links)")
    p.add_argument("--user-name", default="Commander Lab", help="display name for the user row")
    p.add_argument("--user-archidekt-id", default="", dest="user_archidekt_id",
                   help="Archidekt user id for the user row")
    a = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not a.database_url and not a.dry_run:
        p.error("DATABASE_URL / --database-url required")

    transfer_metrics.start_exporter(a.metrics_port)
    if a.from_export:
        recs = list(records_from_export(a.from_export))
    else:
        import redis
        recs = list(with_retry(lambda: list(records_from_redis(redis.from_url(a.redis_url))),
                               a.max_retries))
    conn = psycopg.connect(a.database_url, autocommit=True) if a.database_url else None
    try:
        try:
            s = run_import(conn, recs, a.batch_size, a.max_retries, a.dry_run, a.skip_invalid)
        except IntegrityError as e:
            log.error("INTEGRITY FAILURE: %s", e)
            return 3
        log.info(s.line())
        for k, why in s.rejected:
            log.error("rejected %s: %s", k, why)
        # Typed deck import (decks -> deck_links + users). No-op when --user-id is unset.
        if a.user_id and conn is not None and not a.dry_run:
            n = import_decks_from_records(conn, recs, a.user_id, a.user_name, a.user_archidekt_id)
            log.info("imported %d deck links", n)
        if a.verify and not a.dry_run:
            bad = verify(conn, [r for r in recs if r.key not in {k for k, _ in s.rejected}])
            if bad:
                log.error("verify FAILED for %d keys: %s", len(bad), bad)
                return 2
            log.info("verify OK")
        return 1 if s.rejected else 0
    finally:
        transfer_metrics.push_if_configured()
        if conn:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
