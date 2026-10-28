# Review pipeline: Pass A (brief) and Pass B (analysis)

Card 08. Code: `src/server/domain/analytics/review_pipeline/` (domain, no I/O), `src/server/infra/llm/provider.py`
(transports), `src/server/infra/services/review_trace_service.py` + migration `a010_review_runs` (trace storage).

## Run it
`runner.run_ab(ctx, client, config)` returns `RunResult(brief, analysis, trace)` or raises `ReviewRunFailed` (carrying the failed
trace). It never returns a partial review. `ctx = ReviewContext.build(...)` from the fact pack, deck list, profile and intent.

Live run on a golden deck (not CI): `OPENROUTER_API_KEY=... REVIEW_MODEL_A=openrouter:anthropic/claude-sonnet-5.5
REVIEW_MODEL_B=openrouter:anthropic/claude-opus-5.5 python scripts/review_live_ab.py lara-uncharted OUT`.
It writes `trace.json`, `result.json`, `fixture.json` (replies for replay) and `fact_pack.json`.

## Configuration (env)
Model specs are `<provider>:<model>`; providers: `anthropic` (needs `ANTHROPIC_API_KEY`, D3) and `openrouter` (`OPENROUTER_API_KEY`).
`REVIEW_MODEL_A`, `REVIEW_MODEL_B`, per-axis `REVIEW_MODEL_B_<AXIS>`, `REVIEW_MAX_REPAIRS` (default 2), `REVIEW_MAX_COST_USD`.
No model id appears in pass logic.

## What code enforces (not the model)
Structured output against a JSON schema per pass; on invalid output one repair turn showing the errors, at most
`REVIEW_MAX_REPAIRS` times, then `StructuredOutputError`. A finding is dropped (and logged in `trace.drops`) when an evidence card
is not in the deck, a fact key does not exist or is marked unavailable, the prose names a card known to be outside the deck,
or it cites an unknown owner question. Finding ids are assigned by code. A finding that depends on an open owner question has
confidence capped at 0.5.

## Tracing
`Trace.provenance_of(finding_id)` (or `review_trace_service.provenance_of(session, run_id, finding_id)` on a stored run) returns
pass, axis, prompt version, requested and answering model, input hash and attempt. Each step stores the full request, raw
response, tokens, cached tokens, latency and cost.

## Tests / fixtures
`tests/test_review_pipeline_*.py`, offline. `test_review_pipeline_replay.py` replays one recorded live run
(`tests/fixtures/review_pipeline/`); each recorded reply is pinned to its request hash, so a prompt change fails with the
re-record instruction instead of replaying a stale reply.

## Known limits
`google/gemini-3.1-pro-preview` returned an empty object under strict `json_schema` on OpenRouter; check before using it as the
card-09 critic. The provider cache is the provider's prompt cache only (no Postgres call cache yet).
