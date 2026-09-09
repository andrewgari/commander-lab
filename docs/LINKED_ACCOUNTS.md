# Linked Accounts

`linked_accounts.py` is a registry of provider accounts (Archidekt/Moxfield
usernames) that Commander Lab can pull decks from on a recurring or manual
basis, layered on top of the existing single-deck import flow.

## Storage

All state lives in a single Redis key, `linked_accounts`, holding a JSON
array of account records. Reads/writes to this key go exclusively through
this module (mirroring the pattern used by `instances.py`, `registry.py`,
and `cards.py`).

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
pull-only/non-destructive design below, unlinking is purely a
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

## Pull-only architecture

Linked accounts are **pull-only**. Commander Lab only ever reads decks from
Archidekt/Moxfield via each provider's `list_decks`/`fetch_deck`; it never
writes back to the provider. Nothing in this module (or the deck sync flow
it drives) pushes local edits, card assignments, or reconciliation results
back to Archidekt or Moxfield. Unlinking an account is likewise
non-destructive locally: it only removes the registry entry, never the
decks already pulled from it.
