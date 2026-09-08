# Deck Management View — Design

## Goal
A Moxfield/Archidekt-style **deck management view**, separate from `/inventory`,
at `/deck/{registry_id}/manage` (or a tab on the existing `/deck/{deck_name}`
page). Shows the cards currently bound to one deck, lets you tag/categorize
them Moxfield-style, and add/remove cards via basic search. This is the
sanctioned manual editing path for a deck's card membership — distinct from
the existing bulk `auto_bind_physical` (which binds an entire freshly-imported
decklist at once).

Ownership rule (explicit, from requirements): a card is **"owned"** the
moment an instance is bound `in_deck` to any deck. The moment it's removed
from a deck, it drops back to plain `in_collection` inventory. No new status
is introduced — this reuses the existing `not_owned / in_mail / in_collection
/ in_deck` state machine in `instances.py`/`docs/CARD_DATABASE.md` verbatim.

## Categorization model (the "just like Moxfield" part) — UPDATED per decision
Decision: prefer the existing global **Lab Tags** as the categorization
source when a card already has one — don't fork a separate per-deck value
for something the card is already tagged with globally. But keep **both**
mechanisms available, since Moxfield-style per-deck overrides are still
useful for cards with no global tag yet, or where a card genuinely plays a
different role in this specific deck than its global tag implies:

- **Lab Tags** (existing, unchanged, primary source): global, oracle-level.
  The deck management view groups cards by Lab Tag by default — no new data
  written for the common case.
- **Deck Categories** (new, secondary/override): per-deck, per-card. Same
  Redis hash as before — `deck_categories:{registry_id}` ->
  `{card_name: ["Ramp", "Card Draw"]}` — but now only ever consulted/written
  when you explicitly want a deck-specific override that differs from (or
  adds to) the card's global Lab Tags. Empty by default; most cards will
  never have an entry here.
- **Effective category for grouping** = Deck Category if one exists for that
  card in this deck, else fall back to Lab Tags, else "Uncategorized". The
  per-card editor in the manage view shows both: global Lab Tags (editable —
  editing here writes through to the same global `/api/card/{name}/tags`
  endpoint everyone else uses) and a "deck override" section (only shows an
  input if you choose to add one).
- This means: tagging a card "Ramp" once, globally, makes it show up grouped
  as Ramp in every deck's manage view automatically. You only touch Deck
  Categories when you want *this* deck's grouping to diverge from the global
  tag.

## Add / remove flow (basic search)
**Add:** a search box filters `card_meta` by substring on name (same pattern
as `/tags`), returns matches with a snippet (type/mana cost). Picking a
result:
1. Look for an existing `in_collection` instance of that card name; reuse it
   if found, else `create_instance(..., ownership_status="in_collection")`
   then immediately `transition_status(..., "in_deck", deck_id=registry_id)`.
2. Append to the deck's local decklist entry if not already present (for
   non-Archidekt/Moxfield-sourced additions; imported decks keep their synced
   list as the baseline and this just layers manual adds on top).

Cards not yet in `card_meta` (never seen in any synced deck) are out of scope
for v1 basic search — no live Scryfall lookup yet. Flagged as a fast-follow.

**Remove:** pick one of the deck's bound instances for a card (usually one,
but supports duplicates) and `transition_status(..., "in_collection")`,
which clears `deck_id` per the existing state machine. That instance becomes
ordinary inventory immediately — matches the requirement verbatim.

## Physical-deck lock interaction
`docs/DECK_IMPORT.md` currently locks instance reassignment while
`deck.status == "physical"` to protect bulk auto-bind integrity. The new
manage-view add/remove endpoints are the intended exception to that lock —
they're the sanctioned manual path — so they must explicitly bypass
`is_deck_physical` gating rather than being blocked by it. Existing
programmatic instance PATCH endpoints keep the lock as-is.

## New API surface
- `GET  /api/decks/{registry_id}/manage` — deck's bound instances + card_meta
  + deck_categories, assembled for the manage view (one call, avoid N+1 from
  the template).
- `GET  /api/decks/{registry_id}/categories` — `{card_name: [tags]}` for this
  deck.
- `POST /api/decks/{registry_id}/categories` — `{"card_name", "action":
  "add"|"remove", "tag"}`.
- `GET  /api/decks/{registry_id}/search?q=` — substring search over
  `card_meta` for the add-card box.
- `POST /api/decks/{registry_id}/cards` — `{"card_name"}` → binds/creates an
  instance `in_deck` (the "add" flow above).
- `DELETE /api/decks/{registry_id}/cards/{instance_id}` — unbinds one
  instance back to `in_collection` (the "remove" flow above).

## New template
`templates/deck_manage.html` (extends `base.html`, linked from `deck.html`
header as a "Manage" button): card grid/list grouped by Deck Category,
inline tag chips + editor per card (reusing the tag-chip CSS from
`tags.html`), a search bar + result dropdown for adding, and a remove
control per bound card.

## ADDENDUM (2026-09-08) — Card ORM + import-to-inventory, additive only

Two more requirements layered on top of the above, both **additive to the
existing model** — nothing below removes or renames an existing key,
endpoint, or field. "Update, not replacement" applies to both.

### Card ORM: unique card entries keyed by id
`card_meta:{oracle_name}` (name-keyed) has a known limitation flagged since
the original design (`docs/CARD_DATABASE.md` §"Why not key oracle data by
name long-term"): name collisions across reprints/alternate faces. Resolve
this now rather than deferring further, since the deck view is about to
lean harder on card identity for search/grouping:

- New keyspace: `card:{oracle_id}` — the canonical, unique card record.
  `oracle_id` = Scryfall's `oracle_id` (already available from the existing
  Scryfall/Archidekt sync payload in `sync.py`; not a new external
  dependency). Same fields as today's `card_meta` blob, plus `oracle_id`
  and `name` on the record itself.
- New secondary index: `idx:card_name_to_oracle:{name}` -> `oracle_id`, so
  every existing name-keyed call site (`instances.py`, `/tags`, the new
  deck-manage search) keeps working by resolving name -> oracle_id -> record
  through a thin helper, instead of every caller learning a new lookup.
- **Additive migration, not a rename:** `sync.py` starts writing
  `card:{oracle_id}` alongside the existing `card_meta:{name}` (both kept
  in sync going forward). A one-time backfill script
  (`scripts/migrate_card_meta_to_oracle_id.py`, modeled on the existing
  `scripts/migrate_to_instances.py` pattern — dry-run by default, `--apply`
  to write) populates `card:{oracle_id}` + the index from current
  `card_meta:*` data. Old `card_meta:{name}` keys are left in place
  (nothing deletes them in this pass) so nothing that reads them today
  breaks.
- `instance:{instance_id}` records gain an optional `oracle_id` field
  (nullable, backfilled opportunistically), so instances can eventually be
  looked up/grouped by canonical card identity too — not required for v1
  deck-manage functionality, but the natural next consumer.
- New helper module `cards.py` (mirrors `instances.py`/`registry.py` shape):
  `get_card(r, oracle_id)`, `get_card_by_name(r, name)` (resolves through
  the index), `upsert_card(r, record)`. All new code (deck-manage search,
  category grouping) should call through `cards.py`, not raw
  `card_meta:*`/`card:*` gets — this is the seam that lets a future pass
  retire name-keying entirely without touching every call site again.

### Every imported deck adds its cards to inventory
Today only decks marked `status="physical"` touch the instance registry
(`instances.auto_bind_physical`, per `docs/DECK_IMPORT.md`) — `digital`/
`testing` decks are decklist-only, no instances created. New requirement:
**any** imported deck (any status) should get its cards reflected in
inventory, so the collection view shows what a deck needs even before it's
physically assembled.

This must NOT violate the existing ownership rule (owned only in `in_deck`)
or turn every digital-deck import into fake ownership. Resolution:
`upsert_deck` (in `registry.py`) gains a new post-import step —
`instances.ensure_wishlist_instances(r, registry_id, decklist)` — that, for
every card in the decklist with **zero existing instances of any status**,
creates one `not_owned` instance with `considered_for_deck=registry_id` (the
`considered_for_deck` soft-link already exists in the schema for exactly
this "thinking of putting this here" case — see `docs/CARD_DATABASE.md`).
Cards that already have an instance (owned, in-mail, or in another deck's
consideration) are left untouched — this only fills gaps, never overwrites
an existing instance's status. Marking a deck `physical` later still runs
the existing `auto_bind_physical` flow unchanged, which will find and
promote these `not_owned` placeholders where card names match rather than
creating duplicates.

Net effect: importing any deck (digital, testing, or physical) makes every
one of its cards visible in `/inventory` immediately (as `not_owned`
placeholders if you don't already have them), and flipping a deck physical
later "graduates" the matching placeholders to real owned copies instead of
creating parallel duplicate instances.

## Open items / assumptions — RESOLVED
1. ~~Deck Categories vs global Lab Tags~~ — resolved above: Lab Tags is the
   default grouping source; Deck Categories is an explicit per-deck override
   layer, both mechanisms kept.
2. Basic search v1 only searches cards already known to `card_meta` (i.e.
   already seen via Archidekt sync somewhere). No live Scryfall add-any-card
   yet — confirmed acceptable for now, Scryfall lookup is a fast-follow.
3. Duplicate physical copies of the same card in one deck: supported (each
   is its own instance), remove flow needs a picker if count > 1.
