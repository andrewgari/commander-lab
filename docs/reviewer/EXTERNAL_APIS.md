# External APIs behind the fact pack and candidate pool

Checked live on 2026-10-26 (card 05). Clients: `src/server/domain/analytics/fact_sources.py` (Scryfall, Spellbook, cache), `analytics/providers/edhrec.py`, `analytics/providers/commandersalt.py`. CI uses recorded fixtures only (`tests/fixtures/fact_pack/`, re-record with `record_fixtures.py`).

## Scryfall — official, stable
- Base `https://api.scryfall.com`, HTTPS only. Docs: <https://scryfall.com/docs/api>
- **Required headers:** `User-Agent` (accurate app name/version; don't let the HTTP library choose) and `Accept`. We send `commander-lab/1.0 (+https://github.com/andrewgari/commander-lab)` + `application/json`.
- **Rate limits** (<https://scryfall.com/docs/api/rate-limits>): `/cards/search`, `/cards/named`, `/cards/collection`: 2/s (500 ms); other endpoints 10/s. HTTP 429 locks you out for 30 s; repeated abuse → ban. We space requests ≥ 550 ms (`SCRYFALL_MIN_INTERVAL`).
- **Caching:** Scryfall asks for ≥ 24 h caching; prices update once a day. Default TTL 24 h. Bulk data exists for large jobs; a review needs ≤ a handful of calls.
- `POST /cards/collection` (<https://scryfall.com/docs/api/cards/collection>): ≤ 75 identifiers per call; misses come back in `not_found` (we treat them as unavailable, not as errors). Gives `oracle_text`, `type_line`, `cmc`, `color_identity`, `legalities`, `prices.usd`, `produced_mana`, `game_changer`, `oracle_id`.
- `GET /cards/search`: `q=f:commander id<=wubg usd<=5 otag:ramp`, `order=edhrec`. 404 means "no results". `otag:` (Tagger) is unofficial-but-stable syntax. Prices are strings or null; null price + budget cap ⇒ card excluded.
- Data policy: don't paywall/republish/proxy Scryfall data; we add analysis.

## Commander Spellbook — official public API, polite use
- `POST https://backend.commanderspellbook.com/find-my-combos` with `{"commanders":[{"card":..}],"main":[{"card":..}]}`. OpenAPI: <https://backend.commanderspellbook.com/schema/> (v7.3.1 at time of writing); repo <https://github.com/SpaceCowMedia/commander-spellbook-backend>.
- Response `results`: `included` (complete combos), `almostIncluded` (one card missing; we keep only those with exactly one name absent from the deck), `almostIncludedByAddingColors`, plus `…ByChangingCommanders` buckets. Paginated via `next` (existing `interactions/combos.py` pages up to 10).
- **Guidance from the schema description:** sparse unauthenticated requests, "a few HTTP calls per user interaction"; name your service in `User-Agent`; **80 requests/min is safe**; handle `429`; don't crawl the whole dataset (use `https://json.commanderspellbook.com/variants.json.gz` instead).
- Variant `bracketTag` (e.g. `R` ruthless, `E`, `S`…), `popularity`, `identity`, `produces[].feature.name`. We cache 24 h (`FACT_PACK_SPELLBOOK_TTL_HOURS`).

## EDHREC — **no official API**
- Unofficial static JSON: `GET https://json.edhrec.com/pages/commanders/{slug}.json` (slug = lowercase, hyphenated, punctuation stripped: `atraxa-praetors-voice`; partners joined). Community references: <https://github.com/j4th/mtg-mcp-server/blob/main/docs/SERVICE_CONTRACTS.md>, <https://mightstone.readthedocs.io/>, `pyedhrec`.
- Observed 2026-10-26: S3 + CloudFront, `cache-control: public, max-age=1620`, 200 without auth; `robots.txt` doesn't disallow `/pages`. No published rate limit. We throttle ≥ 0.6 s between calls (existing client), retry 429/5xx with backoff, and cache 24 h.
- **Shape we rely on:** `container.json_dict.card.{name,num_decks,rank,salt}` and `container.json_dict.cardlists[].cardviews[].{name,num_decks,potential_decks,synergy}`. Inclusion = `num_decks / potential_decks` (the `inclusion` field some wrappers mention is **absent** in the live payload). Pages list ~250 cards, so "not listed" ≈ "rare for this commander".
- **Fragility:** endpoints can change without notice. Any change in that shape yields `{"status":"unavailable","reason":"EDHREC response shape changed …"}` for the EDHREC section; the rest of the pack is unaffected.

## Commander Salt
Existing `providers/commandersalt.py` (needs a deck URL; ingest is async). Absent ⇒ `salt: unavailable` with the reason.

## Norms cited in the fact pack
- Lands / ramp / draw / removal / wipes targets: The Command Zone ep. 658 template (`composition.DEFAULT_TEMPLATE`, band = target ±1). Protection, recursion and tutors have no published norm ⇒ `unavailable`.
- Colored sources: Frank Karsten, *How Many Sources Do You Need…? 2022 update*, 99-card column <https://www.tcgplayer.com/content/article/How-Many-Sources-Do-You-Need-to-Consistently-Cast-Your-Spells-A-2022-Update/dc23a7d2-0a16-4c0b-ad36-586fcca03ad8/> (code: `fact_sections.KARSTEN_99`).

## Env
`FACT_PACK_{SCRYFALL,SPELLBOOK,EDHREC,SALT}_TTL_HOURS` (default 24). Existing: `SPELLBOOK_BASE_URL`, `SPELLBOOK_MAX_AGE_DAYS`, `EDHREC_MAX_AGE_DAYS`.
