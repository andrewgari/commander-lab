# Theory (TH-2): backend

Spec: `docs/THEORY_SPEC.md`. Code: `analytics/theory.py`, routes in `app.py` (section "Theory").
Tests: `tests/test_theory.py` (oracle always mocked). The UI is TH-3.

## Oracle skill

`deck-theory` lives in oracle's profile on the Framework:
`~/.hermes/profiles/oracle/skills/mtg/deck-theory/` (`SKILL.md` + `references/theory.schema.json`).
A copy is kept in this repo at `docs/oracle/deck-theory/` so the contract test can check that the
skill's example still validates against `TheoryDoc`. **Edit both together**: change the profile
copy, then `cp -r ~/.hermes/profiles/oracle/skills/mtg/deck-theory docs/oracle/`.
Oracle's SOUL and toolsets are untouched; the skill only needs `skills` + `web` (both already on
`api_server`).

The app sends a system message ("load and follow `deck-theory`") and one user message with the
request JSON: commander(s), colour identity, the 100, Journal (thesis, pillars, pet_cards),
`free_agent_owned` (owned cards with a copy in no deck; colour-filtered in keep mode), caps,
theme and `commander_mode`. Oracle replies with prose + one fenced json block.

## Doc

```
{schema_version: "1", doc_id, deck_id (registry_id), theme, commander_mode: keep|open, created_at,
 sections: [{type, title, data, ...}], verification: {checked_at, corrections[], counts, buy_total_usd,
 commander_changed, commanders}}
```

v1 section types: `thesis_pillars`, `decklist` (`groups[{role, cards[{name, qty, source, price,
over_soft_cap?, why?, salt_flag?}]}]`, `counts`), `cuts` (`cards[{name, qty, reason?, pet?,
commander?}]`), `buy_total` (`total_usd`, `count`, `cards`, `caps`), `commander_change`
(`old_commanders`, `new_commanders`, `reason`, `reuse_count`, `pets_lost`).
**Unknown section types (and unknown keys) are preserved verbatim, in oracle's order.** The app
sets `deck_id`, `theme`, `commander_mode`, `created_at` and `doc_id` itself.

## Verification (oracle is never trusted on facts)

| check | rule |
|---|---|
| resolve | every card via Scryfall `/cards/collection`; unknown → dropped |
| legality | `legalities.commander == legal`, else dropped (commanders exempt) |
| colour identity | inside the (new) commander's identity, else dropped |
| singleton | duplicates dropped / qty forced to 1 (basics + "any number" cards exempt) |
| source | in this deck → `kept`; basic or free owned copy → `owned`; else `buy`. A card sleeved only in another deck is a `buy` (never pulled from other decks) |
| price | every card re-priced from Scryfall USD (`analytics/prices.py`, 1-day cache) |
| caps | buy over hard cap or unpriced → dropped; over soft cap → `over_soft_cap: true`; `no_budget` disables both |
| cuts | recomputed from the real diff; oracle's reasons kept |
| buy_total | sum of verified buy prices |
| commander | `keep` mode: any `commander_change` is removed. `open`: kept only if the commander really changed; `reuse_count` recomputed; pets now off-identity go to `pets_lost` |
| pets | a pet cut without a legality reason is recorded (`pet_missing`) |
| count | not 100 after fixes → recorded (`card_count`), not padded |

Every fix is a `verification.corrections[]` entry `{kind, message, card?, ...}`.

## Storage

- `theory:{deck_id}:{doc_id}`: doc JSON (no TTL)
- `theory_index:{deck_id}`: sorted set, doc_id scored by created_at (epoch)
- `theory_job:{job_id}`: job state, 7-day TTL

## API (`{id}` = deck id, registry_id, or display name)

| method | path | result |
|---|---|---|
| POST | `/api/decks/{id}/theory` `{"theme", "commander_mode": "keep"\|"open"}` | 202 `{job_id, status_url}`; 400 bad input; 503 `{"error": "oracle offline, try again later", "error_kind": "oracle_offline"}` when oracle is down / not configured (8 s `/v1/models` preflight) |
| GET | `/api/decks/{id}/theory/jobs/{job_id}` | `{job: {status: queued\|running\|done\|failed, doc_id, summary?, error?, error_kind?}}` |
| GET | `/api/decks/{id}/theory` | `{docs: [summary]}` newest first |
| GET | `/api/decks/{id}/theory/{doc_id}` | `{doc}` |
| DELETE | `/api/decks/{id}/theory/{doc_id}` | `{deleted}` or 404 |

A job that loses oracle mid-run ends `failed` with `error_kind: oracle_offline` and the same
message. Jobs run in a daemon thread in the app process; a container restart mid-job leaves the
job `running` until its TTL (the UI should treat a stale `running` as failed).
