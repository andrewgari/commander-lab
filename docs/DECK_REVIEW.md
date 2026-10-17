# Deck Review and Recommendation Pipeline

This document describes the recommendation generator, card swap engine, and report formatting pipeline (`deck_review.py`, `analytics/recommendations.py`, `analytics/report.py`) in Commander Lab.

## Overview

The Deck Review service synthesizes the multi-dimensional evaluation produced by `DeckAnalysisEngine` (theme, curve, cohesion, playfeel, intent alignment) and the user's intake goals (`UserDeckIntent`) into an actionable, human-readable deck review.

Key features:
1. **Thematic & Unique Synergy Recommendations**: Identifies archetype enablers, payoffs, and unique interactions tailored to the commander rather than generic staples.
2. **Recent Sets Prioritization**: Highlights high-synergy inclusions from modern Magic expansions (2022–2024: BLB, MH3, OTJ, MKM, LCI, WOE, MOM, ONE, BRO, DSK, FDN).
3. **Actionable 1-to-1 Card Swaps**: Directly pairs candidate cuts (outclassed spells, curve bottlenecks, off-theme pieces, high-salt friction cards) with recommended upgrades, reporting net mana impact and upgrade rationale.
4. **Multi-Format Export**: Generates publication-ready Markdown reports, clean ASCII CLI terminal displays, and structured JSON payloads.
5. **CLI & REST Integration**: Operates via `python deck_review.py`, `python cli.py review`, and FastAPI REST endpoints (`POST /api/review`, `GET /api/decks/{deck_id}/review`).

---

## Architecture & Data Flow

```
                     +-----------------------------+
                     |  Raw Decklist / Cards / ID  |
                     +--------------+--------------+
                                    |
                                    v
+------------------------+  +-------------------------------+
|  User Review Intent    |->|    Deck Intake Service        |
| (Vision, Power, Wincon)|  | (Normalizes cards & intent)   |
+------------------------+  +---------------+---------------+
                                    |
                                    v
                            +-------------------------------+
                            |   Deck Analysis Engine        |
                            | (Theme, Curve, Playfeel, etc.)|
                            +---------------+---------------+
                                    |
                                    v
                            +-------------------------------+
                            |  Recommendation Generator     |
                            | - Evaluates theme & synergies |
                            | - Injects recent set upgrades |
                            | - Identifies candidate cuts   |
                            | - Formulates 1-to-1 card swaps|
                            +---------------+---------------+
                                    |
                                    v
                            +-------------------------------+
                            |     Deck Review Report        |
                            | - Executive identity summary  |
                            | - Thematic assessment         |
                            | - Playfeel & pacing breakdown |
                            | - Strengths & cohesion gaps   |
                            | - Intent alignment verdict    |
                            | - Swaps table & ranked adds   |
                            +---------------+---------------+
                                    |
                +-------------------+-------------------+
                |                   |                   |
                v                   v                   v
         [ Markdown Export ]   [ CLI ASCII Text ]   [ JSON API ]
```

---

## 1. Recommendation Generator (`analytics.recommendations`)

### Candidate Addition Scoring
Each card in the curated catalog and external provider feed is scored based on:
- **Theme & Archetype Alignment**: Matches primary/secondary archetypes or custom themes (`+0.25` for primary, `+0.15` for secondary/preferred).
- **Recent Sets Bonus (2022–2024)**: Injects fresh design equity (`+0.25`), tagging cards with their set badge (e.g. `[BLB]`, `[MH3]`, `[OTJ]`).
- **Unique Synergy**: Rewards cards with non-generic interactions (e.g. proliferating counters, doubling death triggers, token entry triggers).
- **User Intent Modulation**:
  - `tune_up`: Rewards curve reduction (CMC ≤ 3) and tightening operational speed.
  - `new_build`: Rewards foundational engine pieces and mana rocks.
  - `overhaul`: Rewards archetype pivot enablers.
  - Power tier: Tailors recommendations to casual vs. optimized/competitive tiers.
  - Preferred win conditions: Prioritizes cards matching primary/secondary win conditions (combat, attrition, combo) and excludes cards tied to disliked win conditions.
  - Budget constraints: Strictly filters out cards exceeding `max_card_price`.

### Candidate Cut Heuristics
Cards currently in the deck are evaluated for removal:
- **Generic Outclassed Staples**: Flags inefficient removal/tutors (e.g. Diabolic Tutor, Cancel, Murder, Manalith) in favor of modern modal or synergistic options.
- **High-Salt Inclusions**: Identifies high-friction cards (e.g. Stax, mass land destruction, extra turns) when running at casual/focused power tiers.
- **Mana Curve Bottlenecks**: Identifies heavy 5+ CMC spells when deck average CMC is elevated.
- **Thematic Dilution**: Identifies non-ramp cards with minimal alignment to the primary archetype.

### 1-to-1 Card Swap Formulation
Pairs candidate cuts with candidate additions that upgrade the role, calculating:
- `net_cmc_change`: Change in mana cost (`add_cmc - cut_cmc`).
- `synergy_gain`: Improvement in synergy score.
- `swap_rationale`: Human-readable explanation of why this swap improves the deck.

---

## 2. Report Formatter (`analytics.report`)

### Supported Formats
- **Markdown (`to_markdown()` / `export_markdown()`):**
  - Publication-ready with Markdown headers, bulleted lists, inline code badges, and a Markdown table for 1-to-1 card swaps.
- **CLI ASCII Text (`to_cli()` / `export_cli()`):**
  - Plain-text formatted for 80-column terminal display with divider borders (`===`, `---`) and word-wrapped descriptions.
- **JSON (`to_json()` / `export_json()`):**
  - Fully serializable Pydantic model representation suitable for REST APIs.

---

## 3. CLI Usage

### Direct CLI via `deck_review.py`
```bash
# Review a decklist file and print CLI summary:
python deck_review.py --deck my_deck.txt

# Review with user intent JSON and export to Markdown:
python deck_review.py --deck my_deck.txt --intent intent.json --format markdown --output review.md
```

### CLI Subcommand via `cli.py`
```bash
python cli.py review --deck my_deck.txt --format markdown --output review.md
```

---

## 4. REST API Endpoints

### `POST /api/review` (or `/api/decks/review`)
Accepts JSON payload:
```json
{
  "name": "Atraxa Proliferate",
  "format": "commander",
  "decklist": "Commander\n1 Atraxa, Praetors' Voice\n\nDeck\n1 Sol Ring\n...",
  "user_intent": {
    "deck_vision": {"intent": "tune_up"},
    "target_power_level": {"scale": 8, "tier": "optimized"},
    "preferred_win_conditions": {"primary": "combat"}
  }
}
```
Response:
```json
{
  "success": true,
  "review": { ... },
  "markdown": "# Deck Review: ...",
  "cli": "===================..."
}
```

### `GET /api/decks/{deck_id}/review`
Generates a review for an existing deck saved in the Redis library.
