# Draft mode — combine / dismantle decks (Slice 5)

Canonical spec: `docs/ARCHITECTURE.md` §7.8 (staging rule), §4.2 (`instance_events`
provenance), §8 Slice 5. This document describes what is built.

## What draft mode is

Draft mode is the only way decks are combined or dismantled to build another.
A draft is a **staging session**: you open it over 1+ source decks, edit a
staged card list and stage physical-copy moves, and nothing in the canonical
tables (`decks`, `deck_cards`, `card_instances`) changes until you press
**Commit**. **Abandon** deletes every staging row — zero residue.

Two kinds:

- **combine** — 2+ source decks merged into one new deck.
- **dismantle** — exactly 1 source deck; its cards/copies are redistributed to
  build a new deck.

## The staging rule (§7.8, binding)

Uncommitted work stays uncommitted. Every write before Commit lands only in
the staging tables:

- `drafts` — the session row (kind, status, source decks, target name, notes).
- `draft_cards` — the staged card list (oracle_id + qty + board), editable
  independently of the source decks.
- `draft_instance_moves` — staged physical-copy moves, one row per instance:
  `assign` (copy goes into the result deck; legal from `in_collection` or
  `in_deck`) or `unassign` (copy returns to the collection; legal from
  `in_deck` only).

Opening a draft with `prefill` seeds `draft_cards` from the union of the
source lists (commander board wins on conflict) — the sources keep their
cards and copies untouched.

## Commit = ONE transaction

`POST /api/v2/drafts/{id}/commit` validates the full plan in the domain layer
(`src/server/domain/draft/draft_mode.py::plan_commit`) against the instances
ownership state machine, then applies it in a single transaction:

1. the new deck row (`decks.id = draft:<draft_id>`),
2. its `deck_cards` rows from the staged list,
3. every staged move applied to `card_instances`,
4. one `instance_events` row per applied move — `kind=assigned|unassigned`,
   `from_deck` / `to_deck`, `draft_id` (§4.2).

Any validation failure rolls the whole thing back; there is no partial commit.
The `instance_events` stream IS the provenance; the MOVED graph edge derives
from it later (Slice 6) and is deliberately not built here.

## Provenance

`GET /api/v2/instances/{instance_id}/provenance` returns the ordered event
path of one physical copy — "this copy moved A → B in draft X" — with deck
names resolved and a per-step summary. This is the query the Slice 5
acceptance calls for.

## Suggestions

`GET /api/v2/drafts/{id}/suggestions` ports the tuner weak-performer cuts and
reconstruct tiered-pool adds into `src/server/domain/draft/suggestions.py`:

- **Own-first retrieval (§6)**: adds prefer owned copies sitting free in the
  collection, then donor pulls — and a donor pull is only offered when the
  donor deck is left no worse (a replacement is named). Pulls that would
  worsen the donor are hidden and counted (`hidden_donor_pulls` + reason).
- **Honest unavailability**: scoring signals that do not live in Postgres yet
  (edges, pillar fit, EDHREC, played-with) are reported in
  `inputs_unavailable` with the reason — never dressed up as a 0.

## UI

SvelteKit routes: `/drafts` (start a draft, list sessions) and
`/drafts/{id}` (staged list, staged moves, suggestions, Commit / Abandon).
This replaces the old Redis-session tuneup draft flow; session state now
lives in the Postgres staging tables.

## API surface

| Method & path | Purpose |
| --- | --- |
| `POST /api/v2/drafts` | Open a draft (kind, sources, target name, prefill). Staging only. |
| `GET /api/v2/drafts` | List sessions, newest first (`?status=`). |
| `GET /api/v2/drafts/{id}` | Full staging view: draft + cards + moves. |
| `POST /api/v2/drafts/{id}/cards` | Add/update a staged card row. |
| `DELETE /api/v2/drafts/{id}/cards/{oracle_id}/{board}` | Remove a staged card row. |
| `POST /api/v2/drafts/{id}/moves` | Stage an instance move (assign/unassign). |
| `DELETE /api/v2/drafts/{id}/moves/{instance_id}` | Drop a staged move. |
| `GET /api/v2/drafts/{id}/suggestions` | Tuner cuts + reconstruct adds (own-first). |
| `POST /api/v2/drafts/{id}/commit` | ONE-transaction commit + provenance events. |
| `POST /api/v2/drafts/{id}/abandon` | Delete all staging rows (zero residue). |
| `GET /api/v2/instances/{id}/provenance` | Copy-level provenance path. |

## Tests

`tests/test_draft_mode.py` (19 tests, SQLite in-memory): create validation,
prefill union, move state machine, commit plan (events + updates, rejects
empty/closed), staging isolation (canonical snapshot unchanged before
Commit), one-transaction commit, abandon zero residue, provenance path, and
the suggestion pools/cuts (own-first ordering, donor-no-worse hiding, honest
unavailable markers). The same flow is also verified live against real
Postgres (migration `a005` idempotent).
