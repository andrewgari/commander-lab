# Commander Brackets spec (BR-1)

Commander Lab already shows a 1-10 power level (Commander Salt / our own
analysis). This chain adds the official WotC **Commander Brackets** (1-5) next
to it. This document is the human-readable source of truth. The machine-
readable one is `analytics/brackets.py` (`BRACKETS`, `CONSTRAINTS`), and
`GET /api/brackets` serves both the table and the live Game Changers list.

Rules checked against the official articles on **2026-10-15**
(`brackets.RULES_AS_OF`). The system is still labelled beta by WotC; the last
bracket-level change was 2025-10-21, the last Game Changers change 2026-02-09.

## Sources

Official (WotC / Commander Format Panel):

- Introducing Commander Brackets Beta (2025-02-11): the five brackets, the
  original deck-building limits, the mass land denial definition, the first
  40 Game Changers.
  https://magic.wizards.com/en/news/announcements/introducing-commander-brackets-beta
- Beta update 2025-04-22: +18 Game Changers, 5 unbans onto the list, Trouble in
  Pairs and Trinisphere removed. No structural change.
  https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-april-22-2025
- Beta update 2025-10-21: per-bracket expectations and "turns before you win or
  lose", tutor limits removed, Bracket 2 decoupled from precons, 10 Game
  Changers removed (Expropriate, Jin-Gitaxias, Sway of the Stars, Vorinclex,
  Kinnan, Urza, Winota, Yuriko, Deflecting Swat, Food Chain).
  https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-october-21-2025
- Beta update 2026-02-09: Farewell and Biorhythm added (53 cards). No bracket
  changes; "cool it on bracket-level changes".
  https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-february-9-2026
- Format page (current summary: 1-2 no Game Changers, 3 up to three, 4-5 unlimited).
  https://magic.wizards.com/en/formats/commander
- The 2026-10-12 Banned & Restricted announcement only touched Competitive
  Brawl (Arena); nothing for paper Commander or Game Changers.

Game Changers list, live: Scryfall `is:gamechanger`
https://scryfall.com/search?q=is%3Agamechanger (53 cards on 2026-10-15,
matching the 2026-02-09 official list).

Community notions we reuse later (not authoritative):

- Commander Spellbook bracket tags on combos
  (https://commanderspellbook.com/syntax-guide/). Each combo gets a tag; their
  search maps it to a bracket: Ruthless -> 4 ("relevant two-card combo that is
  probably very fast, or infinite turns, mass land denial, infinite control of
  opponents' turns, or 4+ Game Changers"), Spicy -> 3, Powerful -> 3 ("a game
  changer or a slow but relevant two-card combo"), Oddball -> 2, Core
  (formerly "Precon Appropriate") -> 2 ("contains an extra turn card but no
  extra turn result, or a two-card combo too fast for bracket 1"),
  Exhibition (formerly "Casual") -> 1, Banned -> not legal. They also
  run an estimate-bracket API (https://backend.commanderspellbook.com/estimate-bracket).
  We already store the tag per combo as `bracket_tag`
  (`analytics/interactions/combos.py`).
- Commander Salt (https://commandersalt.com/) gives a decimal `bracketRating`
  (e.g. 5.9 for cEDH lists) next to its power level and salt. We already
  store it as `provider_metrics.bracket_rating`
  (`analytics/providers/commandersalt.py`). Its algorithm is proprietary
  (https://commandersalt.com/resources/commandersalt_algorithm.pdf), so we
  treat it as a second opinion only.

## The rule: hard vs advisory

- **Hard constraints are checked deterministically** from the decklist and
  fixed data (the Game Changers list, our mass-land-denial and extra-turn card
  lists, Commander Spellbook combos). Each one gives a *bracket floor*: the
  lowest bracket the deck can be in. The deck's computed minimum bracket is
  the highest floor. Same input, same answer, and every floor names the cards
  that caused it.
- **Fuzzy constraints are advisory only**: tutors, turn expectations,
  intent / "feel", Commander Salt's rating, Spellbook's estimate. They're shown
  as notes and never move the computed bracket. A deck's declared bracket (the
  user's choice) can be higher than the computed minimum, never lower without
  a visible warning.

WotC is explicit that intent comes first and that tools only find the
*lowest possible* bracket ("bracket up" if the deck plays stronger). Our
output says "minimum bracket N", never "this deck is bracket N".

## The brackets

"GC" = Game Changers. The commander(s) count toward the GC total.

| # | Name | GC | Mass land denial | Extra turns | Two-card combos | Expect at least |
|---|------|----|------------------|-------------|-----------------|-----------------|
| 1 | Exhibition | 0 | no | no | no | 9 turns |
| 2 | Core | 0 | no | few, never chained/looped | no | 8 turns |
| 3 | Upgraded | up to 3 | no | few, never chained/looped | no early ones (late game only) | 6 turns |
| 4 | Optimized | any | allowed | allowed | allowed | 4 turns |
| 5 | cEDH | any | allowed | allowed | allowed | any turn |

Tutors: no bracket has a tutor limit since 2025-10-21. The original rules
said "sparse" for 1-2; WotC removed it and relies on the Game Changers list
to catch the efficient tutors. We may still list tutors, advisory only.

### 1. Exhibition

Intent: theme over power, show off something unusual. Card legality and
commander choice can flex by pod agreement (Un-cards, a thematic Game Changer
like Bolas's Citadel in a Bolas deck). Win conditions are thematic or
substandard. Hard: 0 GC, no mass land denial, no extra-turn cards, no
intentional two-card infinite combos. Because Bracket 1 is about theme, a
deck that passes the hard checks is "minimum 1" but we never *suggest*
Exhibition from contents alone; that's the user's call.

### 2. Core

Intent: unoptimized and straightforward, social, with telegraphed and
disruptable win conditions. No longer "the average precon". Hard: 0 GC, no
mass land denial, no intentional two-card infinite combos, extra-turn cards
only in low quantity and never chained or looped.

### 3. Upgraded

Intent: powered up, strong synergy and card quality, can disrupt; GCs are
value engines and game-enders; can win in one big turn from accrued
resources. Hard: up to 3 GC, no mass land denial, extra turns not chained or
looped, no two-card combos that tend to come up in the first ~6 turns
(WotC: "if a combo could frequently come up, it's not the best fit").

### 4. Optimized

Intent: lethal, consistent, fast; GCs are fast mana, snowballing engines,
free disruption, tutors; but not built around the cEDH metagame. Hard: none
beyond the banned list.

### 5. cEDH

Intent: built for the cEDH metagame; winning over self-expression. Hard: none
beyond the banned list. 4 vs 5 is purely intent, so we never compute 5; it's
only ever declared.

### Definitions used by the hard checks

- **Mass land denial** (WotC): cards that "regularly destroy, exile, and
  bounce other lands, keep lands tapped, or change what mana is produced by
  four or more lands per player without replacing them". Examples: Armageddon,
  Ruination, Sunder, Winter Orb, Blood Moon. WotC gives no full list; BR-2
  ships ours as data.
- **Extra turns**: any card that gives an extra turn. "Chained / looped" means
  the deck can take extra turns repeatedly (a recursion loop, or enough
  extra-turn cards plus copy/recursion to take them in succession).
- **Two-card combo**: two cards that together end the game or loop
  infinitely (game-enders, lock-outs, infinites), including the commander.
  Source: Commander Spellbook combos with exactly two cards. "Early" for
  Bracket 3 = Spellbook tag Ruthless (cheap/fast); a two-card Powerful/Spicy
  combo is treated as late-game.
- **Intentional**: we can't see intent. A hard check flags what's in the
  list; the user can mark a hit as "not intentional" and that's an advisory
  override, shown as such.

## Data and code (BR-1)

- `analytics/brackets.py`
  - `BRACKETS`: tuple of frozen `Bracket` rows (number, name, intent,
    expectations, `max_game_changers` (None = unlimited), `mass_land_denial`,
    `extra_turns`, `two_card_combos` (values from `CONSTRAINT_VALUES`:
    `not_allowed` / `no_chains` / `late_game_only` / `allowed`), `tutors`
    text, `min_turns`, sources).
  - `CONSTRAINTS`: each constraint, its kind (`hard` / `advisory`) and which
    `Bracket` field holds its value.
  - helpers: `get_bracket(3 | "3" | "Upgraded")`, `game_changers_allowed(b, n)`,
    `min_bracket_for_game_changers(n)`, `find_game_changers(deck, gcs)`,
    `is_game_changer(name, gcs)`, `brackets_payload()`.
  - `get_game_changers(r)`: Redis `brackets:game_changers` (TTL 24h) ->
    Scryfall `is:gamechanger` via the shared `ScryfallClient` -> checked-in
    snapshot `analytics/data/game_changers.json`. The snapshot is not cached,
    so the next call retries Scryfall. `source` on the result says which one
    answered (`cache` / `scryfall` / `fallback`).
  - Names match on the lower-cased front face (`Tergrid, God of Fright`
    matches `Tergrid, God of Fright // Tergrid's Lantern`).
- `GET /api/brackets[?refresh=true]`: table + constraints + sources +
  `game_changers {count, source, fetched_at, cards}`.
- `scripts/refresh_game_changers.py [--check]`: rewrites the snapshot from
  Scryfall (`--check` exits 1 if it's out of date).

## Deck check (BR-3)

`analytics/bracket_check.py`: `check_deck_brackets(deck, game_changers=, combos=, oracle=)
-> BracketFindings` (pydantic). Pure and deterministic, no LLM, no network.

| Rule | Data | Floor |
|------|------|-------|
| Game Changers | `game_changers` arg (live list from `get_game_changers`), default the BR-1 snapshot; commanders count | `min_bracket_for_game_changers(n)`: 0 -> 1, 1-3 -> 3, 4+ -> 4 |
| Mass land denial | `analytics/data/bracket_cards.json` `mass_land_denial` (Scryfall `otag:mass-land-denial`) + WotC's examples pinned; oracle-text fallback "destroy/sacrifice/exile all lands" | any -> 4 |
| Extra turns | `extra_turns` (`otag:extra-turn`); oracle fallback "take an extra turn" | any card -> 2; an in-deck Spellbook combo producing infinite turns (or an extra-turn card + something infinite) -> 4 |
| Two-card combos | Commander Spellbook in-deck combos (`combos.deck_combos` / `find_my_combos`) with <= 2 cards and no template requirements | any -> 3; tag Ruthless (R) or Banned (B) -> 4 |
| Tutors (advisory) | `tutors` (`otag:tutor -otag:tutor-land -otag:tutor-seek mv<=3`) | never |

The three card lists use the same Tagger queries Commander Spellbook uses for
its own `massLandDenial` / `extraTurn` / `tutor` card flags. Refresh with
`python scripts/refresh_bracket_cards.py [--check]`; curated overrides are the
`*_INCLUDE` / `*_EXCLUDE` tuples in `bracket_check.py`.

`minimum_bracket` = highest floor (never 5). `floors` lists each rule above 1
with its cards. If `combos` is None the combo rule says `checked: false` and a
note warns the minimum may be higher. 3+ extra-turn cards with no loop add a
"check by hand" note but don't move the floor (chaining without a combo needs
judgement). `check_deck_brackets_live(r, deck)` gathers the live inputs
(Game Changers, cached Spellbook combos, the tagger's cached oracle text).

## The BR chain

| Card | Scope |
|------|-------|
| BR-1 | This spec, `analytics/brackets.py` table + helpers, Game Changers from Scryfall with cache and snapshot, `/api/brackets`. |
| BR-2 | Hard-check data: mass-land-denial and extra-turn card lists (Scryfall queries + curated overrides, snapshot like BR-1), with "chained/looped" detection rules. |
| BR-3 | Shipped as the deterministic deck check (`analytics/bracket_check.py`): Game Changers, mass land denial and extra-turn lists (Tagger snapshot + overrides), Spellbook two-card combos by tag, tutors (advisory), `minimum_bracket` with per-floor cards. Covers what BR-2/BR-4 described for data and evaluation. |
| BR-4 | Deterministic evaluator: deck -> per-constraint floors with the cards that caused them, computed minimum bracket. Pure function over the BR-1..3 data, fully unit tested. |
| BR-5 | Advisory signals next to it: tutors, Commander Salt `bracket_rating`, Spellbook estimate, turn expectation vs the deck's speed; plus the user's declared bracket per deck (stored, with "not intentional" overrides). Advisory never changes the BR-4 number. |
| BR-6 | API + deck page / decks list: bracket badge next to the 1-10 power level, with the floors and advisories on hover/expand. |
| BR-7 | "To reach bracket N, cut these" suggestions and Tuner integration (a target bracket constrains suggested adds). |
| BR-8 | Calibration and upkeep: compare BR-4 with Commander Salt / Spellbook across our decks, document gaps, schedule the snapshot refresh and an alert when the official list changes. |

Non-goals for BR-1: no UI, no journal changes, no deck evaluation.

## Target bracket in the Deck Journal (BR-2)

Each deck's journal has an optional top-level `target_bracket`: an integer
1-5, or `null` (unset, the default), which means no bracket checks. It is
stored beside `target_power_level` (1-10) and never derived from it; the two
are independent inputs. Validation is in `user_intent.validate_intent`, and
the field is declared in `schemas/user_intent.schema.json`. The check-in row
`bracket` shows `Bracket N · Name` from `BRACKETS`. See `docs/DECK_JOURNAL.md`
for the API.
