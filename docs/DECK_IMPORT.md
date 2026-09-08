# Deck Import (Archidekt + Moxfield) & Deck Registry

## Overview
Commander Lab tracks a registry of decks across two sources:
- **Archidekt** — full account sync (`sync.py`) plus single-deck import.
- **Moxfield** — single-deck import only (no account-wide sync; Moxfield has
  no official API — see below).

Every deck in the registry carries a lifecycle `status`:
- `physical` — physically assembled; every card is bound 1:1 to an owned
  card Instance (see `docs/CARD_DATABASE.md`). Enforced immutable: instances
  bound `in_deck` to a physical deck cannot be moved or reassigned through
  the normal instance API while the deck stays physical.
- `digital` — decklist only, not assembled (formerly "virtual").
- `retired` — no longer played/maintained.
- `testing` — being brewed/considered (formerly "test", the default for new
  imports).

## Importing a deck
UI: Decks page → "Import Deck" → pick provider, paste a URL or bare id.
API: `POST /api/import {"provider": "archidekt"|"moxfield", "url": "..."}`.
New decks land with status `testing`. Re-importing an existing deck (same
provider + id) refreshes its cardlist/metadata and keeps its current status.

## Marking a deck physical
`POST /api/decks/{registry_id}/status {"status": "physical"}` (or via the
deck modal in the UI). This triggers `instances.auto_bind_physical`:
for every card in the decklist, reuse an existing `in_collection` instance
of that card if one exists, otherwise create a new one and bind it
`in_deck` to this deck. The response includes a per-card bound/created
report and any shortfalls (should not normally occur — creation always
succeeds).

Flipping a deck away from `physical` does not auto-unbind anything; it just
lifts the lock so instances can be moved/edited normally again.

## Moxfield API caveat
Moxfield has no official public API. `providers/moxfield.py` uses the
unauthenticated `api2.moxfield.com/v3/decks/all/{publicId}` endpoint, which
works for PUBLIC decks as of this writing but is undocumented and may break
or start requiring auth without notice. If it starts failing, fall back to
the Playwright + authenticated-session scrape path in the `mtg-apis` skill
(`scripts/setup_auth.py` already captures a Moxfield login session for that
path).

## Registry ids
Archidekt-native decks keep their existing integer `id`. Moxfield (and any
future provider) decks get a synthetic `registry_id` = `f"{source}:{source_id}"`
(e.g. `moxfield:FQ2llEVF2k6oMUoXVA16XA`), which is what's stored as
`instance.deck_id` for cards bound to that deck. `instances.py` treats
`deck_id` as an opaque string/int end-to-end — never assume it's numeric.

## Migrating existing data
`scripts/migrate_deck_status_rename.py [--apply]` renames old deck.status
values (`virtual`→`digital`, `test`→`testing`) already stored in Redis.
This is separate from `scripts/migrate_to_instances.py`, which migrates the
old per-copy ownership blob to the Instance registry.
