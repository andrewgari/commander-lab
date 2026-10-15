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
| Extra turns | `extra_turns` (`otag:extra-turn`); oracle fallback "take an extra turn" | any card -> 2; an in-deck Spellbook combo whose result is explicitly "Infinite turns" and has no template requirement -> 4 (an extra-turn card plus some other infinite isn't a loop; a template-bearing "Infinite turns" combo is only a note, since find-my-combos doesn't verify templates) |
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

## Bracket evaluator (BR-4)

`analytics/bracket_eval.py`: `evaluate_bracket(deck, journal, metadata) ->
BracketEvaluation` (pydantic). Pure and deterministic, no LLM, no network.
`evaluate_bracket_live(r, deck)` gathers the inputs for one deck (BR-3 live
findings, stored journal, engine power estimate, prices, EDHREC, Commander
Salt if it has ingested the deck); any input that fails is left out.

Inputs. `journal`: `target_bracket`, `target_power_level.scale`, `pet_cards`,
`novelties`, `deck_vision.description` (None = no journal). `metadata` (all
optional): `findings` (BR-3; computed from `game_changers` / `combos` /
`oracle` if absent), `commandersalt` (`MetaScores` or `{power, salt,
bracket_rating}`), `power_estimate` (engine playfeel estimate), `edhrec`
(`deck_inclusion()`), `price`, `tags`.

### Signal -> bracket

| Signal | Source | Effect on estimated bracket |
|--------|--------|-----------------------------|
| BR-3 hard floors (Game Changers, MLD, extra turns, two-card combos) | BR-3 data | `minimum_bracket` = highest floor; the estimate never goes below it |
| Minimum 1 | spec ("Exhibition is the user's call") | shown as 2 unless the journal targets 1 |
| Commander Salt power | Commander Salt | `bracket_for_power(power)`; raises the estimate if higher (never lowers) |
| Our engine power estimate | `analytics/engine.py` | none (the heuristic starts every deck at 6.0) |
| Tutors, Commander Salt `bracket_rating` and salt, EDHREC overlap, price, tags | various | none: context in the explanation only |

`estimated_bracket` is capped at 4: 4 vs 5 is intent, so 5 is never estimated.

Power bands (`POWER_BANDS`, inclusive, community convention): 1: 1-4, 2: 4-6,
3: 6-8, 4: 8-10, 5: 9-10. `bracket_for_power` = lowest of 1-4 whose band
reaches the power.

### Signal -> power level (1-10)

1. Commander Salt power if present (`power_source: commandersalt`).
2. Else our engine estimate, clamped into the estimated bracket's band
   (`our_estimate`).
3. Else the midpoint of the estimated bracket's band (`bracket_band`).

`power_delta` = estimated - journal target power; `power_aligned` =
`|delta| <= engine.POWER_ALIGN_TOLERANCE` (1.5, the same constant
`engine.power_aligned` uses).

### Conflicts `{severity, rule, message, cards}`

Only when `target_bracket` is set (otherwise `no_target: true`, `aligned:
null`, no bracket conflicts), except `power_target`, which needs only a target
power.

| Rule | Severity | When |
|------|----------|------|
| `game_changers` / `mass_land_denial` / `extra_turns` / `two_card_combos` | hard | the BR-3 floor for that rule is above the target |
| `pet_card` | advisory | a journal pet card is one of the cards behind a hard conflict; worded as "your call" |
| `journal_card` | advisory | same, for a card named in novelties / deck vision |
| `bracket_mismatch` | advisory | `estimated_bracket != target_bracket`: "plays above target" / "plays below target" (target 5: "we never estimate Bracket 5") |
| `power_band` | advisory | Commander Salt power is outside the target bracket's band |
| `power_target` | advisory | `|estimated power - target power| > POWER_ALIGN_TOLERANCE` |

`aligned` = estimated == target and no hard conflict.

### Explanation

`explanation.signals`: ordered list of `{signal, value, effect, source}`:
hard floors (highest first), the hard rules with no floor, tutors, the
Exhibition rule, Commander Salt power, our power estimate (or the band
fallback), then context (Commander Salt bracket rating and salt, EDHREC
overlap, price, tags). `explanation.summary` is one plain paragraph, e.g.
"1 Game Changer (Thassa's Oracle) + 3 two-card combos (...) + extra-turn loop
(...) -> Bracket 4 (Optimized). Commander Salt power 7.8 -> power 7.8/10 vs
target 7. Commander Salt salt 42.5; ...; deck price $1,234.50. Target
Bracket 3: 2 hard conflicts, 1 advisory."

## Stored evaluations: the bracket worker (BR-5)

The evaluator never runs in the web request path. `analytics/bracket_worker.py`
runs as its own process: compose service `bracket-worker`, container
`commander-lab-bracket-worker`, same image, `python -m analytics.bracket_worker`
(`--once` for a single pass).

- Each tick reads the `decks` registry and computes an input hash per deck:
  sha256 over the decklist (sorted commanders, card names with quantities) and
  the Deck Journal minus its `_journal` bookkeeping (so a no-op save doesn't
  re-run). A deck is evaluated only when its hash differs from the stored one
  or it was requested. Decklist changes from any sync path and journal changes
  are both caught this way.
- Ticks run every `BRACKET_EVAL_INTERVAL_S` (default 300) or sooner: the
  worker `BLPOP`s `bracket:eval:wake`, which the app pushes after a journal
  save / check-in change, a per-deck sync, an import, a linked-account sync
  and `sync.py`. The wake-up is only a latency hint and every push is wrapped
  so it can never fail the caller.
- It stores `evaluate_bracket_live(r, deck).model_dump()` (BR-3 findings,
  `estimated_bracket`, `estimated_power_level`, conflicts and the full
  explanation). `BRACKET_EVAL_COMMANDERSALT=0` skips the Commander Salt call.
- One deck failing stores `status: "error"` with the message, keeps the last
  good `result`, records the failed hash (no retry every tick; it re-runs on
  the next input change or request), and the tick moves on.

Redis keys:

| Key | Value |
|-----|-------|
| `bracket:eval:{registry_id}` | `{registry_id, deck_name, status: ok\|error, evaluated_at, input_hash, result_hash, result, error, error_at, duration_ms}` |
| `bracket:eval:pending` | set of registry ids requested for re-evaluation |
| `bracket:eval:wake` | wake-up list (worker drains it) |
| `bracket:eval:heartbeat` | last tick summary `{at, decks, evaluated, skipped, failed, pid}` |

API (deck id = registry id, raw id or deck name; `/api/decks/...` works too):

- `GET /api/deck/{id}/bracket` -> 200 `{status, evaluated_at, input_hash, stale, pending, evaluation}`;
  `stale` is true when the decklist/journal changed since that evaluation.
  404 `{status: "not_evaluated" | "error", pending, evaluation: null}` before
  the first good evaluation; 404 `deck_not_found`; 503 if Redis is down.
- `POST /api/deck/{id}/bracket` -> 202 `{queued: true}`. Queues the deck and
  wakes the worker; it does not evaluate in the request.

## The BR chain

| Card | Scope |
|------|-------|
| BR-1 | This spec, `analytics/brackets.py` table + helpers, Game Changers from Scryfall with cache and snapshot, `/api/brackets`. |
| BR-2 | Hard-check data: mass-land-denial and extra-turn card lists (Scryfall queries + curated overrides, snapshot like BR-1), with "chained/looped" detection rules. |
| BR-3 | Shipped as the deterministic deck check (`analytics/bracket_check.py`): Game Changers, mass land denial and extra-turn lists (Tagger snapshot + overrides), Spellbook two-card combos by tag, tutors (advisory), `minimum_bracket` with per-floor cards. Covers what BR-2/BR-4 described for data and evaluation. |
| BR-4 | Shipped as the evaluator (`analytics/bracket_eval.py`, section below): BR-3 findings + journal targets + Commander Salt / our power estimate / EDHREC / price -> estimated bracket and power level, hard vs advisory conflicts, ordered explanation. |
| BR-5 | Advisory signals next to it: tutors, Commander Salt `bracket_rating`, Spellbook estimate, turn expectation vs the deck's speed; plus the user's declared bracket per deck (stored, with "not intentional" overrides). Advisory never changes the BR-4 number. |
| BR-6 | Shipped as the UI (section below): Journal bracket picker beside power, deck-page banner + decklist flags from the stored BR-5 result. |
| BR-7 | "To reach bracket N, cut these" suggestions and Tuner integration (a target bracket constrains suggested adds). |
| BR-8 | Calibration and upkeep: compare BR-4 with Commander Salt / Spellbook across our decks, document gaps, schedule the snapshot refresh and an alert when the official list changes. |

Non-goals for BR-1: no UI, no journal changes, no deck evaluation.

## UI: Journal picker + deck page (BR-6)

Direction A (Inline) of the BR-D designer spec (draft PR #97, reference only).
View models and renderers: `static/js/brackets.js` (`window.Brackets`), loaded
on every deck tab by `partials/deck_head.html`; styles: `hud.css` section 9
(`--bracket-*` tokens, `.hud-bracket*` classes).

- **Journal** (`/deck/{name}/journal`): the "Target power" section is now
  "Power & bracket". Under the power scale: an `Off 1 2 3 4 5` `hud-seg--scale`
  (`name="bracket"`, Off = `target_bracket: null`), a live definition card
  (`aria-live`), and an advisory note when the pick is below today's hard-rule
  minimum (never blocks saving). A data notch marks the estimate on both scales;
  the power hint says when it's more than `POWER_ALIGN_TOLERANCE` from target.
  Saves through `PUT /api/decks/{id}/journal`; the check-in's Bracket row now has
  an inline Change. `?bracket=N` preselects N (unsaved): the deck page's
  "Raise target to N" link.
- **Deck page**: header chips (target, always neutral, + a "■ N conflicts" button
  that focuses the banner; "Est. B3" when unset), and a banner between the tabs
  and the hero with the estimated bracket and power against the targets (delta
  coloured by `power_aligned` / bracket ≠ target, never by sign), one row per
  conflict (hard ■ / advisory ▲, bold title, message, card buttons that jump to
  and pulse the card), and the "How this was decided" tray (summary, signals
  table, evaluated-at + input hash). States: conflicts, clean, unset, not
  evaluated (Evaluate now = POST), re-evaluating (stale/pending: last result +
  chip, polls every 2s), failed (last result + "Last check failed · retry").
- **Decklist**: flagged cards get a severity frame + top bar + caption
  (`■ Game Changer · Over Bracket 1`), hard wins; group headers link to the first
  flagged card; the List view shows the same caption as a chip.
- Signals have no kind/drives in BR-4, so the tray table is Signal · Value ·
  Effect · Source. Conflict titles are derived from the rule (first sentence of
  the message otherwise). The power-vs-target advisory is BR-4's `power_target`
  conflict (`engine.POWER_ALIGN_TOLERANCE`); the UI only renders it.

## Target bracket in the Deck Journal (BR-2)

Each deck's journal has an optional top-level `target_bracket`: an integer
1-5, or `null` (unset, the default), which means no bracket checks. It is
stored beside `target_power_level` (1-10) and never derived from it; the two
are independent inputs. Validation is in `user_intent.validate_intent`, and
the field is declared in `schemas/user_intent.schema.json`. The check-in row
`bracket` shows `Bracket N · Name` from `BRACKETS`. See `docs/DECK_JOURNAL.md`
for the API.
