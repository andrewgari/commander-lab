# Archiving Existing Moxfield Decks

Rename existing Moxfield decks, set them private, and file them into a holding
folder — so transcribed paper decks aren't buried among old brews.

## Moxfield has no archive feature

There is no archive flag, no soft-delete, no "hidden" state. Reading
Moxfield's entire app bundle, these are the *only* deck-level mutations that
exist:

| Call | What it does |
|---|---|
| `PUT /v2/decks/{id}` | name / visibility / description |
| `POST /v1/deck-folders/{folderId}/decks/{deckId}` | move into folder |
| `DELETE /v1/deck-folders/{folderId}/decks/{deckId}` | remove from folder |
| `POST /v2/decks/{id}/clone` | clone |
| `DELETE /v1/decks/{id}` | **delete — never called by this code** |

So "archive" here means **rename + set private + move into a folder**. That's
the closest thing Moxfield supports, and it's fully reversible.

## Nothing is ever deleted

Two guarantees, both enforced in code and covered by tests:

1. **No delete path exists.** `src/server/domain/decks/archive.py` never references the deck-delete
   endpoint (there's a static test asserting the string `"DELETE"` does not
   appear in the module). `update_deck` sends only
   name/visibility/description/format/comments — never a cardlist — so no card
   can be dropped either.
2. **Originals are recorded before anything changes.** Each deck's pre-archive
   name, visibility and URL go to `moxfield_archive:{deck_id}` *before* the
   first mutating call, so a rename stays reversible even if the run dies
   halfway. An existing backup is never overwritten, so double-archiving can't
   destroy the true original name.

## Usage

```
python scripts/archive_moxfield_decks.py --dry-run   # always do this first
python scripts/archive_moxfield_decks.py --live
```

Options:

| Flag | Effect |
|---|---|
| `--folder NAME` | archive folder (default `Archive`) |
| `--prefix STR` | name prefix (default `[Archived] `) |
| `--keep-visibility` | don't touch visibility (default: set `private`) |
| `--include-transcribed` | also archive decks this app created |
| `--restore DECK_ID` | undo one deck's archive |

API:

```
POST /api/archive/moxfield
     body: {"folder": str?, "prefix": str?, "visibility": str|null?,
            "skip_transcribed": bool?, "dry_run": bool?}
POST /api/archive/moxfield/{deck_id}/restore
```

## Behaviour

- **Private decks are included.** Listing uses `/v2/decks/personal/search`
  (the authenticated "My Decks" endpoint), not the public
  `/v2/decks/search` — otherwise a private deck would be silently skipped.
- **Idempotent.** A deck whose name already starts with the prefix is skipped,
  so re-running never yields `[Archived] [Archived] Foo`.
- **Transcribed decks are protected.** Decks recorded in
  `moxfield_transcribe:*` (created by `src/server/domain/decks/transcribe.py`) are skipped by default —
  archiving the paper decks you just pushed would defeat the purpose. Override
  with `--include-transcribed`.
- **Partial failure is survivable.** A per-deck failure is collected and the
  batch continues. Failed decks still get a backup row, so they're recoverable
  and retryable.
- **Folder-move failure is non-fatal.** If the rename succeeds but filing
  fails, the deck stays renamed and the result carries `folder_error` — the
  alternative (rolling back) would mean another write against a deck we just
  failed to reach.
- **`--dry-run` only lists.** No folder is created and no deck is modified.

## Undo

```
python scripts/archive_moxfield_decks.py --restore <DECK_ID>
```

Restores the original name and visibility from the backup. The deck stays in
the archive folder — folder membership is harmless filing, and leaving it
avoids a second write. Deck ids are printed in the archive run's output and
stored in `moxfield_archive:{deck_id}`.

## Files

- `src/server/domain/decks/archive.py` — archive/restore logic, backups, protection rules
- `src/server/infra/providers/moxfield_writer.py` — `list_my_decks`, `update_deck`,
  `add_deck_to_folder`
- `src/server/app/main.py` — `/api/archive/moxfield`, `/api/archive/moxfield/{id}/restore`
- `scripts/archive_moxfield_decks.py` — CLI
- `tests/test_archive.py`, `tests/test_archive_routes.py`
