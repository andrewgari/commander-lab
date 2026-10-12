# Deck Journal + review check-in (AN-5)

Spec: `docs/ANALYTICS_SPEC.md` Layer 2 and `docs/design/analytics/UX_SPEC.md` §3.3.
Code: `analytics/journal.py` (store, caps, check-in), `analytics/prices.py`
(Scryfall USD cache), `templates/deck_journal.html`, `user_intent.py` + the schema.

## What it is

The Deck Journal is a form, not a chat. Fill it once, edit it anytime. It
holds the deck's `user_intent` (vision, win cons, target power, budget)
plus four AN-5 fields, which are also in `schemas/user_intent.schema.json`:

| Field | Meaning |
|---|---|
| `pet_cards[]` | never suggested as cuts (weak performers and Tune read them) |
| `novelties` | things you want to see happen; a bonus, never a requirement |
| `budget_constraints.hard_cap` | per-card USD cap: the tuner never suggests a card above it |
| `budget_constraints.soft_cap` | per-card USD cap: allowed, but flagged with a justification |

`max_card_price` is kept as an alias of `hard_cap`. When both are sent,
`hard_cap` wins, and the stored `max_card_price` always equals it. The power
tier is derived from the 1–10 scale and never picked separately. Pillars
live on Identity; the journal links to them.

Every review opens with a check-in, "is this still true?". It shows the
change since the last review (+/− cards, power estimate, price, on-plan %)
and then one row per field. A row's evidence line is computed: deck price
vs. budget, cards over the caps, a pet that left the deck or is the
weakest pet, the power estimate vs. the target, and draft or proposed
pillars. Answers save per row. A Change opens that form section inline
and saves it straight into the journal. Undo puts back the previous value.
Unanswered rows count as still true. Finish (or Skip) appends the review to
the history, and the next check-in diffs against it.

## Entry points

- `/deck/{name}/journal` is the Journal tab (key `4`).
- `/deck/{name}/journal?review=1` is the check-in. The Overview **Review**
  button and the deck list's review action both go here (UX_SPEC D4).
- Finish lands on `?reviewed=1`. When Tune (AN-6) ships, `finish` returns
  `/deck/{name}/tune` as `next` instead.

## API

| Method | Path | Body / result |
|---|---|---|
| GET | `/api/decks/{id}/journal` | `{exists, journal, caps, meta, last_review, open_review, deck_cards[], metrics{power, price, on_plan, unpriced}, pillars[]}` |
| PUT | `/api/decks/{id}/journal` | whole top-level sections, merged in; 400 `{errors[]}` when invalid |
| GET / PUT | `/api/journal/defaults` | global caps `{hard_cap, soft_cap}` (default $30 / $10) |
| GET | `/api/decks/{id}/checkin` | the open check-in, or `null` |
| POST | `/api/decks/{id}/checkin` | open, or resume with fresh metrics; 409 if there is no journal |
| PATCH | `/api/decks/{id}/checkin/rows/{field}` | `{answer: "true"}`, `{answer: "changed", journal: {section}}`, or `{answer: null}` (undo) |
| POST | `/api/decks/{id}/checkin/finish` | `{skipped?: bool}` returns `{review, next}`; 409 if nothing is open |
| GET | `/api/decks/{id}/reviews` | history, newest first |

The check-in fields, in order, are `vision`, `win_cons`, `power`, `budget`,
`pets`, `novelties`, `pillars` and `cards`. Each row may only change its own
section(s). `pillars` links to Identity, and `cards` is context only
("✓ Seen").

`{id}` can be the deck id, the registry_id, or the deck name.

## Redis

| Key | Type | Contents |
|---|---|---|
| `journal:deck:{registry_id}` | string (JSON) | resolved intent + `_journal{created_at, updated_at, baseline}` |
| `journal:defaults` | string (JSON) | global `{hard_cap, soft_cap}` |
| `journal:review:open:{registry_id}` | string (JSON) | the check-in in progress (answers + the metrics it opened with) |
| `journal:reviews:{registry_id}` | list (JSON) | finished reviews, newest first, at most 50 |
| `price:card:{name}` | string (JSON) | `{usd, at}` from Scryfall, refreshed after 24h |

Each review stores a snapshot: `{at, cards{name: qty}, power, price,
on_plan}`. The first save of the journal records the same snapshot as its
`baseline`, so the first check-in has something to diff against.

## Metrics (deterministic, no LLM)

- **Power estimate**: `analysis.playfeel_summary.power_level_estimate` from the
  existing deck review engine.
- **Price**: the sum of Scryfall USD × quantity. A card uses its default
  printing's `usd`, then `usd_foil`, then `usd_etched`. If that printing has
  no price (new or promo sets), the cheapest priced printing is used. The
  per-card cache lasts 24h; if Scryfall is down, the last known value is used.
- **On-plan %**: AN-3 pillar fit (`identity.fit.on_plan_pct`).
- **Weakest pet**: the AN-3 weak-performers `pet_notice`.

A missing input leaves its metric as `null`, which the UI shows as "—".
It never makes the page fail.
