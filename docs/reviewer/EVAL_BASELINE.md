# Reviewer eval baseline (card 10, rerun)

**Status: COMPLETE for the document gate, with reliability caveats at the cheaper tier.** All 8 deck/tier cells produced a review document with 0 gate violations (17 checks each). Two cheaper-tier cells needed re-runs because the first attempt failed (see Reliability). Numbers are measured, not estimated.

Run: `python -m tests.review_evals.run_eval --live --tier <tier>` on origin/main `76619cb` (includes #381-#385). Date 2026-10-28.
Generator per tier: planned `spoton:claude-sonnet-5-5`, cheaper `spoton:claude-haiku-4-5`. Judge `spoton:gpt-5-6-sol`. Pass D critic `spoton:mistral-large-3` (judge, critic and each generator are different models). This replaces the earlier PARTIAL baseline taken on `3110ab6` (1 of 8 cells produced a document).

Schema change: **none.** No schema, contract or pipeline code is touched; this PR adds docs and measured artifacts only.

## Gate results (hard gates, 0 violations required)

| Deck | Cheaper (haiku-4-5) | Planned (sonnet-5-5) |
|---|---|---|
| saffi-rumors | ok, 17 checks, 0 violations | ok, 17 checks, 0 violations |
| lara-uncharted | ok, 17 checks, 0 violations | ok, 17 checks, 0 violations |
| celes-ebbing-away | ok, 17 checks, 0 violations | ok, 17 checks, 0 violations |
| darien-frozen-throne | ok, 17 checks, 0 violations | ok, 17 checks, 0 violations |

## Judge scores and rubric recall (0-10, judge `gpt-5-6-sol`)

| Deck | Tier | Rubric recall | Specificity | Intent alignment | Actionability | Generic filler |
|---|---|---|---|---|---|---|
| celes-ebbing-away | planned | **5/10** | 9 | 4 | 8 | 6 |
| darien-frozen-throne | planned | **3/10** | 9 | 4 | 8 | 3 |
| lara-uncharted | planned | **10/10** | 10 | 8 | 8 | 7 |
| saffi-rumors | planned | **5/10** | 9 | 4 | 8 | 7 |
| celes-ebbing-away | cheaper | **5/10** | 9 | 3 | 3 | 4 |
| darien-frozen-throne | cheaper | **4/10** | 8 | 3 | 6 | 4 |
| lara-uncharted | cheaper | **8/10** | 9 | 5 | 3 | 5 |
| saffi-rumors | cheaper | **5/10** | 9 | 5 | 7 | 6 |

Mean rubric recall: planned **5.75/10**, cheaper **5.50/10**. `generic_filler` is scored as the judge emitted it (lower is better, as in the first baseline); the judge notes call every review "highly repetitive" regardless of that number, so treat it as a rough signal.

## Cost and latency per run (generator + judge, USD)

| Deck | Tier | Cost | Latency (s) | Calls |
|---|---|---|---|---|
| celes-ebbing-away | planned | 1.085 | 267 | 30 |
| darien-frozen-throne | planned | 1.258 | 336 | 34 |
| lara-uncharted | planned | 1.500 | 413 | 45 |
| saffi-rumors | planned | 1.277 | 327 | 35 |
| celes-ebbing-away | cheaper | 0.697 | 234 | 44 |
| darien-frozen-throne | cheaper | 0.464 | 193 | 35 |
| lara-uncharted | cheaper | 0.492 | 220 | 40 |
| saffi-rumors | cheaper | 0.503 | 140 | 36 |

Successful-run totals (generator + judge): planned $5.12 (mean $1.28 per review), cheaper $2.16 (mean $0.54). Failed cheaper attempts cost another $0.90 in generator spend (saffi $0.20, lara $0.20, lara retry $0.51), so the all-in cheaper generator spend for four documents was $2.70 plus judge calls.

## Reliability (important)

Planned tier: 4/4 first attempt. Cheaper tier: **2/4 first attempt**, 4/4 after re-running the failed decks.

- `saffi-rumors` cheaper, attempt 1: Pass C `recommendations` failed after 3 attempts: `cuts_to_consider` cited fact keys that do not exist (`p-interaction_resilience-1`, `p-plan_synergy-3`, `p-concept_execution-1`). Attempt 2 passed.
- `lara-uncharted` cheaper, attempt 1: same Pass C failure (`p-concept_execution-3`). Attempt 2: Pass E writer rejected 3 times for `unverified number: 8 / 38`. Attempt 3 passed.

The committed cheaper review JSONs for saffi-rumors and lara-uncharted are from those passing re-runs. The pipeline's retry bounds worked as designed (it fails instead of shipping unverified text), but haiku-4-5 is not reliable at Pass C fact-key discipline or Pass E number discipline. The planned tier did not show either failure in this run.

## Where reviews are weakest (judge notes, abbreviated)

1. **Owner intent is missed.** celes (planned and cheaper): both reviews say no power target is stated, though the golden input gives lean Bracket 4 aspiring cEDH; speed is never evaluated against that bar. darien: both miss the Aikido/punisher framing; saffi: both miss the pillowfort-first sequencing. Intent alignment is 3-5/10 on 7 of 8 cells (lara planned 8).
2. **MTG facts stated by the writer are sometimes wrong.** Cheaper tier: Stitch Together, Cabal Ritual, Vampiric Tutor (celes); Jade Monolith and self-damage lands (darien); Arcane Denial, Windfall, Mana Drain costs (lara). Planned tier: Passionate Archaeologist "does nothing" (lara), Green Sun's Zenith tutor reach (saffi). The verifier checks names and numbers against the payload, not rules text; these are prose-claim errors the gates cannot see.
3. **Repetition.** The judge flags heavy repetition of the same findings across sections on every cell.
4. **Cheaper tier actionability is the weak axis** (3/10 on celes and lara) even where recall is comparable to planned.

## Residual risk

- Single run per cell (n=1). The cheaper tier already showed run-to-run variance (2 of 4 decks failed the first time), so scores are not stable estimates. Judge scores are one gpt-5-6-sol sample each.
- Rubric recall is low (3-5/10) on 6 of 8 cells; only lara reaches 8-10.
- Gate checks pass, but factual accuracy of prose (point 2) is not covered by any hard gate. Oracle's Commander-correctness review of these documents is the next step and is intentionally outside this PR.
- Review JSON files embed no secrets and no local paths (checked with grep).

## Artifacts

Review documents (schema `schemas/review_document.schema.json`):

- `docs/reviewer/baseline/planned/{celes-ebbing-away,darien-frozen-throne,lara-uncharted,saffi-rumors}.review.json`
- `docs/reviewer/baseline/cheaper/{celes-ebbing-away,darien-frozen-throne,lara-uncharted,saffi-rumors}.review.json`
- `docs/reviewer/baseline/{planned,cheaper}/results.json` (per-deck gates, judge scores, cost, latency; merged from separate invocations of the same pipeline head)

## Next steps

- Oracle: Commander-correctness review of the eight documents (card t_6a130a2d).
- Follow-up: cheaper-tier reliability (Pass C fact-key retries, Pass E number retries) and feeding owner intent/power target into the Pass B/C prompts when the deck input carries it.
