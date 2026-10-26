# Commander Lab — Deep Review (saved, repeatable deck reviews)

## Goal
One reliable, repeatable, saved review per deck: genre, composition, curve + tempo, focus ("how do
you win?"), strong cards, stragglers, combos/engines. Same decklist → same numbers, every time.
Workshopped with Andrew 2026-10-16.

## State on main (v0.4.47) — reuse, don't rebuild
- oracle `skills/mtg/deck-review` (rubric + review.schema.json, P(on-curve), wincon tags, combos).
- App: analytics/composition.py, analytics/interactions/{engines,combos}.py (Spellbook), Tuner
  weak-performer math (analytics/tuner.py), Journal (win cons, pillars, pet cards), produces/consumes tags.
- Theory pipeline = the pattern to copy: POST /api/decks/{id}/theory → background oracle job → saved
  versioned doc (typed sections) in Redis, shown under Ideas.
- Old deterministic GET /api/decks/{id}/review (deck_review.py) has no UI caller. Replace/deprecate it.

## Core rule
All numbers and labels are deterministic app math. oracle only writes prose over them (explains,
never decides). Offline oracle → the review still renders the math with an "explanation offline" note.

## 1. Genre registry (new page, like /tags)
- Andrew defines genres: name, description, required card roles (e.g. Aristocrats: sac outlet +
  death trigger + fodder), expressed in the existing produces/consumes tag vocabulary.
- "Autofill" button: if it's a known MTG theme, oracle drafts the description + roles; Andrew edits/saves.
- Seed with common genres (aristocrats, impulse draw/cast, storm, kindred/tribal, spellslinger,
  voltron, tokens, +1/+1 counters, reanimator, landfall, superfriends, stax, group hug…) as editable drafts.
- Review picks ONLY from this list: one primary + up to two secondary genres, scored by role coverage.

## 2. Review metrics (pure, deterministic)
- Genre match: per-genre coverage score from role counts.
- Composition: vs the Command Zone template (existing composition.py).
- Curve math: P(on-curve commander), land/ramp sufficiency (existing).
- Tempo = "don't durdle": expected unspent mana per turn T2–T6; active-card share (immediate effect:
  ETB, cantrip, board impact); durdle tax (taplands, setup-only, pay-now-payoff-later); flex
  (instant-speed share).
- Focus: 1–3 win lines computed from combos, engines, and wincon-tagged cards. Diff vs the Journal win
  cons: "claimed but unsupported" and "supported but unclaimed".
- Strong cards: high graph degree, in a win line/engine, active.
- Stragglers: Tuner weak-performer math + durdle + not in any win line. Pet cards are shown as protected,
  never stragglers.
- Combos/engines: existing engines + Spellbook combos (in deck, one card away).

## 3. Saved review doc
- Versioned JSON, typed sections (same format/renderers approach as Theory): genre, composition,
  curve_tempo, focus, strong_cards, stragglers, combos_engines, oracle_summary.
- Redis `review:{deck_id}` (latest) + history index. Stores the decklist fingerprint (sorted card names+qty
  + commander) and the genre-registry version.
- Rebuilds only on a Rebuild click (no auto-rebuild on page load). Sync that changes the fingerprint →
  "stale" badge.
- oracle deck-review skill updated (coder writes it in ~/.hermes/profiles/oracle/skills/mtg/deck-review,
  repo copy in docs/oracle/): takes the app metrics as input, outputs prose per section only; the app
  validates against the schema.

## Future (not v1)
- forge_sim section: per-card dead-draw rate and mana starvation turns from MTGForge headless sims, fed as an
  extra signal into the straggler/tempo scores. Blocked on the Forge deploy (t_5f843267, default board).

## Non-goals
No Forge, no deck writes, no auto-rebuild, no LLM-decided numbers/labels, no new DB.

## Build chain (commander-lab board, RV-*, strict parents chain, no triage)
RV-1 genre registry + page + autofill → RV-2 metrics engine (tempo, focus, strong/stragglers, genre match)
→ RV-3 saved doc + oracle skill + Rebuild/stale → RV-4 review UI on deck page → RV-4R designer review.
Each card: PR merged, deployed, verified live (port 8020), evidence attached.
