# Dual-Source Deck Sync

Commander Lab can keep a single local deck in sync with both Archidekt and
Moxfield at the same time, pulling the latest cardlist from both platforms
in one operation and merging the results.

This sits on top of (and is distinct from) two existing features:

- `providers/*` single-deck import (`/api/import`) -- imports one deck from
  one provider by URL/id.
- `linked_accounts.py` -- links a whole provider *account* by username and
  pulls every deck that account owns.

Dual-source sync links individual *decks already in the registry* to more
than one provider deck URL at once, so e.g. a deck tracked on both Moxfield
and Archidekt can be resynced from both with a single click/call.

## Storage: `deck_links.py`

Links live directly on the deck record (the existing `decks` Redis key),
under a `links` dict:

```json
{
  "links": {
    "archidekt":     {"identifier": "https://archidekt.com/decks/6862011/kaalia", "deck_id": "6862011"},
    "moxfield":      {"identifier": "https://moxfield.com/decks/abc123", "deck_id": "abc123"},
    "commandersalt": {"identifier": "https://commandersalt.com/details/deck/xyz", "deck_id": "https://commandersalt.com/details/deck/xyz"}
  }
}
```

`deck_links.set_link(r, registry_id, provider, identifier)` validates/
normalizes the identifier through that provider's own `parse_identifier`
(Archidekt/Moxfield) before persisting -- a bad URL is rejected at
link-time, not at the next sync. This is a save-to-record operation (the
UI calls `POST /api/decks/{id}/links` and the backend writes the link onto
the deck), not a one-off copy/paste import. Calling it again with a new
identifier for the same provider edits the link in place; passing an
empty identifier clears it.

Commander Salt is **link-only**: it's an analytics source
(`analytics/providers/commandersalt.py`), not a cardlist source, so it's
excluded from `deck_links.SYNCABLE_PROVIDERS` and never participates in
the merge below -- only stored for display/reference.

## Sync + merge: `deck_sync.py`

`deck_sync.sync_deck(r, registry_id)`:

1. Reads the deck's `links`, filtered to `SYNCABLE_PROVIDERS` (Archidekt,
   Moxfield).
2. Raises `DeckSyncError` immediately if there's no syncable link at all.
3. Fetches each linked provider's deck independently via
   `providers.fetch_deck(provider, deck_id)`. **One provider failing does
   not abort the other** -- each failure is caught, recorded in the
   report's `sources`/`warnings`, and the sync proceeds with whatever
   source(s) succeeded.
4. If *every* linked source failed, raises `DeckSyncError` and the deck's
   stored cardlist is left completely untouched.
5. Otherwise, merges every successful source's cardlist via
   `merge_normalized_decks` and writes the result with
   `registry.update_deck_cards` (overwrites `cards`/`commanders`/
   `commander_uids`/`color` only -- never touches `source`/`source_id`/
   `registry_id`/`status`).
6. Returns a `SyncReport` (`to_dict()` for the API response) describing
   exactly what happened: per-source status + card count or error,
   per-card merge conflicts, merged card count, and a flat `warnings` list.

### Merge strategy

Cards are unioned by name across every successful source. When a card
appears in more than one source:

- **Quantity** = the **max** quantity seen across sources. This favors a
  complete decklist over an undercount when two platforms briefly
  disagree (e.g. one hasn't picked up a recently-added copy yet).
- A **conflict** is recorded whenever sources disagree on quantity for a
  shared card, *or* when a card is present on only one of the linked
  sources (so the human sees every point of divergence, not just hard
  mismatches).

Commanders and commander UIDs are unioned (dedup, first-seen order).
Color identity is the union of every WUBRG letter seen across sources
(`"C"` if none).

### Single-source behavior (no regression)

A deck with only one syncable link behaves exactly like a normal
single-source resync: `merge_normalized_decks` with one source produces
no conflicts, and `sync_deck` still goes through
`registry.update_deck_cards` (same physical-deck pending-removal diffing
as the existing `registry.upsert_deck` resync path).

### Physical decks

`registry.update_deck_cards` applies the same adjudication-required
resync diff as `registry.upsert_deck`: for a deck with `status ==
"physical"`, instances still bound `in_deck` but no longer present in the
merged cardlist are flagged `pending_removal` (see
`instances.flag_resync_removed`) rather than silently dropped. Newly
merged cards are **not** auto-bound to instances for physical decks --
same "no automatic instance creation" rule as every other resync path
(see `docs/PHYSICAL_RESYNC_ADJUDICATION.md`).

## API

| Method & path                              | Purpose |
|---------------------------------------------|---------|
| `GET /api/decks/{deck_id}/links`             | List a deck's configured provider links. |
| `POST /api/decks/{deck_id}/links`            | Add/edit one provider's link. Body: `{"provider": "archidekt"\|"moxfield"\|"commandersalt", "identifier": "<url or id>"}`. Empty identifier clears the link. |
| `DELETE /api/decks/{deck_id}/links/{provider}` | Remove one provider's link. |
| `POST /api/decks/{deck_id}/sync`             | Run dual-source sync + merge. Returns `{"success": true, "report": {...}}` on at least partial success, or 400 with `{"success": false, "error": ...}` if every linked source failed or none was configured. |

## UI

The deck detail modal (`templates/decks.html`) has a "Sync Sources" editor
with one input + Save button per provider (Archidekt, Moxfield, Commander
Salt), pre-filled from the deck's current `links`, plus the existing
**Sync** button, which now calls `POST /api/decks/{id}/sync` and shows a
per-source summary (cards pulled, failures, conflict count) inline instead
of being a no-op.

## Adding a new provider to dual-source sync

A provider already implementing the `providers/*` contract (`fetch_deck`,
`parse_identifier`) just needs adding to `deck_links.SYNCABLE_PROVIDERS` to
participate in merge-based dual-source sync. `LINKABLE_PROVIDERS` is a
separate, broader set for providers that only need link storage/display
(e.g. Commander Salt) without a cardlist to merge.
