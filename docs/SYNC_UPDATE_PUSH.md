# Deck sync: Update and Push (explicit only)

Decks never sync on a timer, schedule, interval, page load, or save hook.
There are exactly two per-deck operations, each run only when the user asks:

- **Update** = external -> commander.lab. Pulls the linked Archidekt/Moxfield
  deck into the commander.lab deck. Modifies the commander.lab deck only.
- **Push** = commander.lab -> external. Sends the commander.lab cardlist to
  the linked Archidekt/Moxfield deck. Modifies the external deck only.

Code: `deck_sync.update_deck`, `deck_sync.push_deck`, `deck_sync.sync_status`
(service), `providers/{archidekt,moxfield}.push_deck` (writers).
Guard test: `tests/test_sync_split.py::TestNoAutomaticSync`.

## Provider support

| Provider  | Update | Push | Push credential (env var) |
|-----------|--------|------|---------------------------|
| archidekt | yes (public decks, no auth) | yes | `ARCHIDEKT_TOKEN` (JWT; sent as `Authorization: JWT <token>`) |
| moxfield  | yes (public decks, no auth) | yes | `MOXFIELD_TOKEN` (bearer token captured from a logged-in browser; expires) |
| commandersalt | no (link-only) | no | - |

Without the credential, Push returns **501 `push_not_configured`** (never a
silent no-op). Dry-run previews work without credentials.

Write paths (neither verified against a live account yet; test with a
throwaway deck before relying on them):

- Archidekt: `PATCH /api/decks/{id}/modifyCards/` with add/modify/remove ops
  for the mainboard only (Maybeboard/Sideboard untouched). New cards are
  resolved via `/api/cards/v2/?nameSearch=&exact=true`; any unresolvable name
  aborts the push before anything is written.
- Moxfield: `POST /v3/decks/{id}/import` with `replaceAll: true` (see
  docs/moxfield-api.md §4.1). Remote sideboard/maybeboard are re-sent so
  they survive. Printings are Moxfield's preferred printings.

## API

All responses are JSON. Body is optional JSON.

### `GET /api/decks/{deck_id}/sync/status`
Read-only, never contacts a provider.
```json
{"success": true, "registry_id": "archidekt:42",
 "last_updated_at": "ISO8601|null", "last_pushed_at": "ISO8601|null",
 "providers": [
   {"provider": "archidekt", "linked": true, "identifier": "42",
    "can_update": true, "can_push": false,
    "push_unavailable_reason": "archidekt push is not configured on this server",
    "last_updated_at": "ISO8601|null", "last_pushed_at": "ISO8601|null"},
   {"provider": "moxfield", ...}]}
```

### `POST /api/decks/{deck_id}/sync/pull`  (Update)
Body: `{"provider": "archidekt"|"moxfield"|null, "dry_run": false}`.
`provider` null/omitted pulls every linked source and merges (union, max qty).
```json
{"success": true, "operation": "update", "direction": "external_to_local",
 "registry_id": "archidekt:42", "provider": "archidekt", "providers": ["archidekt"],
 "dry_run": false, "timestamp": "ISO8601",
 "changes": {"added": [{"name": "Rhystic Study", "quantity": 1}],
             "removed": [{"name": "Cancel", "quantity": 1}],
             "changed": [{"name": "Island", "from": 10, "to": 9}],
             "counts": {"added": 1, "removed": 1, "changed": 1}},
 "sources": [{"provider": "archidekt", "status": "ok", "card_count": 100, "error": null}],
 "conflicts": [], "merged_card_count": 100, "warnings": []}
```
`changes` is commander.lab before -> after.

### `POST /api/decks/{deck_id}/sync/push`  (Push)
Body: `{"provider": "archidekt"|"moxfield", "dry_run": false}` (provider required).
```json
{"success": true, "operation": "push", "direction": "local_to_external",
 "registry_id": "archidekt:42", "provider": "moxfield", "providers": ["moxfield"],
 "dry_run": false, "timestamp": "ISO8601", "changes": {...same shape...},
 "remote_url": "https://moxfield.com/decks/abc", "warnings": []}
```
`changes` is external before -> after (what the push changes remotely).

### Errors (both operations)
```json
{"success": false, "operation": "update"|"push", "provider": "...",
 "code": "...", "error": "human message", "timestamp": "ISO8601"}
```
| HTTP | code | when |
|------|------|------|
| 400 | `sync_failed` | no link / provider not linked / unknown provider / push without provider |
| 404 | `deck_not_found` | unknown deck id |
| 501 | `push_unsupported` | provider has no push path |
| 501 | `push_not_configured` | provider's push credential env var unset |
| 502 | `provider_error` | provider unreachable, rejected the token, or every source failed |

Timestamps are stored on the deck record: `deck.last_updated_at`,
`deck.last_pushed_at`, and per provider in `deck.sync_state[provider]`.
Dry runs record nothing.

`POST /api/decks/{deck_id}/sync` is a deprecated alias for an all-sources
Update with the legacy `{"success", "report"}` shape.
