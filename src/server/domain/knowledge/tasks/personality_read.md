## Task: Deck Personality Read
A machine parses your reply. The structure is the contract: the same input must always produce the same shape.

The creed: **The rulebook scores are evidence, not the verdict.** Keyword heuristics frequently mislabel tuned, sweaty decks as pure Timmy or Johnny simply because they contain high mana costs or tribal themes. Your job is to make the final, nuanced, expert psychographic call. Every label and score must be justified with concrete cards, combos, table-impact context, and journal lines.

Output must validate against the personality-read schema (`schema_version: "1"`).

### Input
A JSON request with:
- `deck_id`: registry ID (e.g. `archidekt:417707`)
- `deck_name`: deck name
- `commanders`: list of commander names
- `color_identity`: colors (e.g. `WBR`)
- `decklist`: cards with quantities and type lines (`[{name, qty, type_line}]`)
- `rulebook_scores`: deterministic keyword/signal scores for all 5 axes (`timmy`, `johnny`, `spike`, `vorthos`, `melvin`), including per-signal point breakdowns (T1-T5, J1-J5, S1-S7, V1-V4, M1-M5), card scores, journal nudges, and notes.
- `commandersalt`: cached Commander Salt data (`salt`, `power`, `bracket_rating`, `synergy_rating`, `threat_rating`, `high_salt_cards`, `combos`).
- `edhrec_salt`: cached EDHREC salt if present.
- `combos`: detected combos from Commander Spellbook.
- `engines`: detected card-advantage and synergy engines from the deck interaction graph.
- `journal`: thesis, pillars, pets, target bracket, and target power.
- `game_changers`: Game Changers present in this deck.

### Psychographic axis definitions
1. **Timmy (Big, splashy moments)**: Playing to experience something epic. Big creatures, massive spells, dramatic board states, swingy swings.
2. **Johnny (Creative expression & combo)**: Playing to show off a build, find non-obvious synergies, assemble multi-piece engines, or win with unique interactions.
3. **Spike (Efficiency, consistency & intent to win)**: **Spike is defined as intent and efficiency** — tutor density, high interaction count, fast mana, low mana curve, redundancy, resilience, and consistent execution. **CRITICAL: Spike is NOT the absence of flavor.** Flavor and power coexist. A heavily themed, flavorful deck (such as a Doctor Who time travel deck or Kaalia demon tribal) that runs efficient mana, tutors, Free spells, and tight interaction can be **high Spike AND high Vorthos**. Never penalize Spike just because a deck has a story or theme.
4. **Vorthos (Lore, story, art & aesthetic flavor)**: Playing for lore, theme coherence, character flavor, narrative resonance, or art continuity.
5. **Melvin (Rules architecture, mechanical purity & intricate game mechanics)**: Playing to appreciate the rules engine: complex stack interactions, state-based actions, layers, phase manipulation, time travel, counters, or clean mechanical execution.

### Task rules
1. **Output ends with exactly ONE fenced ```json block** matching the schema. Nothing comes after it. Any prose goes before it and stays at 15 lines or fewer.
2. **All 5 axes scored 0-100**: `timmy`, `johnny`, `spike`, `vorthos`, `melvin` must all receive integer scores from 0 to 100.
3. **Per-axis justification**: every axis score must include a concise, clear `justification` citing specific cards from the deck and/or specific journal lines (thesis, pillars, pets). Cite exact card names in the `cards` array.
4. **Rulebook overrides**: the rulebook scores are evidence. Whenever your final score for an axis differs from the rulebook score, record it in the `overrides` array: `{axis, rulebook_score, oracle_score, delta, reason}` (where `delta = oracle_score - rulebook_score`; `oracle_score` is the field name for your final score). Explain why the rulebook's keyword heuristic over- or under-valued the axis.
5. **Table-impact and feel-bad flags**: follow "## Salt and table health": **flag, never forbid.** Include any cards that cause high table friction, salt, stax locks, mass land destruction, fast mana spikes, or extra turn loops in `table_impact_flags`: `{card, category, severity, reason}` (severity: `low`, `medium`, `high`).
6. **Leans toward & runner-up**: set `leans_toward` to the dominant psychographic axis (`timmy`, `johnny`, `spike`, `vorthos`, `melvin`), or `"balanced"` if scores are evenly bunched or no axis stands out. If there is a close second axis, set `runner_up`.
7. **Cite or omit**: do not name cards that are not in the decklist or journal.

### Steps
1. **Examine the evidence**: review the decklist, rulebook scores, and per-signal breakdown. Check the Commander Salt metrics (salt rating, power rating, high-salt cards; second opinion only, see "## Commander Salt") and EDHREC data. Review detected combos, engines, and Game Changers. Review Journal thesis, pillars, and pet cards.
2. **Evaluate Spike vs Vorthos/Timmy**: is this deck highly tuned despite a strong theme? If it has fast mana, tutors, 10+ interaction pieces, or compact combos, give it the Spike score it deserves.
3. **Determine the 5 scores**: assign integer scores (0-100) to each axis. Draft the justification citing exact cards and journal lines.
4. **Identify overrides & table-impact flags**: compare each score to the rulebook score and document all deltas in `overrides`. Identify any feel-bad cards or combos in `table_impact_flags`.
5. **Output**: short introductory prose (under 15 lines), then the fenced ```json block.
