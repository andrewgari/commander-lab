---
name: deck-review
description: "Use when Commander Lab requests a Deep Review of a deck. You get the app's computed metrics plus the Journal and write prose per section only; ends with one JSON block matching review.schema.json."
version: 2.0.0
author: Oracle (Hermes Agent)
license: MIT
metadata:
  hermes:
    tags: [mtg, commander, deck-review, api, json, oracle]
    related_skills: [deck-spirit-interview, table-health, personality-read, deck-theory]
---

# Deck Review (Deep Review): prose only

Commander Lab calls this skill through the API and a machine parses the reply. Commander Lab has
already computed every number, label and pick (genre, composition, curve and tempo, focus, strong
cards, stragglers, combos and engines). **You explain; you never decide.**

Schema: `references/review.schema.json` (`schema_version: "1"`).

## Input
One JSON block: `{deck_id, fingerprint, metrics, journal}`.
- `metrics` is the app's review metrics. Everything you may say about the deck comes from it.
- `journal` is the Deck Journal (thesis, pillars, win cons, pet cards). It may be empty.

## Hard rules
1. **Prose only.** Output one string per section: `genre`, `composition`, `curve_tempo`, `focus`,
   `strong_cards`, `stragglers`, `combos_engines`, `oracle_summary`.
2. **No numbers of your own.** Quote a number only if it appears in `metrics`, copied as given. Don't
   compute, round differently, estimate, convert, or compare against outside benchmarks. The app
   strips any sentence containing a number that isn't in the metrics. Turn references ("turn 4") are
   fine. If you can say it without a number, do.
3. **No labels or picks of your own.** Don't name a genre, win line, strong card or straggler that
   isn't in `metrics`. Don't suggest cuts or adds, and don't re-rank. Pet cards are protected and are
   never described as cuts.
4. **Use the Journal as context.** Where `metrics.focus` shows a Journal win con as "claimed but
   unsupported" or "supported but unclaimed", say so plainly. Never rewrite the thesis.
5. **No outside facts.** No prices, rulings, win rates, salt scores or inclusion rates.
6. **Echo `fingerprint` verbatim.**
7. **Empty is OK.** If a section's metrics are null or empty, return `""` for it. Don't pad.
8. Each section is at most 900 characters, plain text, no markdown. `oracle_summary` is 2-4 sentences
   and must not be empty.

## Output
Any text before the JSON stays under 5 lines. Then exactly ONE fenced ```json block as the last thing
in the reply:

```json
{
  "schema_version": "1",
  "fingerprint": "<echo>",
  "sections": {
    "genre": "…", "composition": "…", "curve_tempo": "…", "focus": "…",
    "strong_cards": "…", "stragglers": "…", "combos_engines": "…", "oracle_summary": "…"
  }
}
```

## Pre-emit checklist
- [ ] Exactly one ```json block, last in the reply
- [ ] `fingerprint` matches the input byte for byte
- [ ] All 8 section keys present, no extra keys
- [ ] Every number appears in `metrics`; no card or genre name outside `metrics`
- [ ] No cut/add suggestions
