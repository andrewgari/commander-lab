---
name: deck-theory
description: "Use when Commander Lab requests a Theory (a thematic overhaul doc) for a deck. Rebuilds the 100 around a theme from this deck, then free owned cards, then buys under the cap; ends with one JSON block matching theory.schema.json."
version: 1.0.0
author: Oracle (Hermes Agent)
license: MIT
metadata:
  hermes:
    tags: [mtg, commander, deck-theory, overhaul, api, json, oracle]
    related_skills: [deck-review, deckbuilding-craft, table-health, commander-math]
---

# Deck Theory (thematic overhaul)

Commander Lab calls this skill through the API, and a machine parses the reply. The structure is
the contract: the same input must always produce the same shape. The creed still applies: a
deck's spirit outranks its power. Theory is a thought experiment; it never edits the deck.

Schema: `references/theory.schema.json` (JSON Schema draft 2020-12, `schema_version: "1"`).

## Input (from Commander Lab)
A JSON request with: `deck_id`, `deck_name`, `theme` (free text), `commander_mode` (`keep` or
`open`), `commanders`, `color_identity`, `decklist` (`[{name, qty}]`, 100 cards incl. the command
zone), `journal` (`thesis`, `pillars[{name, description}]`, `pet_cards[]`), `free_agent_owned`
(names of cards Andrew owns with a copy NOT in any deck), and `caps` (`hard_cap`, `soft_cap` in
USD per card, `no_budget`). If a field is missing, use an empty value and say so in the prose.
Never guess what it should have been.

`mechanically_similar` (optional, may be `{}`): `{card name: [{name, sim}, ...]}` for the
commander, the pets and the first cards of the decklist, a few neighbours each (`sim` is cosine
similarity, 0 to 1, best first). It means **similar rules text and mechanics**: cards that do the
same thing (Sol Ring is near other mana rocks). It is **NOT synergy** and **NOT "played with"**
(that is a different signal, card2vec). Use it to find like-for-like swaps (a cheaper or owned
card that does the same job), never as evidence that two cards combine well. Cards newer than
the dataset (after Aetherdrift) are simply absent. It is advice only; the legality, source and
price checks below still apply to anything you pick from it.

## What the app checks afterwards (so don't fake it)
Commander Lab re-derives every card's `source` from its own collection registry, re-prices every
card from Scryfall USD, recomputes `cuts`, `buy_total` and `reuse_count`, and drops anything
illegal, off-identity, unresolvable, unpriced, or over the hard cap. Your tags and prices are a
best effort; the app's are the truth. Spend your effort on the build, not on bookkeeping.

## Hard rules
1. **Output ends with exactly ONE fenced ```json block** that validates against the schema.
   Nothing comes after it. Any prose goes before it and stays at 15 lines or fewer.
2. **Card pool order: deck → free agents → buys.** Reuse this deck's cards first (`kept`). Then
   `free_agent_owned` (`owned`). Only then buy (`buy`). **Never take a card from another deck.**
   A card not in `decklist` and not in `free_agent_owned` is a `buy`, even if you think Andrew
   owns it.
3. **Buys stay under the cap.** Never add a buy over `caps.hard_cap` (unless `no_budget`). A buy
   over `caps.soft_cap` is allowed only with a one-line justification in `why`. Every buy needs a
   Scryfall USD price; if a card has none, don't buy it.
4. **Commander mode.** `keep`: the commander(s) stay; do NOT emit a `commander_change` section.
   `open`: you may swap the commander if the theme is clearly better served. The new commander
   must be Commander-legal, able to command (legendary creature, Background, or "can be your
   commander"), and, if bought, priced and under the hard cap; otherwise the app reverts the
   swap. If you swap, emit
   `commander_change` with `new_commanders`, `reason`, and `reuse_count`. If you don't swap,
   omit the section.
5. **Pets are kept.** Every `journal.pet_cards` card stays in the list, unless a commander swap
   puts it outside the new colour identity. Then list it in `commander_change.pets_lost` and say
   so in `reason`. Never cut a pet for power.
6. **Legal 100.** Exactly 100 cards including the commander(s); singleton except basics (and
   "any number" cards); every card Commander-legal and inside the (new) commander's colour
   identity. Card names use the Scryfall `name` exactly, including both faces of a DFC (`A // B`).
7. **Salt flags per table-health.** Flag every feel-bad and every high-salt card with
   `salt_flag: {level, reason}` (`reason`: `"<category>: <one line> [source]"`). Flag; never
   remove a card for being salty. Unverified salt is written `salt: unverified`.
8. **Cite or omit.** No made-up cards, prices, salt scores or rulings. If a name won't resolve on
   Scryfall (`https://api.scryfall.com/cards/named?exact=`), leave it out.
9. **Deterministic.** Follow the steps in order and sort as specified.

## Steps
1. **Read the deck.** Resolve the commander(s) and the list on Scryfall. Note the colour
   identity, the Journal thesis/pillars, and the pets.
2. **Theme thesis.** Write a one-sentence thesis that fuses the theme with the deck's spirit,
   and 3-5 pillars (name + one line). If the theme fights the commander and mode is `open`,
   decide on a swap now (rule 4). If mode is `keep`, bend the theme to the commander.
3. **Shape.** Aim for the Command Zone 2025 template: 38 lands, 10 ramp, 12 card advantage,
   12 targeted disruption, 6 mass disruption, about 22 plan cards. Deviate only with a reason.
4. **Fill from the pool, in order** (rule 2): keep every deck card that serves the new thesis or
   a template slot; then free agents; then buys under the cap. Prefer a pillar card over a
   stronger off-plan card. Keep the pets (rule 5).
5. **Cuts.** Every deck card you didn't keep, with a one-line `reason`.
6. **Buy total.** Sum the buys' Scryfall USD prices (the app re-checks it).
7. **Table health.** Run the table-health gates on the new list; flag salty/feel-bad cards (rule 7).
8. **Emit** the JSON.

## Sections (v1), in this order
| type | data |
|---|---|
| `thesis_pillars` | `{thesis, pillars:[{name, description}], theme_fit}`: theme_fit is one line on how the theme and the spirit meet |
| `decklist` | `{groups:[{role, cards:[{name, qty, source, price?, why?, salt_flag?}]}]}`: roles in this order: Commander, Lands, Ramp, Card advantage, Targeted disruption, Mass disruption, then one group per pillar (role = pillar name). `source` is `kept`, `owned` or `buy`; `price` only for buys |
| `cuts` | `{cards:[{name, reason}]}` |
| `buy_total` | `{total_usd}` |
| `commander_change` | only in `open` mode when swapped: `{new_commanders[], reason, reuse_count, pets_lost[]}` |

You may add more sections with other `type`s (e.g. `notes`) after these; the app keeps them.

## Ordering (same input, same structure)
- Groups in the role order above; cards inside a group by `source` (kept → owned → buy), then A-Z.
- `pillars`: the order you'd explain them in; `cuts`: A-Z.
- Empty lists are `[]`. Leave out optional keys you don't use; never send null.

## Output shape (example values only; never copy them)
```json
{
  "schema_version": "1",
  "sections": [
    {"type": "thesis_pillars", "title": "Thesis",
     "data": {"thesis": "Kaalia drops an angel army that wins in the air.",
              "pillars": [{"name": "Angel payload", "description": "Big angels Kaalia cheats in."}],
              "theme_fit": "Angels are already half the spirit."}},
    {"type": "decklist", "title": "Decklist",
     "data": {"groups": [
       {"role": "Commander", "cards": [{"name": "Kaalia of the Vast", "qty": 1, "source": "kept"}]},
       {"role": "Lands", "cards": [{"name": "Plains", "qty": 13, "source": "kept"}]},
       {"role": "Angel payload", "cards": [
         {"name": "Lyra Dawnbringer", "qty": 1, "source": "kept"},
         {"name": "Example Buy Angel", "qty": 1, "source": "buy", "price": 4.5, "why": "lifelink anthem for angels",
          "salt_flag": {"level": "low", "reason": "none: plain beater [edhrec.com/top/salt 2025]"}}
       ]}
     ]}},
    {"type": "cuts", "title": "Cuts", "data": {"cards": [{"name": "Example Demon", "reason": "off-theme"}]}},
    {"type": "buy_total", "title": "Buy total", "data": {"total_usd": 4.5}}
  ]
}
```

## Pre-emit checklist
- [ ] Exactly one ```json block, and it is the last thing in the reply
- [ ] 100 cards incl. commander(s); singleton; legal; inside the colour identity
- [ ] Pool order respected; nothing taken from another deck; buys under the hard cap
- [ ] Every pet kept (or listed in `pets_lost` because of a swap)
- [ ] `commander_change` present only when `commander_mode` is `open` AND you swapped
- [ ] Salt/feel-bad cards flagged; every card name resolves on Scryfall
