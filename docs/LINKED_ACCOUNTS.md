# Linked Accounts (pull-only sync)

Commander Lab can sync a user's entire deck collection from Archidekt or
Moxfield by "linking" that user's provider account via **username**, rather
than importing decks one at a time by URL/id.

## Design

- **Username-based linking.** A linked account is just `(provider, username)`.
  There is no OAuth handshake, API key, or stored credential — usernames are
  public, and both providers expose enough unauthenticated surface area to
  resolve a username to that user's public decks.
- **`list_decks(username)` provider capability.** Each provider module in
  `providers/` implements:
  - `fetch_deck(identifier)` — fetch + normalize a single deck by id/URL
    (used for one-off imports).
  - `list_decks(username)` — return every deck identifier owned by
    `username`, so the whole account can be resynced by calling
    `fetch_deck` on each id in turn.

  `providers/__init__.py` dispatches both by provider name via `PROVIDERS`.

- **Pull-only.** Linked-account sync only ever *reads* from Archidekt/
  Moxfield. Commander Lab never creates, edits, or deletes decks on either
  service, and never writes anything back. All local state (tags, folders,
  physical-instance tracking, etc.) lives only in Commander Lab's own
  registry and is layered on top of the pulled deck data. Unlinking an
  account is likewise non-destructive locally: it only removes the registry
  entry, never the decks already pulled from it.

- **Fail loudly on a bad username.** Both providers raise a specific,
  descriptive error (`ArchidektUserNotFound` / `MoxfieldUserNotFound`)
  instead of silently returning an empty list when a username can't be
  resolved to real decks:
  - Archidekt: `/api/users/?username=...` returns no matching account.
  - Moxfield: the search API has no real "does this user exist" check and
    silently falls back to an unfiltered global feed for bad usernames, so
    `list_decks` keeps only the decks whose `authors` include the requested
    username, ignoring unrelated entries, and raises only if no deck across
    any page lists that user as an author.

## Storage

All state lives in a single Redis key, `linked_accounts`, holding a JSON
array of account records. Reads/writes to this key go exclusively through
`linked_accounts.py` (mirroring the pattern used by `instances.py`,
`registry.py`, and `cards.py`).

### Account record shape

Every account record — whether freshly linked, freshly synced, or updated
after a failed sync — has this shape:

| field              | type                                  | notes |
|--------------------|----------------------------------------|-------|
| `id`               | `str`                                  | `f"{provider}:{username}"` — stable, used for dedup and lookup |
| `provider`         | `str`                                  | `"archidekt"` \| `"moxfield"` |
| `username`         | `str`                                  | provider-native username, as given |
| `enabled`          | `bool`                                 | `sync_all()` only syncs accounts where this is true |
| `created_at`       | `str` (ISO8601 UTC)                    | set once, at link time |
| `last_synced_at`   | `str` (ISO8601 UTC) \| `None`          | set on every sync attempt (success or failure) |
| `last_sync_result` | `{"decks_synced": int, "failures": [...]}` \| `None` | see below |

`failures` is a list of `{"deck_id": str | None, "error": str}`. A `None`
`deck_id` means the failure happened before any per-deck work started
(e.g. `list_decks` itself failed) rather than on a specific deck.

This is the **only** shape returned or persisted by `add_account`,
`sync_account`, and `sync_all` — including their exception/failure paths.
Callers never need to branch on a differently-shaped error object.

## Semantics

### `add_account(r, provider, username)`
Links a new account. Dedups on `id`; raises `LinkedAccountError` if the
account is already linked. Probes with a single `list_decks` call before
persisting, so a typo'd/nonexistent username fails fast instead of leaving
a bad entry in the registry. New accounts start `enabled=True`.

### `remove_account(r, account_id)`
Removes the registry entry only. Never touches decks already pulled from
that account, and never clears their `linked_account_id` stamp — per the
pull-only/non-destructive design above, unlinking is purely a
registry-bookkeeping operation.

### `sync_account(r, account_id)`
Pulls every deck for one linked account: `list_decks` + `fetch_deck` +
`registry.upsert_deck` per deck, stamping `linked_account_id` on each
upserted deck. One bad deck (or a failed `list_decks` call) does not abort
the whole sync — failures are collected into `last_sync_result.failures`
instead. Always updates and persists `last_synced_at` / `last_sync_result`
on the account record, and returns that record.

### `sync_all(r)`
Runs `sync_account` for every **enabled** account. Returns the list of
updated account records, in the standard account record shape described
above. If syncing one account raises unexpectedly (e.g. the provider is
completely unreachable and the failure escapes `sync_account`'s own
handling), that account's record still gets `last_synced_at` /
`last_sync_result` populated to reflect the failure (with the error
recorded under `last_sync_result.failures`) and is persisted and returned
in the same shape as every other account — never a differently-shaped
error object. One bad account never stops the rest of the run.

## Adding a new provider

A new provider only needs to implement `fetch_deck(identifier)` and
`list_decks(username)` with the same contract (raise a clear error rather
than returning an empty list for a bad username) and register itself in
`providers/PROVIDERS`.
