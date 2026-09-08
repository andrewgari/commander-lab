# Physical Deck Resync Adjudication + Non-Physical Isolation + Color Identity Fix

Addendum to `docs/DECK_MANAGEMENT_VIEW.md` / `docs/DECK_IMPORT.md` / `docs/CARD_DATABASE.md`.
Confirmed decisions from user, 2026-09-08.

## 1. Archidekt color-identity bug (root cause, confirmed live)

`providers/archidekt.py fetch_deck()` reads the deck-level `colors` field
(`data.get("colors")`) to compute WUBRG. Verified live against a real
Archidekt deck: this field is `null` on every current deck — Archidekt
stopped populating it. Since `colors_map.get(c, 0) > 0` is always false
against `None`, every Archidekt-imported deck silently falls back to `"C"`
(colorless) regardless of actual color identity.

The correct data is present per-card: `card.card.oracleCard.colorIdentity`
is a list of full color names (`["Black", "Red", "Green"]`), confirmed
correct against the same live deck.

**Fix:** compute deck color identity as the union of every card's
`oracleCard.colorIdentity`, mapped from full names to WUBRG letters
(`Black`→`B`, `Blue`→`U`, `Green`→`G`, `Red`→`R`, `White`→`W`), instead of
reading the broken deck-level field. `providers/moxfield.py` is unaffected
— verified its `colorIdentity` field returns correct WUBRG letters directly
already.

## 2. Non-physical decks are hypothetical/wishlist only — zero inventory footprint

**Reversal of behavior merged in PR #18** (`Wire imported decks into
inventory as wishlist instances`), confirmed by user: that PR made every
imported deck, regardless of status, create `not_owned` wishlist instances
via `instances.ensure_wishlist_instances`. That is now wrong for
non-physical decks.

Corrected rule:
- **Physical decks**: represent real owned cards. Bound instances are real
  ownership. This is the only status where `ensure_wishlist_instances` (or
  any instance creation) should ever run automatically.
- **Non-physical decks** (`digital`, `testing`, `retired`): pure metadata.
  The decklist lives only in `deck.cards[]` on the deck registry object.
  **No instances are created for a non-physical deck's cards, ever, on
  import or resync.** A digital/testing decklist represents a hypothetical
  or wishlist build — it says nothing about what you actually own until it
  goes physical.
- Flipping a deck to `physical` is the one moment a decklist becomes real:
  the existing `instances.auto_bind_physical` flow (unchanged) is what
  creates/reuses instances at that transition, exactly as it does today.
- **Explicitly flagged as isolated-for-now, not deleted as a direction:**
  the user wants virtual/testing decks to eventually be able to query "what
  of this decklist do I already own" as a read-only availability check
  against the instance pool — a cross-reference, not an ownership claim.
  That is a distinct future feature (a "brewing view" reading instances
  without writing them) and explicitly out of scope for this pass.

### Migration required
`registry.upsert_deck` calls `instance_store.ensure_wishlist_instances`
unconditionally today. Change to only call it when
`result_deck.get("status") == "physical"`.

A cleanup script, `scripts/remove_nonphysical_wishlist_instances.py`
(dry-run default, `--apply` to write, matching the existing
`scripts/migrate_to_instances.py` / `scripts/migrate_card_meta_to_oracle_id.py`
convention), finds and deletes `not_owned` instances whose
`considered_for_deck` points at a deck whose current `status` is NOT
`physical` — these were wrongly created by the now-reversed PR #18 logic
and are noise under the corrected rule. Instances with any other
`ownership_status` (in_mail, in_collection, in_deck) are never touched by
this script regardless of their deck's status — only `not_owned` wishlist
placeholders are cleanup candidates, since anything else represents real
inventory a human explicitly created.

## 3. Physical-deck resync adjudication (the core new feature)

When a linked account resyncs (or a single-deck re-import refreshes) a
**physical** deck, the remote decklist may have changed since the last
sync. The existing state machine already allows every needed transition;
what's missing is the *diff + human adjudication* step, because physical
decks must never be silently mutated by upstream (see
`docs/DECK_IMPORT.md`'s existing physical-lock design intent).

### What must NOT happen
No automatic instance mutation for a physical deck's resync-detected
removals. `is_deck_physical` lock stays fully intact — this feature does
not bypass it, it works alongside it.

### What happens instead: diff + pending queue
On every resync of a physical deck, compare the new remote decklist
against the deck's currently-bound (`in_deck`) instances by card name:

- **Cards newly present remotely, not currently bound**: NOT auto-created
  as instances (physical decks don't get automatic wishlist creation
  either — see the note in `auto_bind_physical`'s existing behavior, which
  already only runs at the physical *transition* moment, not every
  resync). Flag these too, in the same review queue, as "remote added this
  card — bind an instance?" so the human can act, but nothing is created
  automatically.
- **Cards bound here, no longer present remotely**: the important case.
  The bound instance is NOT unbound and NOT deleted automatically. Instead
  it gets a `pending_removal` marker:
  ```json
  {
    "pending_removal": {
      "reason": "resync_removed",
      "detected_at": "2026-09-08T12:00:00Z",
      "registry_id": "archidekt:123"
    }
  }
  ```
  `ownership_status` and `deck_id` are untouched — the card stays fully
  bound and the deck stays fully playable/intact until adjudicated. This
  is a new optional field on the instance record (`instances.py`), not a
  new ownership_status value.

### Adjudication (human-in-the-loop, one at a time per user's ask)
Deck-manage UI gets a "Resync changes pending review" panel listing every
instance with a `pending_removal` marker on the current deck, each with
two actions:
- **Save to inventory** — `transition_status(instance_id, "in_collection")`
  (existing `in_deck -> in_collection` transition), clears `deck_id`,
  clears `pending_removal`. Matches the standing rule: out of deck = normal
  inventory, never deleted.
- **Toss** — `transition_status(instance_id, "not_owned")` (existing
  `in_deck -> not_owned` transition, documented today as "sold while
  assembled, unusual but allowed" — this resync-adjudication is the second,
  now-common legitimate use of that transition). Clears `deck_id` and
  `pending_removal`.

No bulk/auto-resolve action — the user explicitly wants one-by-one
adjudication, not a "keep all" / "toss all" shortcut, since each card is a
real physical-ownership decision.

### New API surface
- `GET /api/decks/{id}/resync-review` — list of instances with
  `pending_removal` set for this deck, plus the list of remotely-added
  cards not yet bound (informational, no instance exists yet for those).
- `POST /api/decks/{id}/resync-review/{instance_id}` `{"action": "keep" |
  "toss"}` — resolves one pending-removal instance per the adjudication
  rules above. 404 if the instance has no pending_removal marker (nothing
  to adjudicate) or doesn't belong to this deck.
- Resync itself (`linked_accounts.sync_account`, single-deck re-import via
  `registry.upsert_deck`) computes the diff and writes `pending_removal`
  markers for a physical deck instead of touching bindings; for a
  non-physical deck it just overwrites `deck.cards[]` as today (no
  instances involved at all, per section 2).

## Summary of touched files
- `providers/archidekt.py` — color identity fix (section 1)
- `instances.py` — new optional `pending_removal` field + a resync-diff
  helper (e.g. `flag_resync_removed(r, registry_id, current_decklist)`) +
  keep/toss resolution helper
- `registry.py` — `upsert_deck` gated to only call
  `ensure_wishlist_instances` when `status == "physical"`; physical-deck
  resync path calls the new diff/flag helper instead of silently
  overwriting `cards[]` when bound instances are affected
- `scripts/remove_nonphysical_wishlist_instances.py` — new cleanup script
  (section 2 migration)
- `app.py` — new `/api/decks/{id}/resync-review` GET + POST endpoints
- `templates/deck_manage.html` — new "Resync changes pending review" panel
- Test coverage for: color-identity union logic, non-physical decks
  creating zero instances end-to-end, physical-deck resync diff correctly
  flagging removed/added cards without mutating bindings, keep/toss
  resolution transitions matching the state machine exactly.
