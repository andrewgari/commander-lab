# Review pipeline: Pass C (recommendations) and Pass D (verifier)

Card 09. Code: `src/server/domain/analytics/review_pipeline/{c_recommend,d_verify,checks}.py`, prompts `c_recommend.v1.md`,
`d_critic.v1.md`, `d_regen.v1.md`. Entry point: `runner.run_abcd(ctx, client, pool, config)` returns `ReviewResult` or raises
`ReviewRunFailed`. `ReviewResult.document_sections()` is what may ship; `omitted` is the debug appendix.

## Pass C
The candidate pool is shown to the model as ids (`pc-1`...). The model returns a pool id for the add and one for the alternative,
never a free-text name. Code fills card refs, price, owned flag and budget text from the pool row, so the model cannot misquote them.
Swaps are ranked; `top_changes` picks three with `why_these_first`. If Pass C has fewer than three swaps it lists fewer (CONTRACT §6:
no padding); the document assembler must handle that, since `review_document.v1` wants exactly three.

## Pass D, in order
1. **Code checks** (`checks.py`; `tests/review_evals/gates.py` re-exports the same functions, nothing is duplicated): names resolve,
   legality, color identity, singleton, budget, bracket delta, ids resolve, add in pool, cut in deck, duplicate cut, top_changes
   resolve, bracket effect honest, and **previously rejected by the owner for this deck** (`player_profile.previously_rejected`).
   Items that fail are not shown to the critic.
2. **Critic** from a different model family than every generator (`ConfigError` before any call otherwise). Categories:
   `unsupported_claim`, `generic`, `contradicts_brief`, `tradeoff_missing`. A flag only counts if it quotes the item verbatim;
   a quote that is not in the text gets a repair turn and then fails the run rather than being trusted.
3. **Regenerate only the flagged item** with the reasons, at most `max_verify_loops` (2) times; each round reruns code checks and critic.
4. **Still failing, or withdrawn: removed**, recorded in `omitted[]` with the reason, never in a main section. A swap whose
   problem finding was removed is omitted too. `top_changes` is judged last, on surviving swaps.

`result.stats` counts critic calls, flag events, flagged items, regenerations, regenerated-ok and removed.

## Run it live (not CI)
```
OPENROUTER_API_KEY=... REVIEW_MODEL_A=openrouter:anthropic/claude-sonnet-5.5 REVIEW_MODEL_B=openrouter:anthropic/claude-opus-5.5 \
REVIEW_MODEL_C=openrouter:anthropic/claude-opus-5.5 REVIEW_MODEL_CRITIC=openrouter:google/gemini-3.1-pro-preview \
python -m scripts.review_live_abcd lara-uncharted OUTDIR
python -m tests.review_evals.run_gates --dir OUTDIR/gates     # same gates as `make eval-gates`
```
The script refuses to run on degraded inputs (empty pool or cards without Scryfall color identity, e.g. after a Scryfall 429) and
waits out `Retry-After`. Run one deck at a time.

## Tests
`tests/test_review_pipeline_{checks,recommend,verify}.py` (each code check catches its violation), and
`test_review_pipeline_verify_replay.py` (recorded live critic and regeneration replies: one regeneration loop, one removal).

## Known limits
Gemini 3.1 Pro sometimes truncates or drops required keys under strict `json_schema`; the critic runs with a 16k token budget,
10 items per call, and one repair turn. The critic judges against a text dossier, so it can flag a claim that the fact pack does
support but that the dossier clipped (`DOSSIER_TEXT_CHARS`).
