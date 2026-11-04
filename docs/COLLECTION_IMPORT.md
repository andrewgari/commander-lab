# Collection Import (Archidekt)

Pull your Archidekt **card collection** (not decks) into Commander Lab as
owned card instances.

## Why this needs cookies

Archidekt has no public collection API. Every candidate route is auth-gated
(verified live, 2026-09):

| Route | Unauthenticated result |
|---|---|
| `GET /api/collection/{userId}/` | `400 ["No public collection found."]` |
| `GET /api/collection/v2/{userId}/` | `401 {"detail":"Authentication credentials were not provided."}` |
| `GET /api/users/{id}/collection/` | `404 {"error":"Unknown API route"}` |
| `POST /api/collection/export/v2/{userId}/` | `400 {"error":"No collection found."}` |

The last one is the route Archidekt's own "Collection data viewer" uses, and
it works with a logged-in session — so that's what we use, with the same
`sessionid`/`csrftoken` cookie pair the tag push (`/api/sync-tags`) already
relies on.

## Setup

Add to `.env`:

```
ARCHIDEKT_USERNAME=CovaDax
ARCHIDEKT_SESSION=<sessionid cookie>
ARCHIDEKT_CSRF=<csrftoken cookie>
```

Get the cookies from a logged-in browser: DevTools → Application → Cookies →
`https://archidekt.com` → copy the values of `sessionid` and `csrftoken`.
They expire; when they do, the import fails with an explicit "refresh
ARCHIDEKT_SESSION/ARCHIDEKT_CSRF" error rather than a silent empty result.

## Usage

UI: `/instances` → **Archidekt Collection** panel → *Preview* (diff only) or
*Import collection*.

API:

```
GET  /api/collection/sync          -> {credentialed, username, last_sync}
POST /api/collection/import        -> {success, username, report, synced_at}
     body: {"username": str?, "dry_run": bool?}
```

CLI:

```
python scripts/import_collection.py --dry-run --live
python scripts/import_collection.py
```

## Data model

One physical copy = one instance (`instances.py` is the ownership source of
truth), so a collection row with quantity 3 becomes 3 `in_collection`
instances of that exact printing, carrying set code, set name, collector
number, finish, condition, language, `archidekt_uid`, purchase price and
acquisition date, with `source="archidekt_collection"`.

### Matching (why re-import is safe)

Instances are matched to remote rows by **printing signature**:

```
(card_name, set, collector_number, finish, language)
```

Condition is deliberately excluded — editing a card's condition in Archidekt
must not fork an existing copy into a second instance. Archidekt can also
split one printing across several rows (e.g. by condition); those rows are
summed before matching for the same reason.

Existing copies are counted across `in_collection`, `in_deck` **and**
`in_mail`: a card sleeved in a physical deck is still a copy you own and
Archidekt still counts it, so counting only `in_collection` would duplicate
every card in a built deck on every import. Only the shortfall is created,
which makes the import idempotent — running it twice creates nothing the
second time.

`not_owned` wishlist placeholders for the same card are **promoted** to
`in_collection` (and stamped with the printing details) before any new
instance is created, mirroring `instances.auto_bind_physical`. An imported
collection resolves a wishlist entry instead of leaving it dangling next to a
real copy.

### Never destructive

Copies present locally but absent remotely are reported as `surplus` and
otherwise left completely alone — no deletion, no status transition. Same
principle as the physical-deck resync diff
(`docs/PHYSICAL_RESYNC_ADJUDICATION.md`): removing real cardboard is a human
decision. `dry_run=true` produces the identical report while writing nothing.

Cards with no remote row at all are out of scope for the diff (the
reconciler only indexes the card names the remote mentioned), so unrelated
inventory never shows up as surplus noise.

## Sync record

The last import is stored at Redis key `collection_sync:archidekt`:

```json
{"provider": "archidekt", "username": "...", "synced_at": "ISO8601", "report": {...}}
```

Dry runs never write it — a preview must not look like a completed sync.

## Files

- `src/server/infra/providers/archidekt_collection.py` — fetch + CSV normalization (no Redis)
- `src/server/domain/inventory/collection.py` — reconcile rows into instances, sync record
- `src/server/app/main.py` — `/api/collection/sync`, `/api/collection/import`
- `templates/instances.html` — collection panel
- `scripts/import_collection.py` — CLI
- `tests/test_archidekt_collection.py`, `tests/test_collection_import.py`,
  `tests/test_collection_routes.py`
