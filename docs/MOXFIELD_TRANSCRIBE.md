# Transcribe Archidekt Decks to Moxfield

Push **physical** Archidekt decks to Moxfield as private decks in one folder,
each with a generated description.

## Scope: physical decks only

Only decks whose registry `status == "physical"` are transcribed. This is
enforced in two places: `transcribe.eligible_decks()` filters the batch, and
`transcribe_deck()` re-checks per deck and returns
`action: "skipped_not_physical"` — so even a direct call can't push a
digital/testing/retired deck.

That follows the data model: non-physical decks are hypothetical decklists with
no paper footprint (see `docs/PHYSICAL_RESYNC_ADJUDICATION.md`), so they have
no business in a paper-decks folder. `TRANSCRIBE_STATUSES` in `src/server/domain/decks/transcribe.py`
is the one place to widen this.

## Why this needs a browser session

Moxfield has no public write API. These endpoints were read out of Moxfield's
own app bundle (`assets.moxfield.net/assets/assets/moxfield.*.js`) and are
exactly what the site calls:

| Call | Purpose |
|---|---|
| `POST /v1/account/token/refresh` | session cookies -> bearer `access_token` |
| `GET  /v1/deck-folders` | list folders |
| `POST /v1/deck-folders` | create folder (`{"name": ...}`) |
| `POST /v3/decks` | create deck |
| `PUT  /v2/decks/{id}` | set description |

Auth is `Authorization: Bearer <access_token>` + `X-Session-User: <userId>`.
The `PUT` also needs Moxfield's optimistic-concurrency headers
`X-Public-Deck-ID` and `X-Deck-Version`.

`POST /v3/decks` body (verbatim key set from the bundle's `CREATE_DECK`
action): `name`, `format`, `visibility`, `commanderCardId`, `partnerCardId`,
`mainboard`, `sideboard`, `maybeboard`, `importText`,
`commanderSignatureSpellCardId`, `partnerSignatureSpellCardId`,
`pricingProvider`, `setId`, `deckFolderId`, `usePrefPrintings`.

Note there is **no** `description` field on create — that's why the
description is a second `PUT`.

## Setup

### Recommended: paste a token (no installs, no browser automation)

Moxfield's login endpoint (`POST /v2/account/token`) requires a **Cloudflare
Turnstile CAPTCHA token**, so a headless username/password login is impossible
by design. Many hosts (datacenter IPs) are also hard-blocked from loading
`moxfield.com` at all — though `api2.moxfield.com` stays reachable. So the
simplest credential path is to copy a token from a browser where you are
already logged in:

1. Open Moxfield in your normal browser, logged in.
2. DevTools → Application → Local Storage → `https://www.moxfield.com`.
3. Find the entry holding your session and copy `accessToken` and `userId`.
4. Put them in `.env` where the app runs:

```
MOXFIELD_TOKEN=<accessToken>
MOXFIELD_USER_ID=<userId>
```

A leading `Bearer ` is tolerated. Nothing to install, and it works on a
headless server.

Tokens are short-lived. A pasted token cannot be auto-refreshed, so when it
expires the error says exactly that — paste a fresh one.

### Alternative: saved browser session (auto-refreshing)

```
python scripts/setup_auth.py                       # writes ./.auth
python scripts/setup_auth.py --auth-dir ~/cl-auth  # if the repo dir isn't writable
```

Needs a machine **with a display** and `playwright` installed, and its IP must
not be Cloudflare-blocked. Log in to Moxfield in the browser it opens, then
press ENTER. This writes `.auth/state.json` plus `.auth/moxfield.json`
(your `userId`). Cookies from this path *can* be exchanged for a fresh token
automatically, which is its one advantage.

Your password never passes through Commander Lab in either path — the code
only ever reads a token or cookies.

Sessions expire; when they do, transcribe fails with an explicit message
telling you which credential to refresh.

## Usage

UI: `/decks` -> **Transcribe to Moxfield** panel -> *Preview* or
*Transcribe Physical Decks*.

API:

```
GET  /api/transcribe/moxfield   -> {folder, visibility, eligible, decks[]}
POST /api/transcribe/moxfield   -> {success, report}
     body: {"folder": str?, "visibility": str?, "force": bool?, "dry_run": bool?}
```

CLI:

```
python scripts/transcribe_to_moxfield.py --dry-run
python scripts/transcribe_to_moxfield.py
python scripts/transcribe_to_moxfield.py --folder "Paper Decks" --force
```

## What each deck gets

- **Visibility**: `private` (default; `public`/`unlisted` also accepted)
- **Folder**: `Physical` (default). Moxfield only allows letters, numbers,
  spaces and hyphens in folder names — an invalid name is rejected locally
  before any request goes out.
- **Decklist**: the Archidekt cardlist as MTGO text in `importText`, with
  commanders under a `// Commander` header so Moxfield puts them on the
  commander board rather than the mainboard. Moxfield resolves card names
  itself, so we don't have to map every name to a Moxfield card id.
- **Description**:

  ```
  Commander: Kaalia of the Vast
  Color identity: White / Black / Red
  Cards: 100

  Transcribed from Archidekt: https://archidekt.com/decks/123456
  ```

## Idempotency

Each transcribed deck is recorded in Redis under
`moxfield_transcribe:{registry_id}`:

```json
{"registry_id": "archidekt:123", "moxfield_id": "...",
 "moxfield_public_id": "...", "moxfield_url": "https://moxfield.com/decks/...",
 "folder": "Physical", "visibility": "private", "transcribed_at": "ISO8601"}
```

A deck with a mapping is skipped (`action: "skipped_already"`) unless
`force=true`. This matters more than it does for the collection import: we
cannot delete a Moxfield deck from here, so a duplicate would be manual
cleanup.

## Failure handling

- A per-deck failure is collected and the batch continues (same as
  `linked_accounts.sync_account`) — one bad deck never aborts the run. Failed
  decks get **no** mapping, so the next run retries them.
- If deck creation succeeds but the description `PUT` fails, the deck is kept
  and the result carries `description_error` rather than losing the deck we
  just created.
- Writes are throttled 1.5s apart (`moxfield_writer.WRITE_DELAY_SECONDS`);
  Moxfield rate-limits writes harder than reads.
- A mid-run 401 triggers exactly one token refresh and replay, then gives up
  (no retry loop).
- `dry_run=true` contacts Moxfield not at all — no folder is created, no token
  is even fetched.

## Files

- `src/server/infra/providers/moxfield_writer.py` — auth + folder/deck writes
- `src/server/domain/decks/transcribe.py` — eligibility, MTGO text, description, idempotent mapping
- `src/server/app/main.py` — `/api/transcribe/moxfield` (GET status, POST run)
- `templates/decks.html` — transcribe panel
- `scripts/transcribe_to_moxfield.py` — CLI
- `scripts/setup_auth.py` — session capture (now also saves the Moxfield userId)
- `tests/test_moxfield_writer.py`, `tests/test_transcribe.py`,
  `tests/test_transcribe_routes.py`
