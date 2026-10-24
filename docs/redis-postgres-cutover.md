# Redis → PostgreSQL cutover (Phase 1b)

PostgreSQL now holds the Commander Lab deck-link/user data imported from Redis
with proven zero loss. Reads/writes for deck links switch via a flag while
Redis stays a **read-only** fallback. **No Redis key is ever deleted.**

## Schema

- `users` — one anchor row; every deck/inventory/journal/review/profile row
  references it. Created by alembic migration `a001_create_users_deck_links`.
- `deck_links` — one row per (provider, external_id) link
  (`UNIQUE(provider, external_id)`). Identity is the link, never name or
  commander; there is no merge/dedupe logic. `deck_id` (local registry id) is
  nullable: orphan Redis keys are imported as stub rows with `deck_id NULL`,
  `match_method='orphan_stub'`, and the original key/value in `raw` JSONB.
  A deck is **physical iff** its Archidekt `folder_id` is `588380`.
- `redis_import` — generic landing table holding every Redis key verbatim
  (JSONB value + sha256 checksum). Zero-loss backstop for keys without a
  typed table yet.

## Ownership rules

- **Upstream-owned** columns (`url`, `folder_id`, `name_at_source`,
  `commander_at_source`, `raw`) are overwritten on sync; the importer records
  a content hash (sha256 of canonical `{type, value}`) per row and re-verifies
  it after every batch (see `redis_pg_import.py`).
- **Local-owned** data (physical status/lock, instances, manual tags,
  Archetype/Themes/Folder, journals, ideas, reviews, sims) is never touched
  by the import or by `PgDeckLinksRepository.update`.
- **Upstream deletion** is a tombstone: set `upstream_missing_at`, never
  delete the row.

## The flag

`PG_CUTOVER` (env; `1`/`true`/`yes`; default **off**), evaluated by
`db.config.cutover_enabled()` and applied by
`analytics.deck_links_repository.deck_links_repository()`:

| Flag | Backend | Redis role |
|---|---|---|
| off (default) | Redis registry `decks` key, **read-only** via `RedisDeckLinksRepository` (writes raise `ReadOnlyError`) | authoritative |
| on | PostgreSQL `deck_links` via `PgDeckLinksRepository` | read-only fallback; never written, never deleted |

## Cutover runbook

1. **Backup.** `BGSAVE` on prod Redis, copy `dump.rdb`, and export every key
   (name, type, value, PTTL, sha256 checksum) to JSON **outside the repo**.
   Current prod backup: `/mnt/tower/workspace/personal/backups/commander-lab-redis-20261024T013246Z/`
   (7 keys; re-SCAN before any future cutover — an unmapped pattern is a block).
2. **Migrate.** `DATABASE_URL=... alembic upgrade head` (idempotent DDL).
3. **Import.** `python redis_pg_import.py --from-export <backup>.json --user-id lab --verify`
   (exits 3 on any hash mismatch; `--verify` re-checks every row, exit 2 on mismatch).
4. **Verify.** `python scripts/verify_cutover.py --export <backup>.json --user-id lab
   --report docs/redis-postgres-verification.md`. All expectations are derived
   from the export (no hardcoded counts): per-provider link counts,
   url/external_id byte equality, physical count, orphan stubs, a seeded
   100-record random sample, and a second import adding zero rows. Any
   mismatch exits non-zero. Latest report: `docs/redis-postgres-verification.md`
   (**PASS**, 9/9 checks on the prod export).
5. **Flip.** Set `PG_CUTOVER=1` on the app container and redeploy.
6. **Watch window.** Redis stays read-only for **7 days**. Rollback = unset
   `PG_CUTOVER` (Redis data is intact because nothing was deleted or written).
7. **Later phase.** Route handlers and `sync.py` rewire to the repository
   factory; sync writes revision rows before overwriting upstream-owned
   columns. Only after the window and a clean phase-2 may Redis keys be
   retired (a separate, human-approved step).

## Verification evidence

- Prod export: 7 keys (`comp:presets`, `lab_tags`, `lab_tags_source`,
  `interaction_graph:collection`, `github_changelog_stale`,
  `github_latest_version_stale`, `interactions:runs`) — matches
  `docs/redis-postgres-blockers.md`; no unmapped patterns.
- Verifier **exit 0** on the prod export (report committed).
- Verifier **exit 1** when a `deck_links.url` was deliberately tampered
  (negative test, synthetic database).
