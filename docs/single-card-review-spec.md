# Single Card Review: Domain Spec (rubric, debate prompts, replacement heuristics)

Version 1.0, 2026-10-16. Author: Oracle (kanban t_d9db48e8).

Consumers:
- t_b2e3ff79: evaluation service
- t_c17c813d: debate runner, replacement suggester and endpoint
- t_e49af424: UI

This is an advisory spec, not code. Every number below is one of three kinds:
- a published source, cited inline;
- data fetched on 2026-10-16, cited;
- a **tunable heuristic**, marked as one.

The worked examples use real EDHREC and Scryfall data. Their *deck contexts* (counts, Journal) are illustrative and are labelled that way.

---

## 0. Pipeline and contracts

```
Review(card, deck)
  ├─ 0. Gate checks ........ pet card? illegal? commander itself? (§0.2)
  ├─ 1. Evaluate ........... roles (§1) + strength rubric (§2) → EvaluationResult
  ├─ 2. Evidence packet .... deterministic, ID-tagged facts (§3.1)
  ├─ 3. Debate ............. Remove vs Keep, 2–3 rounds (§3)
  ├─ 4. Judge .............. verdict JSON + code-side confidence calibration (§3.5–3.6)
  └─ 5. Replacements ....... only if verdict = remove (§4)
```

### 0.1 Inputs the rubric needs (the evaluation service supplies these)

| Field | Source | Notes |
|---|---|---|
| `card.oracle_text`, `type_line`, `cmc`, `mana_cost`, `color_identity`, `legalities.commander`, `game_changer`, `edhrec_rank`, `prices.usd`, `oracle_id`, `games` | Scryfall `/cards/named?exact=` | For double-faced cards, use `card_faces[]`. |
| `edhrec.num_decks`, `edhrec.potential_decks`, `edhrec.synergy`, `edhrec.lift` | EDHREC `json.edhrec.com/pages/commanders/<slug>.json` → `container.json_dict.cardlists[].cardviews[]` | Match by name. Can be missing (§2.6). |
| `edhrec.salt` | EDHREC card page `json.edhrec.com/pages/cards/<slug>.json`, field `salt` (0–4) | Commander-page cardviews have no salt. |
| `commander` (1–2 cards + background), `commander_identity` | Deck | For partners and backgrounds, take the union of identities. |
| `decklist[]` with roles (§1) | Deck + §1 classifier | Excludes the Commander, Sideboard, Maybeboard and Considering categories. |
| `journal` {thesis, pillars[{name, match}], wincons[], pet_cards[], bracket_target, confirmed, budget_cap_per_card?} | Deck Journal | Can be empty (§0.2). |
| `deck.kind` (physical/digital), `inventory` (owned counts, other decks using the card) | Redis `inventory` | Feeds own-first sourcing (§4.5). |
| `colored_sources{W,U,B,R,G}`, `avg_mv` | Commander Lab review metrics | Feeds K (§2.3) and the curve check. |
| `combos[]` | Commander Spellbook `backend.commanderspellbook.com/variants?q=card="X"` | Feeds wincon detection and the win-line-change check. |

### 0.2 Gate checks (before any model call)

| Condition | Result | Debate? |
|---|---|---|
| Card is the commander, partner or background | Review is disabled; the UI hides the button. | No |
| Card is in `journal.pet_cards` | `verdict: keep`, `confidence: 1.0`, `key_reasons: ["Pet card (Deck Journal): protected"]`, no replacements. | No |
| `legalities.commander != "legal"`, or `color_identity ⊄ commander_identity` (CR 903.4, 903.5c) | `verdict: remove`, `confidence: 0.99`, reason `illegal: <banned / outside color identity>`. Replacements run in like-for-like mode. | No |
| Journal empty or `confirmed=false` | The review runs normally, but confidence is capped at **0.65**. A remove verdict gets `provisional: true`, and the UI shows "Provisional: confirm the Deck Journal". | Yes |

---

## 1. Card-role taxonomy

### 1.1 Role IDs

Every nonland card gets **one primary role** and **0–3 secondary roles**. Lands get `mana_land` or `utility_land`. Role floors (§1.5) count by `family`.

| Role ID | Family | Definition (the oracle text must do this job) | Sub-tags |
|---|---|---|---|
| `ramp` | ramp | Adds mana or puts lands onto the battlefield. | `land_ramp`, `mana_rock`, `mana_dork`, `cost_reducer`, `burst` (one-shot rituals and Treasure) |
| `card_advantage` | card_advantage | A net card gain, or repeatable selection. A cantrip only replaces itself, so it counts **0.5**. | `draw`, `impulse`, `engine_draw`, `selection` |
| `tutor` | (none) | Searches the library for a nonland card. | `creature`, `any`, `typed` |
| `targeted_removal` | targeted_interaction | Answers one opposing threat: destroy, exile, damage, edict, bounce, -X/-X or shuffle-away. | covers `creature`, `artifact`, `enchantment`, `planeswalker`, `any` |
| `counterspell` | targeted_interaction | Counters a spell. | `hard`, `soft`, `free` |
| `board_wipe` | board_wipe | Answers many permanents at once. | `creature`, `nonland`, `artifact`, `enchantment`; flag `one_sided` |
| `protection` | (none) | Protects the commander, key permanents or the player: hexproof, indestructible, phasing, protection-from, protective counters, fogs. | `commander`, `board`, `player` |
| `recursion` | (none) | Returns cards from your graveyard, or casts them from there. | `creature`, `any`, `self` |
| `wincon` | wincon | Ends the game: alt-win, mass drain, infect/poison, extra combat, mass pump, or a Spellbook combo piece. | `combo_piece`, `finisher`, `alt_win` |
| `engine` | (none) | Repeatable value whose trigger matches the commander or a pillar. | — |
| `synergy_piece` | (none) | Enabler or payoff for the commander's mechanic, or a tribal member. | `enabler`, `payoff`, `tribal`, `sac_outlet`, `token_maker` |
| `stax` | (flag role) | Taxes or locks: no-untap, "can't", cost increases, MLD. Always flagged (table-health). | `mld`, `tax`, `lock`, `hate` |
| `mana_land` | lands | A land with only mana abilities. | `basic`, `dual`, `fetch` |
| `utility_land` | lands | A land with a non-mana ability. | — |

### 1.2 Stage A: deterministic oracle-text rules (always run first)

Lowercase the oracle text and replace the card's own name with `~`. The patterns are Python-style regex and illustrative.

A rule hit is tagged `source: "rule"` and always wins over the model.

| Role/sub-tag | Pattern(s) | Extra condition |
|---|---|---|
| ramp/land_ramp | `search your library for .*(land\|forest\|plains\|island\|swamp\|mountain).*onto the battlefield`, `you may play an additional land`, `put .* land card .* onto the battlefield` | not a Land |
| ramp/mana_rock | `\{t\}(, [^:]*)?: add` | type has Artifact, not Land |
| ramp/mana_dork | `\{t\}(, [^:]*)?: add` | type has Creature |
| ramp/cost_reducer | `spells? you cast costs? \{\d\} less` | — |
| ramp/burst | `add \{[wubrgc]\}` on an instant or sorcery; `create (a\|an\|\w+) treasure` | — |
| card_advantage/draw | `draw (two\|three\|four\|five\|x\|that many) cards`, `draws? cards? equal to` | — |
| card_advantage/engine_draw | `whenever .*, (you may )?draw a card` | repeatable trigger |
| card_advantage (0.5, cantrip) | `draw a card` | instant or sorcery with no other draw match |
| card_advantage/impulse | `exile the top .* you may (play\|cast)` | — |
| tutor | `search your library for an? (card\|creature card\|instant\|sorcery\|artifact\|enchantment\|[a-z ]+ card)` | the search is not for a land |
| targeted_removal | `(destroy\|exile) target (creature\|artifact\|enchantment\|planeswalker\|permanent\|nonland permanent\|nonartifact)`; `deals? (\d+\|x) damage to (target creature\|any target)` (X or ≥2 damage); `target (player\|opponent) sacrifices`; `shuffles it into`; `target creature gets -\d+/-\d+`; `return target .* to (its\|their) owner's hand` (bounce counts 0.5) | — |
| counterspell | `counter target .*spell` | — |
| board_wipe | `(destroy\|exile) (all\|each) (creatures\|nonland permanents\|artifacts\|enchantments\|permanents\|other creatures)`, `deals? \d+ damage to each creature`, `(all\|each) creatures? gets? -\d+/-\d+`, `return all .* to their owners' hands` | set `one_sided` if it says `you don't control` or `your opponents control` |
| protection | `(gains?\|have\|has) (hexproof\|indestructible\|shroud\|protection from)`, `phases? out`, `prevent all combat damage`, `counter target spell .* targets .* you control` | the text targets your own things |
| recursion | `return .* from your graveyard to (your hand\|the battlefield)`, `(cast\|play) .* from your graveyard` | — |
| wincon/alt_win | `you win the game`, `(each\|target) opponent loses the game` | — |
| wincon/finisher | `each opponent loses (\d+\|x) life`, `creatures you control get \+\d+/\+\d+ .*trample`, `additional combat phase`, `infect`, `toxic`, `poison counter` | — |
| stax | `(don't\|doesn't) untap`, `(each player\|each opponent\|players\|your opponents) can't`, `costs? \{\d\} more`, `destroy all lands`, `players can't search` | always flagged |
| utility_land | a `{t}` or sacrifice ability that does not begin with `add` | type has Land |
| mana_land | no utility_land match | type has Land |

### 1.3 Stage B: Hugging Face classifier (fills gaps only)

**When it runs:**
- On cards that have no Stage A primary hit.
- To propose secondary roles.

**Model:** the env var `HF_ROLE_MODEL` picks it, typically a zero-shot NLI model such as `facebook/bart-large-mnli`. The evaluation service owns this choice.
- Candidate labels are the role definitions in §1.1.
- Hypothesis template: `"In Magic: The Gathering, this card's job is: {}."`

**Acceptance thresholds** (tunable heuristics):
- Primary role: accept only if the score is ≥ **0.60**.
- Secondary role: add only if the score is ≥ **0.75**.

**Limits:**
- A model label never replaces a Stage A primary.
- Model labels are tagged `source: "model"`, so advocates can see they are softer evidence.

**Optional H signal (§2.1):** the env var `HF_EMBED_MODEL` (e.g. `sentence-transformers/all-MiniLM-L6-v2`) embeds the oracle text.

### 1.4 Stage C: deck-context rules (what the card does *in this deck*)

Apply these in order:

1. **Journal.** If the card is named in `journal.wincons`, or matches a pillar's `match` predicate, add `wincon` or `engine`/`synergy_piece` with `source: "journal"`.
   - A predicate is any of `type_contains`, `subtype` or `oracle_regex`. Example: Meren's pillar "Recursion value" uses `type_contains: Creature`.
2. **Combo.** If Commander Spellbook lists a combo whose other pieces are all in the deck or the command zone, add `wincon/combo_piece`.
3. **Commander overlap.** Pull mechanic tokens from the commander's oracle text and type line:
   - Tokens: `dies`, `sacrifice`, `token`, `+1/+1 counter`, `proliferate`, `experience`, `graveyard`, `enters`, `attacks`, `cast`, plus creature subtypes.
   - If the card shares at least one token, add `synergy_piece`.
   - It can become primary if its EDHREC synergy is ≥ 0.30.
4. **Engine upgrade.** A repeatable `whenever` trigger whose condition matches a step-3 token is tagged `engine`, not `synergy_piece`.
5. **Primary role precedence:** journal role, then `wincon/combo_piece`, then `engine`, then the functional role with the highest need N (§2.3), then `synergy_piece`, then the rest. Break ties in favour of rule-sourced roles over model-sourced ones.

**Counting weights:**

| Role kind | Weight |
|---|---|
| Primary | 1.0 |
| Secondary | 0.5 |
| Cantrip or bounce | 0.5 |

A card counts at most once per family.

### 1.5 Role floors (99-card deck)

Each base floor is the **lower** of two references:
- The Command Zone template, ep. 658 (2025-02-18): https://www.youtube.com/watch?v=OSNV6224cHg
- EDHREC function means, from MtGDS, *The Deck of Many Things* (2022-07-05): https://edhrec.com/articles/the-deck-of-many-things-card-frequencies-by-type-and-function-across-edh

| Family | Base floor | Derivation |
|---|---|---|
| ramp | 10 | TCZ 10; EDHREC mean 12.7 |
| card_advantage | 7 | TCZ 12; EDHREC draw mean 7.6, rounded down to 7 |
| targeted_interaction | 10 | TCZ targeted disruption 12; EDHREC removal 8.5 + counters 2.4 = 10.9, rounded down to 10 |
| board_wipe | 2 | TCZ mass disruption 6; EDHREC sweepers 2.7, rounded down to 2 |
| wincon | 2 distinct lines | heuristic |
| tutor, recursion, protection, engine, synergy_piece | 0 (no floor) | no published floor |

**Floor modifiers** (apply every one that fits; a floor never goes below 0):

- **Commander does the job:** if the commander's own oracle text does a family's job repeatably, that floor drops by 3.
  - Example: Meren returns a creature card every end step, to hand or to the battlefield, so card_advantage goes from 7 to 4.
- **Go-wide, tokens or aggro thesis** (from the Journal, or the EDHREC top tag if the Journal is empty):
  - The board_wipe floor becomes 0.
  - Symmetric creature wipes also trigger anti-synergy A (§2.3).
- **Control thesis:** board_wipe becomes 4 and targeted_interaction becomes 12.

The floors are minimums, not goals. The thesis fills the rest of the deck (deckbuilding-craft §1).

---

## 2. Strength rubric (0–100)

```
Strength = 0.45·E + 0.55·D
```

- E is "what the format does with this card here".
- D is "what this card does for *this* deck".

Both are on a 0–100 scale. Deck context weighs more, per the creed: power serves the thesis.

### 2.1 E: EDHREC + model signal

| Component | Formula | Anchors | Kind |
|---|---|---|---|
| **I** inclusion | `p = (num_decks + 10) / (potential_decks + 100)`, `I = 100·√p` | 4%→20, 25%→50, 64%→80 | heuristic. The prior shrinks small samples toward 10%. The square root is concave because inclusion is right-skewed. |
| **S** synergy | `S = clamp(50 + 100·synergy, 0, 100)`. **Staple dampening:** if Scryfall `edhrec_rank ≤ 300`, use `S = clamp(50 + 50·synergy, 0, 100)` | −0.5→0, 0→50, +0.5→100 | EDHREC synergy is this commander's inclusion minus the color-identity inclusion. A staple's baseline is saturated, so its synergy is mostly noise: halve it, don't ignore it. |
| **R** global rank | `R = clamp(100·(1 − log10(rank)/log10(25000)), 0, 100)`, using Scryfall `edhrec_rank` | 1→100, 30→66, 300→43, 3000→21 | heuristic |
| **H** model fit (optional) | `cos` = cosine similarity between the card's embedding and the mean embedding of the commander's top 30 EDHREC cards with synergy ≥ 0.20. `H = clamp(100·(cos − 0.20)/0.60, 0, 100)` | uncalibrated, so its weight stays small | heuristic |

```
E = 0.40·I + 0.35·S + 0.15·R + 0.10·H        (without H: (0.40·I + 0.35·S + 0.15·R)/0.90)
```

EDHREC `lift` is shown to the user and goes into the packet, but it is **not scored**. It overlaps with synergy and inflates for niche cards.

### 2.2 Bands and the rubric prior

| Strength | Band | Lean |
|---|---|---|
| 80–100 | Core | keep |
| 65–79.9 | Solid | keep |
| 50–64.9 | Replaceable | weak keep |
| 35–49.9 | Weak | weak remove |
| 0–34.9 | Cut candidate | remove |

Rubric prior: `P_keep = 1/(1 + e^{−(Strength − 50)/8})`. Strength 34 gives 0.12; Strength 66 gives 0.88.

### 2.3 D: deck context

```
D = clamp(0.35·N + 0.35·P + 0.15·C + 0.15·K − A, 0, 100)
```

#### N: role need

For each of the card's families:
- `c'` = the role-weighted count **without** this card.
- `f` = the floor from §1.5.

| Condition | N_family |
|---|---|
| f = 0 | 25 |
| c'/f < 0.80 | 100 |
| 0.80 ≤ c'/f < 1.00 | 75 |
| 1.00 ≤ c'/f < 1.25 | 50 |
| c'/f ≥ 1.25 | 25 |
| wincon: the card is in one of ≤2 lines | 100 (3 lines: 75; more: 50) |

`N = max(N_primary, 0.75·max N_secondary)`

#### P: pillar fit

Use the first row that matches.

| P | Condition |
|---|---|
| 10 | **Harms a pillar** (any A > 0), or would be the third card doing a job outside every pillar (drift rule) |
| 100 | Named in the Journal (pillar card or wincon), or a combo piece in a Journal win line |
| 80 | Matches a pillar `match` predicate, or EDHREC synergy ≥ 0.30 |
| 60 | EDHREC synergy from 0.10 up to 0.30, or a commander-overlap token match (§1.4 step 3) |
| 40 | A generic staple role (ramp, draw, removal, wipe or protection) not tied to a pillar |

#### C: curve fit

C is computed from the **effective MV**:
- Normally, effective MV is `cmc`.
- With an alternative cost (evoke, dash, emerge), use that cost's MV.
- With a self-discount, use `cmc` minus the expected discount. The evaluation service supplies the discount; the default assumes 5 creatures on the battlefield.

The bands follow Karsten's curve lessons: most of the curve sits at MV 2–4, and cheap ramp is MV ≤ 2 (commander-math §4).

| Primary family | ≤2 | 3 | 4 | 5 | 6 | 7 | 8+ |
|---|---|---|---|---|---|---|---|
| ramp | 100 | 70 | 40 | 15 | 15 | 15 | 15 |
| card_advantage, targeted_interaction, board_wipe, protection, tutor | 100 | 85 | 65 | 45 | 30 | 30 | 30 |
| engine, synergy_piece, wincon, recursion, other | 100 | 100 | 100 | 100 | 85 | 70 | 55 |

#### K: color strain

K uses the Karsten 99-card table (commander-math §2):
1. Find the requirement for the hardest pip pattern at the card's MV. Examples: `2CC` needs 26 sources; a single `C` needs 19.
2. Compare it with the deck's sources for that color (lands plus Karsten adjustments).

| Result | K |
|---|---|
| Requirement met | 100 |
| Short by 3 or fewer | 70 |
| Short by more than 3 | 40 |
| Colorless card | 100 |

#### A: anti-synergy penalty

Each rule that fires adds 20, capped at 40. These are detected rules, not opinions.

| Rule | Detect |
|---|---|
| Symmetric creature wipe in a creature deck | `board_wipe` sub-tag creature, not `one_sided`, and either ≥ 25 deck creatures or a go-wide/token pillar |
| Graveyard hate in a graveyard deck | `exile (all\|each\|target player's) graveyard`, plus a recursion or graveyard pillar |
| Artifact or enchantment wipe in an artifact or enchantress deck | the wipe sub-tag matches a pillar's type predicate |
| Hoses the commander or a pillar | e.g. an ETB-stopper in an ETB deck, or "can't sacrifice" in an aristocrats deck |
| Symmetric discard or sacrifice where the deck loses more | `each player (discards\|sacrifices)`, and that hand or board is the pillar |

### 2.4 EvaluationResult.rubric

```json
{
  "strength": 34.1, "band": "cut_candidate", "p_keep_prior": 0.12,
  "E": {"value": 55.1, "I": 61.2, "S": 41.9, "R": 69.5, "H": null, "staple_dampened": true},
  "D": {"value": 17.0, "N": 25, "P": 10, "C": 65, "K": 100, "A": 20,
         "anti_synergy": ["symmetric creature wipe in go-wide deck"]},
  "data_flags": []
}
```

### 2.5 Land variant

`D = 0.5·Kc + 0.5·Pl − A`

- **Kc** is 100 if every color still meets its Karsten requirement without the land, and 40 otherwise.
- **Pl** depends on the land:

| Land | Pl |
|---|---|
| Utility land that matches a pillar | 80 |
| Plain dual | 40 |
| Basic, when the deck is at or above its land target (commander-math §3) | 20 |

### 2.6 Missing-data rules

| Missing | Handling | Confidence cap |
|---|---|---|
| Card not on the commander's EDHREC page | Drop I and S: `E = (0.15·R + 0.10·H)/0.25`, or `E = R` without H. Flag `edhrec_data_missing`. | 0.70 |
| Commander has no EDHREC page (404) | Same, for every card. Flag `edhrec_data_missing`. | 0.70 |
| No `edhrec_rank` | `R = 50`. | — |
| HF unavailable | Renormalise E. Unclassified cards become `synergy_piece` with `source: "fallback"`. | — |
| No price | Budget = 50, flag `price_unknown` | — |

---

## 3. The debate

### 3.1 Evidence packet

The evaluation step builds the packet. **Nothing outside it can be cited.** Every entry has an ID, and its numbers are pre-formatted strings, so models copy numbers and never compute them.

| ID | Content |
|---|---|
| `C1` | Card: name, mana cost, effective MV, type, oracle text, legality, game_changer |
| `C2` | Commander(s) and oracle text |
| `J1` | Journal thesis, or `"unconfirmed"` |
| `J2` | Pillars with their match predicates, and which ones the card matches |
| `J3` | Bracket target |
| `J4` | Journal wincons, and whether this card is one |
| `E1` | Inclusion, e.g. `"16,733 / 44,579 = 37.5% of Krenko, Mob Boss decks (EDHREC, 2026-10-16)"` |
| `E2` | Synergy, and whether staple dampening applied |
| `E3` | Lift (shown, not scored) |
| `E4` | Scryfall edhrec_rank |
| `E5` | Salt score with its year, or `"unverified"` |
| `H1` / `H2` | Model role labels with scores / H similarity (if run) |
| `R1` | Roles: primary/secondary, with source |
| `R2` | Family counts with and without the card vs. the floor, e.g. `"ramp: 9 → 8; floor 10"` |
| `M1` | Precomputed odds, e.g. `"P(≥1 ramp by turn 2, on the draw): 59.2% with card (K=9) vs 54.7% without (K=8); hypergeometric, 99-card library, no mulligans"` |
| `K1` | Effective MV vs. the deck average; C score |
| `K2` | Color requirement vs. sources (Karsten) |
| `A1..An` | Anti-synergy findings, one ID each |
| `X1..Xn` | Spellbook combo lines that include the card |
| `T1` | Table health: salt level, GC status, stax/MLD/extra-turn/theft flags, bracket impact |
| `S1` | Rubric breakdown: Strength, band, E, D and their components |
| `P1` | Physical or digital; owned copies; other decks that use the card |

### 3.2 Round protocol

- `rounds` is configurable: **2 (default) or 3**. The hard maximum is 3.
- **Round 1, openings.** Both advocates run in parallel, and neither sees the other. Each gives ≤ 3 claims.
- **Round 2, rebuttals.** Each side sees the other's Round 1 and gives ≤ 2 rebuttals plus ≤ 1 new claim. This is the final round when `rounds = 2`.
- **Round 3, closing** (only when `rounds = 3`). Each side gives one closing claim of ≤ 2 sentences, with no new evidence IDs.
- **Early stop.** If either side sets `"concede": true`, stop and go to the judge.
- **Claim limits.** Each claim is ≤ 60 words and must cite ≥ 1 ID. The judge discards uncited claims.
- **Models.** Advocates run at temperature ≤ 0.3 and the judge at 0. Both sides use the same model.

### 3.3 Advocate turn schema

```json
{"side":"remove","round":1,
 "claims":[{"id":"RM-1.1","tier":"spirit","text":"…","evidence":["A1","J2"]}],
 "rebuttals":[{"target":"KP-1.2","text":"…","evidence":["R2"]}],
 "concede":false}
```

- Claim IDs are `RM-<round>.<n>` (Remove) and `KP-<round>.<n>` (Keep).
- `tier` is one of `spirit`, `role_math`, `curve_mana`, `table` or `popularity`. The judge can re-tier a claim.

### 3.4 System prompts

The user message for each turn contains:
- the packet JSON;
- the transcript so far (empty in Round 1);
- `{{round}}/{{max_rounds}}`.

#### 3.4.1 Remove advocate

```text
You are the REMOVE advocate in a Commander (EDH) card review run by Commander Lab.
Card under review: {{card_name}}. Commander: {{commander}}. Deck: {{deck_name}} ({{deck_kind}}).
Round {{round}} of {{max_rounds}}.

Your job: make the strongest honest case that this card should leave this deck. You are not
trying to win at all costs. A judge will throw out any claim that breaks these rules.

WHAT COUNTS (strongest first):
1. spirit: the card works against the deck's thesis or a pillar (A*, J1, J2), or it is drift,
   doing a job no pillar asks for.
2. role_math: the card's family stays at or above its floor without it (R2), or the odds barely
   move (M1).
3. curve_mana: its effective MV is high for its role (K1), or its color requirement isn't met (K2).
4. table: the card pushes the deck past its bracket target (T1, J3). Salt alone is NOT a reason to
   cut. Salt is a flag, never a verdict.
5. popularity: low inclusion or synergy (E1, E2, E4). This only supports other reasons; it is never
   your only reason.

HARD RULES:
- Cite evidence ONLY by packet ID. Every claim needs at least one ID.
- Use ONLY numbers that appear verbatim in the packet. Do not compute, round, or estimate.
- Never mention prices, win rates, or cards that aren't in the packet.
- Never argue that a pet card should go. If a J-entry says "pet", concede immediately.
- Never argue for changing how the deck wins (e.g. "cut it for a combo").
- Do not propose replacements. That step comes later.
- If Keep's best claim beats yours in a higher tier and you can't rebut it with packet evidence,
  set "concede": true. Conceding honestly is a good outcome.

ROUND RULES:
- Round 1: up to 3 claims, strongest first.
- Round 2: up to 2 rebuttals of Keep's claims (by claim id) plus at most 1 new claim.
- Round 3: one closing claim of at most 2 sentences, with no new evidence IDs.
- Each claim is at most 60 words.

Output ONLY this JSON, with no prose before or after:
{"side":"remove","round":{{round}},"claims":[{"id":"RM-{{round}}.1","tier":"spirit|role_math|curve_mana|table|popularity","text":"...","evidence":["ID"]}],"rebuttals":[{"target":"KP-x.y","text":"...","evidence":["ID"]}],"concede":false}
```

#### 3.4.2 Keep advocate

```text
You are the KEEP advocate in a Commander (EDH) card review run by Commander Lab.
Card under review: {{card_name}}. Commander: {{commander}}. Deck: {{deck_name}} ({{deck_kind}}).
Round {{round}} of {{max_rounds}}.

Your job: make the strongest honest case that this card should stay. The status quo is not a
reason by itself; give the judge evidence. A judge will throw out any claim that breaks these rules.

WHAT COUNTS (strongest first):
1. spirit: the card serves the thesis or a pillar (J1, J2, J4), or it is a combo piece in a Journal
   win line (X*). A card that embodies how the deck wins or feels outranks a stronger generic card.
2. role_math: cutting it drops a family below its floor (R2), or measurably lowers the odds of
   having the role on time (M1). Overlap counts only if R1 shows it.
3. curve_mana: it is cheap for its role (K1), or it fixes or produces colors the deck needs (K2).
4. table: it keeps the deck within its bracket target, or it answers what the table plays (T1).
5. popularity: high inclusion or synergy (E1, E2, E4). This only supports other reasons;
   "everyone plays it" is never enough by itself.

HARD RULES:
- Cite evidence ONLY by packet ID. Every claim needs at least one ID.
- Use ONLY numbers that appear verbatim in the packet. Do not compute, round, or estimate.
- Never mention prices, win rates, or cards that aren't in the packet.
- Do not deny packet facts. If A* shows real anti-synergy, either argue that the card's value in a
  higher tier outweighs it, or concede.
- If Remove's best claim beats yours in a higher tier and you can't rebut it with packet evidence,
  set "concede": true.

ROUND RULES:
- Round 1: up to 3 claims, strongest first.
- Round 2: up to 2 rebuttals of Remove's claims (by claim id) plus at most 1 new claim.
- Round 3: one closing claim of at most 2 sentences, with no new evidence IDs.
- Each claim is at most 60 words.

Output ONLY this JSON, with no prose before or after:
{"side":"keep","round":{{round}},"claims":[{"id":"KP-{{round}}.1","tier":"spirit|role_math|curve_mana|table|popularity","text":"...","evidence":["ID"]}],"rebuttals":[{"target":"RM-x.y","text":"...","evidence":["ID"]}],"concede":false}
```

#### 3.4.3 Judge

```text
You are the JUDGE in a Commander (EDH) card review run by Commander Lab.
Card: {{card_name}}. Commander: {{commander}}. You receive the evidence packet and the full
debate transcript. Rule KEEP or REMOVE.

CREED: A deck's spirit outranks its power. When a stronger card would change what the deck is,
the weaker card that keeps the deck whole wins. EDHREC data is evidence, not a reason.

STEP 1: CLEAN THE RECORD. Discard any claim or rebuttal that:
- cites no evidence ID, or cites an ID that isn't in the packet;
- contains a number not found verbatim in the packet;
- uses salt as the reason to cut, unless T1 shows the card pushes the deck past J3's bracket target;
- argues to change the win line, or to cut a pet card.
If a claim's only support is popularity (only E*/H* IDs), downgrade it to tier "popularity".
List every discarded item with a short reason.

STEP 2: RE-TIER each surviving claim by the evidence it cites, highest tier first:
  spirit (J*, A*, X* tied to J4) > role_math (R*, M*) > curve_mana (K*) > table (T*) > popularity (E*, H*, S1)

STEP 3: DECIDE, working down from the top tier:
- Find the highest tier where either side has an unrebutted claim. That side wins.
- A claim counts as "rebutted" only if the other side cited packet evidence that contradicts it.
- If both sides have unrebutted claims at the same tier, compare one tier lower.
- If every tier ties, follow the rubric: S1 strength >= 50 means keep, below 50 means remove.
- A concession ends the debate in favour of the other side.

STEP 4: CONFIDENCE. Give judge_confidence in [0.50, 0.95]:
- 0.85-0.95: the win is at tier spirit or role_math, unrebutted, and S1's band agrees.
- 0.70-0.84: the win is at spirit or role_math with a partial rebuttal, or the band mildly disagrees.
- 0.50-0.69: the decision came at curve_mana or lower, by tie-break, or against S1's band.
Code combines this with the rubric prior and applies caps. Do not do that math yourself.

STEP 5: KEY REASONS. Give 2-4 one-sentence reasons, strongest first. Each ends with its cited IDs
in brackets, e.g. "Kills the deck's own goblin swarm, which is pillar 1. [A1, J2]". Use only
packet numbers.

STEP 6: REPLACEMENT BRIEF (only if the verdict is remove). Choose the mode:
- "like_for_like": the card's family falls below its floor without it (R2), or the only problem
  was cost or curve.
- "function_change": the family stays >= 1.25x its floor without the card AND another family is
  below its floor, OR the card's job itself works against a pillar (A*).
- "mixed": any other case.
List target_roles (role IDs from R1/R2) and an "avoid" list (e.g. "symmetric creature wipes").

Output ONLY this JSON:
{"verdict":"keep|remove","judge_confidence":0.0,"key_reasons":["... [IDs]"],
 "deciding_tier":"spirit|role_math|curve_mana|table|popularity|rubric_tiebreak",
 "winning_claims":["RM-1.1"],"discarded":[{"id":"KP-1.3","why":"..."}],
 "replacement_brief":{"mode":"like_for_like|function_change|mixed","target_roles":["..."],"avoid":["..."]} | null}
```

### 3.5 Code-side validation of the judge's output

1. **Parse the JSON.** If it's invalid, retry once with the instruction "Return valid JSON only".
2. **Fallback on a second failure.** Return a fallback verdict instead:
   - `verdict` follows the rubric lean;
   - `confidence` = min(P of that verdict, 0.60);
   - `key_reasons` = the 3 largest S1 components, written as text;
   - `fallback: true`.
3. **Check every key reason.** Each must contain at least one valid ID. Strip any sentence with a number that isn't in the packet (the deck-review rule). If fewer than 2 reasons survive, use the fallback verdict.
4. **Enforce like-for-like when a floor would break.** If the verdict is remove and the card's primary family would fall below its floor, force the mode to `like_for_like`, or `mixed` with at least 1 like-for-like.
5. **Check claim references.** Every claim in `winning_claims` must exist in the transcript.

### 3.6 Final confidence (deterministic, in code)

```
prior = P_keep if verdict == keep else 1 − P_keep
conf  = round(0.5·prior + 0.5·judge_confidence, 2)
caps:  EDHREC data missing ≤ 0.70 · journal unconfirmed ≤ 0.65 · fallback ≤ 0.60 · floor 0.50
```

If the judge overturns the rubric, the prior pulls confidence down automatically. That is intended.

### 3.7 Verdict object (API)

`verdict`, `confidence` and `key_reasons` are the required core. Every other field is required whenever it applies.

```json
{"verdict":"keep|remove","confidence":0.84,"key_reasons":["…[IDs]"],
 "provisional":false,"deciding_tier":"spirit","fallback":false,
 "rubric":{"…§2.4…":""},"roles":{"primary":"board_wipe","secondary":[],"sources":{"board_wipe":"rule"}},
 "transcript":[{"side":"remove","round":1,"claims":[]}],"discarded":[],
 "replacement_brief":{"mode":"function_change","target_roles":["targeted_removal"],"avoid":[]},
 "replacements":["…§4.6…"],"flags":[],"data_as_of":{"edhrec":"2026-10-16","scryfall":"2026-10-16"}}
```

---

## 4. Replacement heuristics (only when the verdict is remove)

### 4.1 Hard filters (enforced in code and tested)

Drop a candidate if any of these hold:

1. **Color identity.** `color_identity ⊄ commander_identity`. Scryfall's `color_identity` already counts symbols in rules text (CR 903.4). Hybrid symbols count as both colors; the Feb 2026 bracket update shelved the proposed change.
2. **Legality.** `legalities.commander != "legal"`.
3. **Duplicates.** Its `oracle_id` is already in the deck or the command zone, or it is the reviewed card. Basic lands and "A deck can have any number of cards named…" cards are exempt.
4. **Paper only.** The deck is physical and `"paper" ∉ games`.
5. **Protected donor cards.** It is a pet or pillar card in a donor deck. Such a card can still appear as a *buy*, but never as a *steal*.
6. **Spirit gate.** It fills no `target_role` AND its PillarFit is < 60. This stops drift.
7. **Budget cap.** `budget_cap_per_card` is set, the price exceeds it, and the card isn't owned.

### 4.2 Like-for-like vs. function change

- `like_for_like` = the candidate's primary role equals the reviewed card's primary role.
- `same_card_type` is reported separately (e.g. creature for creature).

| Mode | When (judge §3.4.3, step 6) | Composition of the 3–5 results |
|---|---|---|
| `like_for_like` | The family drops below its floor, or the only problem was curve or cost | All like-for-like. Prefer a lower effective MV. |
| `function_change` | The family has slack (≥ 1.25× its floor) and another family is below its floor, OR the job itself fights a pillar | All function-change, aimed at the most deficient family: max `(f − c)/f`. |
| `mixed` | Any other case | ≥ 1 of each. Default split is 3 like-for-like / 2 function-change, filled by score. |

**Adjacent swaps.** When the job hurts a pillar, keep the *intent* and change the *shape*. These are preferred within function-change:
- `board_wipe` → `targeted_removal`
- `counterspell` ↔ `targeted_removal`

A ramp swap that moves from rock to dork or land ramp stays like-for-like (same role), in the shape a pillar prefers.

### 4.3 Candidate pool

1. Start with the evaluation pool: EDHREC cardviews for the commander that share `target_roles`, plus high-synergy cards in other roles.
2. If fewer than 8 candidates survive §4.1, widen with Scryfall: `otag:<tag> id<=<ci> f:commander game:paper order:edhrec`.
   - `otag:ramp`, `otag:removal` and `otag:draw` are verified. Verify any other otag before using it.
   - Classify widened candidates with §1.
3. Always add owned free agents from inventory that pass §4.1 and fill a target role.

### 4.4 Ranking score

```
Rep = 0.30·RoleFit + 0.25·PillarFit + 0.15·E_c + 0.10·CurveFit + 0.10·Own + 0.10·Budget − Penalties
```

| Term | Definition |
|---|---|
| RoleFit | 100 if the primary role is in `target_roles`; 70 if a secondary role is; 0 otherwise. +10 (cap 100) if it also fills another family below its floor, as shown in R1. |
| PillarFit | The P scale (§2.3), computed for the candidate. |
| E_c | The candidate's E (§2.1) for this commander. |
| CurveFit | The C scale (§2.3) for the candidate's role and effective MV. |
| Own | 100 for a free agent; 50 for a steal (name the donor); 0 for a buy. |
| Budget | Owned: 100. Otherwise use the cheapest non-null Scryfall `prices.usd`, dated: ≤$1 100 · ≤$3 85 · ≤$5 70 · ≤$10 50 · ≤$25 30 · ≤$50 15 · >$50 0 · unknown 50 (flag). |

**Penalties.** These are always flagged and never hidden. None of them is a filter (flag, never forbid).

| Condition | Penalty | Flag |
|---|---|---|
| Game Changer when the deck is at or over its target's GC allowance (B1–2: 0; B3: 3) | −25 | `adds-gc (n→n+1)` |
| Salt ≥ 2.0 | −15 at ≤ B3; −5 at B4+ | `salt: high <score> (EDHREC <year>)` |
| Salt 1.5–2.0 | −5 | `salt: medium …` |
| Stax, MLD, extra turns, theft, or long solo turns | −15 at ≤ B3; 0 at B4+ | the table-health reason line |
| Completes a two-card infinite when the thesis isn't combo | −20 | `win-line change: <Spellbook id>` |
| Color strain (the candidate's K would be 40) | −10 | `color strain: need <n>, have <m>` |
| Average MV rises by more than 0.15 | −5 | `curve +<x>` |

**Output rules:**
- **Diversity.** At most 2 results may share a sub-tag. Dedupe by `oracle_id`.
- **Short pool.** If fewer than 3 candidates pass, return what passes with `pool_exhausted: true`.
- **Steals.** A steal includes `donor_deck` and the note "Donor needs a replacement: run Review on <donor>".
- **Physical decks.** The action becomes "Add to want/buy list"; an owned free agent shows "Pull from binder".

### 4.5 Own-first order

Free agents rank above steals, and steals above buys. If a free agent scores within **10** Rep of the top buy, list the free agent first and state the gap in its rationale (deckbuilding-craft §5).

### 4.6 Suggestion object

```json
{"card":"Gempalm Incinerator","oracle_id":"467ea3e3-7767-405a-9d1a-8fa5de696376",
 "role":"targeted_removal","like_for_like":false,"same_card_type":false,
 "rationale":"Goblin that kills a creature for X = goblins on the battlefield; cycles when you need gas. Serves pillar 'Goblin swarm'.",
 "score":77.5,"score_breakdown":{"RoleFit":100,"PillarFit":80,"E_c":60.1,"CurveFit":85,"Own":0,"Budget":100,"penalties":[]},
 "tier":"buy","donor_deck":null,"price_usd":0.24,"price_date":"2026-10-16",
 "game_changer":false,"bracket_impact":"none","salt_flag":{"level":"low","reason":"…"},"flags":[]}
```

- `bracket_impact` takes one of: `none`, `adds-gc (n→n+1)`, `raises-floor-to-B<k>`, `lowers`, `unverified`.
- The rationale is ≤ 200 characters, names the pillar or floor it serves, and states every flag in plain words.

---

## 5. Config block (single source of tunables)

```yaml
review:
  strength_weights: {E: 0.45, D: 0.55}
  E_weights: {I: 0.40, S: 0.35, R: 0.15, H: 0.10}
  D_weights: {N: 0.35, P: 0.35, C: 0.15, K: 0.15}
  inclusion_prior: {p0: 0.10, alpha: 100}
  staple_rank_cutoff: 300
  rank_log_base: 25000
  hf: {role_accept: 0.60, secondary_accept: 0.75, sim_lo: 0.20, sim_span: 0.60}
  anti_synergy: {per_rule: 20, cap: 40}
  prior_logistic: {center: 50, scale: 8}
  confidence_caps: {edhrec_missing: 0.70, journal_unconfirmed: 0.65, fallback: 0.60}
  floors: {ramp: 10, card_advantage: 7, targeted_interaction: 10, board_wipe: 2, wincon_lines: 2}
  floor_modifiers: {commander_does_job: -3, go_wide_wipe_floor: 0, control_wipe_floor: 4, control_targeted_floor: 12}
  debate: {rounds: 2, max_rounds: 3, claims_r1: 3, rebuttals_r2: 2, new_claims_r2: 1, claim_words: 60,
           advocate_temperature: 0.3, judge_temperature: 0}
  replacements:
    min: 3
    max: 5
    mixed_split: [3, 2]
    free_agent_margin: 10
    rep_weights: {RoleFit: 0.30, PillarFit: 0.25, E: 0.15, Curve: 0.10, Own: 0.10, Budget: 0.10}
    budget_bands: [[1,100],[3,85],[5,70],[10,50],[25,30],[50,15]]
    over_50: 0
    unknown_price: 50
    penalties: {gc_over_target: 25, salt_high_low_bracket: 15, salt_high_b4: 5, salt_medium: 5,
                feelbad_low_bracket: 15, win_line_change: 20, color_strain: 10, curve_drift: 5}
```

---

## 6. Worked examples (real data, illustrative decks)

**Data:**
- EDHREC commander JSON and Scryfall `/cards/named`, both fetched **2026-10-16**.
- EDHREC deck counts: Krenko, Mob Boss 44,579; Meren of Clan Nel Toth 22,431; Atraxa, Praetors' Voice 45,629.

**Assumptions:**
- H was not computed, so E uses the renormalised form.
- **Deck counts and Journals are illustrative.**
- All candidates are assumed to be buys (Own = 0).

### 6.1 Krenko, Mob Boss: Blasphemous Act → REMOVE (function change)

**Card facts.**
- Blasphemous Act, {8}{R} sorcery: "costs {1} less to cast for each creature on the battlefield… deals 13 damage to each creature." Not a GC; edhrec_rank 22; $0.86.
- Krenko: "{T}: Create X 1/1 red Goblin creature tokens, where X is the number of Goblins you control."

**Illustrative deck.**
- Journal: "Goblin swarm: Krenko builds a horde, sac/burn outlets finish".
- Pillars: "Goblin swarm" (`subtype: Goblin`) and "Sac/burn finish".
- B3; 33 creatures; targeted_interaction 5; board_wipe 1 (this card).

**Roles.** Primary `board_wipe` (rule), creature sub-tag, not one-sided.

**E.**
- I: p = 16,743/44,679 = 0.3747, so I = 100·√0.3747 = **61.2**.
- S: synergy −0.162, and rank 22 is dampened: 50 − 8.1 = **41.9**.
- R: 100·(1 − 1.342/4.398) = **69.5**.
- E = (24.48 + 14.67 + 10.43)/0.90 = **55.1**.

**D.**
- N = 25: the go-wide modifier sets the board_wipe floor to 0.
- A = 20: a symmetric creature wipe with 33 creatures and a swarm pillar. That sets P = 10.
- C = 65: effective MV is 4 (9 − 5).
- K = 100: one red pip, needs 19 sources, the deck has about 30 Mountains.
- D = 8.75 + 3.5 + 9.75 + 15 − 20 = **17.0**.

**Strength** = 0.45·55.1 + 0.55·17.0 = **34.1**, which is **Cut candidate**. P_keep = **0.12**.

**Debate (abridged).**
- RM-1.1 [spirit]: "13 damage to each creature wipes the horde and Krenko; it attacks pillar 1" [A1, J2, C1].
- KP-1.1 [role_math]: "It's the only mass answer" [R2].
- KP-1.2 [popularity]: "37.5% of Krenko decks run it" [E1].
- RM-2.1 rebuts KP-1.1: "The board_wipe floor is 0 for this thesis" [R2].
- KP-2.1: "Krenko rebuilds faster". Discarded: no ID, and not in the packet.

**Judge.**
- `deciding_tier: spirit`; judge_confidence 0.80.
- **Final confidence = 0.5·0.88 + 0.5·0.80 = 0.84.**
- Key reasons:
  - "Deals 13 damage to each creature, killing the goblin swarm the deck is built on. [A1, J2]"
  - "With a go-wide thesis the board_wipe floor is 0, so cutting it leaves no role gap. [R2]"
  - "Negative synergy for Krenko (−0.162) backs this up. [E2]"
- Brief: `function_change`. targeted_interaction is 5 against a floor of 10, and the card's job attacks a pillar. Target `targeted_removal`; avoid symmetric creature wipes.

**Replacements.**

| # | Card | Oracle | EDHREC (Krenko) | E_c | RoleFit | Pillar | Curve | Budget | **Rep** |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Gempalm Incinerator | Goblin; cycling {1}{R}: X damage to target creature, X = Goblins on battlefield | 15,896 = 35.7%, syn +0.303, rank 6205 | 60.1 | 100 | 80 | 85 | 100 ($0.24) | **77.5** |
| 2 | Goblin Cratermaker | Goblin; {1}, sac: 2 damage to target creature, or destroy target colorless nonland permanent | 7,446 = 16.7%, syn +0.137, rank 6901 | 45.1 | 100 | 80 | 100 | 100 ($0.27) | **76.8** |
| 3 | Chaos Warp | Instant; owner shuffles target permanent into library | 23,187 = 52.0%, syn −0.089, rank 29 | 60.9 | 100 | 40 | 85 | 50 (`price_unknown`: default print null) | **62.6** |

Worked score for #1: 30 + 20 + 9.0 + 8.5 + 0 + 10 = 77.5.

All three are `like_for_like: false`, have bracket impact `none` and are not GCs. Salt is `unverified`.

The two goblins outrank the far more popular Chaos Warp because they *are* the swarm. That is the creed in action.

### 6.2 Meren of Clan Nel Toth: Commander's Sphere → REMOVE (mixed)

**Card facts.**
- Commander's Sphere, {3} artifact: "{T}: Add one mana of any color in your commander's color identity. Sacrifice this artifact: Draw a card." edhrec_rank 54; $0.30.

**Illustrative deck.**
- Journal: "Sacrifice creatures, Meren brings them back".
- Pillars: "Recursion value" (`type_contains: Creature`) and "Death triggers".
- B3; ramp 12 (including this card); card_advantage 8.
- The card_advantage floor is 4 (7 − 3, because Meren does the job).
- targeted_interaction 7; 27 black sources. Sakura-Tribe Elder is already in the deck.

**Roles.** Primary `ramp/mana_rock`; secondary `card_advantage` (sacrifice to draw, weight 0.5).

**E.**
- I: 1,157/22,531 = 0.0514, so I = **22.7**.
- S: synergy −0.008, dampened: **49.6**.
- R: rank 54 gives **60.6**.
- E = (9.08 + 17.36 + 9.09)/0.90 = **39.5**.

**D.**
- N = 50: ramp without the card is 11, and 11/10 = 1.10. The secondary family gives 25 × 0.75 = 18.75, which is lower.
- P = 40: an artifact that matches no pillar.
- C = 70, K = 100, A = 0.
- D = 17.5 + 14 + 10.5 + 15 = **57.0**.

**Strength** = 17.8 + 31.35 = **49.1**, which is **Weak**. P_keep = **0.47**.

**Debate (abridged).**
- RM-1.1 [spirit]: "A noncreature in a creature-recursion deck; Meren can't return it" [J2, C1].
- RM-1.2 [role_math]: "Ramp stays at 11 vs floor 10" [R2].
- KP-1.1: "Ramp drops 12 → 11" [R2]. Rebutted by RM-2.1 [R2].
- KP-1.2: "Rank 54 staple" [E4]. This is popularity-tier only.

**Judge.**
- Spirit tier: RM-1.1 shows neutrality, not harm (A is empty, P = 40), so spirit ties. This is the same reasoning as §6.3.
- Role_math tier: RM-1.2 is unrebutted, while KP-1.1 was rebutted. `deciding_tier: role_math`; judge_confidence 0.70.
- **Final confidence = 0.5·0.53 + 0.5·0.70 = 0.61.**
- Key reasons:
  - "Ramp stays at 11 against a floor of 10 without it. [R2]"
  - "An artifact that serves neither pillar, and Meren can't recur it. [J2, C1]"
- Brief: `mixed`. Ramp stays above its floor but below 1.25×; interaction is 7/10. Targets `ramp` and `targeted_removal`.

**Replacements.** Sakura-Tribe Elder is dropped by rule 4.1.3 (already in the deck).

| # | Card | Oracle | EDHREC (Meren) | E_c | Role / L4L | Pillar | Curve | Budget | **Rep** |
|---|---|---|---|---|---|---|---|---|---|
| 1 | Shriekmaw | Evoke {1}{B}; ETB destroy target nonartifact, nonblack creature | 7,729 = 34.5%, syn +0.314, rank 1602 | 62.2 | targeted_removal / false | 80 | 100 (eff. MV 2) | 100 ($0.28) | **79.3** |
| 2 | Ravenous Chupacabra | {2}{B}{B}; ETB destroy target creature an opponent controls | 8,523 = 38.0%, syn +0.348, rank 1144 | 65.4 | targeted_removal / false | 80 | 65 | 100 ($0.97) | **76.3** |
| 3 | Wood Elves | {2}{G}; ETB put a Forest card onto the battlefield | 7,628 = 34.0%, syn +0.266, rank 678 | 61.6 | ramp / **true** | 80 (creature) | 70 | 100 ($0.28) | **76.2** |

- Mixed mode is satisfied: there is at least 1 like-for-like.
- After taking Shriekmaw, ramp is 11 (≥ 10 ✔) and interaction rises to 8.
- Chupacabra's 2CC needs 26 sources; the deck has 27 ✔.
- None of these are GCs; bracket impact `none`.

### 6.3 Atraxa, Praetors' Voice: Birds of Paradise → KEEP

**Card facts.**
- Birds of Paradise, {G} 0/1 flyer: "{T}: Add one mana of any color." edhrec_rank 35; $7.67.
- Atraxa costs {G}{W}{U}{B}.

**Illustrative deck.**
- Journal: "Proliferate +1/+1 and loyalty counters".
- Pillar: "Proliferate engines" (`oracle_regex: proliferate`).
- B3; ramp 9 (including Birds); 20 sources of each color.

**E.**
- I: 14,654/45,729 = 0.3205, so I = **56.6**.
- S: synergy **−0.127**, dampened: **43.6**.
- R: rank 35 gives **64.9**.
- E = **52.9**.

Read synergy alone and Birds looks like a cut. Staple dampening is why it doesn't sink: Birds is everywhere in green, so its baseline is saturated.

**D.**
- N = 75: ramp without Birds is 8, and 8/10 = 0.80.
- P = 40, C = 100, K = 100, A = 0.
- D = **70.3**.

**Strength** = 23.8 + 38.6 = **62.5**, which is **Replaceable**. P_keep = **0.83**.

**M1: on-time ramp odds** (hypergeometric; 9 cards seen by turn 2 on the draw; no mulligans):
- With Birds (K = 9): 1 − Π(90−i)/(99−i) = **59.2%**.
- Without Birds (K = 8): **54.7%**.
- Method check: the same calculation reproduces commander-math's K=10 cell, 63.3%.

**K2: color sources.** A dork counts as ½ source of each color it makes, for MV 2+ spells (Karsten). Atraxa needs 16 sources per color (the 3C row), plus 1 for the gold rule, so 17 each. Birds adds 0.5 to each of the four colors.

**Debate (abridged).**
- RM-1.1 [popularity]: "Negative synergy" [E2].
- RM-1.2 [spirit]: "Matches no pillar" [J2].
- KP-1.1 [role_math]: "Ramp 9 → 8 vs floor 10" [R2].
- KP-1.2 [role_math]: "59.2% → 54.7%" [M1].
- KP-1.3 [curve_mana]: "½ source to all four colors" [K2].
- KP-2.1 rebuts RM-1.2: "Not matching a pillar isn't harming one; A is empty" [S1].

**Judge.**
- Spirit tier: there is no harm, only neutrality, so it ties.
- Role_math tier: Keep is unrebutted. `deciding_tier: role_math`; judge_confidence 0.70.
- **Final confidence = 0.5·0.83 + 0.5·0.70 = 0.76. KEEP.**
- No replacements.

---

## 7. Test checklist (t_c17c813d, mocked LLM)

1. Pet card → keep, confidence 1.0, zero LLM calls.
2. Banned card, or a card outside the color identity → remove at 0.99, no debate, like-for-like brief.
3. Krenko fixture → Strength 34.1 ± 0.1 (cut_candidate).
4. Atraxa fixture → Strength 62.5 ± 0.1.
5. Mock keep → no replacements. Mock remove → 3–5 replacements.
6. Off-identity, banned, in-deck, and digital-only (physical deck) candidates never appear.
7. Mixed mode → at least one `like_for_like` true and at least one false.
8. Invalid judge JSON twice → `fallback: true`, confidence ≤ 0.60.
9. A key reason containing a number not in the packet is stripped.
10. Journal unconfirmed + remove → `provisional: true`, confidence ≤ 0.65.
11. EDHREC card missing → E = R, confidence ≤ 0.70, flag set.
12. A GC candidate for a B2 deck is still listed, with −25 and the `adds-gc` flag.

---

## 8. Sources

- **EDHREC** commander JSON, fetched 2026-10-16: `krenko-mob-boss`, `meren-of-clan-nel-toth`, `atraxa-praetors-voice`.
- **Scryfall** `/cards/named`, fetched 2026-10-16: Blasphemous Act, Gempalm Incinerator, Goblin Cratermaker, Chaos Warp, Commander's Sphere, Wood Elves, Sakura-Tribe Elder, Shriekmaw, Ravenous Chupacabra, Birds of Paradise, Krenko, Meren, Atraxa.
- **The Command Zone** ep. 658 (2025-02-18).
- **MtGDS**, *The Deck of Many Things* (EDHREC, 2022-07-05).
- **Frank Karsten**, sources (2022 update, rev. 2025-02-13) and optimal-curve articles (TCGplayer). Summarised in commander-math.
- **Comprehensive Rules** 903.4 and 903.5c (CR effective 2026-09-25).
- **WotC brackets and Game Changers**: the table-health brackets.md summary (2026-10-12) and the Feb 9 2026 update.
- **EDHREC salt thresholds** per table-health: high ≥ 2.0, medium 1.5–2.0.
