# Integration Migration Guide — moving existing integrations onto the Provider pattern

Status: draft (architect, t_c0c3217f). Companion to
`provider-integration-spec.md` (t_61c41cfb); this is the operational
step-by-step. Verified against the repo @ 2e2cc4e.

## 0. What "migrating an integration" means here

Each existing integration moves behind the uniform `Provider` protocol
(`fetch_deck` / `list_decks` / `push_tags` / `health`, capabilities,
`ProviderError`) in `providers/`. The hard invariants, in force for every
step below:

1. **Redis keys never change shape** in migration PRs: `decks`, `inventory`,
   `card:*`, `card_tags:*`, `lab_tags`, `tag_vocabulary`, `deck_status:*`
   keep their current formats. Adapters must write exactly what today's
   code writes; only the *call path* changes.
2. **External links are derived, never stored.** Archidekt/Moxfield/
   CommanderSalt links in `templates/deck.html:514-528` and
   `templates/decks.html:416-418` are built from `deck.id` /
   `deck.moxfield_id` (i.e. `source` + `source_id`, see
   `providers/__init__.py::registry_id`). As long as `source_id` values are
   untouched, no external link can break. Do not rename or reformat these
   fields during migration.
3. **Lab-owned data is never re-derived by a sync pass**:
   `deck_status:*`, physical statuses, and `card_tags:*`/`lab_tags` are
   Commander Lab's source of truth (ownership matrix in the spec §2).

## 1. Pre-flight (once, before PR-1)

- [ ] Snapshot Redis: `redis-cli --rdb /tmp/pre-migration.rdb` (or
      `BGSAVE` + copy). The migration is designed to be key-compatible, but
      this makes rollback trivial.
- [ ] Record baseline counts for later comparison:
      `LEN`-equivalents — number of decks in `decks`, number of
      `deck_status:*` keys, number of `card_tags:*` keys.
- [ ] Run a full `python sync.py` and one `POST /api/sync-tags` on a test
      deck; save the resulting deck JSON + Archidekt tag state as the golden
      reference for post-migration diffing.
- [ ] Decide CommanderSalt's fate (owner: Andrew — see §4). Nothing else in
      the migration blocks on this, but PR-1 ships the decision.

## 2. Archidekt — step by step

Archidekt is the only integration with both a read and a write path, so it
gets two PRs.

### PR-2: write path (`push_tags`)

Current state: tag-push logic lives in `app.py:547`
(`sync_deck_tags_to_archidekt`) and is duplicated in
`scripts/sync_to_archidekt.py`.

1. Move the cookie+CSRF auth and the tag-push HTTP calls from
   `app.py::sync_deck_tags_to_archidekt` into
   `providers/archidekt.py::push_tags(deck_id, tags)` unchanged in behavior
   (same endpoints, same payload shape, same env vars).
2. Reduce `POST /api/sync-tags` in app.py to: resolve deck →
   `providers.fetch`-style dispatch → `archidekt.push_tags(...)` → map
   `ProviderError` to the existing HTTP error responses. Keep the route's
   request/response contract identical — the deck page JS depends on it.
3. Rewrite `scripts/sync_to_archidekt.py` as a thin CLI over
   `archidekt.push_tags` (or delete it). Two copies of auth logic is the
   drift bug this removes.
4. Add `archidekt.health()`: GET one known public deck (or the account's
   deck list) with a 5s timeout; `ok=False` on 401/403 with detail
   "cookie expired" — this is the silent-expiry failure made visible.
5. Verify: push tags to one test deck via the API route and via the script;
   diff Archidekt-side tags against the pre-flight golden reference.
   `card_tags:*` keys must be byte-identical before/after.

Rollback: revert the PR. No Redis writes differ, so no data step is needed.

### PR-3: read path (`list_decks` + `fetch_deck`)

Current state: `sync.py` does account enumeration and per-deck fetch
inline (no timeouts at lines 43, 57, 126; serial loop with `sleep(0.5)`;
`raise_for_status()` aborts the whole sync on one bad deck).

1. Move the folder enumeration + per-deck HTTP into
   `providers/archidekt.py::list_decks(account)`; keep the `sleep(0.5)`
   rate limiting inside the adapter (it's an Archidekt politeness rule,
   not orchestration).
2. Add explicit `timeout=20` to every moved call (matches the existing
   `providers/archidekt.py:28` convention).
3. Change per-deck failure handling: one deck's `raise_for_status` becomes
   a logged, skipped deck with a counter in the sync summary — never an
   abort of the whole run. This changes failure behavior only; successful
   output is identical.
4. sync.py becomes orchestration: `list_decks` → per-deck `fetch_deck` →
   the existing Redis write code, untouched. Do not refactor the Redis
   write code in this PR.
5. Keep the `migration:legacy_card_keys_purged` guard (sync.py:15)
   byte-for-byte; do not widen scope into the card-keyspace migration.
6. Verify: run sync against production Redis copy; diff `decks`,
   `inventory`, `tag_vocabulary` against the pre-flight snapshot — only
   expected upstream changes may differ.

Rollback: revert. Again no key-format change, so rollback is code-only.

## 3. Moxfield — step by step

Read-only, already an adapter (`providers/moxfield.py`). Small PR:

1. Add `moxfield.health()`: GET a tiny known public deck, 5s cap. On
   non-200, detail must say "api2 endpoint unreachable/changed (HTTP nnn)"
   — this endpoint is unofficial; the health check is our early warning.
2. Add a module docstring noting `api2.moxfield.com` is unofficial and may
   change or 403 without notice.
3. Declare `capabilities = {READ_DECKS}` (no `list_decks` — Moxfield bulk
   enumeration isn't in the app today; don't add it speculatively).
4. Annotate `commander_uids` as `scryfall_id` scheme per spec §3.4
   (Archidekt's are `archidekt_uid`) — do this in the same PR as the
   Archidekt annotation so consumers see one change.
5. Verify: fetch one Moxfield deck by URL and by bare publicId; confirm
   normalized shape unchanged and the deck page's Moxfield link still
   renders (it uses `deck.moxfield_id`, untouched).

Rollback: revert; no writes exist for this provider.

## 4. CommanderSalt — the decision step

Verified current state: **there is no runtime CommanderSalt integration.**
`commandersalt` appears only in the copy-pasted `ENABLED_PROVIDERS`
default (app.py:25, 30, 49) and in template JS that builds an outbound
link from the *Archidekt* URL (deck.html:519-521, decks.html:416-418).
Any provider dispatch on `commandersalt` raises `ProviderError` today.

Two acceptable resolutions for PR-1:

- **Option A (recommended): remove it.** Delete `commandersalt` from the
  default in the new single `enabled_providers()` helper, and leave the
  template links alone — they are plain outbound links derived from the
  Archidekt deck URL, not an integration. Zero data risk; nothing users
  can see changes.
- **Option B: stub it.** Add `providers/commandersalt.py` implementing the
  protocol with `capabilities = set()` and
  `health() → HealthResult(ok=False, "not implemented", ...)`. Choose this
  only if an implementation exists on another branch that will land soon
  (the root card references `analytics/providers/commandersalt.py`, which
  does not exist in this tree — unverified on main; check before choosing).

Either way: the template links stay as-is in this migration. Moving them
behind provider-driven rendering is a designer/UI change, out of scope.

## 5. Cross-cutting PR-1 checklist (scaffolding, no behavior change)

1. `providers/base.py`: `Capability`, `HealthResult`, `Provider` protocol.
2. `providers/__init__.py::enabled_providers()` — single parse of
   `ENABLED_PROVIDERS`; replace the three duplicates in app.py. Startup
   validation: unknown/unimportable names log loudly and are excluded,
   never raise on the request path.
3. `GET /api/integrations/health` — concurrent `health()` per provider,
   5s per-provider cap, cached in Redis `integration_health:{provider}`
   TTL 5min. Page loads read the cache; a missing/failed entry renders
   "degraded", never silently absent.
4. Resolve CommanderSalt per §4.
5. Verify: all existing tests pass; `/`, `/decks`, one `/deck/...` page,
   and `POST /api/sync-tags` behave identically (this PR must be a no-op
   at runtime).

## 6. Data-loss & link-integrity checklist (run after every PR)

- [ ] `decks`, `inventory`, `tag_vocabulary` diff vs pre-flight snapshot:
      only upstream-source drift allowed.
- [ ] `deck_status:*` count unchanged; spot-check 3 decks' statuses
      unchanged.
- [ ] `card_tags:*` count and contents unchanged (PR-2 must not write
      these; sync must not overwrite manual tags).
- [ ] Every deck on `/decks` still renders Archidekt (+ Moxfield where
      applicable) links that resolve — these derive from `source_id`,
      which no migration PR may modify.
- [ ] `integration_health:*` shows each enabled provider; a deliberately
      broken credential shows `ok=False` with a useful detail string
      (proves dead integrations are now visible).

## 7. Explicitly out of scope (do not fold in)

- Legacy `card:{name}` → `card:{oracle_id}` keyspace convergence (tied to
  `migrate_to_instances.py`). Migration adapters must write the oracle_id
  keyspace only, but the convergence itself is a separate program.
- `FOLDER_MAP` (sync.py:82) move to config and first-sync-only
  `deck_status` defaults — spec PR-4, after a read-only audit confirms no
  consumer depends on folder re-derivation.
- Any UI/layout change to how links or health badges render (designer).
- New providers, and the analytics/Spellbook pipeline integrations.
