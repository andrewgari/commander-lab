# Card Database & Ownership Registry — Design

## Goal
Commander Lab becomes the source of truth for **card ownership** (what you physically
have, where it is, what state it's in). Archidekt remains the source of truth for
**deck composition** (decklists, categories, commanders) — Lab reads decks from it
read-only, same as today. The two are linked but independently authoritative:

- Archidekt says "Deck X wants 1x Sol Ring."
- Lab says "You own 3 Sol Rings: 1 assigned to Deck X, 1 in your collection box, 1
  still in the mail."

Lab does not push ownership data to Archidekt, and does not treat Archidekt's
decklist quantities as inventory counts (a deck listing "1x Sol Ring" is a
requirement, not a receipt).

## Core concept: the Card Instance

Every physical (or in-transit) copy of a card is a unique record — never a bare
quantity counter. Two "Sol Ring, Commander Masters, foil, NM" copies you own are
two separate instance rows, each with its own id, history, and deck assignment.

Card-level (oracle) data — name, colors, type line, oracle text, keywords — stays
separate from instance-level data, because that's shared across every copy of a
card regardless of printing.

```
card_meta:{oracle_name}          -- oracle/gameplay data, one row per unique card
instance:{instance_id}           -- one row per physical/tracked copy (uuid4)
```

### card_meta:{oracle_name}
```json
{
  "type": "Artifact",
  "color": "C",
  "super_types": [],
  "sub_types": [],
  "keywords": ["Ramp"],
  "oracle_text": "...",
  "cmc": 1
}
```
Sourced from Archidekt/Scryfall sync, same as today's `card:{name}` blob minus the
`copies` array (that array is replaced by real Instance records).

### instance:{instance_id}
```json
{
  "id": "uuid4",
  "card_name": "Sol Ring",
  "set": "CMM",
  "set_name": "Commander Masters",
  "collector_number": "123",
  "finish": "foil | nonfoil | etched",
  "condition": "NM | LP | MP | HP | DMG",
  "language": "EN",

  "ownership_status": "not_owned | in_mail | in_collection | in_deck",
  "deck_id": 123456,              // set iff ownership_status == in_deck, else null
  "deck_name": "Meren Value Town", // denormalized for display, kept in sync with deck_id
  "considered_for_deck": null,    // optional soft link: "thinking of putting this here", never binding

  "price_paid": 4.50,
  "source": "TCGPlayer | LGS | Trade | ...",
  "date_ordered": "2026-08-01",
  "date_acquired": "2026-08-10",

  "archidekt_uid": "abc123",      // printing uid on Archidekt, for cross-reference only
  "notes": "",

  "created_at": "2026-08-01T12:00:00Z",
  "updated_at": "2026-08-10T09:00:00Z",
  "history": [
    {"status": "not_owned", "at": "2026-08-01T12:00:00Z"},
    {"status": "in_mail", "at": "2026-08-01T12:00:00Z"},
    {"status": "in_collection", "at": "2026-08-10T09:00:00Z"}
  ]
}
```

### Secondary indexes (Redis sets, for real cardinality instead of `KEYS` scans)
```
idx:instances_by_card:{card_name}   -> set of instance ids
idx:instances_by_status:{status}    -> set of instance ids
idx:instances_by_deck:{deck_id}     -> set of instance ids
idx:instances_by_set:{set_code}     -> set of instance ids
```
All writes go through `instances.py` helpers that keep these indexes consistent —
never write `instance:*` directly from route handlers.

## Ownership state machine

```
not_owned --------> in_mail --------> in_collection --------> in_deck
    ^                   |                   ^   |                |
    |___________________|                   |   |________________|
                                             |
                                     (returned to box)
```

Allowed transitions, enforced in code (`instances.transition_status`):
- `not_owned -> in_mail` (ordered)
- `not_owned -> in_collection` (walked in with it / instant local pickup)
- `in_mail -> in_collection` (arrived)
- `in_mail -> not_owned` (order cancelled)
- `in_collection -> in_deck` (assigned; requires `deck_id`)
- `in_collection -> not_owned` (sold/lost)
- `in_deck -> in_collection` (pulled from deck; clears `deck_id`)
- `in_deck -> not_owned` (sold while assembled; clears `deck_id`, unusual but allowed)

`deck_id` is only ever non-null when `ownership_status == in_deck`. The transition
helper clears it automatically on any move away from `in_deck` and rejects setting
it otherwise. One instance = one deck, max, matching physical reality (per your
call — no proxy/multi-deck counting).

## Why not key oracle data by name long-term
Card names collide across reprints with different backs, and Scryfall's real
identity key is `oracle_id`. Name-keying is kept for this pass because it matches
the current sync.py/Archidekt integration, but flagged as a known limitation —
migrate `card_meta` to `oracle_id`-keyed if double-faced/alternate-name cards
start causing collisions.

## Migration from the old model
Old `card:{name}` blobs stored a `copies[]` array conflating deck slot and
ownership in one `status` field (`have|virtual|pending|possible`). Migration
(`scripts/migrate_to_instances.py`) maps:

| old copy.status | new ownership_status | deck_id                  |
|---|---|---|
| have             | in_deck               | resolved from copy.deck  |
| pending          | in_mail               | null (considered_for_deck = copy.deck) |
| possible         | not_owned             | null (considered_for_deck = copy.deck) |
| virtual          | not_owned             | null (considered_for_deck = copy.deck) |

This is a heuristic, one-time, reviewable migration — it does not run
automatically on every sync. `card_meta` is written for every card either way.
The old `card:{name}` keys are left untouched until you confirm the migration
looks right, then can be deleted.

## What sync.py still does vs. what it must stop doing
- Still: pull decks, decklists, categories, commanders, deck-level status from
  Archidekt into `decks` (read-only, unchanged).
- Still: refresh `card_meta` (oracle-level Scryfall/Archidekt data) for every
  card seen in any decklist.
- Stops: no longer inventing per-copy ownership state from label text
  (`have`/`pending`/`possible`/`virtual` guessing). That guessing logic was a
  reasonable stopgap but is now replaced by you explicitly managing instances
  through the Lab UI/API.

## New API surface (see app.py)
- `POST   /api/instances`                create an instance (any starting status)
- `GET    /api/instances`                list/filter by card, status, deck, set
- `GET    /api/instances/{id}`           fetch one
- `PATCH  /api/instances/{id}`           update fields / transition status / assign deck
- `DELETE /api/instances/{id}`           remove (e.g. logged in error)
- `GET    /api/cards/{card_name}/instances`   all instances of one card, with a
  rollup: owned count, in-deck count, in-mail count, per-deck breakdown.

## Recommendations / open items worth deciding now
1. **Oracle identity key.** Using card name as the primary key will eventually
   break on double-faced cards or alternate-art variants with different display
   names. Fine for now; revisit if it bites.
2. **Deck/instance reconciliation view.** Since Archidekt owns decklists and Lab
   owns assignment, decklists and instance assignments *can* drift (you swap a
   card on Archidekt without moving the instance in Lab). Plan a "reconcile"
   view per deck: for each card the decklist wants, show assigned instance(s)
   vs. required quantity, flag shortfalls/overages. This is the natural next
   feature after this migration.
3. **Brewing workflow.** The "assign cards to decks" tool you asked for should
   pull candidates from `in_collection` instances of the right card, let you
   pick a specific instance (not just decrement a counter), and call the
   `in_collection -> in_deck` transition. This is the first "small tool" to
   build on top of the instance API.
4. **Audit trail.** Every instance carries a `history[]` of status transitions
   with timestamps — cheap now, useful later for "when did I get this," "how
   long has this been in the mail," and detecting stale in_mail records worth
   following up on.
5. **Condition/finish vocabulary.** Recommend fixing an explicit enum now
   (`NM/LP/MP/HP/DMG`, `nonfoil/foil/etched`) rather than free text, so the UI
   can filter/sort reliably.
6. **Duplicate collisions.** Because instances are always unique even if
   identical (same set/finish/condition), don't collapse them into a quantity
   field — that was explicitly your call. UI list views should group by
   card+set+finish+condition and show a count, but the underlying records stay
   one-per-copy.
7. **`not_owned` bookkeeping.** A "not_owned" instance is essentially a wishlist
   line (you're tracking a card you want but haven't bought). Worth deciding
   later whether these should auto-expire/archive vs. persist indefinitely —
   not blocking for this pass.
