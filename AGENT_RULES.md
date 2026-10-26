# Commander Lab — Agent Coding Rules

Every agent working in this repo follows these rules. The full plan and
rationale: `docs/ARCHITECTURE.md`. (This file is referenced from `AGENTS.md`;
if that link is missing, treat this file as authoritative anyway.)

## Target architecture (approved plan)
Python + FastAPI + Postgres (source of truth) + SvelteKit UI; pgvector +
Apache AGE as **derived, rebuildable** indexes; thin vertical slices touching
decks+inventory+profile together. Target layout:
`src/server/{app,domain,infra,workers}`, `src/web/`, `docs/`, `tests/`.

## Rules (enforceable — checked in review/CI)
1. **Postgres is the only source of truth.** Redis = cache/queues.
   Vector/graph stores are rebuildable indexes — writes only via
   `src/server/workers/index_rebuild.py`.
2. **`src/server/domain/` imports no FastAPI, SQLAlchemy, or `src/server/infra/*`**
   (import-linter in CI).
3. **All card facts come from the DB or Scryfall.** LLM output is
   prose/judgment only; validate every LLM card name against Scryfall before
   storing.
4. **Missing data renders "unavailable" + reason** — never guessed, never
   shown as 0.
5. **Numbers and labels are deterministic code.** If the LLM could have made
   it up, it doesn't ship.
6. **No new root-level `.py` files.** Endpoints go in `src/server/app/api/` with
   typed pydantic responses; logic in the matching `domain/` module.
7. **Thin vertical slices** (decks+inventory+profile together); every slice
   ships with tests and a docs update.
8. **Draft mode is staging-only**: nothing reaches canonical tables until
   the user commits.
9. **Load-bearing conventions**: deck statuses `Have/Virtual/Pending/
   Possible`; structural categories `Commander/Sideboard/Maybeboard/
   Considering` (preserved, excluded from tag learning); active physical
   folder ID `588380`. Don't rename or repurpose.

10. **Consult the Oracle on MTG questions.** Any Magic question — rules,
   card judgment, brackets, mana math — goes to the Oracle profile (card
   assigned to `oracle`), not a guess and not Andrew. Iterate with the Oracle
   until resolved. Oracle answers are advisory; the coder still owns the code
   and the final decision.

## Changelog (Keep a Changelog 1.1.0)
User-facing change → one consolidated line under `## [Unreleased]` in the
same PR (`### Added|Changed|Deprecated|Removed|Fixed|Security`); no
user-facing change → tick the PR-template checkbox, add nothing. Describe
user impact, not implementation. Never write PR numbers, hashes, branch
names, task ids, or version bumps. Breaking changes prefixed `**BREAKING:**`.
