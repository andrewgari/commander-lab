# Deck Reviewer: Agent Contract

Commit this file to `docs/reviewer/CONTRACT.md` on `main` before any card runs.

Every deck-reviewer card starts by reading this file, `AGENTS.md`, `docs/ARCHITECTURE.md`, and (from card 02 on) `docs/reviewer/DECISIONS.md`. When DECISIONS.md and this file disagree, **DECISIONS.md wins**. It is the approved, more specific record.

This contract is reconciled with the ratified rearchitecture (`docs/ARCHITECTURE.md`, approved 2026-10-25). Where an older doc (Redis, Jinja templates, root-level `*.py`) disagrees, the rearchitecture wins.

---

## 1. Goal

Production-grade, professional Commander deck reviews, personalized to one player (Andrew), delivered as a styled SvelteKit page and PDF.

**Review quality outranks speed, cost, and feature breadth.** When a trade-off comes up, pick the option that makes the review more accurate, more specific, or more useful to Andrew.

## 2. Non-negotiables

1. **LLMs never supply card facts.** Oracle text, legality, color identity, prices, combos, counts and statistics come from code and data. LLMs do judgment and prose only.
2. **Every card name an LLM emits is validated** against Scryfall data before it can reach any output. Unknown names are removed and logged, never shipped.
3. **Recommendations come only from `candidate_pool`.** No free-form card suggestions.
4. **Extend, don't fork.** Build inside the new architecture: backend code in `src/server/` (domain logic in `src/server/domain/`, FastAPI only in `src/server/app/`), frontend in `src/web/` (SvelteKit). Reuse what exists (`src/server/domain/analytics/fact_pack.py`, the providers, `src/server/app/api/reviews.py`). If you must deviate, say why in the PR.
5. **Missing data degrades explicitly**: an `unavailable` marker with a reason. Never a silent default, never a guess.
6. **Storage is Postgres only.** Postgres is the source of truth (decks, inventory, profile, reviews, feedback, traces). pgvector and Apache AGE are derived, **rebuildable indexes**, never sources of truth. No Redis, no Jinja. Never write to the live production database from tests: tests use a throwaway database/schema. Reading live data to build fixtures is allowed.
7. **Don't invent external API behavior.** Read the docs or test the endpoint, then record what you found in `docs/reviewer/EXTERNAL_APIS.md`.
8. **No secrets in commits, comments, or handoff metadata.**
9. **Forge game logs are on demand only** (import → analyze → report → update profile). No continuous sync, no background ingestion; a review pass that wants game evidence requests it explicitly and degrades with `unavailable` when none exists.
10. **Oracle writes the reviews.** The `oracle` profile owns MTG judgment and prose (the writer pass, prompt craft for analysis/recommendation voice). Coder consults Oracle for MTG questions instead of guessing: ask via a `kanban_comment` on your card addressed to oracle, or file a small card assigned to `oracle` when the question needs real work.

## 3. Target architecture

```
deck + intent ──▶ FACT PACK (deterministic, cached)     ← DONE (src/server/domain/analytics/fact_pack.py, PR1)
player profile ─┘        │
                         ▼
          Pass A  brief: what the deck is for, ambiguity questions
                         ▼
          Pass B  analysis, one call per axis → findings with evidence
                         ▼
   candidate_pool ─▶ Pass C  recommendations (swaps from the pool only)
                         ▼
          Pass D  verifier: code checks, then a critic LLM; regenerate ≤2×; drop what fails
                         ▼
          Pass E  writer (oracle profile): prose from verified JSON only
                         ▼
          review_document (JSON, schema-validated, stored in Postgres)
                         └──▶ SvelteKit renderer (src/web, no LLM) + PDF export
```

| Layer | Deterministic code | LLM |
|---|---|---|
| Fact pack, charts, candidate pool | ✅ | ❌ |
| Brief, analysis, recommendations, critic | facts in, schema out | ✅ judgment |
| Verifier code checks | ✅ | ❌ |
| Writer | schema enforced | ✅ prose only, no new facts |
| Renderer (SvelteKit + PDF) | ✅ | ❌ |

## 4. Default locations (DECISIONS.md may override)

| What | Where |
|---|---|
| Player profile model | `src/server/domain/profile/player_profile.py`, `src/server/domain/profile/schemas/player_profile.schema.json`; data + full history in Postgres |
| Fact pack | `src/server/domain/analytics/fact_pack.py` (**exists, merged**) |
| Candidate pool | `src/server/domain/analytics/candidates.py` |
| Review passes | `src/server/domain/analytics/review_pipeline/` (`a_brief.py`, `b_analysis.py`, `c_recommend.py`, `d_verify.py`, `e_writer.py`, `runner.py`) |
| Prompts | `src/server/domain/analytics/review_pipeline/prompts/<pass>.v<N>.md` (never edit a released version; add `v<N+1>`) |
| Review document schema | `schemas/review_document.schema.json` |
| Review API routes | `src/server/app/api/reviews.py` (extend; `/api/review` fact-pack wiring **exists, merged**) |
| Review page (frontend) | `src/web/src/routes/decks/[id]/review/` + `src/web/src/lib/components/review/` (SvelteKit) |
| Evals | `tests/review_evals/` |
| Reviewer docs | `docs/reviewer/` |

## 5. Review document v1 (pipeline ↔ renderer contract)

Card 02 formalizes this as JSON Schema. Both the pipeline and the renderer build to it.

```
meta            deck_id, deck_name, commanders[], generated_at, pipeline_version,
                prompt_versions{}, models{}, fact_pack_hash
verdict         headline (≤20 words), summary (≤80 words), grade (A–F), confidence (0–1)
deck_identity   what_it_does, win_paths[], success_criteria[], intentional_quirks[]   ← Pass A
scorecard[]     axis, score (1–10), one_line, finding_ids[]
strengths[]     finding
problems[]      finding, ordered by severity
top_changes     exactly 3 swap_ids + why_these_first
swaps[]         id, cut, add, resolves_finding_id, tradeoff, bracket_effect, budget_effect,
                alternative, owned (bool), price
cuts_to_consider[]   card, reason, evidence (may be empty)
bracket         estimated, target, reasons[]
mana            summary, issues[]   (narrative only; numbers live in charts)
questions_for_owner[]
charts          curve, pips_vs_sources, roles, synergy_clusters
                ← copied from the fact pack by code, never produced by an LLM
forge           optional on-demand game-log evidence; absent = explicit unavailable
omitted[]       items the verifier removed, with reason (rendered only in a debug appendix)

finding = { id, axis, claim, evidence{cards[], fact_keys[]}, severity (low|med|high|critical),
            confidence (0–1) }
```

## 6. Review quality standard (goes into the Pass B, C, D and E prompts)

- **Specific over generic.** Every claim names the cards and the mechanism.
- **Judge the deck against its own goal**, not a generic power ideal. Andrew builds unusual decks on purpose.
- **Prioritize.** Say what matters most and what to ignore.
- **Separate fact from opinion.** State confidence honestly.
- **Every swap states its trade-off.**
- **Staples only when they serve the plan** and the profile allows them.
- **If the deck is good, say so.** Don't invent problems to look thorough.

## 7. Player profile (evolving, with overrides and full history)

- Inferred from decks, ratings, and games; **user-steerable overrides**; **full history kept** in Postgres (every change/version, with provenance and timestamp).
- A learned/inferred value never overwrites a user-stated override; deck-level intent beats profile on conflict.
- Plain-language summary + charts come from stored data, not from an LLM at render time.

## 8. Card workflow (every implementation card)

1. `kanban_show()`. Read the body, **parent task results** (they carry decisions and PR links), and the comment thread.
2. `cd $HERMES_KANBAN_WORKSPACE`. You're in a worktree on branch `reviewer/<card>`. Run `git fetch origin && git merge --ff-only origin/main` before you start. Never rebase a pushed branch, never force-push.
3. **Worktrees don't copy untracked files.** If you need `.env`, symlink it from the main checkout (first entry in `git worktree list`). Never commit it.
4. **Scope.** Touch only what your card names, plus tests, `CHANGELOG.md`, and `docs/reviewer/`. If you need something outside that, explain in a `kanban_comment` and do the minimum.
5. **No fan-out (except MTG questions for Oracle).** Put extra work you discover under `followups` in your handoff metadata.
6. **Ambiguity.** If the call is reversible, make it and record it under `decisions`. If it is irreversible or changes review-quality policy, use `kanban_block(kind="needs_input", reason="<one question, with your recommended default>")`. For MTG domain judgment, consult Oracle (§2.10) first.
7. **Long work.** Call `kanban_heartbeat` at least every 30 minutes.
8. **CHANGELOG is a collision hotspot.** Parallel cards all add under `[Unreleased]`. On conflict, keep both lines.
9. **Finish.** Run the tests (plus `make eval-gates` once it exists), open a PR titled `reviewer: <card title>` with `--body-file`. When CI is green on the head commit, create a tester card (`TEST REVIEW: PR #N ...`, no parents, body = PR URL, this card's id, In-Scope Files) per the TESTER HANDOFF RULE. Wait for the `[QA_PASS]` comment, then merge per the SELF-MERGE RULE (`merge_ready_check.py` must print CLEAN; `gh pr merge <n> -R andrewgari/commander-lab --squash`). Never merge with unresolved comments or red CI.

## 9. Handoff metadata (required keys)

```json
{
  "published_pr": "https://github.com/andrewgari/commander-lab/pull/N",
  "branch": "reviewer/05-candidate-pool",
  "changed_files": ["..."],
  "verification": ["<command> → <result>"],
  "decisions": ["<reversible calls you made and why>"],
  "residual_risk": ["<what isn't tested or is fragile>"],
  "followups": ["<work you found but didn't do>"]
}
```

Use `null` for keys that don't apply. A summary is ≤6 lines.

## 10. QA protocol (tester)

The tester is the skeptic. Assume the work is wrong until you've checked it.

1. Run the full test suite, and `make eval-gates` once it exists.
2. Walk the card's **Acceptance** list item by item. For each one, record the evidence (command output, file:line).
3. Hunt for violations of §2: LLM-sourced facts, unvalidated card names, silent fallbacks, Redis/Jinja creep, writes to live data.
4. Flag scope creep and unrelated changes.
5. **Pass:** comment `[QA_PASS]` on the implementer's card with evidence. The implementer then merges per §8.9.
6. **Fail:** comment `[QA_FAIL]` with a numbered list of required changes, each one verifiable. The implementer fixes production code; tests are never edited to force a pass. Max 3 coder↔tester rounds, then the card blocks with the open failures.
