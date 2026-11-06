# Deck Reviewer Decisions

**Status: APPROVED 2026-10-26** (Andrew; D1–D12 accepted, D3 amended; locked by card 02). Once approved, this file overrides CONTRACT §4 where they differ. Evidence for each call is in `docs/reviewer/REVIEWER_AUDIT.md` (cited as "Audit Qn").

---

## D1. Module locations
- **Options:** (a) keep CONTRACT §4 as written; (b) amend to reflect the existing `src/server/domain/review/` package and the two same-named fact packs.
- **Recommendation: (b).** Audit Q1 found a v2 pipeline already at `src/server/domain/review/` (`pipeline.py`, `candidates.py`, `verifier.py`, `writer.py`, `doc.py`, `fact_pack.py`) and orchestration in `src/server/infra/services/review_service.py`. CONTRACT §4 puts new code in `domain/analytics/candidates.py` and `domain/analytics/review_pipeline/`, which would fork it ("extend, don't fork", §2.4).
- **Amended table:**

| What | Where |
|---|---|
| Card-facts pack (Scryfall/EDHREC/Salt) | `domain/analytics/fact_pack.py` (keep; feeds legacy `/api/review`) |
| Deterministic metrics pack | `domain/review/fact_pack.py` (keep; this is the pipeline's pack) |
| Candidate pool | `domain/review/candidates.py` (extend, not a new file) |
| Passes A–E | `domain/review/passes/{brief,analysis,recommend,verify,writer}.py`; `pipeline.py:run` stays the orchestrator |
| LLM calls (the only place) | `infra/llm/` (`review_writer.py` exists; add `provider.py`, `passes.py`) |
| Prompts | `domain/review/prompts/<pass>.v<N>.md` |
| Schemas | `src/server/domain/review/schemas/review_document.schema.json` (not top-level `schemas/`; matches `domain/intake/schemas/`) |
| Player profile | `domain/profile/player_profile.py` + `schemas/player_profile.schema.json` (as §4) |
| Review API | `app/api/reviews.py` (v2 router under `/api/v2`); legacy `main.py:api_deck_review` untouched |
| Review page | `src/web/src/routes/decks/[id]/review/` + `src/web/src/lib/components/review/` (as §4) |
| Evals | `tests/review_evals/` (as §4) |

- **Also required:** fact pack naming. Add a module docstring cross-reference in each, and refer to them in prompts/docs as "card facts" vs "metrics pack", never both as "the fact pack". Optional rename later.
- **Why:** AGENT_RULES.md §2 bans infra imports from domain; keeping LLM transport in `infra/llm/` and prompt/schema/logic in `domain/review/` respects that.
- **Reversible:** yes (file moves), cheap before cards 03+ land.

## D2. Storage (all Postgres)
- **Options:** (a) JSONB blobs on existing `reviews` and `profile_snapshots`; (b) new normalized tables for profile, review documents, feedback, traces; (c) hybrid.
- **Recommendation: (c) hybrid; new tables, JSONB payloads, relational keys.** Existing `reviews` keeps legacy v2 rows; new work gets its own tables so migrations are additive and nothing that works is rewritten. Audit Q7: `reviews` has no model/prompt/trace/feedback columns, `profile_overrides` has no history or provenance, intent has no table.
- **Tables (new, alembic `a009`+, idempotent DDL like a001–a008):**

| Table | Purpose | Key columns |
|---|---|---|
| `player_profile_versions` | Full history; current = max(version). Append-only. | `id, owner_id, version, kind (inferred\|override\|merged), data JSONB (validated by player_profile.schema.json), provenance JSONB (source, evidence refs), created_at, supersedes_id` |
| `player_profile_overrides` | User statements, never overwritten by inference (CONTRACT §7). Each change also appends a version row. | `id, owner_id, key, value JSONB, note, set_at, retracted_at NULL` (rows are never updated; a retraction sets `retracted_at`) |
| `deck_intents` | Deck-level intent that beats profile on conflict | `id, deck_id FK, version, data JSONB, source (user\|inferred), created_at` |
| `review_documents` | Stored `review_document` v1 | `id, deck_id FK, review_id NULL FK reviews, schema_version, pipeline_version, fact_pack_hash, profile_version_id FK, intent_id FK, doc JSONB, status, created_at` |
| `review_runs` | One row per pipeline run (the trace parent) | `id, document_id FK NULL, deck_id, started_at, finished_at, status, models JSONB, prompt_versions JSONB, cost_usd, error` |
| `review_run_steps` | One row per pass/call | `id, run_id FK, pass (A..E, critic), attempt, model, prompt_version, input_hash, request JSONB, response JSONB, tokens_in, tokens_out, latency_ms, verdict (ok\|retry\|dropped\|failed), error` |
| `recommendation_feedback` | Andrew's response to each swap | `id, document_id FK, swap_id, card_oracle_id, decision (accepted\|rejected\|deferred\|already_owned), reason, created_at` |
| `forge_evidence` (only when card 11 needs it) | On-demand game evidence per deck | defer to that card |

- **`owner_id`** nullable column on every new table, matching existing convention (`orm_models.py` tables all carry it).
- **Rebuildable derivatives (not truth):** `card_embeddings` / `deck_embeddings` (exist, a007); a `review_similarity` index is **not** proposed. AGE graph (`derived_index_status.store='graph'`): card↔role/tag edges from `card_tags` + `card_interaction_edges`, deck membership from `deck_cards`, commander co-inclusion from cached EDHREC. Review/profile/feedback rows are never derived. Feedback may *influence* candidate ranking through a computed view, not a stored index.
- **Why not JSONB-only on `reviews`:** the trace and feedback need querying (what did pass C return on attempt 2; which cards does Andrew always reject) and the eval harness needs per-pass rows.
- **Reversible:** additive migrations are reversible by dropping tables; data written is the cost. Column set is the part to review before card 02.

## D3. Runtime LLM access and per-pass model plan
- **Facts (Audit Q3):** the app only talks to the oracle Hermes gateway (`ORACLE_URL`/`ORACLE_KEY`, 600 s timeout floor, full agent loop per request, no retries, no structured-output mode, one model per client) and Gemini/Nous for tagging. There is no direct provider client suitable for 5–8 structured calls per review.
- **Options:** (a) route every pass through the oracle gateway; (b) add a direct provider client (Anthropic API or OpenAI-compatible endpoint) in `infra/llm/` for structured passes A–D, keep oracle for E; (c) Gemini via the existing `interactions/llm.py` provider for A–D.
- **Recommendation: (b), with the provider chosen by Andrew.** I recommend an Anthropic-or-OpenAI-compatible API behind one `infra/llm/provider.py` interface (`generate_json(system, prompt, schema, model, max_retries) -> (obj, usage)`) with native schema/tool-forced JSON, retry on 429/5xx/invalid-JSON (≤2, then explicit failure), and a per-call `model` argument. Oracle gateway stays the writer because CONTRACT §2.10 makes the oracle profile the owner of review prose, and its agent loop latency is acceptable for one call.
- **AMENDMENT (Andrew, approved): D3: "okay anthropic then" — Anthropic API key as Docker secret for passes A–D, critic on Gemini; oracle writes prose**
- **Resolved (was: needs Andrew):** vendor/key. Chosen: **Anthropic API, `ANTHROPIC_API_KEY` as a Docker secret** (file `secrets/ANTHROPIC_API_KEY`, same mechanism as `ORACLE_KEY` in `docker-compose.prod.yml`), because the existing `llm.py` already reserves an `anthropic` slot. Never in git, never in cards.
- **Per-pass plan (generator ≠ critic ≠ judge):**

| Pass | Role | Model class | Notes |
|---|---|---|---|
| A brief | judgment, small | mid (e.g. Sonnet-class) | schema-forced |
| B analysis (per axis) | judgment, evidence | strong (Opus-class) or mid | one call per axis, parallel |
| C recommendations | choose from `candidate_pool` | strong | candidate ids only, not free text |
| D critic | adversarial review of B+C | **different family** from the generator (e.g. Gemini Pro if generator is Anthropic) | the diversity is the point |
| E writer | prose | oracle profile (its configured model) | prose only |
| Eval judge | scores reviews in `tests/review_evals/` | **different from both** generator and critic where feasible; at minimum different from the generator | |

- **Operational rules:** pin model ids in config, record them in `review_run_steps.model` and `review_document.meta.models`; cache identical `(prompt_version, input_hash, model)` calls in `review_run_steps` (Postgres, not Redis) to make evals reproducible and cheap; budget guard (`REVIEW_MAX_COST_USD`) fails the run explicitly, never truncates silently.
- **Reversible:** yes. The interface isolates the vendor; changing models is config.

## D4. PDF engine
- **Options:** (a) Playwright/Chromium rendering the SvelteKit review page; (b) WeasyPrint rendering a separate HTML template.
- **Facts:** Python image is `python:3.11-slim` (`Dockerfile`); web image is `node:24-alpine` (`src/web/Dockerfile`); no PDF code exists (Audit Q10.9). Charts in the web app are Svelte components producing SVG (`src/web/src/lib/components/CurveChart.svelte`). Playwright is already a dev dependency for e2e tests (`ci.yml`), not a runtime one.
- **Recommendation: (a) Playwright/Chromium**, run in a **separate optional `pdf` container** (Playwright's official image) that the API calls over the internal network, not baked into the API image.
  - CSS fidelity: PDF equals what Andrew sees on screen (one renderer, one set of charts); WeasyPrint lacks modern CSS (grid gaps, container queries, some flex edge cases) and needs a *second* template that will drift from the Svelte page.
  - SVG: Chromium prints the exact inline SVG from the Svelte charts; WeasyPrint supports SVG but not all CSS-styled/embedded-font cases, and a second chart implementation would be needed.
  - Size: Chromium adds ≈ 400–600 MB to an image (estimate, **UNVERIFIED**, not measured). Isolating it in its own container keeps the API image small and lets it be off by default. WeasyPrint is ≈ 50–100 MB (estimate, UNVERIFIED) plus pango/cairo apt packages.
  - Cost: slower cold start, extra container to run on Unraid. Acceptable for a single-user tool.
- **Print contract:** the review route supports `?print=1` (no nav, print CSS, fixed page size), and the PDF service loads it with a short-lived signed token so it needs no login/cookies.
- **Reversible:** yes. The PDF endpoint's contract (`GET /api/v2/reviews/{id}/pdf`) stays the same if the engine changes.

## D5. Review document: extend or introduce `review_document` v1
- **Options:** (a) extend `ReviewDoc` (`domain/review/doc.py`) in place; (b) introduce `review_document` v1 (CONTRACT §5) as a new schema and store; keep old output alongside.
- **Recommendation: (b) new, alongside, no rewrite of the old.**
  - What's broken in the old doc (Audit Q4, Q10): it is a list of fact sections with optional prose per section. It has no verdict, findings-with-evidence, swaps with trade-offs, or provenance (`meta.models`, `prompt_versions`), and its recommendations are unpaired `adds`/`cuts`. Retrofitting that shape changes every consumer.
  - What works and stays: `domain/review/pipeline.py:run`'s fact-pack/candidates/verifier stages and `verifier.py`; `reviews`/`review_jobs` rows; `GET /api/v2/decks/{id}/reviews*` responses.
- **How:** `POST /api/v2/decks/{id}/reviews` gains an opt-in `?format=v1` (or a new `/review-documents` resource, final choice in card 02); old format remains the default until the v1 page ships, then flip the default.
- **Legacy exports:** `DeckReportFormatter.to_markdown/to_cli/to_json` and the CLI `review` subcommand (`workers/cli.py`) keep consuming `DeckReview` (legacy 1a); they are untouched. Add a Markdown exporter *from* `review_document` as a new function, not a replacement.
- **Reversible:** yes; v1 is additive. The schema's field names are the sticky part: finalize in card 02.

## D6. Fate of the curated catalog (`CURATED_CARD_CATALOG`)
- **Options:** (a) retire; (b) demote to one input of `candidate_pool`; (c) keep as-is.
- **Recommendation: (a) retire from the new pipeline; keep it only inside the legacy `/api/review` path until that path is removed.** Do **not** make it a `candidate_pool` input.
  - What's broken (Audit Q2): 49 hand-written cards, `RECENT_SETS` ends at 2024, no refresh job, no color-identity/legality check, prices default to 2.0. It would inject stale, unvetted names into a pool that CONTRACT §2.3 treats as the only source of recommendations.
  - Value worth keeping: theme hints per card. That information is better derived from `card_tags` + EDHREC synergy + pgvector similarity.
  - The new pool (card 05): EDHREC commander page (exists: `candidates.py:edhrec_candidates`), owned-card tag matches (exists: `tag_candidates`), pgvector neighbours of deck cards (`infra/vector/store.py:similar_cards`), profile-liked cards, all filtered by legality, color identity, bracket, budget, already-in-deck.
- **Reversible:** yes. The catalog code stays in git; marking it deprecated is one line in the legacy module + CHANGELOG.

## D7. Test data isolation: the throwaway-database mechanism
- **Problem (Audit Q8):** `test_redis_pg_import.py` and `test_transfer_metrics.py` fall back to the live `DATABASE_URL` and drop tables; `test_pg_store.py`/`test_index_rebuild.py` run `drop_all`/`DROP TABLE` on `TEST_DATABASE_URL` with no check it is a test DB; `test_cutover_verify.py` creates scratch DBs but never drops them.
- **Options:** (a) SQLite in-memory only; (b) one shared test DB; (c) per-session throwaway Postgres database created and dropped by a shared fixture, with guards.
- **Recommendation: (c).** A single `tests/pg_isolation.py` + `tests/conftest.py` fixture (card 01 delivers it; all later cards use it):
  1. Read `TEST_DATABASE_URL` only. **Never** read `DATABASE_URL` in tests. Remove the `or DATABASE_URL` fallbacks.
  2. Connect to the `postgres` maintenance DB on that server and `CREATE DATABASE t_<pid>_<uuid8>`; apply `Base.metadata.create_all` plus the raw-SQL pgvector DDL; yield an engine; `DROP DATABASE ... WITH (FORCE)` in teardown and an `atexit` sweep of `t_*` databases older than 1 h.
  3. **Guards (refuse to run, `pytest.exit`):** target database name must start with `t_` or end `_test`; the server host:port + database must not equal `DATABASE_URL`'s; for any DSN, reject if the database name equals the one in `DATABASE_URL`. Document in `docs/reviewer/` and AGENT_RULES.
  4. Session-scoped autouse fixture unsets `DATABASE_URL` and calls `dispose_engine()` so an un-overridden `deps.db_session` can't reach the live DB (it would raise `db_unavailable` instead).
  5. Default for agents: SQLite in-memory for unit tests (as `test_slice3.py` does); Postgres fixture only for pgvector/AGE/JSONB-operator tests, which skip with an explicit reason when `TEST_DATABASE_URL` is unset.
  6. Where Postgres is on the same host as the live app, run a **separate Postgres instance/role** for tests: `docker run` from `docker/db/` image on port 5433, role `tester` with `CREATEDB` but no access to the live database. A role without privileges on the live DB is the guard that doesn't depend on code being correct.
- **Approved (default accepted):** permission to run that second Postgres container (or a restricted role on the existing server). Default: the separate container via `make test-db` (card 01).
- **Reversible:** yes (test-only).

## D8. Oracle consultation mechanics
- **Options:** (a) `kanban_comment` addressed to oracle on the coder's card; (b) a dedicated card assigned to oracle per question; (c) hybrid.
- **Recommendation: (c), with a convention that never blocks:**
  1. **Quick MTG question** (answerable in a few lines): `kanban_comment` on your own card starting `ORACLE?:` + the question + your **proposed default**. Do not wait. Continue with the default and record it under `decisions` in handoff metadata with "pending oracle". If oracle answers differently before merge, fix it in the same PR; after merge, file a follow-up.
  2. **Real work** (write or review prompt text, rubric for a scorecard axis, judge ruleset): create a card assigned to `oracle` with `parents=[]`, titled `ORACLE: <topic>`, with the exact deliverable and file path under `docs/reviewer/oracle/`. Coder cards that *need* the artifact list it as a parent **at creation by the planner** (card 09 prompts, card 12 rubric), not mid-card.
  3. **Prompts are Oracle-owned content** (CONTRACT §2.10): coder builds the plumbing with a placeholder prompt version `v1` clearly marked `DRAFT`; Oracle's card supplies `v2`. Pipeline runs with `v1` in the meantime, so nothing stalls.
- **Why:** the Hermes comment channel alone can sit unread (the dispatcher only wakes a profile on its own card); a dedicated card is the unit oracle is scheduled on.
- **Reversible:** yes (process only).

## D9. (added) Retire or isolate the legacy review path
- **Finding (Audit Q1, Q10):** three review paths coexist (legacy `/api/review`, v2 stored review, RV-3 Redis deep review) with two curve/theme implementations (`engine.py:_analyze_curve` vs `review_metrics.py`).
- **Options:** (a) leave all three; (b) freeze the legacy two and route new UI only to v2/v1 document; (c) delete legacy now.
- **Recommendation: (b).** No changes to `main.py:api_deck_review` or `review_doc.py` in this build except bug fixes. Mark them deprecated in `CHANGELOG.md` when the review page ships; remove in a later dedicated card. Deleting now would break CLI/markdown exports that still work (D5).
- **Reversible:** yes.

## D10. (added) One Scryfall access path
- **Finding (Audit Q5):** three code paths; two (`main.py:_scryfall_collection`, `review_service.py:scryfall_named_lookup`) have no cache, rate limiter, or retry, and a lookup failure makes the verifier drop a valid card (`verifier.py:verify_names`).
- **Recommendation:** card 03/04 routes review-path Scryfall access through `infra/providers/scryfall.py` (`ScryfallClient`: rate limiter, TTL cache, typed errors) and distinguishes "not found" (drop) from "lookup failed" (mark `unavailable`, don't drop). Reversible: yes.

## D11. (added) Redis in the review path
- **Finding (Audit Q7):** `review_service.py:_default_cache` and `edhrec_inclusion.commander_page` use Redis for provider caching, against "no Redis" in the target.
- **Recommendation:** leave as-is for this build (it is cache-only and falls back to a dict); the review path's *new* caches (LLM calls, provider payloads) go to Postgres. Remove Redis in the rearchitecture's own cutover slice, not here. Reversible: yes.

## D12. (added) Where intent comes from
- **Finding (Audit Q7):** deck intent has no table; `parse_user_intent` runs per request.
- **Recommendation:** `deck_intents` (D2) is written by Pass A's ambiguity questions answered by Andrew, or by an explicit intent form; pass B/C read the latest row. Missing intent degrades with an `unavailable` marker plus `questions_for_owner`, not a guess. Reversible: yes.

---

## Open questions for Andrew
All resolved: D3 amended as above (Anthropic for passes A–D, Gemini critic); D4 and D7 extra containers accepted as defaults; all other defaults accepted.

---

## Binding for later cards

- **Module paths:** as the D1 table, with one exception: the review document schema lives at top-level `schemas/review_document.schema.json` (CONTRACT §4, card 02 scope), not under `domain/review/schemas/`. Cross-reference checks: `tests/review_document_refs.py:check_references`. Fixture: `tests/fixtures/review_document/example.json`.
- **Storage:** Postgres only, hybrid per D2 (new tables `player_profile_versions`, `player_profile_overrides`, `deck_intents`, `review_documents`, `review_runs`, `review_run_steps`, `recommendation_feedback`; alembic `a009`+; nullable `owner_id`). New caches (LLM calls, provider payloads) go to Postgres, not Redis.
- **Runtime model plan:** direct provider client in `infra/llm/provider.py`. Passes A–D on Anthropic (key from Docker secret `ANTHROPIC_API_KEY`); critic on Gemini (different family); pass E prose by the oracle profile via the oracle gateway; eval judge different from the generator. Model ids pinned in config and recorded in `meta.models` and `review_run_steps.model`.
- **PDF engine:** Playwright/Chromium in a separate optional `pdf` container, rendering the review page with `?print=1` and a short-lived signed token.
- **Test isolation:** per-session throwaway Postgres database via `tests/pg_isolation.py` + `tests/conftest.py` (card 01), `TEST_DATABASE_URL` only, name guards (`t_*` / `*_test`), never `DATABASE_URL`; separate test Postgres container (`make test-db`, port 5433, role without access to the live DB); SQLite in-memory for unit tests.
- **Review document:** `review_document.v1`, new alongside the legacy doc (D5); ids match `^[a-z][a-z0-9_-]{0,63}$`; `charts` is copied from the fact pack by code only.
