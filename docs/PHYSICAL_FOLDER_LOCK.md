# Physical folder lock

Decision (2026-10-13): Archidekt is the source of truth for which decks are
physical.

## Rule

- A deck is **physical** if and only if its Archidekt parent folder is the
  physical folder: `ARCHIDEKT_PHYSICAL_FOLDER_ID` (default `588380`, the
  "Commander Decks" folder). The folder name is never used.
- Physical decks are **locked**. Their status can't be changed in Commander Lab.
- Every other deck can be set to digital (Virtual), testing (WIP) or retired.
  Nothing can be set to physical by hand.

Outside the folder:

- An existing saved non-physical status is kept.
- A deck with none is seeded: Archidekt `theorycrafted` → testing, otherwise →
  digital.
- Legacy note: before this rule, sync defaulted every deck outside the folder
  to `testing`. A `testing` status with no `status_origin` is treated as that
  default and re-seeded. A legacy `digital` or `retired` (which only a user
  could have set) is kept.

## Data

Each Archidekt deck in the `decks` registry stores the folder data from its
last sync:

| field              | from Archidekt                             |
|--------------------|--------------------------------------------|
| `parent_folder_id` | `parentFolderId` (list) / `parentFolder` (detail) |
| `theorycrafted`    | `theorycrafted`                            |
| `status_origin`    | ours: `archidekt_folder`, `seed` or `user` |

`/api/decks` adds a computed `status_locked` per deck.

## Where it runs

`registry.apply_folder_rule` runs on every sync path:

- `sync.py` (full account pull): after the registry merge, through `apply_folder_rules`
- linked-account sync (`linked_accounts.sync_account`): per deck in `upsert_deck`, then over the whole registry
- `/api/import` (Archidekt): in `upsert_deck`
- per-deck dual-source sync (`deck_sync.sync_deck`): through `registry.set_folder_data`

An Archidekt deck with no folder data yet (not synced since the rule shipped)
is left alone. It stays locked while it is physical, until the next sync.

## Transitions

Every status change goes through `registry.set_status`:

- **Entering the folder** (→ physical): `instances.auto_bind_physical` binds the
  decklist. In-collection copies are reused first. Any instance still flagged
  from an earlier exit has that flag cleared.
- **Leaving the folder** (physical → seed rule): nothing is unbound or deleted.
  Every instance still bound to the deck is flagged `pending_removal` with
  reason `left_physical_folder`. Each one is kept or tossed in the deck manage
  page's resync review panel (`/api/decks/{id}/resync-review`).

## Moxfield

- A Moxfield deck matched to an Archidekt deck (`registry.match_provider_deck`)
  is attached to that deck as `links.moxfield`, so it shares that deck's status
  and lock. Archidekt wins: a match is attached to the Archidekt deck even if an
  older import also registered it under `moxfield:<id>`. That older entry is
  left in place.
- An unmatched Moxfield-only deck is never physical and is always editable. It
  is seeded as digital. A legacy physical Moxfield-only deck is moved to digital,
  and its instances are flagged `pending_removal`.

## API

`POST /api/decks/{id}/status` with body `{"status": "digital"|"testing"|"retired"}`:

| case                          | response |
|-------------------------------|----------|
| `physical` (any deck)         | 400 `Physical can't be set here: …` |
| unknown status / bad body     | 400 |
| locked (physical) deck        | 409 `… managed by your Archidekt Commander Decks folder …` (`locked: true`) |
| unknown deck                  | 404 |

## UI

`HUD.deckStatusControl(deck)` and `HUD.bindDeckStatus(root, deck)` live in
`static/js/hud.js` and are used by the deck modal on `/decks` and the
Lifecycle panel on `/deck/{name}`:

- Locked deck: a disabled select showing Physical, with the note "Physical:
  managed by your Archidekt Commander Decks folder".
- Other decks: Virtual / WIP / Retired, mapped to digital / testing / retired.

`/decks?status=` filters are unchanged.

`scripts/tag_physical.py`, the old folder-name hack, has been removed.
