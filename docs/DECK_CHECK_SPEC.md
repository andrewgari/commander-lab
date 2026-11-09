# Deck Check (FR-1): technical specification

Status: draft v1, Oracle, 2026-11-08. Kanban card t_cfd5521a. Proposed repo path: `docs/DECK_CHECK_SPEC.md`.
Scope: a deterministic report with four sections (Legality, Bracket, Combos, Composition) for one deck.
Out of scope: UI, code, AI calls, auto-fix, and the Intent step (FR-2).

Every number in §7 and §8 came from running the repo's own pure functions on origin/main @ 22fc530.
The read-only probe scripts are attached to the card (see §9).

---

## 0. Control deck: read this first

The card asks for the **"Brew test deck"** as the control. It doesn't exist anywhere I can read:
- the repo and its fixtures
- the tester Redis/PG containers (only "Atraxa Superfriends", 3 cards)
- CovaDax's 59 public Archidekt decks (re-checked 2026-11-08)

Prod `commander-lab-redis` has been stopped for 5 days. I asked for it on 2026-11-06 (comment on t_cfd5521a) and got no decklist back.

So this spec uses the repo's **established control deck** instead. It is "The Laws of Time are Mine!!" (`archidekt:5620189`, The Tenth Doctor + Rose Tyler), the same control as `docs/PERSONALITY_SPEC.md` §5 and `tests/fixtures/ps4/doctor_rose.json`. It's a useful control because it lights up every section:
- a Doctor/companion pairing (legality)
- three bracket hard rules
- 0 in-deck combos but 34 one-away (combos)
- five composition rows off target

**If Andrew names a different "Brew test deck", re-run §9 on it and replace §7.** §8's mutations work on any 100-card list that has at least 8 basics.

---

## 1. What Deck Check is

One button, one report: *"Is this deck legal, what bracket does it really play at, what combos are in it, and is its skeleton sound?"*

- **Deterministic:** same deck plus same data snapshot gives byte-identical `sections`. No LLM calls and no randomness.
- **Advisory, not prescriptive.** It reports facts and flags. It never suggests cuts. Cuts belong to Deep Review, which sits behind the Deck Journal / pet-card gate.
- **Composes existing modules**, with no new rules logic (§3). Each section is a thin adapter over a function that already ships and already has tests.

---

## 2. Output shape

### 2.1 Envelope

```json
{
  "version": 1,
  "deck": {
    "registry_id": "archidekt:5620189",
    "name": "The Laws of Time are Mine!!",
    "commanders": ["The Tenth Doctor", "Rose Tyler"],
    "cards_sig": "<identity.cards_signature(deck)>",
    "mainboard_qty": 100,
    "unique_names": 95,
    "excluded": {"maybeboard": 10, "considering": 0}
  },
  "status": "pass | warn | fail | partial",
  "generated_at": "2026-11-08T21:33:48+00:00",
  "data_as_of": {
    "card_data": "2026-11-08",
    "game_changers": "2026-10-15",
    "bracket_card_lists": "2026-10-15",
    "bracket_rules": "2026-10-15",
    "spellbook_fetched_at": "2026-11-08T21:33:48+00:00",
    "composition_template": "Default"
  },
  "sections": {
    "legality":    { "...": "§2.2" },
    "bracket":     { "...": "§2.3" },
    "combos":      { "...": "§2.4" },
    "composition": { "...": "§2.5" }
  },
  "flags": [ { "...": "§2.6, flattened, sorted" } ],
  "table_health": [ { "...": "§2.7" } ]
}
```

**Common section header** (every section has it):

```json
{
  "key": "legality",
  "status": "pass | warn | fail | unavailable",
  "confidence": 0.0,
  "confidence_band": "high | medium | low",
  "confidence_reasons": ["..."],
  "summary": "one sentence, deterministic template",
  "source": "module.function",
  "elapsed_ms": 0
}
```

**Top-level `status` rules:**
- `fail` if `legality.status == fail`.
- Otherwise `partial` if any section is `unavailable`.
- Otherwise `warn` if any flag has severity `warn`.
- Otherwise `pass`.

**Determinism rules:**
- `generated_at`, `elapsed_ms` and `data_as_of.spellbook_fetched_at` are the only fields that may differ between two runs on identical inputs.
- Card lists are sorted: deck order for legality (validator order), then name. Flags are sorted by `(severity_rank, section_order, code, cards[0])`.

### 2.2 `sections.legality`

```json
{
  "key": "legality", "status": "pass",
  "valid": true,
  "commander_pair": {"mode": "doctor+companion", "valid": true},
  "color_identity": "WUR",
  "deck_size": {"expected": 100, "actual": 100},
  "errors": [
    {"code": "BANNED", "cards": ["Mana Crypt"], "message": "Banned in Commander: Mana Crypt. Remove them.",
     "details": {}, "depends_on": null}
  ],
  "excluded_slots": {"maybeboard": ["Bad Wolf Bay", "..."]},
  "remapped": ["Sideboard -> Maybeboard (10 cards)"]
}
```

`errors[].code` is the `deck_validator.RuleCode` value, unchanged. `depends_on` is set by the post-processing rules in §3.1.

### 2.3 `sections.bracket`

```json
{
  "key": "bracket", "status": "warn",
  "minimum_bracket": 4, "minimum_bracket_name": "Optimized",
  "estimated_bracket": 4,
  "target_bracket": null, "aligned": null,
  "floors": [{"rule": "mass_land_denial", "floor": 4, "cards": ["Worldfire"]}],
  "rules": {
    "game_changers":    {"count": 2, "floor": 3, "cards": ["Farewell", "Mystical Tutor"]},
    "mass_land_denial": {"count": 1, "floor": 4, "cards": ["Worldfire"]},
    "extra_turns":      {"count": 1, "floor": 2, "cards": ["Regenerations Restored"], "loops": []},
    "two_card_combos":  {"checked": true, "count": 0, "floor": 1, "combos": []},
    "tutors":           {"count": 3, "advisory": true, "cards": ["Rory Williams", "Amy Pond", "Mystical Tutor"]}
  },
  "conflicts": [],
  "signals": [ { "signal": "...", "value": 0, "effect": "...", "source": "..." } ],
  "notes": []
}
```

- `rules.*` is `BracketFindings` (BR-3), reshaped one-for-one.
- `estimated_bracket`, `conflicts` and `signals` come from `BracketEvaluation` (BR-4), with the power fields **removed** (§3.2, decision D3).

### 2.4 `sections.combos`

```json
{
  "key": "combos", "status": "pass",
  "identity": "URW",
  "counts": {"in_deck": 0, "one_away": 34, "one_away_owned": 0, "one_away_owned_available": 0,
             "off_identity_one_away": 9},
  "in_deck": [
    {"id": "...", "url": "https://commanderspellbook.com/combo/.../", "cards": ["A", "B"],
     "size": 2, "requires": [], "produces": ["..."], "bracket_tag": "R", "bracket_tag_name": "Ruthless",
     "early": true, "two_card": true}
  ],
  "one_away": [ { "...": "same as in_deck, plus missing, missing_owned, owned" } ],
  "top_missing_cards": [ {"name": "...", "combos": 0, "owned": null} ],
  "cached": false, "stale": false
}
```

This is `combos.deck_combos()` output. `one_away` is capped at **10** in the report, and `counts` stay complete. The full list stays at `GET /api/decks/{id}/combos`.

### 2.5 `sections.composition`

```json
{
  "key": "composition", "status": "warn",
  "template": {"name": "Default", "overridden": false, "source": "The Command Zone ep. 658 ..."},
  "rows": [{"key": "lands", "label": "Lands", "actual": 36, "target": 38, "delta": -2, "status": "short", "cards": 31}],
  "total_cards": 100,
  "unknown_cards": [],
  "land_odds": {"lands": 36, "library": 98, "p_opener_2_to_5": 0.795, "p_4_lands_by_t4_on_draw": 0.631}
}
```

- `rows` is `composition.compute_composition()`, unchanged.
- `land_odds` is hypergeometric. Library = 100 minus the commanders in the command zone; opener = 7 cards; T4 on the draw = 11 cards seen.

### 2.6 `flags[]` (flattened across sections)

```json
{"code": "LEG_BANNED", "section": "legality", "severity": "error | warn | info",
 "cards": ["Mana Crypt"], "message": "...", "source": "deck_validator.RuleCode.BANNED"}
```

### 2.7 `table_health[]`

```json
{"code": "TH_MASS_LAND_DENIAL", "cards": ["Worldfire"], "reason": "Exiles every land; WotC bracket rule: B4+ only",
 "salt": 2.38}
```

These are table-health flags: allowed and never hidden (§4.5). `salt` appears only when the card source carries it. Archidekt `oracleCard.salt` is EDHREC's 0-4 salt score.

---

## 3. Section logic: mapping to existing code

| Section | Pure function (the logic) | Live wrapper / data | Endpoint today |
|---|---|---|---|
| Legality | `draft/deck_validator.validate_deck(DraftDeck, CardCatalog)` | `DraftDeck.from_builder_draft(deck)` / `from_dict`, `catalog_from_redis(r, names)` | none (Reconstruct-only today) |
| Bracket | `analytics/bracket_check.check_deck_brackets(deck, game_changers, combos, oracle)` then `analytics/bracket_eval.evaluate_bracket(deck, journal, {"findings": f})` | `check_deck_brackets_live(r, deck)`, `brackets.get_game_changers(r)`, `journal.load_journal` | bracket worker (BR-5) |
| Combos | `analytics/interactions/combos.deck_combos(r, deck)` (`SpellbookClient.find_my_combos`, 7-day cache `spellbook:fmc:*`) | same | `GET /api/decks/{id}/combos` (`api_deck_combos`, main.py:3686) |
| Composition | `analytics/composition.compute_composition(deck, card_texts, template)` + `classify_card` | `insights.deck_composition(r, deck)` (`card_texts`, `get_template`) | `GET /api/decks/{id}/composition` |
| (card-level, not called) | `card_eval/service.evaluate_card`, `card_review/review.run_review` | | |

Why `card_eval` and `card_review` aren't called: they are per-card and need EDHREC / the HF model / an LLM debate. Deck Check reuses only their **gate semantics**, so a card Deck Check calls illegal would also hit the `illegal` gate in Single Card Review:
- `card_source.mainboard_gate` covers Commander-legal, fail-closed, no variant type class.
- `card_review.review` gate order is pet card, then illegal, with `GATE_ILLEGAL_CONFIDENCE = 0.99`.

**D1.** Legality uses `deck_validator`, not `card_eval`. The validator is the only module that checks deck-level rules: size, singleton, commander pair, identity union and slot gates. `card_eval` checks one card against one identity.

### 3.1 Legality post-processing (deterministic, in the adapter)

1. **Slot input.** Run `deck_slots` normalization first. `Sideboard` is remapped to `maybeboard` (deck_slots.py:18). Only `commanders` and `mainboard` count toward the 100. Variant slots (`planar_deck`, `scheme_deck`, `attraction_deck`) are gated by `slot_gate`, and `maybeboard`/`considering` are listed under `excluded_slots`, never as errors. Report the remap in `remapped`.
2. **De-duplicate per card.** A plane in the mainboard currently produces **two** `NOT_LEGAL` errors for the same card: one for legality `not_legal` and one for the slot gate (K5, §8). Merge them into one error and keep the slot reason, because it's the more useful message.
3. **Cascade suppression.** When `PARTNER_INVALID` or `COMMANDER_ILLEGAL` fires, the identity union was built from an invalid pair, so `COLOR_IDENTITY` hits are a symptom. Keep the error, set `depends_on: "PARTNER_INVALID"`, and make the flag severity `info`. K6 shows why: 37 cards flagged off-identity because Rose was swapped out.
4. **Unknown is not legal.** `UNKNOWN_CARD` and `UNKNOWN_LEGALITY` are errors (fail closed, as the validator does), but they lower confidence rather than claim the deck is illegal (§4.1).

### 3.2 Bracket adapter

- Use the **live** Game Changer list (`brackets.get_game_changers(r)`), falling back to the BR-1 snapshot. Record which one in `data_as_of`.
- Pass the Spellbook data the Combos section fetched (one fetch per check, §5).
- Pass the Journal when one exists. `target_bracket` enables `conflicts`, and `pet_cards` produce the pet-card advisories that already ship in `bracket_eval`.
- **D3. Drop the power fields** (`estimated_power_level`, `power_source`, `power_*`). Without Commander Salt or an engine estimate, `evaluate_bracket` falls back to the band midpoint. On the control deck that prints **"power 9.0/10"** for a casual Doctor Who deck, purely because one Worldfire set the floor to B4. That's a fabricated-looking number in a report sold as deterministic facts. Power belongs to Deep Review.
- **D4.** `estimated_bracket` keeps BR-4's Exhibition rule: a minimum of 1 is shown as 2 unless the Journal targets 1. Bracket 5 is never estimated.

### 3.3 Combos adapter

- `deck_combos(r, deck, limit=None)`, then truncate `one_away` to 10 in the report.
- **D5. Two-card definition = BR-3's** `is_two_card` (≤ 2 cards by quantity, no `requires`). Combos that need templates are listed but don't count toward a floor.
- When Spellbook fails and there's no cache: section `unavailable`, Bracket's `two_card_combos.checked = false`, plus the BR-3 note "minimum bracket may be higher".

### 3.4 Composition adapter

- `insights.deck_composition(r, deck)`, unchanged. Statuses come from `delta_status`: ±1 is `ok`, ≤ −2 is `short`, ≥ +2 is `over`.
- Add `land_odds` (§2.5). It's pure arithmetic, so no new data.
- A **`short` Lands row with `delta ≤ −4`** is a `warn` flag. Other `short`/`over` rows are `info`. Plan `over` is never a flag on its own, because it's the residual.

---

## 4. Signal table and confidence

### 4.1 Signal table

Severity: **error** means the deck isn't legal as built. **warn** is a real risk or a table-health item. **info** is context.

| Code | Section | Trigger (deterministic) | Source of truth | Severity |
|---|---|---|---|---|
| LEG_DECK_SIZE | legality | mainboard + commanders ≠ 100 | `RuleCode.DECK_SIZE` | error |
| LEG_COMMANDER_COUNT | legality | not 1 or 2 commanders | `COMMANDER_COUNT` | error |
| LEG_COMMANDER_ILLEGAL | legality | `CardInfo.can_be_commander` false | `COMMANDER_ILLEGAL` (mirrors Scryfall `is:commander`) | error |
| LEG_PARTNER_INVALID | legality | `check_partner_pair` reason | `PARTNER_INVALID` | error |
| LEG_COLOR_IDENTITY | legality | card CI ⊄ commander union | `COLOR_IDENTITY` | error, or info when it depends on a pairing error |
| LEG_SINGLETON | legality | copies > `singleton_limit_from_text` / `SINGLETON_EXCEPTIONS` | `SINGLETON` | error |
| LEG_BANNED | legality | `commander_legality == "banned"` (snapshot fallback) | `BANNED` | error |
| LEG_NOT_LEGAL | legality | `not_legal`, or slot-gate failure (merged per §3.1) | `NOT_LEGAL` | error |
| LEG_UNKNOWN_CARD | legality | name not in catalog | `UNKNOWN_CARD` | error, lowers confidence |
| LEG_UNKNOWN_LEGALITY | legality | record has no synced legality | `UNKNOWN_LEGALITY` | error, lowers confidence |
| BR_GAME_CHANGERS | bracket | count ≥ 1: floor 3; ≥ 4: floor 4 | `bracket_check` / `brackets.min_bracket_for_game_changers` | info at floor 3, warn at floor 4 |
| BR_MASS_LAND_DENIAL | bracket | any Tagger `otag:mass-land-denial` / WotC example / "destroy all lands" text | `bracket_check` | warn, plus TH |
| BR_EXTRA_TURNS | bracket | any `otag:extra-turn` card: floor 2 | `bracket_check` | info; warn when ≥ 3 cards (BR-3 note) |
| BR_EXTRA_TURN_LOOP | bracket | Spellbook "Infinite turns", no `requires`: floor 4 | `bracket_check.is_extra_turn_loop` | warn, plus TH |
| BR_TWO_CARD_COMBO | bracket | a two-card in-deck combo: floor 3 | `bracket_check.is_two_card` | info |
| BR_EARLY_COMBO | bracket | two-card combo tagged R or B: floor 4 | `EARLY_COMBO_TAGS` | warn |
| BR_TUTORS | bracket | `otag:tutor` mv ≤ 3 (advisory, never a floor) | `bracket_check` | info |
| BR_TARGET_CONFLICT | bracket | Journal target < a hard floor | `bracket_eval` hard `Conflict` | warn |
| BR_PET_CONFLICT | bracket | a pet card causes the conflict | `bracket_eval` `pet_card` advisory | info ("your call") |
| BR_COMBOS_UNCHECKED | bracket | no Spellbook data | BR-3 note | warn, lowers confidence |
| CMB_IN_DECK | combos | each `included` variant | Spellbook | info (the bracket impact is carried by BR_*) |
| CMB_ONE_AWAY_OWNED | combos | `missing_owned and all available` | `deck_combos` | info |
| CMB_STALE | combos | served from cache after a fetch error | `deck_combos.stale` | warn, lowers confidence |
| CMP_LANDS_SHORT | composition | Lands delta ≤ −4 | `compute_composition` | warn |
| CMP_ROW_OFF | composition | any other row `short`/`over`, except Plan | `delta_status` | info |
| CMP_UNKNOWN | composition | `unknown_cards` non-empty | `compute_composition` | info, lowers confidence |
| TH_MASS_LAND_DENIAL / TH_EXTRA_TURNS / TH_HIGH_SALT | table_health | MLD card; extra-turn card; card salt ≥ 2.0 | BR-3 lists; card salt | (separate list) |

### 4.2 Confidence (per section, 0-1, deterministic)

Bands: **high ≥ 0.90**, **medium 0.70-0.89**, **low < 0.70**. Each deduction appends a reason string.

| Section | Start | Deductions |
|---|---|---|
| Legality | 1.00 | × (known-legality entries ÷ all entries). Each `UNKNOWN_CARD` or `UNKNOWN_LEGALITY` entry counts as unknown. Card data older than 30 days: −0.05. |
| Bracket | 1.00 | Combos unchecked: **cap 0.60** (the floor is a lower bound only). Spellbook cache stale: −0.10. Game Changers from the snapshot rather than live: −0.05. Bracket card lists (`bracket_cards.json as_of`) older than 120 days: −0.10. Any `oracle_text` regex hit (not a list hit): −0.05 each, max −0.15. |
| Combos | 1.00 | Stale: −0.20. Fetched more than 7 days ago (`DEFAULT_MAX_AGE_DAYS`): −0.10. Unavailable: section `unavailable`, confidence 0. |
| Composition | 0.90 (cap, because the classifier is regex) | × (1 − unknown_cards ÷ total_cards). Template overridden: no deduction. |

**D6.** The composition cap is 0.90 by design. The regex classifier is a heuristic with known misses. It is not ground truth.

---

## 5. Live step progress

Same job pattern as Single Card Review (`POST .../review` returns 202 with `job_id`; `GET .../jobs/{id}` polls `status`/`stage`), so the UI can reuse the poller.

```
POST /api/decks/{deck_id}/check          -> 202 {job_id, status_url}
GET  /api/decks/{deck_id}/check/jobs/{id} -> {status: queued|running|done|failed, stage, steps[], result?}
```

`steps[]` is always all five entries, in this fixed order. Each one is `{key, label, state: pending|running|done|skipped|failed, started_at, elapsed_ms, summary}`.

| # | key | label | work | depends on |
|---|---|---|---|---|
| 0 | `load` | Loading deck | resolve deck, slot-normalize, build `CardCatalog` (`catalog_from_redis`) and `card_texts` once | none |
| 1 | `legality` | Checking legality | `validate_deck` + §3.1 post-processing | load |
| 2 | `bracket` | Checking bracket | sub-stage `fetching_combos` (`find_my_combos`, cached 7 d), then `check_deck_brackets`, then `evaluate_bracket` | load |
| 3 | `combos` | Listing combos | `deck_combos` using the **same** Spellbook record from step 2 (cache hit, zero network) | bracket |
| 4 | `composition` | Checking composition | `compute_composition` + `land_odds` | load |

Rules:
- One Spellbook request per check, at most. Step 3 must hit the `spellbook:fmc:` key step 2 wrote.
- **A failed step doesn't stop the run.** Legality `fail` still runs Bracket, Combos and Composition, because an illegal deck still has a bracket. A Spellbook failure marks step 2 `done` with `checked=false` and step 3 `failed`, and the top-level status becomes `partial`.
- Step 0 failure (deck not found / catalog unavailable) fails the job.
- Card-data-only steps (1, 4) do no network I/O. All I/O happens in step 0 and the step-2 sub-stage.
- No LLM preflight (`_oracle_preflight`) is needed, since no AI is called.

---

## 6. Caching and invalidation

- Key: `deck_check:{registry_id}`. Value: the last report.
- Freshness: the report is fresh if `cards_sig` matches and every `data_as_of` entry is unchanged. Otherwise show it greyed out with "stale: deck changed" or "stale: data refreshed".
- No TTL, same as the Spellbook cache: an old copy beats nothing.

---

## 7. Expected output: control deck

Deck `archidekt:5620189`, Archidekt `updatedAt` 2026-09-11, checked 2026-11-08. The commanders are The Tenth Doctor and Rose Tyler. The Archidekt list has 105 entries:
- **95 names / 100 cards** in mainboard + commanders.
- **10 planes in "Sideboard"**: Bad Wolf Bay, The Lux Foundation Library, Amy's Home, Human—Time Lord Meta-Crisis, Lake Silencio, Prime Minister's Cabinet Room, New New York, Ood Sphere, Pompeii, Two Streams Facility. They are remapped to maybeboard and excluded.

Archidekt marks its "Sideboard" category `includedInDeck: true`. An adapter that trusts that flag would count 110 cards and flag 10 planes as `NOT_LEGAL`. That's why §3.1 rule 1 matters.

### 7.1 Legality: `pass`, confidence 1.00 (high)
- `valid: true`, `errors: []`.
- `commander_pair: doctor + doctor's companion` is valid, and the CI union is **WUR**. Rose Tyler is W; The Tenth Doctor is UR (`Spellbook identity "URW"`).
- `deck_size 100/100`. Basics: Plains 3, Island 3, Mountain 2.
- `remapped: ["Sideboard -> Maybeboard (10 cards)"]`.

### 7.2 Bracket: `warn`, minimum **4 (Optimized)**, confidence 1.00 if live Game Changers (0.95 with the snapshot)

| Rule | Count | Floor | Cards |
|---|---|---|---|
| mass_land_denial | 1 | **4** | Worldfire |
| game_changers | 2 | 3 | Farewell, Mystical Tutor |
| extra_turns | 1 | 2 | Regenerations Restored |
| two_card_combos | 0 (checked) | 1 | none |
| tutors (advisory) | 3 | none | Rory Williams, Amy Pond, Mystical Tutor |

- I cross-checked against Archidekt's own per-card flags: Worldfire `massLandDenial: true`, Regenerations Restored `extraTurns: true`, Farewell and Mystical Tutor `gameChanger: true`.
- Scryfall confirms Farewell `game_changer: true`, Worldfire `game_changer: false`, and all of them `commander: legal`.
- Archidekt does **not** tag Rory Williams or Amy Pond as tutors. Our Tagger snapshot does, because "Partner with" fetches the partner from the library. That's expected, it stays advisory, and it never moves the floor.
- `estimated_bracket 4`, `target_bracket null`, `conflicts []`. There's no saved Journal, as in PERSONALITY_SPEC §5.
- `summary`: "2 Game Changers (Farewell, Mystical Tutor) + 1 extra-turn card + mass land denial (Worldfire) -> Bracket 4 (Optimized)."
- Power is **not** reported (D3). BR-4 would have printed "power 9.0/10" from the band midpoint.
- Flags: `BR_MASS_LAND_DENIAL` (warn), `BR_GAME_CHANGERS` (info, floor 3), `BR_EXTRA_TURNS` (info), `BR_TUTORS` (info).

**What this means for Andrew:** one card, Worldfire, is the whole difference between B3 and B4. Without it the floor is 3, from 2 Game Changers. That's information, not a suggestion. If Worldfire is part of the deck's thesis, B4 is the honest bracket.

### 7.3 Combos: `pass`, confidence 1.00
- Spellbook identity `URW`; `in_deck: 0`, `one_away: 34`, `off_identity_one_away: 9` (live, 2026-11-08).
- Top one-away by popularity:
  - Hullbreaker Horror + Sol Ring (E)
  - Tidespout Tyrant + Sol Ring (S)
  - Approach of the Second Sun + Mystical Tutor (P)
  - The Tenth Doctor + Rousing Refrain (E)
  - Wedding Ring + Consecrated Sphinx (S)
  - There and Back Again + Clockspinning (C)
  - Felidar Guardian + Flesh Duplicate (E)
  - The Tenth Doctor + Chronomantic Escape (S)
- Flags: none. `one_away_owned` needs the prod `instance:*` index, which wasn't reachable, so the owned counts are unverified here.
- **Tests must not assert the one-away counts against live Spellbook**, because they drift as combos are added. Freeze a fixture, the way `tests/fixtures/bracket_check_decks.json` does.

### 7.4 Composition: `warn`, confidence 0.90 (high, at the cap)

| Row | Actual | Target | Δ | Status |
|---|---|---|---|---|
| Lands | 36 | 38 | −2 | short |
| Ramp | 9 | 10 | −1 | ok |
| Card advantage | 9 | 12 | −3 | short |
| Targeted disruption | 10 | 12 | −2 | short |
| Mass disruption | 3 | 6 | −3 | short |
| Plan | 34 | 22 | +12 | over |

- `total_cards 100`, `unknown_cards []`. Lands are 31 unique and 36 by quantity.
- `land_odds`: 36 lands in a 98-card library.
  - P(2-5 lands in the opening 7) = **0.795**
  - P(≥ 4 lands by T4 on the draw) = **0.631**
- Flags: `CMP_ROW_OFF` × 4 (info). **No `CMP_LANDS_SHORT`**, because −2 is above the −4 threshold.
- Plan +12 is the residual, and it's normal for a theme deck: time counters and suspend are the plan.

### 7.5 Table health
- `TH_MASS_LAND_DENIAL`: Worldfire, salt 2.38 (exiles every permanent, hand and graveyard; life totals become 1).
- `TH_HIGH_SALT`: Farewell, salt 2.32.
- `TH_EXTRA_TURNS`: Regenerations Restored, a single extra turn after a 12-turn vanishing clock. No loop.
- Sol Ring has salt 1.59, under the 2.0 threshold, so it's not flagged.

### 7.6 Top-level
`status: "warn"`. Legality passes and no section is `unavailable`. The only warn-severity flag is `BR_MASS_LAND_DENIAL`.

---

## 8. Known-bad scenarios: expected flags

Each scenario mutates the control deck (100 cards kept unless noted) and re-runs the same pure functions. These are real outputs, not predictions.

| # | Mutation | Legality | Bracket | Combos | Composition | Top status |
|---|---|---|---|---|---|---|
| K1 | Mountain → **Mana Crypt** | `LEG_BANNED [Mana Crypt]` | unchanged (4) | unchanged | Lands 35, Ramp 10 | **fail** |
| K2 | Mountain → **Birds of Paradise** | `LEG_COLOR_IDENTITY [Birds of Paradise (has G)]`, allowed WUR | unchanged | unchanged | Lands 35 | **fail** |
| K3 | remove one basic (99 cards) | `LEG_DECK_SIZE` "Deck has 99 ... Add 1." | unchanged | unchanged | Lands 35 | **fail** |
| K4 | basic → 2nd **Sol Ring** | `LEG_SINGLETON [Sol Ring x2 (max 1)]` | unchanged | unchanged | Ramp 10 (counts quantity) | **fail** |
| K5 | basic → **Bad Wolf Bay** (plane) in mainboard | `LEG_NOT_LEGAL [Bad Wolf Bay]`. Today **two** errors (legality + slot gate); spec merges to one, reason "plane not legal in mainboard slot (belongs in planar_deck)" | unchanged | unchanged | Targeted 11 (the plane's text matches "exile up to one target") | **fail** |
| K6 | Rose Tyler → **Jhoira of the Ghitu** as 2nd commander | `LEG_PARTNER_INVALID [The Tenth Doctor, Jhoira]` + `LEG_COLOR_IDENTITY` × **37** cards (has W). Spec: CI errors get `depends_on: PARTNER_INVALID`, severity info | unchanged | identity changes | unchanged | **fail** |
| K7 | basic → **"Totally Fake Card"** | `LEG_UNKNOWN_CARD`; legality confidence 95/96 entries known = 0.99 (high), status still fail | unchanged | unchanged | `CMP_UNKNOWN [Totally Fake Card]`, Plan 35 | **fail** |
| K8 | 3 basics → **Rhystic Study, Smothering Tithe, Cyclonic Rift** | pass | GC **5**: floor 4 (`BR_GAME_CHANGERS` warn). Floors now GC 4 + MLD 4 | unchanged | Lands 33 (Δ −5: **`CMP_LANDS_SHORT` warn**), CA 11, Targeted 11 | warn |
| K9 | 2 basics → **Kiki-Jiki + Felidar Guardian** | pass | `BR_EARLY_COMBO`: Kiki-Jiki + Felidar (R, early) floor 4; `BR_TWO_CARD_COMBO`: Felidar Guardian + Flesh Duplicate (E). The existing Flesh Duplicate pairs with the new Felidar | `in_deck: 2` | Lands 34 | warn |
| K10 | 2 basics → **Time Warp, Temporal Manipulation** | pass | extra_turns 3 cards, floor stays 2; BR-3 note "3 extra-turn cards: Brackets 2-3 allow only a few, never chained..." → `BR_EXTRA_TURNS` **warn** | unchanged | Lands 34 | warn |
| K11 | all 8 basics → **8× Persistent Petitioners** | **pass** ("A deck can have any number" is honoured) | unchanged | unchanged | Lands **28** (Δ −10): `CMP_LANDS_SHORT` warn; P(2-5 in opener) **0.646**, P(4 by T4) **0.386** | warn |
| K12 | control deck, Spellbook unavailable, no cache | pass | `two_card_combos.checked=false`, note "may be higher", `BR_COMBOS_UNCHECKED` warn, bracket confidence **capped 0.60 (low)** | section `unavailable` | unchanged | **partial** |

Scryfall facts used (2026-11-08):
- Mana Crypt: `commander: banned`.
- Birds of Paradise: CI G.
- Rhystic Study, Smothering Tithe and Cyclonic Rift: `game_changer: true`.
- Kiki-Jiki, Felidar Guardian, Time Warp, Temporal Manipulation and Persistent Petitioners: legal, not Game Changers.

Spellbook tagged Kiki-Jiki + Felidar Guardian **R (Ruthless)**.

### 8.1 Behaviours the implementation must keep (regression list)
1. Bracket and Composition still run when Legality fails (K1-K7 all report a bracket).
2. Composition counts by quantity (K4: Ramp 10 with two Sol Rings).
3. Singleton exceptions come from oracle text (K11 passes).
4. Commander pairing errors are reported once, with the identity cascade marked dependent (K6).
5. One error per card for slot violations (K5).
6. Missing Spellbook data never reads as "no combos" (K12).

---

## 9. Re-running this

Scripts are in `docs/evidence/deck-check-v1/`. Both are read-only: they use an in-memory Redis stand-in and write nothing to any service.
- `probe.py <archidekt_deck.json> <repo_root> <out.json>`: the control run (§7).
- `mutations.py <archidekt_deck.json> <repo_root> <out.json>`: K1-K12 (§8).
- `hyper.py`: the land odds.

Example invocation, with the deck JSON from `GET https://archidekt.com/api/decks/{id}/`:
`uv run --no-project --python 3.11 --with-requirements requirements.txt python -I probe.py deck.json . out.json`

The repo `.venv` is currently broken: its interpreter path points to `/opt/data/...`.

---

## 10. Decisions and open questions

| # | Decision | Why |
|---|---|---|
| D1 | Legality = `deck_validator`, not `card_eval` | the only module with deck-level rules |
| D2 | Four sections + `load`; Intent left for FR-2 | card scope |
| D3 | No power level in Deck Check | the band-midpoint fallback prints 9.0/10 for a casual deck |
| D4 | Keep BR-4's Exhibition (1 → 2) rule; never estimate 5 | BRACKETS_SPEC |
| D5 | Two-card = BR-3 `is_two_card` | one definition app-wide |
| D6 | Composition confidence capped at 0.90 | regex classifier |
| D7 | Lands warn at Δ ≤ −4 only | −2/−3 is a style choice (36-37 lands is common). P(≥4 lands by T4 on the draw, 98-card library): 38 lands → 0.686, 36 → 0.631, 34 (Δ −4) → **0.573** (−11 points), 33 → 0.543, 28 → 0.386 |

Open, for Andrew:
- **Q1.** Is "Brew test deck" a real list? If so, send the id or list and I'll redo §7. Until then the Doctor/Rose control stands.
- **Q2.** Should Archidekt "Sideboard" stay remapped to Maybeboard for Deck Check (current app default)? Or should the 10 planes go to `planar_deck` so the slot gate checks them? The latter is stricter and correct for Planechase decks.
- **Q3.** Salt threshold 2.0 for `TH_HIGH_SALT`? Is the salt field synced into `card_meta` today? It isn't verified; the probe read it from Archidekt.
