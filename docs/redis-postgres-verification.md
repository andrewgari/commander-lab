# Redis → PostgreSQL cutover verification

- **Result: PASS**
- Export: `/mnt/tower/workspace/personal/backups/commander-lab-redis-20261024T013246Z/export.json` (7 keys)
- Run at: 2026-10-24 01:34:44 UTC
- Verifier: `scripts/verify_cutover.py` (no hardcoded counts; all expectations derived from the export)

| # | Check | Result |
|---|---|---|
| 1 | schema: users, deck_links, redis_import exist | ✅ PASS |
| 2 | users: exactly one anchor row | ✅ PASS |
| 3 | redis_import: every export key present, checksum matches stored value | ✅ PASS |
| 4 | deck_links: per-provider link counts match export | ✅ PASS |
| 5 | deck_links: url/external_id byte equality per (provider, external_id) | ✅ PASS |
| 6 | deck_links: physical count (Archidekt folder 588380) | ✅ PASS |
| 7 | deck_links: orphan stub for every non-decks export key | ✅ PASS |
| 8 | random sample: 7 records (seed 20261024) re-verified | ✅ PASS |
| 9 | idempotency: second import adds zero deck_links / redis_import rows | ✅ PASS |

## Details

### 1. schema: users, deck_links, redis_import exist — PASS
- all 3 tables present

### 2. users: exactly one anchor row — PASS
- user 'lab'

### 3. redis_import: every export key present, checksum matches stored value — PASS
- 7/7 keys verified

### 4. deck_links: per-provider link counts match export — PASS
- {}

### 5. deck_links: url/external_id byte equality per (provider, external_id) — PASS
- 0 links byte-equal

### 6. deck_links: physical count (Archidekt folder 588380) — PASS
- 0 physical decks

### 7. deck_links: orphan stub for every non-decks export key — PASS
- 7 orphan keys stubbed (deck_id NULL, raw.key round-trips)

### 8. random sample: 7 records (seed 20261024) re-verified — PASS
- 7 sampled records match

### 9. idempotency: second import adds zero deck_links / redis_import rows — PASS
- deck_links=7, redis_import=7, second import unchanged=7

