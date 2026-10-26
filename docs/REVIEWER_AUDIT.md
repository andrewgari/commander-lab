# Reviewer Audit — Deck Review Pipeline

Verified by reading code at HEAD `34d7aaa` (2026-10-25). Where something is absent it says "not present in code". Two distinct systems share the word "review":

- **A. `generate_deck_review`** — the deterministic review in `analytics/report.py` (what this audit's section numbering targets).
- **B. RV-3 "Deep Review"** — `analytics/review_doc.py` + `analytics/review_metrics.py`: deterministic metrics + an LLM ("oracle") prose layer, saved to Redis with history. This is the closest existing thing to the target architecture.

---

## 1. End-to-end trace of `generate_deck_review`

Entry points:
- `deck_review.py:37-38` — `review_deck = generate_deck_review` (facade re-export).
- `analytics/__init__.py:83` — re-export. Parity of all three is asserted in `tests/test_deck_review_integration.py:156 test_facade_parity`.
- HTTP: `app.py:2268 api_deck_review` (`POST /api/review`, `/api/decks/review`) — loads deck via `registry.find_deck(r, deck_id)`, calls `engine_mod.load_staple_theme_vocab(r)` then `review_deck(deck=..., intent=parse_user_intent(...))`. **No external metrics or pricing are passed.**
- CLI: `cli.py` `review` subcommand (tested in `test_deck_review_integration.py:193`).

Flow (`analytics/report.py`):
1. `generate_deck_review(deck, intent, external_metrics, card_metadata, external_recommendations, pricing)` → `default_review_pipeline.review(...)` (`report.py:614`, `DeckReviewPipeline.review`, class at ~`report.py:540`).
2. `DeckAnalysisEngine.analyze(...)` (`analytics/engine.py:670`) — pure heuristics over the decklist + optional `card_metadata` dict. **No network, no LLM.** If `card_metadata` is not supplied, card data (cmc, types, salt) is resolved only from in-module staple tables (`engine.py:271` salt staples, `:338` staple salt cache, `:448` ramp names) — unknown cards get `salt: None` (`engine.py:833`).
3. `RecommendationGenerator.generate(...)` (`analytics/recommendations.py:876`) — scores candidates from `CURATED_CARD_CATALOG` against detected archetypes/curve/budget; pure Python.
4. Synthesis of `identity_summary`, `strengths`, `cohesion_gaps`, `upgrades_summary` — string templating in `report.py` (e.g. `_build_upgrades_summary` ~`report.py:595`).
5. Output: `DeckReview` pydantic model with `to_markdown()/to_cli()/to_json()` via `DeckReportFormatter` (`report.py:112`).

Data inputs: decklist text or deck dict (parsed via `deck_intake.py` → `decklist_parser.py`), `UserDeckIntent` (`user_intent.py`), optional `DeckAnalyticsResult` from the aggregator, optional pricing map.
**LLM call sites: none.** `generate_deck_review` is 100% deterministic templating.

## 2. Recommendations origin — `CURATED_CARD_CATALOG`

- `analytics/recommendations.py:213` — a hand-written Python list of **49 `CuratedCard` entries** (verified by grep of `CuratedCard(`).
- Freshness: anchored to `RECENT_SETS` (`recommendations.py:98`) — **16 sets, 2022–2024 only** (newest: FDN/DSK/BLB/MH3 2024). "Recent" means `set_code in RECENT_SETS`; nothing 2025+ exists. Maintenance is fully manual code edits; no refresh script, no Scryfall/EDHREC backfill of the catalog.
- Quality limits:
  - Coverage ceiling: only ~49 candidate cards across all archetypes, so additions/swaps repeat the same staples; `_evaluate_candidate_additions` (`:1038`) can only rank what exists.
  - Cuts (`_evaluate_candidate_cuts` `:1220`) are heuristic (off-theme flag, low synergy score, curve outliers), no play-rate or synergy-graph data.
  - Swaps (`_formulate_card_swaps` `:1329`) pair by category fit, not verified legality/color identity (color-identity checking is not present in code in this module).
  - Prices are static `approx_price_usd: float = 2.0` defaults unless a `pricing` map is passed in — and `api_deck_review` never passes one.
  - No dedup against owned inventory; `is_already_in_deck` (`:90`) only checks the deck itself.

## 3. `prompts/` and `providers/`

`prompts/`: contains exactly one file, `prompts/reconstruct_builder_guide.md` — a human guide doc, **not LLM prompt templates**. The actual LLM prompts live inline in code:
- `analytics/deck_writeup/prompts.py` — DESCRIPTION_SYSTEM / PRIMER_SYSTEM + VOICE_PRESETS.
- `analytics/interactions/llm.py:67` — SYSTEM_PROMPT for produce/consume tagging.
- `analytics/genre_text_improve.py` — small reword prompt with `RESPONSE_SCHEMA`.
- `analytics/card_review/prompts.py` — per-card debate prompts (remove/keep/judge).
- `analytics/review_doc.py:60` — oracle instruction "reply with exactly one fenced ```json block matching review.schema.json".

`providers/` (top-level — **decklist fetchers**, not LLM providers):
- `providers/archidekt.py` — official-ish v3 JSON API; `parse_identifier`, `list_decks` (paged `ownerId` pull), single-deck fetch for `/api/import`.
- `providers/moxfield.py` — unofficial `api2.moxfield.com/v3/decks/all` with browser `USER_AGENT`; doc notes it may 403 and the fallback is a Playwright scrape (not implemented here).
- `providers/raw_paste.py` — raw text paste parser.
- `providers/errors.py`, `providers/__init__.py` (dispatch).

`analytics/providers/` — analytics data providers:
- `analytics/providers/edhrec.py` — `EDHRecClient`: `json.edhrec.com` unofficial JSON; `max_retries=3` exponential backoff (`:145-207`); **in-memory dict cache per commander slug, per-process, no TTL, no Redis** (`:299-316`). Typed errors: `EDHRecNotFoundError/RequestError/ResponseError`.
- `analytics/providers/commandersalt.py` — `api.commandersalt.com/decks?id=...` unauthenticated; distinguishes `CommanderSaltNotIngestedError` (lazy ingestion, 200 + `status.exists=False`) from real 404.
- `analytics/provider.py` + `analytics/registry.py` — `BaseAnalyticsProvider` + registry; `analytics/aggregator.py` runs providers concurrently with `ThreadPoolExecutor`, per-provider timeouts and error isolation.

LLM providers (structured output / retries / caching / model choice):
- `analytics/interactions/llm.py` — chain: **Gemini primary** (`gemini-3.8-flash` default; `GOOGLE_API_KEY`/`GEMINI_API_KEY`) → **Nous** fallback (`Hermes-4-70B`, `NOUS_API_KEY`) → anthropic slot present but never configured (raises). JSON-only instruction; enforcement is post-hoc: labels outside `vocab.py` are dropped; token usage + USD estimate in `service.RunLog` (`PRICING` table). Tags cached forever in Redis per card (`analytics/interactions/tagger.py:170`, no TTL).
- `analytics/oracle_client.py` — OpenAI-compatible chat to the "oracle" Hermes gateway (`ORACLE_URL`/`ORACLE_KEY`, 600 s timeout floor); typed `OracleUnavailable/Auth/Response` errors. Used by `review_doc.build_review` (`:406-418`) and `analytics/card_review/llm.py` `OracleLLM`.
- Structured-output enforcement: `review_doc.validate_prose` (`:130`) — fenced-block extraction, pydantic `ReviewProse` validation, fingerprint echo check, and number-carrying sentences not backed by metrics are stripped; failure → doc saved with `INVALID_NOTE`.
- Per-call model choice: hard-coded per module (Gemini flash for tagging; "oracle" for review/writeup/debate). No per-call model selection, no LLM response caching beyond the tagger's card-tag cache, retries only in EDHREC/Salt HTTP clients, not for LLM calls.

## 4. `DeckReview` JSON schema

`analytics/report.py:48 class DeckReview` (pydantic v2). Sections:
`deck_name`, `format` (default "commander"), `commanders: [str]`, `overall_score 0-100`, `identity_summary`, `thematic_assessment: ThemeAnalysisBreakdown` (score, consistency_tier, primary_archetype(+score), secondary_archetypes, thematic_tags_matched, off_theme_cards, excluded_archetype_violations, mechanic_distribution, description — `engine.py:80`), `playfeel_summary: PlayfeelAnalysisBreakdown` (salt_score/tier, high_salt_cards, salt_categories, playfeel_label/summary — `engine.py:206`), `curve_summary: CurveAnalysisBreakdown` (score, speed_score/tier, avg/median cmc, ramp counts, estimated_operational_turn — `engine.py:~120`), `cohesion_summary: CohesionAnalysisBreakdown`, `intent_alignment: IntentAlignmentReport`, `strengths: [str]`, `cohesion_gaps: [str]`, `recommendations: RecommendationSet` (additions, cuts, swaps, recent_set_cards, unique_synergies, intent_aligned_upgrades, category_counts, summary — `recommendations.py:153`), `upgrades_summary`, `metadata`. Serializers: markdown/CLI/JSON via `DeckReportFormatter`.

Separately, the RV-3 Deep Review doc schema: `MATH_SECTIONS = (genre, composition, curve_tempo, focus, strong_cards, stragglers, combos_engines) + oracle_summary` (`review_doc.py:46-52`), versioned `SCHEMA_VERSION="1"`.

## 5. External sources actually wired

| Source | Wired? | Fetch path | Caching | Rate limits / retries | Failure modes |
|---|---|---|---|---|---|
| Archidekt | Yes | `providers/archidekt.py` + account sync `sync.py` | Redis (`decks` key etc.) | none in code (plain `requests`) | exceptions propagate; parse errors via `parse_identifier` |
| Moxfield | Yes (single public decks) | `providers/moxfield.py` | none | browser UA; no retries | 403 risk documented; ValueError on bad id |
| Scryfall | Yes | `src/integrations/scryfall.py` (async `ScryfallClient` on `src/shared/http/client.py`), plus `analytics/prices.py` (`/cards/collection`), `analytics/brackets.py` (`is:gamechanger`) | SharedHttpClient in-memory TTLCache (300 s, 256 LRU, `:62-64`); prices in Redis `price:card:{name}` 24 h with stale-on-outage fallback; game changers Redis `CACHE_TTL_S` + checked-in snapshot `analytics/data/game_changers.json` | token-bucket `src/shared/http/rate_limiter.py` (Scryfall 10 rps); typed `ScryfallRateLimitError(429)` | typed error tree; stale-cache fallback for prices; snapshot fallback for game changers |
| EDHREC | Yes | `analytics/providers/edhrec.py` (+ `src/integrations/edhrec.py` for the unified card service; `analytics/edhrec_inclusion.py`) | per-process slug dict, no TTL | 3 retries × exp backoff | typed NotFound/Request/Response errors |
| Commander Salt | Yes | `analytics/providers/commandersalt.py` (+ `src/integrations/commandersalt.py`) | none visible | retries per client | `NotIngested` distinguished from `NotFound` |
| TCGplayer | **URL builder only** | `tcgplayer.py build_tcgplayer_url` — mass-entry cart links | n/a | n/a | No TCGplayer API/pricing integration; prices come from Scryfall (`analytics/prices.py`) |

README claims not (fully) implemented:
- README intro lists TCGplayer implicitly nowhere — it lists Archidekt/Moxfield/Scryfall/EDHREC/Commander Salt; TCGplayer integration is only cart URLs (above).
- README "Tech Stack" says **Tailwind CSS (CDN) + FontAwesome** — the actual UI is the HUD kit (`static/css/hud.css`, `templates/partials/hud.html`) per AGENTS.md/CLAUDE.md; stale doc.
- AGENTS.md says tests run via `python tests/<test_name>.py`; README says `python -m unittest discover -s tests` — tests are pytest-style (`conftest.py`, `tests/fixtures`).

## 6. Analytics inventory (deterministic vs heuristic)

| Analytic | Code | Type |
|---|---|---|
| Bracket table + Game Changers | `analytics/brackets.py` (CONSTRAINTS HARD vs ADVISORY) | **Deterministic** (Game Changer count, MLD, extra-turn chains from list membership) + advisory-only heuristics never used for placement |
| Bracket evaluation | `analytics/bracket_eval.py evaluate_bracket` | **Deterministic**, "pure, no LLM, no network" (module docstring) |
| Bracket check worker | `analytics/bracket_check.py`, `bracket_worker.py` | deterministic checks + regex fallback on oracle text |
| Mana curve / speed | `engine.py _analyze_curve` (`:1078`) | **Heuristic scoring** over deterministic inputs (avg/median cmc, ramp counts) |
| Curve & tempo (RV-2) | `analytics/review_metrics.py` — hypergeometric land/ramp model, P(on-curve commander), expected unspent mana | **Deterministic** ("byte-identical JSON", no clock/random/LLM) |
| Mana/categories | `analytics/categories.py`, `composition.py` (Command Zone template) | deterministic counting over autotag tags |
| Theme/archetype | `engine.py _analyze_theme` (`:892`) + `archetypes.py` + Redis-backed `staple_theme_registry.py` | **Heuristic** keyword/staple matching |
| Cohesion/synergy | `engine.py` cohesion analysis | heuristic |
| Salt/playfeel | `engine.py` playfeel + `analytics/providers/commandersalt.py` | heuristic + external |
| Genre | `analytics/genres.py genre_match` | **Deterministic** ("pure function", role coverage vs registry) |
| Roles / produce-consume tags | `analytics/interactions/` (`tagger.py`, `oracle_rules.py`, `llm.py`) | deterministic rules first, **LLM fallback** for untagged cards, cached forever in Redis |
| Combos/engines | `analytics/interactions/combos.py`, `engines.py`, `graph.py` | deterministic graph over cached tags |
| Bracket/power estimate | `engine.py` playfeel `power_level_estimate` | heuristic |

## 7. Storage: Redis vs Postgres

- **Postgres is not present in code on main**: no `db/`, no `alembic.ini`, no `redis_pg_import.py`, no sqlalchemy/psycopg imports anywhere outside `.worktrees/` (unmerged in-progress branches `t_8bd32ce2`, `t_405e48ad` contain an alembic + `models/` SQL schema — not merged).
- Source of truth is Redis. Verified keys:
  - Decks/inventory/status/tags: `decks`, `inventory`, `deck_status:{id}`, `card_tags:{name}` (CLAUDE.md §Domain).
  - Card metadata: `card_meta:{name}` migrating to oracle-id keys (`cards.py` header; `scripts/migrate_card_meta_to_oracle_id.py`).
  - Reviews (RV-3): `review:{deck_id}` latest, `review:index:{deck_id}` zset history, `review:doc:{deck_id}:{doc_id}`, `review:job:{job_id}` 7-day TTL, `HISTORY_KEEP=20` (`review_doc.py:14-19,36-39`).
  - Genres `genres` + `genres:version`; categories `categories:global`, `categories:deck:{id}`; prices `price:card:{name}`; interaction tags `tag:{name}` (no TTL); staple themes `staple_themes`.
  - Intent: parsed per request (`user_intent.parse_user_intent`); **no dedicated intent persistence key found** — intent lives inside Deck Journal / intake payloads (journal in `analytics/journal.py`).
- **`generate_deck_review` output (system A) is never persisted** — `api_deck_review` returns it inline; only system B reviews are stored.

## 8. Tests & fixtures usable to seed an eval set

- `tests/test_deck_review_integration.py` — 3 full end-to-end decks with intents: `SAMPLE_ATRAXA_DECKLIST` (counters), Wilhelt aristocrats (`:118`), Stella spellslinger (`:140`); facade parity, CLI, and API tests. **Best eval seeds.**
- `tests/test_report_formatter.py` — builds review via `generate_deck_review` + minimal deck (`:57`,`:142`).
- `tests/test_analysis_engine.py`, `tests/test_recommendations.py`, `tests/test_budget_rules.py` — unit-level expectations for scores/recs.
- `tests/test_edhrec_provider.py` + fixtures `tests/fixtures/edhrec_atraxa_commander_page.json`, `edhrec_kaalia_commander_page.json` — recorded EDHREC payloads.
- `tests/fixtures/bracket_check_decks.json`, `personality_real_decks.json`, `spellbook_holy_hellfire.json`, `holy_hellfire_cards.json`, `theory_doc_holy_hellfire.json`, `card_review_job_{keep,remove}.json` — more deck/job corpora.
- `tests/test_review_doc.py`, `test_review_metrics.py` — golden behavior for the deterministic RV-2/RV-3 pipeline (scripted fake LLM via `DebateLLM` protocol pattern in `card_review/llm.py`).
- `tests/conftest.py` — shared Redis fakes; `tests/shared/http/` — fake HTTP client + rate-limiter tests usable to mock Scryfall.

## 9. Gaps vs target architecture (ranked by impact on review quality)

Target: fact pack → intent+player profile → multi-pass analysis → candidate retrieval → recommendations → verifier → writer → render.

1. **No candidate retrieval** (biggest). Recommendations come from a static 49-card catalog frozen at 2024 sets. No EDHREC/Scryfall-backed candidate generation per deck. Fix = biggest quality jump.
2. **No verifier.** Nothing checks legality, color identity, format bans, or that suggested cards exist/aren't already owned before rendering. `review_doc`'s number-stripping validates prose, not card picks.
3. **No writer/LLM pass in `generate_deck_review`.** Identity/strengths/gaps are string templates; system B has an oracle writer but only for prose over metrics — the two systems aren't unified.
4. **No fact pack.** Engine re-derives everything from decklist + hardcoded staple tables; `card_metadata`/pricing aren't populated by the API path, so unknown cards score as blanks (`engine.py:833`). A single assembled fact pack (Scryfall + EDHREC + Salt + prices + tags) would feed both systems.
5. **Intent + player profile partially present**: `user_intent.py` + `analytics/personality*.py` + journal exist, but intent isn't persisted as first-class and isn't threaded into system B; profile data doesn't reach recommendations.
6. **Multi-pass analysis partially present** (RV-2 metrics + genre + composition + graph), but system A's engine is single-pass heuristics and duplicates RV-2 concepts (curve, theme) with different math — two sources of truth for the same numbers.
7. **Render** is fine (markdown/CLI/JSON + review UI), but system A renders unverified template text.
8. **No persistence of system A reviews** — no history/diffing for the `/api/review` output.

## 10. Proposed small-PR sequence

1. **PR1 — Fact pack builder** (new `analytics/fact_pack.py`): assemble Scryfall metadata + prices + cached tags + EDHREC/Salt aggregates for a deck; pure function over injectable fetchers. Tests: recorded fixtures (`edhrec_*.json`, `holy_hellfire_cards.json`). No user-facing change (PR-template checkbox).
2. **PR2 — Wire fact pack into `/api/review`**: pass `card_metadata`+`pricing` into `review_deck`; no schema change. Tests: API test asserting unknown-card metadata now populated. CHANGELOG: "Fixed: deck reviews no longer score unknown cards as blanks."
3. **PR3 — Candidate retrieval v1**: extend `RecommendationGenerator` with an EDHREC-backed candidate source merged with `CURATED_CARD_CATALOG` (catalog becomes fallback). Tests: fake EDHREC client; catalog-fallback test. CHANGELOG: "Changed: recommendations now draw from live EDHREC data, not only a fixed card list."
4. **PR4 — Verifier pass** (`analytics/review_verify.py`): legality + color identity + already-in-deck/owned filters on additions/swaps; strip-and-annotate on failure. Tests: illegal-color and banned-card cases. CHANGELOG: "Fixed: reviews no longer suggest cards outside your commander's color identity."
5. **PR5 — Refresh RECENT_SETS + catalog freshness job**: move RECENT_SETS to data file + `scripts/refresh_*.py` pattern already used for game changers. Tests: snapshot load. CHANGELOG: "Changed: 'recent set' recommendations now include 2025 sets."
6. **PR6 — Unify curve/theme numbers**: make system A consume `review_metrics.compute_review` sections instead of its own heuristics where they overlap. Tests: parity goldens from `test_review_metrics.py`. No CHANGELOG (internal).
7. **PR7 — Persist system A reviews** into the existing `review:*` history keys. Tests: history round-trip. CHANGELOG: "Added: quick reviews are now saved alongside deep review history."
8. **PR8 — Writer pass**: route system A's `identity_summary`/`upgrades_summary` through oracle with the `review_doc` validation pattern; deterministic template as offline fallback. Tests: scripted fake oracle + offline fallback. CHANGELOG: "Added: narrative summaries in deck reviews (with offline fallback)."

Each PR is independently revertable; PR1–PR2 are prerequisites for everything else; PR3/PR4 deliver the largest user-visible quality gain.
