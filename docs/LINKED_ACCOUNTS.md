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
  registry and is layered on top of the pulled deck data.

- **Fail loudly on a bad username.** Both providers raise a specific,
  descriptive error (`ArchidektUserNotFound` / `MoxfieldUserNotFound`)
  instead of silently returning an empty list when a username can't be
  resolved to real decks:
  - Archidekt: `/api/users/?username=...` returns no matching account.
  - Moxfield: the search API has no real "does this user exist" check and
    silently falls back to an unfiltered global feed for bad usernames, so
    `list_decks` additionally verifies that every returned deck's `authors`
    actually includes the requested username before accepting the results.

## Adding a new provider

A new provider only needs to implement `fetch_deck(identifier)` and
`list_decks(username)` with the same contract (raise a clear error rather
than returning an empty list for a bad username) and register itself in
`providers/PROVIDERS`.
