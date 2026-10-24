"""Verify the Redis -> PostgreSQL cutover against the cutover Redis export.

No hardcoded counts: every expectation is derived from the export file itself.

Checks (any mismatch exits non-zero):
 1. Schema: users, deck_links, redis_import tables exist.
 2. Users: exactly one row (the cutover's anchor user).
 3. redis_import completeness: every export key has a row whose stored value
    re-hashes to the export checksum (zero loss, byte-level via canonical hash).
 4. deck_links per provider: counts derived from the export's ``decks`` key
    match ``deck_links`` (match_method='registry_link') grouped by provider.
 5. URL / external_id byte equality for every linked deck.
 6. Physical count: decks whose Archidekt folder_id is 588380 match.
 7. Orphan mapping: every non-``decks`` export key has a stub deck_links row
    (deck_id NULL, match_method='orphan_stub', raw.key = the Redis key).
 8. Random sample (default 100, seeded) of export records re-verified against
    redis_import (checksum of the stored JSONB value).
 9. Idempotency: running the deck import a second time adds zero deck_links
    and zero redis_import rows.

Usage:
    DATABASE_URL=postgresql://... python scripts/verify_cutover.py \
        --export dump.json --user-id lab [--report docs/redis-postgres-verification.md]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import redis_pg_import as imp  # noqa: E402

PHYSICAL_FOLDER_ID = 588380  # AGENTS.md: active physical Archidekt folder


class Check:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.lines: list[str] = []

    def fail(self, msg: str) -> None:
        self.ok = False
        self.lines.append(f"FAIL: {msg}")

    def info(self, msg: str) -> None:
        self.lines.append(msg)


def expected_links(records) -> list[dict]:
    """Provider link rows expected from the export's ``decks`` key."""
    out = []
    for rec in records:
        if rec.key != imp.DECKS_KEY:
            continue
        try:
            decks = json.loads(rec.value)
        except (TypeError, ValueError):
            decks = []
        if not isinstance(decks, list):
            continue
        for deck in decks:
            if not isinstance(deck, dict):
                continue
            for provider, entry in (deck.get("links") or {}).items():
                if provider in imp.LINKABLE_PROVIDERS and isinstance(entry, dict):
                    out.append(imp._deck_link_row(deck, provider, entry, user_id="?",
                                                  now=datetime.now(UTC)))
    return out


def run_checks(conn, records, user_id: str, sample_size: int, seed: int) -> list[Check]:
    checks: list[Check] = []

    # 1. schema ------------------------------------------------------------
    c = Check("schema: users + deck_links + landing tables exist")
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        tables = {r[0] for r in cur.fetchall()}
    for t in ("users", "deck_links", "redis_import"):
        if t not in tables:
            c.fail(f"missing table {t}")
    if c.ok:
        c.info("all 3 tables present")
    checks.append(c)

    # 2. single user -------------------------------------------------------
    c = Check("users: exactly one anchor row")
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM users")
        users = [r[0] for r in cur.fetchall()]
    if len(users) != 1:
        c.fail(f"expected exactly 1 user row, found {len(users)}: {users}")
    elif users[0] != user_id:
        c.fail(f"user id {users[0]!r} != expected {user_id!r}")
    else:
        c.info(f"user {user_id!r}")
    checks.append(c)

    # 3. redis_import completeness ----------------------------------------
    c = Check("redis_import: every export key present, checksum matches stored value")
    with conn.cursor() as cur:
        cur.execute("SELECT redis_key, redis_type, value, checksum FROM redis_import")
        stored = {k: (t, v, cs) for k, t, v, cs in cur.fetchall()}
    missing = [r.key for r in records if r.key not in stored]
    bad = []
    for r in records:
        row = stored.get(r.key)
        if row is None:
            continue
        t, v, cs = row
        actual = imp.content_hash(t, v)
        if actual != r.checksum or cs != r.checksum:
            bad.append(r.key)
    extra = sorted(set(stored) - {r.key for r in records})
    if missing:
        c.fail(f"{len(missing)} keys missing: {missing}")
    if bad:
        c.fail(f"{len(bad)} checksum mismatches: {bad}")
    if extra:
        c.fail(f"{len(extra)} unexpected rows: {extra}")
    if c.ok:
        c.info(f"{len(records)}/{len(records)} keys verified")
    checks.append(c)

    # 4. link counts per provider ------------------------------------------
    exp = expected_links(records)
    c = Check("deck_links: per-provider link counts match export")
    with conn.cursor() as cur:
        cur.execute("SELECT provider, count(*) FROM deck_links "
                    "WHERE match_method='registry_link' GROUP BY provider")
        actual_counts = Counter(dict(cur.fetchall()))
    exp_counts = Counter(r["provider"] for r in exp)
    if actual_counts != exp_counts:
        c.fail(f"expected {dict(exp_counts)}, got {dict(actual_counts)}")
    else:
        c.info(f"{dict(exp_counts)}")
    checks.append(c)

    # 5. URL / external_id byte equality ------------------------------------
    c = Check("deck_links: url/external_id byte equality per (provider, external_id)")
    with conn.cursor() as cur:
        cur.execute("SELECT provider, external_id, url, name_at_source, commander_at_source, "
                    "folder_id, deck_id FROM deck_links WHERE match_method='registry_link'")
        rows = {(p, e): (u, n, cmd, f, d) for p, e, u, n, cmd, f, d in cur.fetchall()}
    mismatches = []
    for r in exp:
        got = rows.get((r["provider"], r["external_id"]))
        if got is None:
            mismatches.append(f"missing {(r['provider'], r['external_id'])}")
            continue
        u, n, cmd, f, d = got
        if u != r["url"] or n != r["name_at_source"] or (cmd or None) != (r["commander_at_source"] or None) \
                or f != r["folder_id"] or d != r["deck_id"]:
            mismatches.append(f"{(r['provider'], r['external_id'])}: {got} != expected "
                              f"{(r['url'], r['name_at_source'], r['commander_at_source'], r['folder_id'], r['deck_id'])}")
    if mismatches:
        for m in mismatches[:20]:
            c.fail(m)
        if len(mismatches) > 20:
            c.fail(f"... and {len(mismatches) - 20} more")
    else:
        c.info(f"{len(exp)} links byte-equal")
    checks.append(c)

    # 6. physical count ------------------------------------------------------
    c = Check(f"deck_links: physical count (Archidekt folder {PHYSICAL_FOLDER_ID})")
    # Physical iff the deck's Archidekt parent folder is 588380. folder_id is
    # stamped on every provider link row, so count distinct deck_ids across
    # providers (a physical deck linked only via Moxfield still counts).
    exp_physical = len({r["deck_id"] for r in exp
                        if r["folder_id"] == PHYSICAL_FOLDER_ID and r["deck_id"] is not None})
    with conn.cursor() as cur:
        cur.execute("SELECT count(DISTINCT deck_id) FROM deck_links "
                    "WHERE folder_id=%s AND deck_id IS NOT NULL AND match_method='registry_link'",
                    (PHYSICAL_FOLDER_ID,))
        actual_physical = cur.fetchone()[0]
    if actual_physical != exp_physical:
        c.fail(f"expected {exp_physical}, got {actual_physical}")
    else:
        c.info(f"{actual_physical} physical decks")
    checks.append(c)

    # 7. orphan mapping ------------------------------------------------------
    c = Check("deck_links: orphan stub for every non-decks export key (content byte-equal)")
    orphans = {r.key: r for r in records if r.key != imp.DECKS_KEY}
    with conn.cursor() as cur:
        cur.execute("SELECT external_id, deck_id, provider, url, name_at_source, raw "
                    "FROM deck_links WHERE match_method='orphan_stub'")
        stubs = {e: (d, p, u, n, raw) for e, d, p, u, n, raw in cur.fetchall()}
    bad = []
    for k, rec in orphans.items():
        stub = stubs.get(k)
        if stub is None:
            bad.append(f"{k}: stub missing")
            continue
        deck_id, provider, url, name, raw = stub
        exp_provider = k.split(":")[0] if ":" in k else "orphan"
        if deck_id is not None:
            bad.append(f"{k}: deck_id {deck_id!r} not NULL")
        if provider != exp_provider:
            bad.append(f"{k}: provider {provider!r} != {exp_provider!r}")
        if url != k:
            bad.append(f"{k}: url {url!r} != {k!r}")
        if name != k:
            bad.append(f"{k}: name_at_source {name!r} != {k!r}")
        if not isinstance(raw, dict) or raw.get("key") != k or raw.get("type") != rec.type \
                or raw.get("value") != rec.value:
            bad.append(f"{k}: raw JSONB does not hold the original key/type/value")
    if bad:
        for m in bad[:20]:
            c.fail(m)
        if len(bad) > 20:
            c.fail(f"... and {len(bad) - 20} more")
    else:
        c.info(f"{len(orphans)} orphan keys stubbed; url/name/provider/raw content byte-equal")
    checks.append(c)

    # 8. random sample -------------------------------------------------------
    sample = list(records)
    random.Random(seed).shuffle(sample)
    sample = sample[:sample_size]
    c = Check(f"random sample: {len(sample)} records (seed {seed}) re-verified")
    bad = []
    for r in sample:
        row = stored.get(r.key)
        if row is None or imp.content_hash(row[0], row[1]) != r.checksum:
            bad.append(r.key)
    if bad:
        c.fail(f"sample mismatches: {bad}")
    else:
        c.info(f"{len(sample)} sampled records match")
    checks.append(c)

    # 9. idempotency: second sync adds zero rows -----------------------------
    c = Check("idempotency: second import adds zero deck_links / redis_import rows")
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM deck_links")
        links_before = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM redis_import")
        import_before = cur.fetchone()[0]
    try:
        s2 = imp.run_import(conn, list(records), dry_run=False, skip_invalid=False)
        imp.import_decks_from_records(conn, list(records), user_id)
    except Exception as e:
        # A tampered redis_import value raises IntegrityError here; that must
        # surface as a failed check, not crash the verifier before the report.
        c.fail(f"second import raised {type(e).__name__}: {e}")
        checks.append(c)
        return checks
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM deck_links")
        links_after = cur.fetchone()[0]
        cur.execute("SELECT count(*) FROM redis_import")
        import_after = cur.fetchone()[0]
    if links_after != links_before:
        c.fail(f"deck_links {links_before} -> {links_after}")
    if import_after != import_before:
        c.fail(f"redis_import {import_before} -> {import_after}")
    if s2.inserted or s2.updated:
        c.fail(f"second import reported inserted={s2.inserted} updated={s2.updated}")
    if c.ok:
        c.info(f"deck_links={links_after}, redis_import={import_after}, "
               f"second import unchanged={s2.unchanged}")
    checks.append(c)

    return checks


def render_report(checks: list[Check], export_path: str, n_records: int, started: datetime) -> str:
    status = "PASS" if all(c.ok for c in checks) else "FAIL"
    lines = [
        "# Redis → PostgreSQL cutover verification",
        "",
        f"- **Result: {status}**",
        f"- Export: `{export_path}` ({n_records} keys)",
        f"- Run at: {started.strftime('%Y-%m-%d %H:%M:%S %Z')}",
        f"- Verifier: `scripts/verify_cutover.py` (no hardcoded counts; all expectations derived from the export)",
        "",
        "| # | Check | Result |",
        "|---|---|---|",
    ]
    for i, c in enumerate(checks, 1):
        lines.append(f"| {i} | {c.name} | {'✅ PASS' if c.ok else '❌ FAIL'} |")
    lines += ["", "## Details", ""]
    for i, c in enumerate(checks, 1):
        lines.append(f"### {i}. {c.name} — {'PASS' if c.ok else 'FAIL'}")
        for ln in c.lines:
            lines.append(f"- {ln}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--export", required=True, help="cutover Redis export JSON (with values)")
    p.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    p.add_argument("--user-id", required=True, help="the single lab user id used at import")
    p.add_argument("--sample-size", type=int, default=100)
    p.add_argument("--seed", type=int, default=20261024)
    p.add_argument("--report", help="write a markdown report here")
    a = p.parse_args(argv)
    if not a.database_url:
        p.error("DATABASE_URL / --database-url required")

    import psycopg
    started = datetime.now(UTC)
    records = list(imp.records_from_export(a.export))
    conn = psycopg.connect(a.database_url, autocommit=True)
    try:
        checks = run_checks(conn, records, a.user_id, a.sample_size, a.seed)
    finally:
        conn.close()

    ok = all(c.ok for c in checks)
    report = render_report(checks, a.export, len(records), started)
    print(report)
    if a.report:
        Path(a.report).write_text(report + "\n", encoding="utf-8")
        print(f"report written to {a.report}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
