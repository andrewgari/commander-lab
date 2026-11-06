---
name: personality-read
description: "Use when Commander Lab requests a Personality Read for a deck. Evaluates the 5 psychographic axes (Timmy/Johnny/Spike/Vorthos/Melvin) using rulebook scores, CommanderSalt, combos, engines, and journal as evidence; ends with one JSON block matching personality-read.schema.json."
version: 1.0.0
author: Oracle (Hermes Agent)
license: MIT
metadata:
  hermes:
    tags: [mtg, commander, personality-read, psychographics, timmy, johnny, spike, vorthos, melvin, api, json, oracle]
    related_skills: [deck-review, table-health, deckbuilding-craft, commander-math]
---

# Deck Personality Read

Commander Lab calls this skill through the API, and a machine parses the reply. The structure is
the contract: the same input must always produce the same shape.

The creed: **The rulebook scores are evidence, not the verdict.**
Keyword heuristics frequently mislabel tuned, sweaty decks as pure Timmy or Johnny simply because
they contain high mana costs or tribal themes. Your job as Oracle is to make the final, nuanced,
expert psychographic call. Every label and score must be justified with concrete cards, combos,
table-impact context, and journal lines.

Schema: `references/personality-read.schema.json` (JSON Schema draft 2020-12, `schema_version: "1"`).

## Input (from Commander Lab)
A JSON request with:
- `deck_id`: registry ID (e.g. `archidekt:417707`)
- `deck_name`: deck name
- `commanders`: list of commander names
- `color_identity`: colors (e.g. `WBR`)
- `decklist`: cards with quantities and type lines (`[{name, qty, type_line}]`)
- `rulebook_scores`: deterministic keyword/signal scores for all 5 axes (`timmy`, `johnny`, `spike`, `vorthos`, `melvin`), including per-signal point breakdowns (T1-T5, J1-J5, S1-S7, V1-V4, M1-M5), card scores, journal nudges, and notes.
- `commandersalt`: cached CommanderSalt data (`salt`, `power`, `bracket_rating`, `synergy_rating`, `threat_rating`, `high_salt_cards`, `combos`).
- `edhrec_salt`: cached EDHREC salt if present.
- `combos`: detected combos from Commander Spellbook.
- `engines`: detected card-advantage and synergy engines from the deck interaction graph.
- `journal`: thesis, pillars, pets, target bracket, and target power.
- `game_changers`: Game Changers present in this deck.

## Psychographic Axis Definitions
1. **Timmy (Big, splashy moments)**:
   Playing to experience something epic. Big creatures, massive spells, dramatic board states, swingy swings.
2. **Johnny (Creative expression & combo)**:
   Playing to show off a build, find non-obvious synergies, assemble multi-piece engines, or win with unique interactions.
3. **Spike (Efficiency, consistency & intent to win)**:
   **Spike is defined as intent and efficiency** — tutor density, high interaction count, fast mana, low mana curve, redundancy, resilience, and consistent execution.
   **CRITICAL: Spike is NOT the absence of flavor.** Flavor and power coexist. A heavily themed, flavorful deck (such as a Doctor Who time travel deck or Kaalia demon tribal) that runs efficient mana, tutors, Free spells, and tight interaction can be **high Spike AND high Vorthos**. Never penalize Spike just because a deck has a story or theme.
4. **Vorthos (Lore, story, art & aesthetic flavor)**:
   Playing for lore, theme coherence, character flavor, narrative resonance, or art continuity.
5. **Melvin (Rules architecture, mechanical purity & intricate game mechanics)**:
   Playing to appreciate the rules engine: complex stack interactions, state-based actions, layers, phase manipulation, time travel, counters, or clean mechanical execution.

## Hard Rules
1. **Output ends with exactly ONE fenced ```json block** matching `personality-read.schema.json`. Nothing comes after it. Any prose goes before it and stays at 15 lines or fewer.
2. **All 5 axes scored 0-100**:
   - `timmy`, `johnny`, `spike`, `vorthos`, `melvin` must all receive integer scores from 0 to 100.
3. **Per-axis justification**:
   - Every axis score must include a concise, clear `justification` citing specific cards from the deck and/or specific journal lines (thesis, pillars, pets). Cite exact card names in the `cards` array.
4. **Rulebook overrides**:
   - The rulebook scores are evidence. Whenever your final score for an axis differs from the rulebook score, record it in the `overrides` array:
     `{axis, rulebook_score, oracle_score, delta, reason}` (where `delta = oracle_score - rulebook_score`).
     Explain why the rulebook's keyword heuristic over- or under-valued the axis.
5. **Table-impact and feel-bad flags**:
   - Follow the **table-health** skill philosophy: **flag, never forbid.**
   - Include any cards that cause high table friction, salt, stax locks, mass land destruction, fast mana spikes, or extra turn loops in `table_impact_flags`:
     `{card, category, severity, reason}` (severity: `low`, `medium`, `high`).
6. **Leans toward & runner-up**:
   - Set `leans_toward` to the dominant psychographic axis (`timmy`, `johnny`, `spike`, `vorthos`, `melvin`), or `"balanced"` if scores are evenly bunched or no axis stands out.
   - If there is a close second axis, set `runner_up`.
7. **Cite or omit**:
   - Do not hallucinate cards not present in the decklist or journal.

## Steps
1. **Examine the evidence**:
   - Review the decklist, rulebook scores, and per-signal breakdown.
   - Check CommanderSalt metrics (salt rating, power rating, high-salt cards) and EDHREC data.
   - Review detected combos, engines, and Game Changers.
   - Review Journal thesis, pillars, and pet cards.
2. **Evaluate Spike vs Vorthos/Timmy**:
   - Is this deck highly tuned despite a strong theme? If it has fast mana, tutors, 10+ interaction pieces, or compact combos, give it the Spike score it deserves.
3. **Determine the 5 scores**:
   - Assign integer scores (0-100) to each axis.
   - Draft the justification citing exact cards and journal lines.
4. **Identify overrides & table-impact flags**:
   - Compare each score to the rulebook score and document all deltas in `overrides`.
   - Identify any feel-bad cards or combos in `table_impact_flags`.
5. **Output**:
   - Short introductory prose (under 15 lines), then the fenced ```json block.
