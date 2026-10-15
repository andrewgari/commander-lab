# Deck personality spec (PS-1): Timmy / Johnny / Spike / Vorthos / Melvin

Status: v1, the source of truth for `analytics/personality.py` (PS-2) and the
ability-score UI (PS-D). Rule-based and deterministic: the same deck and the
same cached inputs always give the same five numbers. No LLM anywhere.

Each deck gets five scores from 0 to 100, one per MTG psychographic. The
scores are independent, not a pie: a deck can be high on two axes, and a
plain pile can be low on all five. One label, `leans_toward`, sums them up.

| Axis | The player who… | What the rules look for |
|---|---|---|
| Timmy | wants big, splashy moments | big creatures, splashy effects, creature count, ramp, little interaction |
| Johnny | wants to show off a build | combos, synergy density, off-meta picks, cards built around the commander, engines |
| Spike | wants to win | power estimate, Game Changers, tutors, fast mana, interaction, low curve, compact combos |
| Vorthos | wants the deck to tell a story | one shared creature type or set/universe, named characters, cards that reference the commander, story links |
| Melvin | enjoys the mechanics themselves | unusual keywords, rules-object manipulation (counters, phasing, copies, suspend), loops, cantrips, odd card types |

## 1. Inputs (all already on main)

The scorer is a pure function, `score_deck_personality(deck, journal=None)`
(PS-2). It takes a context with these inputs. The I/O wrapper builds the
context from Redis only. It must never make a network call: no Scryfall,
Spellbook, EDHREC or LLM.

**Cache-only reads.** `deck_combos` and `deck_inclusion` are cache-first,
not cache-only: on a miss or an expired entry they fetch
(`SpellbookClient.find_my_combos` → `_fetch`, `edhrec_inclusion.commander_page`
→ `EDHRecClient`). PS-2 must not call them as they are. It reads the raw
cache records instead, at any age (stale is fine; the score is only as old
as the cache):

* combos: `r.get(analytics.interactions.combos.decklist_key(commanders, names))`
  with `names` from `combos._deck_names(deck)`. The value is the normalized
  `find_my_combos` record; `included` is the in-deck list (`counts.in_deck`
  = its length). A missing key or bad JSON means **combos unavailable**.
* EDHREC: `r.get(analytics.edhrec_inclusion.cache_key(commander_slug(commanders)))`,
  then build the per-card rows the same way `deck_inclusion` does (pure
  code after the `commander_page` call). Missing means **EDHREC
  unavailable**.
* graph and engines: `get_deck_graph` and `get_deck_engines` are already
  Redis-only (`_NoScryfall`, empty `LLMChain`). Use them as they are.
* power: `review_deck(deck=deck)` is offline (no `external_metrics`, no
  client). Use it as is.

PS-2 can add a small `cache_only=True` path to those two helpers instead of
duplicating the reads, as long as the cache-first behaviour of the existing
routes doesn't change.

| Ctx key | Source on main | What we read |
|---|---|---|
| `deck` | `registry.list_decks` / Redis `decks` | `cards[].name`, `cards[].quantity`, `commanders`, `registry_id` |
| `cards` | `inventory_view.build_inventory(r, decks=[deck_id])` (the `/api/inventory` rows the hand run used: `instance:*` joined with `card_meta:*`). `commander_roles.load_cards(r, names)` may supply the full `type_line` where the row's `type` is only the primary type. One row per card name, chosen deterministically (below) | per card: `type` + `super_types` + `sub_types` (type line), `oracle_text`, `keywords`, `cmc`, `set` |
| `composition` | `analytics.composition.compute_composition(deck, analytics.insights.card_texts(r, deck))` | `actual.ramp`, `actual.targeted_disruption`, `actual.mass_disruption`, `cards_by_row.ramp` (regexes `RAMP_PATTERNS`, `TARGETED_PATTERNS`, `MASS_PATTERNS`) |
| `combos` | cache record at `analytics.interactions.combos.decklist_key(...)` (what `deck_combos` / Commander Spellbook writes; read cache-only, see above) | `included[]` (`len` = `counts.in_deck`), `included[].card_names` |
| `graph` | `analytics.interactions.service.get_deck_graph(r, deck)` (`analytics.interactions.graph.build_graph`) | `counts.nodes`, `counts.edges`, `edges[].source/target` |
| `engines` | `analytics.interactions.service.get_deck_engines(r, deck, graph)` (`engines.detect_engines`) | `engines[].kind` (`chain` / `loop`), `engines[].cards`, `engines[].signature` |
| `edhrec` | cache record at `analytics.edhrec_inclusion.cache_key(slug)`, turned into `per_card` rows like `analytics.edhrec_inclusion.deck_inclusion` does (what `/api/decks/{id}/edhrec` returns; read cache-only, see above) | `status`, `per_card[].name/inclusion/land` |
| `power` | `deck_review.review_deck(deck=deck).playfeel_summary.power_level_estimate` (`analytics.engine.DeckAnalysisEngine._analyze_playfeel`), same call as `analytics.journal.collect_context` | float 1.0–10.0 |
| `brackets` | `analytics.bracket_check.check_deck_brackets(deck, game_changers=<list>, combos=<cached combos or None>, oracle={name: {oracle_text}})` (BR-3, pure). `<list>` is `analytics.brackets._read_cache(r)` (the `CACHE_KEY` Redis copy) when present, else omitted so it uses the checked-in snapshot (`brackets.load_fallback`). Never `get_game_changers`, which can fetch from Scryfall | `game_changers.cards` |
| `journal` | `analytics.journal.load_journal(r, deck)` (store) passed through `analytics.journal.public` (`user_intent.resolve_intent_defaults`) | fields from `schemas/user_intent.schema.json` (see §4) |

**One row per card name.** `build_inventory` can return several rows for one
name: several copies, bound (`in_deck`) and considered (`not_owned`)
copies, and different printings. Card text, keywords and `cmc` are the same
on all of them; only `set` differs. Pick the row by this key, smallest
first:

1. bound before considered: `ownership_status == "in_deck"` first, then
   `not_owned`.
2. `set` ascending, empty last.
3. `instance_id` ascending.

That makes V1 depend only on which printings exist, never on Redis key
order. (On today's prod data no deck in the sample has two printings of one
name, so this changes no number in §5.)

Not used on purpose: `lab_tags` / `tag_vocabulary` (learned Archidekt
categories). They are free-form user labels ("Time Counters", "Board
Presence"), different in every deck, and a test draft that scored "theme
tag concentration" from them only measured how carefully a deck was
tagged. Commander roles (`commander_roles.assign_roles`) only feed V3 via
the "Doctor's companion" keyword, and are otherwise just used to know which
cards are commanders.

Definitions used below:

* **Front type**: the part of `type_line` before ` — ` and before ` // `.
  A *land* has `Land` in the front type and no `Creature`.
* **Nonland pool `NL`**: every distinct card name in `deck.cards` that is not
  a land, commanders included. `|NL|` counts names, not copies (the
  Relentless Rats exception doesn't change any rate enough to matter).
* **`NL\C`**: `NL` without the commanders.
* **`mv`**: `cmc` from `card_meta`.
* **Text**: `oracle_text` lowercased, reminder text `(...)` stripped,
  `—` turned into `-` and `’` into `'`. This is exactly
  `analytics.composition._norm`; reuse it.
* **Flavor word**: a `keywords` entry that appears in the raw oracle text as
  `"<word> —"` at the start of a line and is in neither the CR 207.2c
  ability-word list (`ABILITY_WORDS`, §6) nor `DASH_RULES_KEYWORDS` (§6:
  real rules keywords printed the same way, e.g. "Companion — …",
  "Exhaust — …"). Examples: "Allons-y!", "Bad Wolf", "The Last Centurion".
  Scryfall puts these in `keywords`, so they must not count as mechanics.
  On today's data the only rules keywords that matched the dash pattern
  were Companion (Keruga) and Exhaust; every other match was flavor.
* **`ramp(x, lo, hi)`** = `clamp((x - lo) / (hi - lo), 0, 1)`. Every signal is
  one ramp, so each sub-score is in [0, 1].
* **Undefined is 0.** When a rate or mean has an empty denominator, the
  sub-score is 0, not a fallback value run through the ramp. This matters
  most for the inverted signals: an empty `NL\C` must not give S5 a mean of
  0 and so the full 15 points. The cases: V1 type part with no creatures in
  `NL`, V1 set part with no non-ramp cards in `NL` (each part becomes 0 on
  its own, before taking the max), J3 with no EDHREC rows, J4 and S5 with an
  empty `NL\C`, and T2, V2, M2 and J5 with an empty `NL`. T5 is not a rate,
  so an empty composition (0 interaction) still gives T5 its full value.
  That's correct, and the "N cards without metadata" note (§7) flags it.

## 2. Signal table

`axis score = Σ weight × sub-score`. The weights on each axis add up to 100,
so the card-based score is already on 0–100. Thresholds (lo → hi) were
calibrated on all 39 real decks with complete card data (§5.2).

### Timmy

| # | Signal | Exact rule | Ramp lo → hi | Weight |
|---|---|---|---|---|
| T1 | Big creatures | creatures in `NL` (front type has `Creature`) with `mv >= 6` | 1 → 8 cards | 30 |
| T2 | Creature density | creatures in `NL` / `|NL|` | 0.20 → 0.45 | 15 |
| T3 | Ramp | `composition.actual.ramp` | 5 → 14 | 15 |
| T4 | Splashy effects | cards in `NL` whose text matches any `SPLASHY` regex (§6): extra combats, extra turns, doubling, "you win the game", "can't be countered", "each opponent loses N", +X/+X, "deals damage equal to", "without paying its mana cost" | 1 → 8 cards | 25 |
| T5 | Low interaction | `1 − ramp(targeted_disruption + mass_disruption, 6, 16)` | inverted | 15 |

### Johnny

| # | Signal | Exact rule | Ramp lo → hi | Weight |
|---|---|---|---|---|
| J1 | Combos in deck | `len(combos.included)` (Spellbook `included` bucket; `/api/decks/{id}/combos` shows it as `counts.in_deck`) | 0 → 4 | 25 |
| J2 | Synergy density | `graph.counts.edges / graph.counts.nodes` | 3 → 12 | 25 |
| J3 | Off-meta picks | share of `edhrec.per_card` nonland, non-commander rows with `inclusion` < 0.15 (null = not listed = 0) | 0.05 → 0.30 | 20 |
| J4 | Built around the commander | share of `NL\C` that has a graph edge to or from a commander | 0.15 → 0.50 | 15 |
| J5 | Engine coverage | share of `NL` in at least one `engines[]` with `kind == "chain"` | 0.60 → 1.00 | 15 |

`one_away` combos are NOT a signal: the median real deck has 38 of them,
so they don't separate anything.

### Spike

| # | Signal | Exact rule | Ramp lo → hi | Weight |
|---|---|---|---|---|
| S1 | Power estimate | `power_level_estimate` (it sits at 6.5 on 45 of 55 real decks, so it carries little weight) | 5.0 → 9.0 | 10 |
| S2 | Tutors | cards in `NL` matching `TUTOR` (§6): "search your library for a/an … card" where the clause doesn't mention a land | 0 → 5 | 15 |
| S3 | Fast mana | `FAST_MANA` names (§6) plus any card in `composition.cards_by_row.ramp` with `mv <= 1` | 0 → 4 | 15 |
| S4 | Interaction | `targeted_disruption + mass_disruption` | 6 → 16 | 15 |
| S5 | Efficiency | `1 − ramp(mean mv of NL\C, 2.5, 4.0)` | inverted | 15 |
| S6 | Compact combos | `combos.included` entries with `len(card_names) <= 3` | 0 → 3 | 15 |
| S7 | Game Changers | `analytics.bracket_check.check_deck_brackets(deck, combos=..., oracle=...).game_changers.cards` (BR-3; pure, Game Changers from `analytics.brackets` / the BR-1 snapshot, no network) | 0 → 4 | 15 |

S4 and T5 read the same number in opposite directions, on purpose: a
removal-heavy deck is Spikier and less Timmy. S2 keeps its own `TUTOR`
regex rather than BR-3's advisory `tutors` list (Tagger `otag:tutor mv<=3`),
because BR-3 also counts creature tutors like Rory/Amy partner fetches. S7
counts Game Changers no matter which bracket they push the deck to;
`minimum_bracket` itself is not a signal, since one Worldfire (mass land
denial) already sets it to 4.

### Vorthos

| # | Signal | Exact rule | Ramp lo → hi | Weight |
|---|---|---|---|---|
| V1 | Shared theme | `max(type_theme, set_theme)`. `type_theme = ramp(top creature subtype count / creatures in NL, 0.25, 0.60)`, with "Human" ignored (too common to mean a theme). `set_theme = ramp(top set count / |NL − ramp cards|, 0.30, 0.80)`, where ramp cards (`composition.cards_by_row.ramp`) are left out because reprinted rocks follow the precon, not the theme | per part | 35 |
| V2 | Named characters | `Legendary` cards in `NL` / `|NL|` | 0.10 → 0.40 | 25 |
| V3 | Commander-linked text | cards in `NL\C` whose text names a commander's subtype (not Human) or the commander's short name (before the comma), plural allowed; or that have the "Doctor's companion" keyword | 2 → 12 cards | 15 |
| V4 | Story links | cards in `NL` with any of: Saga or Background subtype; "partner with" keyword; a flavor word; text that names another legendary card in the deck (short name, more than 3 letters, and not also a keyword in the deck) | 1 → 8 cards | 25 |

### Melvin

| # | Signal | Exact rule | Ramp lo → hi | Weight |
|---|---|---|---|---|
| M1 | Mechanic diversity | distinct `keywords` across `NL`, minus `EVERGREEN` (§6), ability words and flavor words | 3 → 12 | 25 |
| M2 | Rules-object manipulation | cards in `NL` whose text matches any `RULES` regex (§6: time/lore/charge/… counters, removing counters, proliferate, phasing, becoming a copy, gaining suspend, time travel, casting from exile, untapping a target, exchanging control, copying a spell, planeswalking, "the stack", pairing) or that have a `RULES_KEYWORDS` keyword (suspend, vanishing, storm, cascade, foretell, …), divided by `|NL|` | 0.05 → 0.30 | 30 |
| M3 | Loops | `engines[]` with `kind == "loop"` | 0 → 4 | 10 |
| M4 | Cantrips and filtering | cards in `NL` with `mv <= 2` matching `CANTRIP` (draw a card / two / three, scry, surveil, "look at the top") | 2 → 10 | 15 |
| M5 | Odd types and rules | cards in `NL` with subtype Saga/Vehicle/Class/Case/Room/Attraction/Contraption/Spacecraft, a Planeswalker or Battle front type, or text matching `ODD_TEXT` (alt-win, phasing, planeswalk, venture, the Ring, vote, coin, dice, dungeon, monarch, initiative, day/night) | 1 → 8 | 20 |

M3 has the smallest weight because `detect_engines` finds at least one loop
in 38 of 39 real decks.

## 3. Normalisation

1. **Per signal**: each signal is one `ramp`, so it lands in [0, 1]. Counts
   that grow with deck size (M2, T2, V2, J3, J4, J5) are rates over `NL`, so
   a 60-card deck and the 143-card "Gamer" deck compare fairly.
2. **Per axis**: the weights add up to 100, so `Σ weight × sub` is 0–100
   with no extra scaling.
3. **Across axes**: the ramp bands were set so each axis has a similar
   spread over the real collection. On the 39 complete decks the final
   medians are Timmy 40, Johnny 44, Spike 47, Vorthos 29, Melvin 37. Every
   axis spans at least 54 points (Timmy 20–74, Johnny 6–95, Spike 25–90,
   Vorthos 0–96, Melvin 8–79). The leans split is johnny 9, spike 8,
   vorthos 8, timmy 6, melvin 4, balanced 4 (§5.2, §8).
4. **Rounding**: the final score is `floor(x + 0.5)`, an int 0–100. Python's
   `round()` rounds to even, so don't use it.
5. **No relative scaling**: scores don't depend on the other decks in the
   collection, so a deck's score only changes when its own cards or caches
   change.

## 4. Journal nudges (small, capped)

The journal can move each axis by at most **±10** points. It is applied after
the card score and then clamped to 0–100. With no journal (`load_journal`
returns `None`) the nudge is 0 and the evidence says so (§7). Use the stored
record passed through `analytics.journal.public`, so missing fields get the
schema defaults. A journal that was never saved has `exists: false` in
`/api/decks/{id}/journal` and counts as no journal: its defaults (combat,
midrange, power 7) must not nudge anything.

| Field (`schemas/user_intent.schema.json`) | Nudge |
|---|---|
| `target_power_level.scale` | Spike `clamp((scale − 6) × 2.5, −5, +5)` |
| `target_bracket` (1–5 or null) | Spike: 1 → −3, 2 → −1, 3 → 0, 4 → +2, 5 → +4, null → 0. Counts toward the same ±10 cap as the power nudge |
| `preferred_win_conditions.primary` | combat: Timmy +4 · commander_damage: Timmy +2 · combo: Johnny +3, Spike +2 · alternate_wincon: Melvin +3, Johnny +2 · lockout: Spike +3 |
| `preferred_win_conditions.secondary[]` | half the primary value, per entry |
| `preferred_win_conditions.disliked_or_excluded[]` | minus half the primary value, per entry |
| `archetype_preferences.primary_archetype` | aggro/voltron/ramp: Timmy +3 · combo: Johnny +3, Spike +2 · stax: Spike +3 · control: Spike +2 · tribal: Vorthos +4 · group_hug: Johnny +2 · spellslinger: Melvin +2 · others: 0 |
| `archetype_preferences.secondary_archetypes[]` | half the primary value, per entry |
| `novelties` non-empty | Johnny +2 |
| Free text: `deck_vision.description`, `novelties`, `freeform_notes`, `archetype_preferences.custom_themes[]`, `preferred_win_conditions.custom_win_conditions[]` | per axis, +1 for each distinct `LEXICON` word found (whole word, case-insensitive), at most +4 per axis |

`LEXICON` (§6): Timmy: big, huge, fatty, stompy, smash, swing, giant,
massive, overwhelm, epic, splashy, explosive, swarm. Johnny: combo, synergy,
engine, brew, jank, unusual, novel, build-around, off-meta, unique, weird.
Spike: optimize(d), efficient, competitive, cedh, tuned, consistent, fast,
tight, meta. Vorthos: theme(d), flavor/flavour, lore, story, character(s),
tribal, universe, canon, iconic. Melvin: rules, mechanic(s), trigger(s),
loop(s), layer(s), timing, puzzle, counters.

`pet_cards` doesn't nudge anything (it says which cards the player loves,
not why). `budget_constraints` and `deck_vision.intent` don't either.

Worked example, real journal on Holy Hellfire (Kaalia, `archidekt:417707`):
primary combat (Timmy +4), secondary commander_damage (Timmy +1), text has
"fatty", "swing", "swarm" (Timmy +3), so Timmy gets +8. Excluded combo
(Johnny −1.5, Spike −1), excluded lockout (Spike −1.5), novelties set
(Johnny +2), scale 8 (Spike +5), no target_bracket (0). Final: Timmy
65→73, Johnny 24→25, Spike 53→55. The cards alone already lean Timmy; the
journal makes it clearer without being able to flip a deck by itself.

## 5. Control deck: The Tenth Doctor + Rose Tyler

Deck: "The Laws of Time are Mine!!", `archidekt:5620189`, Physical. The
commanders are The Tenth Doctor (doctor) and Rose Tyler (doctors_companion),
with 95 names / 100 cards: 31 lands and 64 nonland. It has **no saved
journal** (`exists: false`), so this is a cards-only score. Inputs are a
snapshot of the live app (v0.4.31 on :8020, 2026-10-15): `/api/decks`,
`/api/inventory`, and `/api/decks/5620189/{composition,combos,engines,interactions,edhrec,review}`,
plus `check_deck_brackets` run locally on main (v0.4.34) over the same
deck and combos. Power estimate 6.5, interaction graph built
2026-10-15T08:00Z (95 nodes, 402 edges), Spellbook fetched 2026-10-12,
Game Changers from the BR-1 snapshot.

### 5.1 Result

| Axis | Score |
|---|---|
| Timmy | **51** |
| Johnny | **31** |
| Spike | **39** |
| Vorthos | **76** |
| Melvin | **79** |

`leans_toward: "melvin"`, `runner_up: "vorthos"` (3 points behind).

That fits the deck: it's built on time counters, suspend, vanishing and
time travel, the mechanic itself is the point (Melvin), and it's an almost
pure Doctor Who deck full of companions (Vorthos). It has no combos and runs
at power 6.5 with 2 Game Changers (low Johnny, low Spike), with a few big suspend creatures
(middling Timmy).

Per signal (sub-score → points):

| Signal | Value | Sub | Pts |
|---|---|---|---|
| T1 big creatures | 5: Judoon Enforcers, Star Whale, Dinosaurs on a Spaceship, Atraxi Warden, Absolute Virtue | 0.571 | 17.14 |
| T2 creature density | 22 / 64 | 0.575 | 8.62 |
| T3 ramp | 9 | 0.444 | 6.67 |
| T4 splashy | 5: Regenerations Restored (extra turn), Barren Glory (win), The War Doctor, Kate Stewart, Absolute Virtue | 0.571 | 14.29 |
| T5 low interaction | 13 (10 targeted + 3 mass) | 0.300 | 4.50 |
| **Timmy** | | | **51.22** |
| J1 combos in deck | 0 | 0 | 0 |
| J2 synergy density | 402 / 95 = 4.23 | 0.137 | 3.42 |
| J3 off-meta | 8 / 62 below 15% (Gandalf of the Secret Fire, An Unearthly Child, Barren Glory, Jace the Mind Sculptor, Mystical Tutor, Chromatic Lantern, Worldfire, Absolute Virtue) | 0.316 | 6.32 |
| J4 around the commander | 0.44 of non-commander nonland touches a commander in the graph | 0.816 | 12.24 |
| J5 engine coverage | 0.83 | 0.570 | 8.55 |
| **Johnny** | | | **30.53** |
| S1 power | 6.5 | 0.375 | 3.75 |
| S2 tutors | 1: Mystical Tutor | 0.200 | 3.00 |
| S3 fast mana | 2: Sol Ring, Sol Talisman | 0.500 | 7.50 |
| S4 interaction | 13 | 0.700 | 10.50 |
| S5 efficiency | mean mv 3.29 | 0.473 | 7.10 |
| S6 compact combos | 0 | 0 | 0 |
| S7 Game Changers | 2: Farewell, Mystical Tutor | 0.500 | 7.50 |
| **Spike** | | | **39.35** |
| V1 shared theme | set WHO: 41 / 55 non-ramp nonland = 0.75 (type: Alien 5/22 = 0.23) | 0.891 | 31.18 |
| V2 named characters | 16 legendary / 64 | 0.500 | 12.50 |
| V3 commander-linked | 7: Donna Noble, Amy Pond, An Unearthly Child, The War Doctor, TARDIS, The Eleventh Doctor, K-9 Mark I | 0.500 | 7.50 |
| V4 story links | 11: Rory Williams, Amy Pond (partner with), The Parting of the Ways, An Unearthly Child (Saga), Wilfred Mott, Time Beetle, The Eleventh Doctor, Sibylline Soothsayer, K-9, The Tenth Doctor, Rose Tyler (flavor words) | 1.000 | 25.00 |
| **Vorthos** | | | **76.18** |
| M1 mechanics | 9: buyback, doctor's companion, escape, skulk, soulbond, storm, suspend, time travel, vanishing | 0.667 | 16.67 |
| M2 rules objects | 44 / 64 = 0.69 | 1.000 | 30.00 |
| M3 loops | 4 | 1.000 | 10.00 |
| M4 cantrips | 3: Regenerations Restored, Wibbly-wobbly Timey-wimey, Brainstorm | 0.125 | 1.88 |
| M5 odd types | 8: Out of Time, The Parting of the Ways, An Unearthly Child, Barren Glory, Jace, The War Doctor, TARDIS, The Moment | 1.000 | 20.00 |
| **Melvin** | | | **78.54** |

### 5.2 Rules fixed during the hand run

The first draft gave the control deck Timmy 69 / Johnny 52 / Spike 37 /
Vorthos 58 / Melvin 71, with Timmy as the runner-up. That's wrong for a
deck with 9 rocks and 13 removal spells. Fixes made before finishing:

1. **T1 counted every mv≥6 card.** Farewell, Worldfire, Reality Strobe and
   Inspiring Refrain are sweepers and suspend spells, not Timmy fatties. T1
   now counts creatures only. T4 dropped "destroy/exile all" for the same
   reason (that's control).
2. **V1 used shared creature type only.** The Doctor deck has no tribe
   (Human is ignored; the top type is Alien at 23%), so Vorthos scored 0 on
   its strongest signal. V1 is now the max of creature-type theme and
   set/universe theme. Ramp rocks are left out of the set share, because
   reprinted Sol Ring and Talismans follow the precon, not the theme.
3. **V4 counted the keyword "Suspend" as a character reference.** The card
   *Suspend* is in the deck, so every suspend card "named" it. Name matches
   now only count legendary cards, and a name that is also a keyword in the
   deck is skipped.
4. **Flavor words counted as mechanics** ("Allons-y!", "Bad Wolf", "I. AM.
   TALKING!" are in Scryfall `keywords`). They now go to Vorthos V4 and are
   removed from Melvin M1.
5. **J1 credited one-away combos** (0.05 each, capped at 1 combo). 36 of
   the 39 complete decks have 10+ one-away combos (median 38), so it was
   nearly a constant. Dropped.

Then the rules were run over the whole collection (55 decks; the 16 with
more than 5 cards missing from inventory were left out, leaving 39). Three
problems showed up and were fixed: Johnny's mean was 55 (J2/J5 bands too
low), Timmy's was 36 (T1/T4 bands too high), and M3 loops saturated on 29
decks (weight cut to 10, M5 raised to 20). A fourth fix came after main
gained the bracket work (BR-1..3): the power estimate is 6.5 on 45 of 55
decks, so S1 barely separated anything, and Spike only spanned 32–81. S1
went from 25 to 10 and S7 (Game Changers, from `check_deck_brackets`) was
added at 15. For the control deck that moves Spike from 37 to 39 (Farewell
and Mystical Tutor are Game Changers) and nothing else. After that, the
other real decks read as expected, which is the check that the rules
aren't tuned to the control deck alone:

| Deck | T | J | S | V | M | Leans |
|---|---|---|---|---|---|---|
| The Laws of Time are Mine!! (Tenth Doctor/Rose), control | 51 | 31 | 39 | 76 | 79 | melvin |
| My Big, Fat, Dead Wedding (Edgar Markov, vampires) | 44 | 24 | 46 | 79 | 21 | vorthos |
| Everything is permitted. (Ezio, Assassin's Creed) | 36 | 6 | 29 | 96 | 43 | vorthos |
| Batteries Not Included (Urza, artifacts) | 37 | 65 | 30 | 22 | 10 | johnny |
| The world is slowly ebbing away... (Celes, 4 Game Changers) | 34 | 45 | 90 | 4 | 39 | spike |
| The Word is 'Gamer' (Unfinity attractions) | 33 | 49 | 58 | 10 | 73 | melvin |
| Holy Hellfire (Kaalia, with journal) | 73 | 25 | 55 | 33 | 8 | timmy |

PS-2 must reproduce the control row exactly from the same inputs. Small
drift is fine when the caches move (a new graph build or a Spellbook
refresh), but the lean must stay melvin with vorthos close behind, unless
the decklist itself changed.

## 6. Constant lists

The full lists live in code (`analytics/personality.py`). These are the
contents the hand run used; PS-2 copies them as-is.

* `SPLASHY`: `additional combat`, `extra combat`, `take an extra turn`,
  `\bdouble (?:the|its|their|that)`, `twice that many`, `you win the game`,
  `can't be countered`, `each opponent loses (?:\d+|x)`, `\+x/\+x`,
  `deals damage equal to`, `without paying (?:its|their) mana cost`.
* `TUTOR`: `search your library for (?:a|an|up to \w+) (?![^.]*\bland\b)[^.]*card`.
* `FAST_MANA`: Mana Crypt, Sol Ring, Mana Vault, Mox Diamond, Chrome Mox,
  Mox Opal, Lotus Petal, Jeweled Lotus, Lion's Eye Diamond, Grim Monolith,
  Dark Ritual, Cabal Ritual (the `analytics.engine` fast-mana names plus
  rituals).
* `EVERGREEN` (not counted in M1): flying, trample, vigilance, haste, first
  strike, double strike, deathtouch, lifelink, reach, menace, hexproof,
  indestructible, ward, defender, flash, scry, equip, enchant, fight, mill,
  surveil, investigate, partner, partner with, crew, protection, transform,
  cycling, kicker, flashback.
* `ABILITY_WORDS` (CR 207.2c): adamant, addendum, alliance, battalion,
  bloodrush, celebration, channel, chroma, cohort, constellation, converge,
  council's dilemma, coven, delirium, descend 4, descend 8, domain, eerie,
  eminence, enrage, fateful hour, fathomless descent, ferocious, flurry,
  formidable, grandeur, hellbent, heroic, imprint, inspired, join forces,
  kinship, landfall, lieutenant, magecraft, metalcraft, morbid, pack
  tactics, paradox, parley, radiance, raid, rally, revolt, secret council,
  spell mastery, strive, survival, sweep, tempting offer, threshold,
  undergrowth, valiant, void, will of the council.
* `DASH_RULES_KEYWORDS` (keyword abilities printed as "Keyword — cost", so
  never flavor words): companion, exhaust, doctor's companion. Add new
  keywords here when a set prints one that way.
* `RULES`: `\b(?:time|lore|fade|age|charge|oil|verse|quest|level|loyalty) counters?\b`,
  `\bremove (?:a|an|one|x|that many|\w+) [^.]*counters?\b`, `\bproliferate\b`,
  `\bphases? (?:out|in)\b`,
  `\b(?:enter|enters) as a copy\b|\bbecomes? a copy\b|\bcreate[^.]* copy of\b`,
  `\bgains? suspend\b`, `\btime travel\b`,
  `\bcast [^.]*from exile\b|\bplay [^.]*from exile\b`, `\buntap (?:another )?target\b`,
  `\bexchange control\b`, `\bcopy target (?:spell|ability|instant|sorcery)\b`,
  `\bplaneswalk\b`, `\bthe stack\b`, `\bpair\b`.
* `RULES_KEYWORDS`: suspend, vanishing, fading, time travel, storm, cascade,
  foretell, plot, adventure, soulbond, buyback, rebound, split second,
  mutate, ninjutsu, morph, disguise, cloak, manifest, dredge, phasing,
  companion, doctor's companion.
* `CANTRIP`: `\bdraw a card\b|\bdraw (?:two|three) cards\b|\bscry\b|\blook at the top\b|\bsurveil\b`.
* `ODD_SUBTYPES`: Saga, Vehicle, Class, Case, Room, Attraction, Contraption,
  Spacecraft. `ODD_TEXT`: `you win the game`, `\bphases? out\b`,
  `\bplaneswalk\b`, `venture into`, `the ring tempts you`, `\bvote\b`,
  `\bcoin\b`, `roll (?:a|\w+) d\d+|\broll a die\b`, `\bdungeon\b`,
  `\bmonarch\b`, `\binitiative\b`, `\bday\b.*\bnight\b`.
* `LEXICON`: §4.

Changing a list, weight or band is a spec change: update this doc and the
control row in §5 in the same PR, and bump `PERSONALITY_VERSION` (starts at
1) so stored results can be told apart.

## 7. Output shape

```python
class AxisEvidence(BaseModel):
    signal: str              # "T1", "J3", ..., or "journal"
    label: str               # "Big creatures"
    value: float | int | str # the raw measure, e.g. 5, 0.43, "41/55 WHO"
    sub_score: float         # 0..1 after the ramp (journal rows: 0)
    points: float            # weight * sub_score, or the journal nudge (+/-)
    cards: list[str] = []    # exact card names behind the signal, sorted
    fields: list[str] = []   # journal or cache fields read, e.g. "preferred_win_conditions.primary=combat"

class AxisScore(BaseModel):
    score: int               # 0..100, final
    card_score: float        # before the journal nudge
    journal_nudge: float     # already capped to [-10, +10]
    evidence: list[AxisEvidence]

class PersonalityScores(BaseModel):
    version: int = 1
    timmy: AxisScore
    johnny: AxisScore
    spike: AxisScore
    vorthos: AxisScore
    melvin: AxisScore
    leans_toward: Literal["timmy", "johnny", "spike", "vorthos", "melvin", "balanced"]
    runner_up: Optional[str] = None   # second axis when within 5 points of the top; None otherwise
    journal_used: bool
    notes: list[str] = []             # degraded inputs, see below
```

Evidence includes every signal, even ones worth 0 points, so the UI can
show what's missing ("0 combos in deck"). Card lists are sorted by name.
They can be long (M2 lists 44 cards for the control deck); the UI truncates
them, the model doesn't.

**Degraded inputs** never raise. The signal scores 0 and gets a note:

| Missing | Effect | Note text |
|---|---|---|
| journal (`None`, or `exists: false`) | nudge 0 on every axis, one evidence row per axis with `signal: "journal"`, `points: 0` | `"no journal: cards-only score"` |
| `combos` (Spellbook error / empty cache) | J1 and S6 score 0 | `"combos unavailable"` |
| `graph` empty or `counts.untagged > 25%` of nodes | J2, J4, J5, M3 score 0 | `"interaction graph missing or stale"` |
| `edhrec.status != "ok"` | J3 scores 0 | `"EDHREC unavailable"` |
| `power` failed | S1 scores 0 | `"power estimate unavailable"` |
| `check_deck_brackets` raised | S7 scores 0 | `"bracket check unavailable"` |
| card metadata missing for more than 5 cards | score anyway | `"N cards without metadata"` (16 of 55 real decks hit this today, mostly testing/digital decks whose cards aren't in inventory) |

The scores stay comparable because a missing source scores 0 and isn't
reweighted. That's honest (the evidence for that signal is missing), and
the note tells the UI to flag it.

## 8. The `leans_toward` rule

Rank the five final scores, high to low. Ties break in the fixed order
timmy, johnny, spike, vorthos, melvin.

* **balanced** when the top score is below 35 (nothing stands out), or when
  `top − third < 8` (three or more axes are bunched together).
* Otherwise `leans_toward` is the top axis, and `runner_up` is the second
  axis when `top − second <= 5`. The UI can then say "leans Melvin, close
  second Vorthos". Two axes tied at the top are not "balanced": the tie
  order picks one and the other is `runner_up`.

On the 39 real decks this gives 4 balanced and 5 with a runner-up. The
balanced decks: "Bird UP!!!!" (Choco, 39/70/65/63/31: Johnny, Spike and
Vorthos within 7), "Uncharted: Lara's Legacy" (50/56/52/29/36: Timmy,
Johnny and Spike within 6), "Where the Wild Things Will Be" (24/34/34/4/9:
top below 35) and "For Cheese and Country" (Mabel, 24/19/38/38/32: Spike,
Vorthos and Melvin within 6).

## 9. Non-goals

No UI, no LLM, and no per-user calibration. The bands are fixed numbers in
code, not percentiles of the current collection. Storage, the API and the
player profile were added by PS-3 (§10).

## 10. Storage, API and player profile (PS-3)

### 10.1 Storage

`analytics/personality_store.py`. The scorer runs in the worker process
(`python -m analytics.bracket_worker`, compose service `bracket-worker`):
every tick runs the bracket pass, then `personality_store.recompute_all`.
The worker is woken by every sync path and every Deck Journal save, and
ticks every `BRACKET_EVAL_INTERVAL_S` anyway, so scores follow a sync or a
journal edit within seconds. Nothing scores in the web request path.

* `personality:deck:{registry_id}`: `{registry_id, deck_name, status: ok|error,
  version, input_hash, result_hash, computed_at, scores, error, error_at, duration_ms}`.
  `scores` is `PersonalityScores.model_dump()` with `computed_at` filled in.
* `personality:pending`: registry_ids requested for a rescore (`POST`).
* `personality:heartbeat`: last pass summary.

**Input hash** (sha256): `PERSONALITY_VERSION` + decklist (commanders, card
names and quantities) + Deck Journal content without `_journal` bookkeeping
+ the stamps of every cache the scorer reads: interaction graph
`built_at`, Spellbook `fetched_at`, EDHREC `fetched_at`, Game Changers
`fetched_at`. Same hash as the stored record: skipped. So an idle
collection costs one hash per deck per tick; a new graph build, a
Spellbook refresh or a version bump rescores.

**Failure isolation.** A deck whose inputs can't be read or whose scorer
raises gets `status: error` with the reason; its last good `scores` stay;
the failed hash is recorded, so it isn't retried every tick, only when
something changes or it's requested. The pass moves on. A failing
personality pass never stops the bracket tick.

### 10.2 API

`GET /api/decks/{id}/personality` (`id` = id, registry_id or name):

| Status | Body |
|---|---|
| 200 | `{success, registry_id, deck_name, status: ok|error, computed_at, stale, pending, error, error_at, personality}` (`status: error` with 200 = last pass failed, older scores shown) |
| 404 | `status: deck_not_found` or `status: not_scored` (the worker hasn't got to it) |
| 500 | `status: error`: scoring failed and there are no earlier scores |
| 503 | `status: unavailable`: Redis trouble |

`POST /api/decks/{id}/personality` queues a rescore (202).
`AxisEvidence.weight` is the signal's weight (`points / weight` = share of
the max); journal rows have `weight: null`.

`GET /api/profile/personality?scope=physical|all` (default physical):
`{success, profile}` where `profile` is `PlayerProfile` plus `not_scored`
(decks in scope with no record) and `scope_counts {physical, all}`.

### 10.3 Player profile rules

`analytics.personality_profile.build_player_profile(all_scores, *, failed,
scope, decks_total)`. Pure: same stored scores, same profile, in any
order. Failed decks (no scores at all) are listed in `failed[]` and never
averaged.

* `average`: per axis, the mean of the decks' final scores, half up.
* `low_data`: fewer than 3 scored decks. Averages still shown, headline
  "Early read: X, so far.", `trends: []`, `decks_to_unlock_trends = 3 − n`.
* Headline from the averages ranked high to low (ties in axis order):
  top − third < 3 → `even` (`dominant: "balanced"`, "You play a bit of
  everything."); top − second ≤ 5 → `close` (`dominant_runner_up` set);
  else `clear`. No decks → `none`.
* `why` reconciles average vs count ("By count, Vorthos and Melvin tie…").
* `groups` per deck, from the V1 evidence: `tribal` when the top creature
  subtype is ≥ 50% of creatures; `universe` when the top set is ≥ 60% of
  non-ramp nonland cards.

Trends, at most 8, in this order; none may name the same deck + axis as an
earlier one (a later one is dropped or moves to the next candidate). Text is
the sentence after the number; `<b>` only around axis and deck names, deck
names quoted and cut at 34 characters on a word boundary.

| Kind | Rule | Action |
|---|---|---|
| `most_common_lean` | the axis (or tied axes) most decks lean; "3 / 6 decks lean Spike" | filter axis |
| `average_vs_count` | the highest-average axis isn't the most common lean | sort axis |
| `none` / `rarest_lean` | axes no deck leans ("0 / 6 decks lean Johnny or Melvin"); if every axis has one, the rarest lean when it's below the top | sort / filter axis |
| `balanced_count` | some decks are balanced | filter balanced |
| `outlier` (≤ 2, different decks) | deck score ≥ 70 and ≥ 30 above the axis average, biggest gap first | open deck |
| `group` | ≥ 2 tribal (or universe) decks and ≥ 1 other; the axis whose group average beats the rest by most, if by ≥ 10 ("your Vorthos is highest in tribal decks") | filter group |
| `spread` | widest axis range ≥ 40 whose extreme decks aren't already named for that axis | sort axis |
| `journal_coverage` | always: decks with a journal | filter no_journal |
