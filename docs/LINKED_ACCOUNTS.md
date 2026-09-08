# Linked Accounts: pull-only full-account sync — Design

## Goal
Let the user register one or more external accounts (Archidekt username
and/or Moxfield username) with Commander Lab. Each linked account is
periodically (and on-demand) resynced: Lab pulls every deck owned by that
account and keeps a durable local record per deck — name, full decklist,
metadata, and a link back to the external source. This supersedes the
one-deck-at-a-time `/api/import` as the *primary* way decks enter Lab, while
`/api/import` (single deck by URL/id) stays available for importing someone
else's public deck ad hoc.

**Hard requirement, explicit from the user: PULL ONLY.** Lab must never
write back to Archidekt or Moxfield. This retires `scripts/sync_to_archidekt.py`
and the `/api/sync-tags` push endpoint's *write* path — Lab Tags remain a
Lab-only concept (see `docs/LAB_TAGS_IMPLEMENTATION.md`); syncing them
outward to Archidekt is a write and must stop. `ARCHIDEKT_SESSION` /
`ARCHIDEKT_CSRF` (the write-auth cookies) are no longer needed by anything
except the piece being removed.

## Why this isn't a small tweak to existing `sync.py`
`sync.py` already does account-wide Archidekt pull today, but it:
- Only supports Archidekt (env var `ARCHIDEKT_USERNAME`, singular, one
  account, edited by hand in `.env` and requiring a redeploy to change).
  Moxfield only has single-deck import via `/api/import`, never full-account.
- Writes on top of the *same* `decks` Redis key and one implicit "the"
  account — there's no concept of "which linked account does this deck
  belong to", multiple accounts, or user-driven add/remove of accounts from
  the UI.
- Is a standalone script run manually (`python sync.py`), not reachable from
  the API/UI at all.

This design generalizes that into a first-class **Linked Accounts** registry
(add/remove from the UI, any number of accounts across both providers),
reusing the existing per-deck `providers/*.fetch_deck` and
`registry.upsert_deck` machinery for the actual pull — this is additive
infrastructure around already-proven code, not a rewrite of deck fetching.

## Data model (additive — new keys only)

### `linked_accounts` (Redis key, JSON array)
```json
[
  {
    "id": "archidekt:CovaDax",
    "provider": "archidekt",
    "username": "CovaDax",
    "added_at": "2026-09-08T12:00:00Z",
    "last_synced_at": "2026-09-08T13:00:00Z",
    "last_sync_result": {"decks_found": 53, "decks_new": 2, "decks_updated": 51, "errors": []},
    "enabled": true
  }
]
```
`id` = `f"{provider}:{username}"`, stable, used as the linkage key. No
tokens/cookies stored here — public username-based read APIs only (per the
mtg-apis skill: Archidekt's `/api/decks/v3/?ownerId=` and Moxfield's
`/v2/decks/search?authorUserNames=` both work unauthenticated for public
decks/profiles). If a future private-account case needs auth, that's a
separate, explicitly-scoped follow-up — not built here.

### Deck records gain a `linked_account_id` field
Every deck upserted via account sync (as opposed to single-deck
`/api/import`) is stamped with `linked_account_id` = the owning account's
`id`. Decks imported via ad hoc `/api/import` (someone else's public deck,
or a deck not owned by any linked account) keep `linked_account_id: null` —
unchanged from today. This is how the UI distinguishes "your synced
collection" from "one-off imports" without needing a second deck list.

No other existing deck field changes shape. `source`/`source_id`/
`registry_id`/`cards`/`status` all stay exactly as `registry.upsert_deck`
already produces them — account sync is just upsert_deck called in a loop
per discovered deck, same as single import does for one.

## New provider capability: `list_decks(username) -> [identifier, ...]`
Both provider modules gain a `list_decks(username)` function alongside the
existing `fetch_deck(identifier)`:

- **`providers/archidekt.py`**: resolve username -> user id via
  `GET archidekt.com/api/users/?username={username}`, then page through
  `GET archidekt.com/api/decks/v3/?ownerId={id}&pageSize=100` (existing
  `sync.py` logic, lifted into the provider module so both the old script's
  logic and the new account-sync path share one implementation). Returns a
  list of deck ids (strings).
- **`providers/moxfield.py`**: page through
  `GET api2.moxfield.com/v2/decks/search?pageSize=100&pageNumber=N&authorUserNames={username}`
  while `pageNumber < totalPages` (see mtg-apis skill, confirmed live
  2026-09). Returns a list of `publicId`s.

`providers/__init__.py` gets a matching `list_decks(provider, username)`
dispatcher, mirroring the existing `fetch_deck(provider, identifier)` shape.

## New module: `linked_accounts.py`
Mirrors the `instances.py`/`registry.py`/`cards.py` pattern:
- `list_accounts(r) -> list`
- `add_account(r, provider, username) -> dict` — validates provider is
  known, checks the account isn't already linked (dedup on `id`), does a
  cheap existence probe (one `list_decks` call) so a typo'd username fails
  immediately with a clear error instead of silently syncing zero decks
  forever, then persists with `enabled: true`.
- `remove_account(r, account_id) -> None` — removes the linked_accounts
  entry. **Does not delete or unlink the decks it previously synced** —
  those decks and their instances stay exactly as they are (they simply
  stop receiving pulled updates); their `linked_account_id` is left as-is
  for historical provenance rather than nulled out, since "this deck came
  from that account" remains a true fact even after unlinking. Flag this
  explicitly in the UI copy so it isn't a surprise.
- `sync_account(r, account_id) -> dict` — the actual pull: `list_decks` for
  the account's provider+username, `fetch_deck` + `registry.upsert_deck`
  each one (stamping `linked_account_id`), updates
  `last_synced_at`/`last_sync_result` on the account record. Returns a
  summary dict `{decks_found, decks_new, decks_updated, errors: [...]}` —
  per-deck fetch failures are collected and reported, not fatal to the
  whole sync (one bad deck shouldn't block 52 good ones).
- `sync_all(r) -> dict` — runs `sync_account` for every `enabled` account,
  returns a summary keyed by account id. This is what the scheduled sync
  calls.

## New API surface
- `GET  /api/linked-accounts` — list accounts + their last sync status.
- `POST /api/linked-accounts` — `{"provider": "archidekt"|"moxfield",
  "username": "..."}` → validates + adds + immediately triggers first sync
  (so adding an account produces decks right away, not after waiting for
  the next scheduled tick).
- `DELETE /api/linked-accounts/{account_id}` — unlink (per the semantics
  above — decks stay, sync stops).
- `POST /api/linked-accounts/{account_id}/sync` — manual re-sync one
  account on demand.
- `POST /api/linked-accounts/sync-all` — manual re-sync every enabled
  account on demand (for a "sync now" button, independent of the schedule).

## Scheduling the periodic pull
Reuse the existing hourly commander-lab cron pattern already set up for
this project (`~/.hermes/scripts/commander_lab_deck_view_dispatch.sh` +
cron job `b33f2ed323f7`) as the template: a small standalone script,
`scripts/sync_linked_accounts.py`, calls `linked_accounts.sync_all(r)`
against the deployed Redis (`REDIS_URL` from `.env`, same as every other
script in this repo) and exits — no FastAPI dependency, no LLM involved.
Scheduled via a **new**, separate cron job (`no_agent` script mode, hourly
or whatever cadence the user wants) rather than piggybacking on the
existing kanban-dispatch cron, since this is a completely different concern
(data sync vs. task-queue nudging) that happens to share an hourly cadence
by coincidence. Ask before creating it — cadence (hourly vs daily) and
whether it should run against the Tower-deployed instance or wherever
Redis actually lives in prod is a real decision, not a default to assume.

## What gets removed / stopped (the "no longer push" requirement)
- `scripts/sync_to_archidekt.py` — deleted. Its only job was pushing Lab
  Tags to Archidekt as deck categories; that's the write path being retired.
- `POST /api/sync-tags` in `app.py` — removed (it invoked the same push
  logic inline). `GET /api/tags` and `POST /api/card/{name}/tags` (managing
  Lab Tags themselves, not pushing them anywhere) are untouched — Lab Tags
  remain fully functional as a Lab-only concept per the existing design.
- `templates/tags.html`'s "Sync All Decks to Archidekt" / "Sync Selected
  Decks" buttons — removed from the UI along with the endpoint.
- `ARCHIDEKT_SESSION` / `ARCHIDEKT_CSRF` env vars — no longer read anywhere
  after the above removals; can be dropped from `.env` (not automated here,
  flagging for the user to clean up since they're credentials).
- `docs/TAG_MANAGEMENT.md` / `docs/LAB_TAGS_IMPLEMENTATION.md` — both
  describe the push-to-Archidekt workflow at length; need a doc pass
  striking the "Sync to Archidekt" sections once the code lands, so the
  docs don't describe a removed capability.

`sync.py` (the original standalone one-account Archidekt pull script) stays
as-is for now — it's superseded in practice by
`linked_accounts.sync_account`/`sync_all` but nothing requires deleting it
immediately; flagged as safe cleanup once the new path is proven, not a
blocker for this feature.

## UI
New section on `/decks` (or a new `/accounts` page — small enough either
works, default to adding it as a card at the top of `/decks`): list linked
accounts with provider icon, username, last synced time, deck count, a
"Sync now" button per account, a "Remove" button (with the
decks-stay-linked caveat shown inline), and an "Add account" form
(provider dropdown + username field). No credential fields anywhere in this
UI — usernames only, matching the pull-only/unauthenticated-read design.

## Decisions (confirmed 2026-09-08)
1. **Private accounts/decks are out of scope** for v1 — same limitation
   flagged above, confirmed acceptable. Public decks/profiles only.
2. **Seed account:** `archidekt:CovaDax` (same username as the existing
   `.env` `ARCHIDEKT_USERNAME`) AND `moxfield:CovaDax` (same username,
   confirmed by user — one identity across both platforms). Both should be
   seeded as `linked_accounts` entries as part of the migration so history
   isn't lost; `upsert_deck`'s existing dedup-by-`registry_id` makes
   re-syncing already-known Archidekt decks a safe no-op refresh.
3. **Sync cadence: hourly.** New standalone cron job (separate from the
   existing commander-lab kanban-dispatch cron), `no_agent` script mode,
   running `scripts/sync_linked_accounts.py` every hour.
4. **Push-path removal confirmed in scope for this feature** — proceed
   with deleting `scripts/sync_to_archidekt.py`, `POST /api/sync-tags`, and
   the "Sync to Archidekt" UI buttons in `templates/tags.html` as part of
   this work, not a separate follow-up.
