# Deck Reviewer Audit (card 00)

Audited at `origin/main` 16b682a (2026-10-26). Builds on `docs/REVIEWER_AUDIT.md` (Oracle, 2026-10-25, HEAD 34d7aaa). That earlier audit predates the rearchitecture, so much of it is stale (see "Corrections" below). Every claim cites `path:function`; `UNVERIFIED` marks what I could not confirm and why.

## Corrections to `docs/REVIEWER_AUDIT.md`

| Old claim | Status at 16b682a |
|---|---|
| Entry point is `app.py:api_deck_review` | Root `app.py` is gone. Handler is `src/server/app/main.py:api_deck_review`. |
| "Postgres is not present in code on main" (§7) | **Wrong now.** Postgres is wired: `src/server/infra/db/orm_models.py` (about 25 tables), alembic migrations `a001`–`a008`, `src/server/infra/db/repositories.py`. |
| "Reviews (RV-3) stored in Redis `review:*`" | Still true for the legacy RV-3 path (`src/server/domain/analytics/review_doc.py`), but a Postgres-backed review pipeline now exists (see Q1). |
| "No fact pack" | Two exist (see Q1): the card-facts pack `domain/analytics/fact_pack.py` and a deterministic-metrics pack `domain/review/fact_pack.py`. |
| "No candidate retrieval, no verifier" | Both exist in the **v2** pipeline (`domain/review/candidates.py:retrieve`, `domain/review/verifier.py`). They are absent from the legacy `/api/review` path. |
| Tests "pytest-style (`conftest.py`)" | `tests/conftest.py` is 5 lines (sets `INTERACTIONS_DISABLED`); it has no shared fakes. CI runs both `unittest discover` and a named pytest list (`.github/workflows/ci.yml`). |

The old audit's analysis of the legacy system (catalog size 49, RECENT_SETS, EDHREC client behavior) still holds; I re-verified the counts below.

---

## 1. Review paths end to end

**There are three review paths, not two. Which one the card body calls "reviews.py" matters.**

### 1a. Legacy `POST /api/review` and `POST /api/decks/review` (same handler)
- Route: `src/server/app/main.py:api_deck_review`. `src/server/app/api/reviews.py` does **not** contain it; that file is the v2 router (see 1b).
- Inputs: JSON body `deck_id` | `deck` | `decklist` | `cards`, optional `user_intent`.
  - Deck loaded from **Redis** via `src/server/domain/analytics/registry.py:find_deck` (`r = datastore.from_url(REDIS_URL)`, `main.py` module level).
  - Intent parsed by `src/server/domain/intake/user_intent.py:parse_user_intent`.
  - `engine_mod.load_staple_theme_vocab(r)` reads Redis staple themes.
- Fact pack: `src/server/app/main.py:_build_review_fact_pack` calls `src/server/domain/analytics/fact_pack.py:build_fact_pack` with injected fetchers:
  - Scryfall: `main.py:_scryfall_collection` (`POST /cards/collection`, batches of 75, 20 s timeout, `httpx`, `raise_for_status`).
  - EDHREC: `domain/analytics/providers/edhrec.py:EDHRecClient.get_commander_page`.
  - Salt: `domain/analytics/providers/commandersalt.py:CommanderSaltClient.get_deck`.
  - `tags` fetcher is **never passed**, so every card's `tags` is `unavailable` (`_build_review_fact_pack` passes only scryfall/edhrec/salt).
- Fact pack to engine: `main.py:_review_card_inputs` maps pack to `card_metadata` and `pricing`; unavailable markers are dropped.
- Review: `domain/decks/deck_review.py:review_deck` (alias of `domain/analytics/report.py:generate_deck_review`) then `DeckAnalysisEngine.analyze` (`domain/analytics/engine.py`) then `RecommendationGenerator.generate` (`domain/analytics/recommendations.py`).
- **LLM calls: none.** Verified by reading the handler chain; `report.py`, `engine.py`, `recommendations.py` import no LLM client (`grep -n "oracle_client\|llm\|httpx"` over `report.py`, `engine.py`, `recommendations.py` returns no match; transitive imports not traced).
- Persistence: none. Response is inline: `{success, review, markdown, cli, fact_pack}`.
- Failure: fact pack failure is caught and returned as `fact_pack: {status: "unavailable", reason}`; review still renders (`api_deck_review`).

### 1b. v2 stored review `POST /api/v2/decks/{deck_id}/reviews` (the closest thing to the target)
- Route: `src/server/app/api/reviews.py:build_review`, mounted under `/api/v2` (`src/server/app/api/__init__.py`). Also `list_reviews`, `latest_review`, `get_review`.
- Orchestration: `src/server/infra/services/review_service.py:build_review`. Reads deck, cards, card instances, profile snapshot and overrides from **Postgres** (`infra/db/repositories.py`), plus Commander Spellbook (`domain/analytics/interactions/combos.py:SpellbookClient`) and EDHREC (`domain/analytics/edhrec_inclusion.py:commander_page`) through the Redis cache handle (`review_service.py:_default_cache`, falls back to an in-process dict).
- Pure pipeline: `src/server/domain/review/pipeline.py:run`: fact pack (`domain/review/fact_pack.py:build_fact_pack`), candidates (`domain/review/candidates.py:retrieve`), verifier (`domain/review/verifier.py:verify_analysis`), writer (LLM), doc (`domain/review/doc.py:build_doc`).
- **LLM call: one**, the writer: `src/server/infra/llm/review_writer.py:ReviewWriter.__call__` to `OracleClient.ask` (`domain/analytics/oracle_client.py`). Absent or failing LLM degrades to `offline_blocks` and status `partial` (`pipeline.py:run`).
- Persistence: `repo.insert_review` and `repo.insert_review_job` into `reviews` and `review_jobs` (`orm_models.py:ReviewRow`, `ReviewJobRow`; DDL in `migrations/versions/a007_create_reviews.py`).
- **Not called by anything in the frontend I could find.** `src/web/src/routes/decks/[id]/` has only `+page.svelte` and `+page.server.ts`; no `review/` route exists, and `+page.server.ts` only fetches `/api/v2/decks/{id}`.

### 1c. Legacy RV-3 "Deep Review"
`GET /api/decks/{deck_id}/review`, `/review/history` in `main.py` (`api_get_deck_review`, `api_deck_review_history`) over `domain/analytics/review_doc.py` plus `review_metrics.py`, stored in Redis. Oracle writes prose. Superseded in intent by 1b; I did not trace it further. `/api/decks/{deck_id}/reviews` (`main.py:api_deck_reviews`) is a *journal* listing (`journal_mod.list_reviews`), unrelated.

**Naming hazard:** `domain/analytics/fact_pack.py` (card facts, Scryfall/EDHREC/Salt, feeds 1a) and `domain/review/fact_pack.py` (curve, mana math, composition, bracket, inventory, profile; feeds 1b) share a name and do different jobs. CONTRACT §3 calls the former "the fact pack"; the richer one is the latter. See D1.

## 2. Where recommendations come from

| Path | Source | Cap |
|---|---|---|
| 1a legacy | `CURATED_CARD_CATALOG`, `domain/analytics/recommendations.py` | see below |
| 1b v2 | `domain/review/candidates.py:edhrec_candidates` (EDHREC commander-page high-inclusion cards) + `tag_candidates` (owned cards that fill composition-role gaps); ranked by `_score`; `recommend` picks adds/cuts | EDHREC page only for the commander; ≤ `MAX_CANDIDATES=40` candidates, `MAX_ADDS=15` |

Curated catalog facts (verified):
- **49 entries** (`grep -c "CuratedCard("` = 49; list at `recommendations.py:CURATED_CARD_CATALOG`).
- **Freshness:** `RECENT_SETS` has 16 sets, newest 2024 (FDN, DSK, BLB, MH3, OTJ, MKM) (`recommendations.py:RECENT_SETS`). Nothing from 2025 or later, so cards from new sets can never be flagged "recent".
- **Maintenance:** hand-edited Python. No refresh script or Scryfall/EDHREC backfill: `scripts/` has only `refresh_bracket_cards.py`, `refresh_creature_types.py`, `refresh_game_changers.py`.
- **Prices:** per-card `approx_price_usd` defaults to 2.0 (`CuratedCard` model); real prices only enter via the `pricing` argument, which the legacy path now fills from the Scryfall fact pack (`main.py:_review_card_inputs`).
- **Quality cap:** at most 49 distinct additions across all archetypes and decks, so reviews recommend the same staples. No color-identity or legality check in this module (`grep color_identity|legal` in `recommendations.py` matches only a comment at line 45). v2 fixes identity via `candidates.py:edhrec_candidates` (`allowed`/`card_info` filter) and `verifier.py`.
- v2 weak spot: `edhrec_candidates` filters identity only when `card_info` resolved (`identity_checked` flag); `review_service.py:build_card_info` resolves only the top `MAX_CARD_INFO=60`. Candidates beyond that are not emitted at all, which is safe but narrow. There is **no legality/banlist check** (Scryfall `legalities` are not read in the v2 path; only name + color identity).

## 3. LLM providers

| Provider | Where | Used by | Structured output | Retries | Cache | Model selection |
|---|---|---|---|---|---|---|
| "oracle" Hermes gateway (OpenAI-compatible) | `domain/analytics/oracle_client.py:OracleClient` (re-exported by `infra/llm/client.py`) | v2 review writer; RV-3; card review; theory; sim | None at API level. Prompt asks for one fenced JSON block; `domain/review/writer.py:parse_reply` validates with pydantic | **None** (single `httpx.Client` request; timeout floor 600 s; errors typed `OracleUnavailable/Auth/Response`) | none | `ORACLE_MODEL` env, default `"oracle"`; one client-wide value, no per-call arg (`chat(messages, **extra)` could pass `model` through extra but nothing does; UNVERIFIED) |
| Gemini | `domain/analytics/interactions/llm.py:GeminiProvider` | card produce/consume tagging, small text rewrites | JSON-only instruction + optional `schema` arg in `generate_json`; labels outside vocab dropped post hoc | none in LLM calls (UNVERIFIED for `_default_post`) | per-card tags cached in Redis (`interactions/tagger.py`) | `DEFAULT_GEMINI_MODEL="gemini-3.8-flash"` |
| Nous | `llm.py:NousProvider` (fallback, `NOUS_API_KEY`) | same | same | none | same | `Hermes-4-70B` |
| Anthropic | `llm.py` slot class | nothing | n/a | n/a | n/a | never configured, raises |

Deployment: `docker-compose.yml` passes `ORACLE_URL`/`ORACLE_KEY`; `docker-compose.prod.yml` also `GOOGLE_API_KEY`/`NOUS_API_KEY`. The app has **no direct OpenAI/Anthropic/OpenRouter runtime client**. Only one pass (the writer) exists in v2, so there is no generator-vs-critic separation today.

Consequence for the target: oracle's gateway runs a full agent loop per request (30 s to minutes, `oracle_client.py` docstring), so multi-pass A–E at ~5+ calls per review is slow and entangles app runtime with a Hermes profile's availability. See D3.

## 4. Current review output shape

### 1a `DeckReview` (`domain/analytics/report.py:DeckReview`, pydantic)
`deck_name, format, commanders[], overall_score (0-100), identity_summary, thematic_assessment (ThemeAnalysisBreakdown), playfeel_summary (PlayfeelAnalysisBreakdown), curve_summary (CurveAnalysisBreakdown), cohesion_summary (CohesionAnalysisBreakdown), intent_alignment (IntentAlignmentReport), strengths[], cohesion_gaps[], recommendations (RecommendationSet: additions, cuts, swaps, recent_set_cards, unique_synergies, intent_aligned_upgrades, category_counts, summary), upgrades_summary, metadata`. Serializers: `to_markdown`, `to_cli`, `model_dump`/`to_json` via `DeckReportFormatter`. The HTTP response wraps these with `fact_pack`. Prose fields are string templates in `report.py`, not LLM text.

### 1b `ReviewDoc` (`domain/review/doc.py:ReviewDoc`, `build_doc`)
`{deck_id, fingerprint, sections[], verification{}}`; each section `{type, title, data}`. Section order (`doc.py:SECTION_ORDER`): `overview, mana_math, composition, curve_tempo, focus, strong_cards, stragglers, combos_engines, bracket_salt, genre, deck_spirit, inventory, recommendations, profile`. Writer-explained sections (`WRITER_SECTIONS`): all of those except `genre`, `deck_spirit`, `inventory`, `profile`. Each explained section gets `data.explanation = {status: ok|offline, text, cards[]}`. API wrapper `schemas.ReviewResponse` adds `panels` (`doc.py:dashboard_panels`) and `jobs`.

**Gap to CONTRACT §5:** the v2 doc has no verdict/grade, scorecard, findings with evidence, top_changes, swaps with trade-off, questions_for_owner, or `meta.models/prompt_versions`. Recommendations are `adds`/`cuts` rows with a deterministic `why`, not paired swaps.

## 5. External sources

| Source | Wired | Fetch | Cache | Rate limit / retry | Failure mode |
|---|---|---|---|---|---|
| Archidekt | yes | `infra/providers/archidekt.py`; account sync `workers/sync.py` | decks to Redis legacy; Postgres via importer | UNVERIFIED (did not read provider body) | UNVERIFIED |
| Moxfield | yes, single public decks | `infra/providers/moxfield.py` | none seen | unofficial API; docs say 403 risk (`docs/moxfield-api.md`); no Playwright fallback in code | UNVERIFIED beyond docs |
| Scryfall | yes, 3 independent paths | (a) `infra/providers/scryfall.py:ScryfallClient` on `infra/http/client.py` (in-memory `TTLCache` 300 s/256 LRU, token bucket `infra/http/rate_limiter.py`, `infra/http/retry.py`); (b) `main.py:_scryfall_collection` raw `httpx.post`, **no cache, no rate limiter, no retry**; (c) `infra/services/review_service.py:scryfall_named_lookup` raw `httpx.get` fuzzy lookup, **per name, no cache, no retry** | prices: `domain/analytics/prices.py` (Redis, 24 h per my read of the old audit; UNVERIFIED) | (a) only | (b) raises on any HTTP error, fact pack degrades; (c) returns None, which the verifier treats as "unknown card" → drops the name. A Scryfall outage therefore silently shrinks recommendations. |
| EDHREC | yes | `domain/analytics/providers/edhrec.py:EDHRecClient` (unofficial `json.edhrec.com`); v2 via `edhrec_inclusion.py:commander_page` | v2: Redis, entries aged by `_max_age()`; legacy client: per-process dict, no TTL | `max_retries=3` backoff | typed `EDHRec*Error`; v2 returns `{cards:{}, error}` (`review_service.py:fetch_edhrec`) |
| Commander Salt | yes | `providers/commandersalt.py:CommanderSaltClient` | none seen | `max_retries=3` | `CommanderSaltNotIngestedError` vs 404 |
| Commander Spellbook | yes (v2 only) | `interactions/combos.py:SpellbookClient` | Redis via cache handle | UNVERIFIED | `fetch_combos` returns error dict, never raises |
| TCGplayer | **cart URL builder only** | `infra/providers/tcgplayer.py:build_tcgplayer_url`, `cart` | n/a | n/a | No price API. Prices are Scryfall `prices.usd`. |

README mismatches (`README.md` "Tech Stack & Architecture"): says Storage is Redis only, Frontend is "Jinja2 templates, Tailwind CSS (CDN), FontAwesome". Both contradict the ratified architecture (Postgres, SvelteKit); and Quick Start says `python -m unittest discover -s tests` while CI also needs pytest for the pytest-style modules. TCGplayer pricing is not claimed in the README but is named in the card body; it is not implemented. `docs/reviewer/EXTERNAL_APIS.md` (CONTRACT §2.7) does not exist yet.

## 6. Analytics inventory (`src/server/domain/analytics/`)

| Area | Code | Deterministic / heuristic |
|---|---|---|
| Brackets | `brackets.py` (HARD vs ADVISORY constraints, Game Changer list), `bracket_eval.py:evaluate_bracket` (docstring: pure, no LLM, no network), `bracket_check.py`, `bracket_worker.py` | Deterministic from list membership; advisory heuristics not used for placement. Game Changers: Redis cache + checked-in `data/game_changers.json` snapshot. |
| Curve / tempo | `review_metrics.py` (hypergeometric lands/ramp, P(on-curve commander)); legacy `engine.py:_analyze_curve` | `review_metrics`: deterministic. `engine`: heuristic scoring. **Two different curve computations coexist.** |
| Mana | `domain/review/fact_pack.py:mana_math_section` (pips vs sources via `_pips`, `_colors_produced`) | Deterministic; `_colors_produced` parses oracle text with string rules (heuristic parsing) |
| Roles / tags | `role_tags.py`, `categories.py`, `composition.py`, `interactions/oracle_rules.py` + `interactions/llm.py` | Rules first (deterministic), **LLM fallback** for unmatched cards, cached forever |
| Deck genre | `genres.py:genre_match` | Deterministic. In v2 it is **disabled**: `domain/review/fact_pack.py:build_fact_pack` passes `genres: []` and sets `genre.available=False` with reason `GENRE_UNAVAILABLE`. |
| Archetype / theme | `archetypes.py`, `engine.py:_analyze_theme`, `staple_theme_registry.py` (Redis) | Heuristic keyword/staple matching |
| Salt / playfeel / power | `engine.py` playfeel, `providers/commandersalt.py`, `domain/profile/power.py:estimate_power` | Heuristic + external |
| Combos / engines / graph | `interactions/combos.py`, `engines.py`, `graph.py` | Deterministic over tags |
| Embeddings / similarity | `card2vec.py`, `mech_similar.py`, `infra/vector/store.py:similar_cards` | Deterministic given model; derived index |
| Sim | `sim_*.py` | Out of scope here; UNVERIFIED |

## 7. Postgres storage

Existing (`orm_models.py`; DDL `migrations/versions/`): `users`, `decks`, `deck_cards`, `cards`, `printings`, `card_instances`, `instance_events`, `deck_links`, `deck_compositions`, `composition_presets`, `games`, `game_imports`, `game_telemetry`, `legacy_games`, `play_style_profile`, `drafts`/`draft_cards`/`draft_instance_moves`, `profile_snapshots` (append-only history; `trigger`, `inputs`, `metrics`, `summary_text`), `profile_overrides` (key/value, **no history of its own**), `reviews`, `review_jobs`, `card_tags`, `card_interaction_edges`, `card_notes`, `derived_index_status`, plus raw-SQL `card_embeddings`/`deck_embeddings` (a007, pgvector, 384-d).

Findings:
- **Intent has no table.** `domain/intake/user_intent.py` parses per request; no `deck_intent` row in `orm_models.py` (`grep -i intent orm_models.py` returns no match). UNVERIFIED whether `deck_compositions.data` or journal data carries it.
- **Profile history exists but is shallow.** `profile_service.py:set_override` appends a `profile_snapshots` row on every override (per `ProfileOverrideRow` docstring). Override history is recoverable only from snapshot `inputs`; `profile_overrides` keeps only the current value, with no provenance (who/why) column.
- **Reviews exist** (`reviews`, `review_jobs`) but store the whole fact pack, analysis, and doc as three JSONB blobs with no `prompt_versions`, `models`, token usage, or per-pass trace, and no feedback.
- `reviews.status` is `String(8)`: fits `complete`/`partial` only.
- **AGE:** the database image installs it (`docker/db/Dockerfile`, `00-extensions.sql`) but no application code uses it (grep for `ag_catalog|cypher` in `src/server` finds nothing; `workers/index_rebuild.py` docstring says graph is "a PR2 follow-up"). `derived_index_status.store` already allows `graph`.
- Redis is still imported by the app (`main.py` `r`, `infra/cache/`, `review_service.py:_default_cache`), contrary to "no Redis" in the target; v2 uses it only as a provider cache.

Placement recommendations are in DECISIONS D2. Rebuildable derivatives: pgvector `card_embeddings` (exists) and `deck_embeddings` (exists); proposed `review_embeddings` is **not** needed. AGE graph: card↔tag/role edges (source `card_interaction_edges`, `card_tags`), card↔deck membership (`deck_cards`), commander↔card co-play (EDHREC). All rebuildable from tables above.

## 8. How tests isolate data today

Verdict: **mixed; four test modules can write to whatever `DATABASE_URL` points at, with no guard.** Agents run on the same host as the live app, so this is a real hazard.

| Module | DB used | Isolation | Risk |
|---|---|---|---|
| `tests/test_slice1.py`, `test_slice3.py`, `test_card_dossier.py`, `test_draft_mode.py`, `test_forge_import.py` | `create_engine("sqlite://", StaticPool)` + `Base.metadata.create_all/drop_all` | In-memory SQLite, never touches Postgres | none |
| `tests/test_pg_store.py` | SQLite always; Postgres only if `TEST_DATABASE_URL` set | `Base.metadata.drop_all(eng)` then `create_all` on that URL | **destructive if `TEST_DATABASE_URL` is the live DB**; no check that the DB name looks like a test DB |
| `tests/test_index_rebuild.py:pg_engine` | `TEST_DATABASE_URL` only | `_drop_schema` drops `card_embeddings`, `deck_embeddings`, `derived_index_status` and more, then recreates | same: drops real tables if URL is live |
| `tests/test_redis_pg_import.py`, `test_transfer_metrics.py` | `TEST_DATABASE_URL` **or `DATABASE_URL`** (`DSN = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")`) | `DROP TABLE IF EXISTS redis_import` in setUp/tearDown | **fall back to live `DATABASE_URL`**; drops a table named `redis_import` on live DB |
| `tests/test_cutover_verify.py` | same fallback | `fresh_db` runs `CREATE DATABASE t_cutover_<pid>_<n>` on the server and migrates only users/deck_links into it | best isolation (unique scratch DB) but still needs server admin on the live Postgres host; **never drops the scratch DB** (`grep "DROP DATABASE"` in the file finds nothing), so scratch databases accumulate on the live server |
| Everything else (Redis-era tests) | fakes/Redis stubs | `tests/conftest.py` only sets `INTERACTIONS_DISABLED=1` | n/a |

Also: `src/server/app/api/deps.py:db_session` reads `DATABASE_URL` through `get_session_factory`/`get_engine` (global singleton, `infra/db/engine.py:get_engine`). A test that uses the real dependency without overriding it hits the live DB when `DATABASE_URL` is set in the environment. `test_card_dossier.py` overrides it (`app.dependency_overrides[deps.db_session]`).

CI uses a service container (`.github/workflows/ci.yml`: `postgres:16-alpine`, `commander_lab_test`) which is isolated. CI's postgres:16-alpine has **no pgvector/AGE** so those paths skip there (`test_index_rebuild.py` skips when the extension is missing). Mechanism proposed in D7.

## 9. Tests and fixtures usable as an eval seed

- **Decks with intents (best seeds):** `tests/test_deck_review_integration.py` (Atraxa counters, Wilhelt aristocrats, Stella spellslinger; 1a path). Gold expectations there are about shape, not quality.
- **Recorded EDHREC pages:** `tests/fixtures/edhrec_atraxa_commander_page.json`, `edhrec_kaalia_commander_page.json`.
- **Card oracle data:** `tests/fixtures/holy_hellfire_cards.json`, `spellbook_holy_hellfire.json`, `theory_doc_holy_hellfire.json`.
- **Deck corpora:** `bracket_check_decks.json`, `personality_real_decks.json`, `card_review_job_{keep,remove}.json`, `tests/fixtures/an4/`, `ps4/`, `forge/` (UNVERIFIED contents of the last four directories).
- **v2 pipeline fixtures:** `tests/test_slice3.py` has `DECK`/`CARDS` literals, `seed_deck`, an injectable `_known_resolver`, and a SQLite `make_factory`; this is the right base for pipeline-level evals. (`grep` shows it has `make_factory` and 19 collected `def test`-style items by my count; the count is unreliable because some tests are methods.)
- `tests/test_fact_pack.py` covers `domain/analytics/fact_pack.py` with recorded EDHREC.
- **Missing:** any deck with a human-written "right answer" (known problems, expected cuts). `tests/review_evals/` does not exist. Andrew's real decks live in live Postgres (read-only fixture extraction is permitted by CONTRACT §2.6).

## 10. Gaps against CONTRACT §3, ranked by impact on review quality

1. **No LLM passes A–D exist.** Only the writer exists (`pipeline.py:run`). No brief, per-axis analysis with evidence, LLM recommendation selection, or critic. This is the whole "professional review" layer; everything else feeds it. *Highest.*
2. **No `review_document` v1.** Current docs are fact sections with explanation text; no verdict, findings, swaps with trade-offs, top changes, questions. (Q4.)
3. **Recommendation pool is thin and uneven.** v2 draws only from one EDHREC commander page plus owned tag matches; no legality/banlist check, no budget filter, no bracket-effect estimate, no similarity retrieval (pgvector exists but is not used by review; `similar_cards` is only an API). Legacy path is the 49-card catalog.
4. **Player profile is not threaded into judgment.** `profile_section` copies snapshot/overrides into the pack, but no pass uses them, and intent has no storage (§7). Profile has no stated-preference schema (staples policy, budget, power target, dislikes) the way CONTRACT §7 needs.
5. **Verifier covers only names** (`verifier.py`): it does not check legality, color identity at the swap level, duplicates vs owned, claim evidence, or numbers in LLM prose. No critic. Number-stripping exists only in legacy `review_doc.validate_prose`, not v2.
6. **Two parallel truth sources for the same numbers** (curve/theme in `engine.py` vs `review_metrics.py`; two fact packs). Reviews could disagree with themselves across endpoints.
7. **Genre disabled in v2** (`GENRE_UNAVAILABLE`); deck identity depends on it.
8. **No run trace / model / prompt-version recording**, no review feedback loop, no determinism for LLM passes (no caching, no retries, no model pinning).
9. **Rendering:** no `src/web/src/routes/decks/[id]/review/` page and no PDF code (`grep -ri pdf src/server` returns no match); the SvelteKit chart components (`CurveChart.svelte`, `ArchetypeMixChart.svelte`, `PowerPanel.svelte`) are reusable for charts.
10. **Scryfall access is fragmented** (3 code paths, 2 unprotected), and outages shrink recommendations silently (§5).
11. **Test isolation hazards** (§8) must be fixed before many agents run DB tests on the live host.
