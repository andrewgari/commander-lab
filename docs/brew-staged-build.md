# Brew: staged build (skeleton → core → deck → review)

The default builder (`StubBrewBuilder`, `domain/draft/brew_builder.py`) now builds in explicit stages. Each stage is a
function in `brew_stages.py` / `brew_panel.py`, unit-tested on its own with mocked EDHREC, Scryfall and LLM
(`tests/test_brew_stages.py`, `tests/test_brew_panel.py`). The donor-deck path, hard rules (BREW-2), composition-template
precedence (form > profile > app), the release gate and the oracle review keep their contracts.

| # | Stage | LLM? | Module | What it does |
|---|-------|------|--------|--------------|
| 1 | Skeleton | no | `brew_stages.run_skeleton`, `brew_edhrec_pool` | Land target from the template's `lands` slot, split evenly over the commander's colour identity (Wastes if colourless); downloads the temporary EDHREC pool for commander **and** archetype. |
| 2 | Core construction | panel (2 lenses) | `brew_stages.run_core` | Landbase upgrades inside the budget, then ~25 spells: template-category needs, then cards on ≥40% of both the commander's and the archetype's decks. |
| 3 | Deck construction | panel (4 lenses + critic) | `brew_plan`, `brew_builder.StubBrewBuilder.build` | Builds a deck plan from the notes, themes and commander text; donor → owned → plan-driven Scryfall candidates; replaces popular-only scaffold; panel reviews scaffold + picks; cuts are refilled. |
| 4 | Final review | existing oracle gate + panel (advisory) | `brew.execute_build`, `brew_review` | Oracle pass/fail stays the only gate. The panel's concerns are recorded for the UI, never block. |

## EDHREC is a template, not the build
Stages 1-2 use EDHREC to scaffold (categories, the 40%+ cards, staples). Everything EDHREC contributes is marked
`provenance: scaffold_edhrec` and is **provisional**; stage 3 plans from Scryfall instead:
* `brew_plan.build_plan`: plan items from the user's themes, mechanics found in the commander's rules text/notes/idea
  (counters, tokens, sacrifice, graveyard, ...) and the commander's creature types, each a Scryfall search
  (`o:"..."`, `otag:...`, `t:...`, Commander-legal, inside identity, no lands; ≤8 live searches/build, cached 24 h).
* Scoring in stage 3 weighs EDHREC inclusion/synergy at 25% (`EDHREC_PRIOR_SCALE`) and doubles commander-synergy; the plan's
  terms join the themes the scorer looks for. Retrieval order from Scryfall decides only which ≤175 hits of a broad query
  come back; the scorer ranks them.
* Popular-only scaffold (EDHREC-sourced, in no template category, not on the plan, not owned) is cut, up to half of it
  and no more than there are plan candidates, lowest inclusion first. Functional scaffold (ramp, draw, removal, wipes) stays.
* The panel gets the plan and the scaffold list, scaffold is NOT protected in stage 3, and the critic's standing question is
  "is this just a popular pick?"
* Degradation matrix (all tested): EDHREC down → Scryfall tags + plan carry it; Scryfall down → EDHREC scaffold + pools;
  both down → pools, #527's ranker (itself a fallback ordering of Tier 3, not the final ranking) and basics still give a
  complete deck; stage statuses say `degraded`.
* Provenance values on every draft card: `commander`, `donor`, `owned`, `scaffold_edhrec`, `scaffold_template`
  (land staples, Scryfall-tag category picks), `plan`, `fill` (generic top-up), `basic`.

## Data kept on the session between steps
* `session["stage"]` / `session["progress"]` ("Core construction: reviewing 25 core picks"): the UI already shows `progress`.
* `session["build_stages"][stage]` = `{label, status: running|done|degraded, message, summary, degraded?}`; summaries carry
  the land plan, EDHREC card counts, chosen archetype, lands added, spells per source.
* `session["stage_reviews"][stage]` = the panel record: per-lens status/cut counts, the challenged ("contested") picks,
  critic status, per-card decisions with reasons, accepted suggestions, call count.
* `session["profile_snapshot"]` (profile read once at start: playstyle text, power tendency, dislikes, budget norms) and
  `session["budget"]` (USD, optional).
* Temp EDHREC pool: Redis `brew:edhrec_pool:{session_id}`, 6 h TTL, deleted in `execute_build`'s `finally`. The raw page
  cache (`card_eval:edhrec:{slug}[/{theme}]`, 7 days, shared with card review) is *not* temp and is reused.

## Stage 1: partners, backgrounds, archetype
* All commanders go into one slug (`commander_slug`), exactly as EDHREC's partner/background URLs do.
* Archetype = the first of the session's `themes` that EDHREC has a page for under this commander
  (`commanders/{slug}/{theme}.json`); unknown themes are skipped and listed in the stage summary.
  No match → commander-only data (the ≥40% rule then uses the commander page alone).
* Degradation: EDHREC down/missing → empty pool, stage status `degraded`; stage 2 falls back to Scryfall tags, stage 3
  to the pools (and #527's ranker), and the gate still refills.

## Stage 2: lands, budget, spells
* Land order: Command Tower, Exotic Orchard, Path of Ancestry; EDHREC's ≥40% lands; battlebond, check, shock, triome
  lands; Evolving Wilds, Terramorphic Expanse, Reliquary Tower. Every land is verified against the card database (colour
  identity inside the commander's, not banned, known price) so pair lands only appear in decks that can play them.
  At most half of the planned lands are replaced ("half-completed manabase").
* Budget (`land_budget`): owned lands are free. Otherwise an explicit `budget` (form field or the profile's soft/hard cap)
  gives lands 25% of it; with none, `best_build` is uncapped (each land ≤ $40) and `owned_first` spends ≤ $15 (each ≤ $8).
* Spells: for each template category with a deficit (ramp, card advantage, removal, wipes), candidates are EDHREC
  card-list tags plus Scryfall Tagger tags (`otag:ramp`, `draw`, `removal`, `sweeper`; cached 24 h, ≤4 live lookups per
  build) scored by the existing scorer (`CATEGORY_SHARE` = 12 slots); then the ≥40%/≥40% staples up to 25 spells; then
  next-most-played if still short. Everything passes the builder's `add()` (resolution, singleton, power gate) and hard rules.

## Reviewer panel (`brew_panel.py`, LLM task `brew_panel`)
* Lenses: power/consistency, synergy/flavor, mana base, playstyle fit. Stage 2 uses power + mana base; stage 3 and the
  final review use all four. Reviewers run in parallel, one call each; unmentioned cards are keeps.
* Adversarial pass: ONE critic call per stage, only for challenged picks: contested (some but not all reviewers cut) or
  high-impact (Game Changer/fast-mana/combo names, ≥ $25) with ≥1 cut. Unanimous cuts and uncontested keeps never reach it.
* Reconciliation (deterministic, no extra call): unanimous cut by ≥2 reviewers → cut. Challenged pick: reviewers + critic
  as one extra voice, cut iff cut weight > keep weight; ties keep; owned or EDHREC-core picks also need a reviewer
  majority. A lone reviewer never cuts alone. Suggestions must come from the offered candidates and have 2 backers.
* Budgets (`PanelBudget`, env `BREW_PANEL_BUDGET` JSON): 2500 tokens/call, one retry at 8000 on truncation (#526 lesson),
  6 calls/stage, 12 critic picks, 60 proposals. Model/tokens also via `LLM_MAX_TOKENS_BREW_PANEL`. Truncated twice,
  unparseable, over budget or no LLM configured → that voice is ignored and recorded `unavailable`; the stage stands.

## Playstyle
The profile snapshot feeds the panel's `PLAYSTYLE` line (builder identity, power tendency/notes, "welcomes generic
staples", disliked strategies/play patterns). Disliked *cards* are excluded deterministically from every stage.
