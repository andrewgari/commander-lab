---
name: deck-sim-review
description: "Use when Commander Lab asks for a one-off simulation review of a deck. You get the simulation telemetry compared against the static Review, the user's notes and focus areas and the Deck Journal; ends with one JSON block matching sim_review.schema.json."
version: 1.0.0
author: Oracle (Hermes Agent)
license: MIT
metadata:
  hermes:
    tags: [mtg, commander, simulation, deck-review, api, json, oracle]
    related_skills: [deck-review, deck-theory, personality-read]
---

# Deck Sim Review: one-off, single turn

Commander Lab ran Forge AI pods with the user's deck and diffed the telemetry against the static
Deep Review. You read that diff and explain what the simulation shows. **Single turn: no tools, no
follow-up questions.** Schema: `references/sim_review.schema.json` (`schema_version: "1"`).

## Input
One JSON block: `{deck_id, fingerprint, params, comparison, static_review_summary, journal}`.
- `params`: `opponent_mode`, `games`, `opponents`, `notes` (free text; may be empty), `focus` (chip slugs) and `focus_snapshot` ([{slug,label}]). Focus and notes are EMPHASIS only: still write the full review, lead with the focused areas, and use only computed numbers; if a focus area has no matching metric, say so.
- `comparison`: tempo (land drops, T5 mana starvation, commander cast turn vs mana value, mana
  utilization), card comparison (confirmed stragglers, surprise dead draws, workhorses), pod breakdown.
- `journal`: thesis, pillars, win cons, pet cards. May be empty.

## Hard rules
1. **Numbers come from the input.** Quote only numbers present in `comparison` or `params`, as given.
   Never invent, round differently or estimate. Small samples: say so when `games` is low.
2. **Respond to the notes.** If `params.notes` is non-empty, `notes_response` answers it directly and
   cites the run's numbers. If empty, return `""`.
3. **Static vs simulated.** Name where the sim agrees with the paper read and where it disagrees.
4. **Pet cards are protected.** Never suggest cutting a Journal pet card; if one underperforms, say so
   and suggest supporting it instead (curve, ramp, enablers).
5. **Tweaks are concrete and telemetry-backed.** 2 to 4 `actionable_tweaks`, each tied to a number or
   card from `comparison` (e.g. "add one land: T5 starvation rate was X vs Y expected"). Only name cards
   that appear in the input; do not recommend outside cards by name unless generic ("a cheap ramp piece").
6. **No outside facts**: no prices, rulings, win rates from other sources.
7. Echo `fingerprint` verbatim. Plain text, no markdown. Section strings at most 900 characters, each tweak at most 300.

## Output
Any text before the JSON stays under 5 lines. Then exactly ONE fenced ```json block, last in the reply:

```json
{
  "schema_version": "1",
  "fingerprint": "<echo>",
  "headline": "…",
  "notes_response": "…",
  "tempo_and_mana_read": "…",
  "card_performance_read": "…",
  "actionable_tweaks": ["…", "…"]
}
```

## Pre-emit checklist
- [ ] Exactly one ```json block, last in the reply
- [ ] `fingerprint` matches byte for byte
- [ ] 2-4 tweaks, none cutting a pet card
- [ ] Every number appears in the input
