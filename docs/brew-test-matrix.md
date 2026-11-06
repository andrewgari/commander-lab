# Brew verification: commander test matrix (brew-test-matrix-v1)

Oracle, 2026-11-06. This is the input for card t_bde5b9f5 (suite) and t_f965a9e5 (verification).
It builds on brew-domain-v1 (t_e6929d24), which defines the V1-V12 validator, the brackets and the power bands.
It also targets the PR #457 review findings from t_d461913b: F2 (rules-text identity), F3 (hybrid list form), F4 (partial partner identity) and F10 (colourless Wastes).

## Sources checked today (2026-11-06)
- **Comprehensive Rules.** I used the new file `MagicCompRules 20261106.txt`, linked from magic.wizards.com/en/rules, which replaces the 2026-09-25 file. Sections 903.3-903.5f, 903.11a, 702.124a-n, 702.139a-d and 201.3b have the same wording as before.
- **Scryfall `/cards/collection` and `/cards/search`.** Every card named below was checked for `color_identity`, `legalities.commander` and `game_changer`.
  - `banned:commander` returns 83 cards, the same as on 2026-11-05.
  - `is:gamechanger` returns 53 cards.
  - `o:"a deck can have"` returns 13 cards.
- **Commander Spellbook `/variants`.** I checked the combo lines and their bracket tags. R means Ruthless, S Spicy, P Powerful, O Oddball, E Exhibition, and B means it contains a banned card.
- **WotC, 2026-02-09:**
  - Biorhythm was unbanned and became a Game Changer.
  - Lutri was unbanned but stays banned as a companion, and it is not a Game Changer.
  - Farewell joined the Game Changers.
  - The hybrid rule is unchanged: hybrid counts as both colors.

Tests should compare identities as **sets**. Scryfall returns colors in WUBRG-sorted letters, such as `BGR`.

---

## 1. Summary table

The suite should cover rows 1-7. Rows 8-9 are optional extras.

| # | Commander(s) | Type covered | Exact CI | Key edge cases | Low / Mid / High (bracket) |
|---|---|---|---|---|---|
| 1 | **Krenko, Mob Boss** | mono-color | `{R}` | Off-color activations (Orcish Lumberjack); Phyrexian (Gut Shot); hybrid R/G excluded; Seven Dwarves ×≤7 | B2 goblin tokens / B3 + Bombardment and Matron / B4 + Kiki + Conscripts |
| 2 | **Meren of Clan Nel Toth** | 2-color | `{B,G}` | Damn (overload W); Deathrite (B/G) allowed; Dismember and Birthing Pod (Phyrexian) allowed; Redcap (B/R) excluded | B2 aristocrats / B3 + altars and Grim Tutor / B4 + Necro and DT, extra-turn loop flagged |
| 3 | **Korvold, Fae-Cursed King** | 3-color | `{B,R,G}` | Rakdos Cackler and Manamorphose allowed; Kitchen Finks (G/W) excluded; Spellskite (U/P in text) excluded; Dockside banned | B2 treasure sac / B3 + Animation Module lines / B4 + Food Chain + Eternal Scourge |
| 4 | **Kozilek, the Great Distortion** | colorless | `{}` (empty) | Wastes only; no Plains; Eldrazi Displacer (devoid, W) excluded; Iron Myr, Spellskite and Haywire Mite excluded; Command Tower legal but makes no mana | B2 Eldrazi ramp / B3 + Karn and One Ring / B4 + Basalt + Rings or Monument |
| 5 | **Thrasios, Triton Hero + Tymna the Weaver** | partner pair | `{W,U,B,G}` | Union of two identities (F4); Redcap and Manamorphose excluded; Leovold, Prophet and Nadu fit the CI but are **banned** | B2 midrange value / B3 + 3 GCs / B4 Thoracle + Consultation or Pact |
| 6 | **Wort, the Raidmother** | hybrid in cost | `{R,G}` | Hybrid `{R/G}` counts as both (F3); Kitchen Finks and Figure of Destiny excluded; Ram-Gang allowed; Mountain and Forest only; Brain Freeze (U) excluded | B2 conspire tokens / B3 + Storm-Kiln / B4 + Breach, LED, Kiki |
| 7 | **Alesha, Who Smiles at Death** | hybrid in **rules text** | `{W,B,R}` | Cost is `{2}{R}`, but `{W/B}` in the text adds W and B (F2); Swords allowed; Kitchen Finks and Deathrite excluded; Dockside combo lines are banned | B2 recursion / B3 + Karmic Guide loops / B4 + Master of Cruelties, MLD flagged |
| 8 | *(optional)* **Bosh, Iron Golem** | rules-text symbol on colorless cost | `{R}` | Cost `{8}`, text `{3}{R}`: Lightning Bolt and Mountain must be allowed | - |
| 9 | *(optional)* **Wilson, Refined Grizzly + Raised by Giants** | Background pair | `{G}` | Background is legal only as a second commander | - |

---

## 2. Per-commander detail

Every card below was checked on Scryfall on 2026-11-06. GC means the card is on the Game Changers list. Banned cards are excluded at **every** power level.

### 2.1 Krenko, Mob Boss: mono-red, CI `{R}`
Krenko costs `{2}{R}{R}`.

**Color-identity assertions**

| Card | Why it's tricky | Expected |
|---|---|---|
| Orcish Lumberjack | Costs `{R}`, but its text adds `{G}`, so CI = RG | **EXCLUDE** |
| Etali, Primal Conqueror | Red DFC, CI = GR | **EXCLUDE** |
| Boggart Ram-Gang | Cost `{R/G}×3`; hybrid counts as both colors, so CI = RG | **EXCLUDE** |
| Gut Shot | Phyrexian `{R/P}`, CI = R | **ALLOW** |
| Iron Myr | Colorless artifact, but its text adds `{R}`, so CI = R | **ALLOW** |
| Seven Dwarves | Up to 7 copies are legal; 8 are illegal | ALLOW ≤7 |

**Banned traps:** Dockside Extortionist (R, the classic goblin include), Falling Star (R), Mox Ruby, Jeweled Lotus and Mana Crypt.

**Power tiers**

| Tier | Looks like |
|---|---|
| Low (B2) | Precon-plus goblin tokens: Goblin Warchief, Goblin Chieftain, Impact Tremors, Purphoros, Coat of Arms, Skullclamp, Sol Ring. GC = 0. No 2-card combo. Tutors: at most Goblin Matron. Average nonland MV is about 3.2-3.6. |
| Mid (B3) | Adds Goblin Bombardment, Goblin Recruiter, Muxus, Lightning Greaves and Wheel of Fortune, plus at most 3 GCs from Gamble, Jeska's Will and Underworld Breach. The 3-card loop Krenko + Skirk Prospector + Goblin Warchief (Spellbook S) is allowed. Average MV is about 3.0. |
| High (B4) | Kiki-Jiki + Zealous Conscripts (2-card, **R**). Krenko + Thornbite Staff + Goblin Bombardment (E). Fast mana: Mana Vault, Grim Monolith, Chrome Mox, LED, Ancient Tomb, Lotus Petal. Tutors: Gamble, Goblin Matron, Goblin Recruiter. Average MV ≤3.0. |

### 2.2 Meren of Clan Nel Toth: Golgari, CI `{B,G}`

| Card | Why it's tricky | Expected |
|---|---|---|
| Damn | Costs `{B}{B}`, but its overload cost `{2}{W}{W}` makes CI = BW | **EXCLUDE** |
| Murderous Redcap | Hybrid `{B/R}`, CI = BR | **EXCLUDE** |
| Kitchen Finks | Hybrid `{G/W}`, CI = GW | **EXCLUDE** |
| Deathrite Shaman | Hybrid `{B/G}`, CI = BG | **ALLOW** |
| Dismember / Birthing Pod | Phyrexian `{B/P}` and `{G/P}` | **ALLOW** |
| Elves of Deep Shadow | Costs `{G}`, text adds `{B}`, so CI = BG | **ALLOW** |

**Banned traps:** Recurring Nightmare (the reanimator pick), Sylvan Primordial, Primeval Titan, Rofellos, Fastbond, Channel, Griselbrand, Yawgmoth's Bargain and Leovold. Leovold is BUG, so it's off-color *and* banned, which makes it a double-fail fixture.

| Tier | Looks like |
|---|---|
| Low (B2) | Sacrifice-and-recursion value: Viscera Seer, Blood Artist, Pitiless Plunderer, Eternal Witness, Sakura-Tribe Elder, Spore Frog, Ophiomancer. GC = 0. |
| Mid (B3) | Adds Ashnod's Altar, Phyrexian Altar, Living Death and Grim Tutor, plus ≤3 GCs, for example Survival of the Fittest and Worldly Tutor. Loops such as Ashnod's Altar + Nim Deathmantle (E) are allowed. |
| High (B4) | Necropotence, Demonic Tutor, Vampiric Tutor, Imperial Seal, Natural Order, Gaea's Cradle (all GC), plus Dark Ritual and Mana Vault. **Flag:** Meren + Coretapper + Magistrate's Scepter gives *infinite turns* (Spellbook R). It is allowed only at B4, and the extra-turns flag must fire. |

### 2.3 Korvold, Fae-Cursed King: Jund, CI `{B,R,G}`

| Card | Why it's tricky | Expected |
|---|---|---|
| Rakdos Cackler | Hybrid `{B/R}` | **ALLOW** |
| Manamorphose | Hybrid `{R/G}` | **ALLOW** |
| Kitchen Finks | `{G/W}`: green alone doesn't qualify | **EXCLUDE** |
| Spellskite | Colorless cost, but `{U/P}` in its text makes CI = U | **EXCLUDE** |
| Mayhem Devil | Gold BR | **ALLOW** |

**Banned traps:** Dockside Extortionist, Recurring Nightmare, Primeval Titan, Sylvan Primordial, Fastbond, Rofellos, Griselbrand and Channel. Golos is WUBRG, so it's off-color *and* banned.

| Tier | Looks like |
|---|---|
| Low (B2) | Treasure, Food and sacrifice fodder plus Mayhem Devil. GC = 0. No Animation Module loops. |
| Mid (B3) | Korvold + Animation Module + Ashnod's Altar (3-card, S) and Korvold + Blood Pet + Mortuary (O). ≤3 GCs, for example Demonic Tutor and Survival. |
| High (B4) | Food Chain + Eternal Scourge (2-card, **R**). Underworld Breach + LED. Necropotence, Vampiric Tutor, Worldly Tutor, Gaea's Cradle, Mana Vault, Chrome Mox. **Trap:** Underworld Breach + LED + Brain Freeze is a known line (P), but Brain Freeze is **U**, so it must be absent. |

### 2.4 Kozilek, the Great Distortion: colorless, CI `{}`
Kozilek costs `{8}{C}{C}`. `{C}` is not a color. This row tests F10.

| Card | Why it's tricky | Expected |
|---|---|---|
| Wastes / Snow-Covered Wastes | Basic, no basic land type | **ALLOW**, any count |
| Plains (or any basic with a type) | 903.5d: it could produce W, which isn't in a colorless CI | **EXCLUDE** |
| Eldrazi Displacer | Devoid, so `colors=[]`, but cost `{2}{W}` makes CI = W | **EXCLUDE** (catches code that reads `colors`) |
| Spellskite / Blinding Souleater | Phyrexian `{U/P}` / `{W/P}` in text | **EXCLUDE** |
| Iron Myr / Haywire Mite | Text `{R}` / `{G}` | **EXCLUDE** |
| Thought-Knot Seer, Matter Reshaper, Reality Smasher | `{C}` costs; the reminder text "({C} represents colorless mana)" is ignored | **ALLOW** |
| Bloodstained Mire, Evolving Wilds | Fetch text names land types but has no mana symbols, so CI = ∅ | ALLOW (legal; pointless in mono-Wastes, so a soft quality check) |
| Command Tower, Arcane Signet | Legal (CI ∅), but the Scryfall ruling of 2020-11-10 says they produce **no** mana with a colorless commander | **SHOULD NOT include** (soft check; brew-domain-v1 §2 fill step 6) |
| Chrome Mox | Legal, but it has nothing colored to imprint | SHOULD NOT include (soft) |

**Banned traps:** Mana Crypt, Jeweled Lotus, Black Lotus, Paradox Engine, Sundering Titan, Emrakul, the Aeons Torn, Time Vault, Chaos Orb and Library of Alexandria. Golos and Karakas are also off-color.

**Pool sanity:** `id<=c f:commander game:paper -t:basic` returns 2,750 cards today, of which 2,388 are nonland. If a "colorless" pool comes back much bigger than that, the filter is broken.

| Tier | Looks like |
|---|---|
| Low (B2) | Eldrazi ramp: Sol Ring, Mind Stone, Thran Dynamo, Eldrazi Temple, Thought-Knot Seer, Reality Smasher, Wurmcoil Engine, Ulamog, the Ceaseless Hunger, All Is Dust, Ugin, the Ineffable. GC = 0. About 33-36 Wastes plus colorless utility lands. |
| Mid (B3) | Adds Karn, the Great Creator and Walking Ballista, plus ≤3 GCs from The One Ring, Ancient Tomb and Mana Vault. |
| High (B4) | Basalt Monolith + Rings of Brighthearth (2-card, **R**). Basalt Monolith + Forsaken Monument (R). Voltaic Construct + Basalt + Karn, the Great Creator (E). Grim Monolith, Mishra's Workshop, LED, Mox Diamond, Panoptic Mirror and Glacial Chasm (GC). **Stax flag:** The Tabernacle at Pendrell Vale (GC) is allowed but must be flagged. **Trap:** Kozilek + Mind Over Matter is on Spellbook, but Mind Over Matter is U. |

### 2.5 Thrasios, Triton Hero + Tymna the Weaver: partner pair, CI `{W,U,B,G}`
Thrasios is GU and Tymna is BW. The deck's identity is their **union** (702.124c), so this row tests F4.

| Card | Why it's tricky | Expected |
|---|---|---|
| Kitchen Finks (G/W), Deathrite (B/G), Spellskite (U/P) | All inside WUBG | **ALLOW** |
| Murderous Redcap (B/R), Rakdos Cackler (B/R) | Contain R | **EXCLUDE** |
| Manamorphose (R/G), Gut Shot (R/P) | Contain R | **EXCLUDE** |
| Jegantha, the Wellspring / Reaper King | Five-color CI | **EXCLUDE** |
| *Single-commander regression* | Brew with only Thrasios: Tymna-side cards (Swords to Plowshares, Dismember) must disappear, and the pool must not become unfiltered | **EXCLUDE** |

**Banned traps (these are the important ones, because they fit WUBG):** Leovold, Emissary of Trest (BUG), Prophet of Kruphix (GU), Nadu, Winged Wisdom (GU), Hullbreacher, Flash, Tinker, Time Walk, Ancestral Recall, Recurring Nightmare, Balance, Iona, Karakas and Library of Alexandria.

**Pool sanity:** `id<=wubg` returns 24,823 cards.

| Tier | Looks like |
|---|---|
| Low (B2) | Combat-damage draw (Tymna) plus mana sink (Thrasios), evasive creatures, Sol Ring, Arcane Signet and Command Tower. GC = 0, no Thoracle. |
| Mid (B3) | ≤3 GCs, for example Rhystic Study, Smothering Tithe and Cyclonic Rift. Mystic Remora (not a GC). Dramatic Reversal + Isochron Scepter (2-card, S) is borderline: allow it only if `minimum_bracket` stays at 3 or below. |
| High (B4/cEDH-lite) | Thassa's Oracle (GC) + Demonic Consultation **or** Tainted Pact (2-card, **R**). Force of Will, Fierce Guardianship, Demonic Tutor, Vampiric, Imperial Seal, Mystical, Enlightened, Worldly, Necropotence, Ad Nauseam, Notion Thief, Grand Arbiter, Chrome Mox, Mox Diamond, Mana Vault, Gaea's Cradle. Expect ≥8 tutors and an average MV ≤2.4. |

### 2.6 Wort, the Raidmother: hybrid in cost, CI `{R,G}`
Wort costs `{4}{R/G}{R/G}`. This is the CR's own example in 903.5c and 903.5d, and it tests F3.

| Card | Why it's tricky | Expected |
|---|---|---|
| Boggart Ram-Gang, Manamorphose | Hybrid R/G | **ALLOW** |
| Kitchen Finks (G/W) | Shares G, but W is outside | **EXCLUDE** |
| Figure of Destiny (R/W) | Shares R, but W is outside | **EXCLUDE** |
| Murderous Redcap (B/R) | Shares R, but B is outside | **EXCLUDE** |
| Birthing Pod (G/P), Gut Shot (R/P) | Phyrexian, in color | **ALLOW** |
| Mountain, Forest | 903.5d example | ALLOW; **Plains, Island and Swamp EXCLUDE** |
| Brain Freeze | A tempting Breach piece, but U | **EXCLUDE** |

**Feed both input shapes (F3):** `color_identity: ["R","G"]` and a raw `mana_cost` with `{R/G}` must both give `{R,G}`. A hybrid list like `["R/G"]` must not be dropped.

**Banned traps:** Dockside Extortionist, Primeval Titan, Sylvan Primordial, Fastbond, Rofellos and Channel.

| Tier | Looks like |
|---|---|
| Low (B2) | Spells-matter tokens: burn and rituals copied by conspire, Goblin Warriors, Faithless Looting. GC = 0. |
| Mid (B3) | Storm-Kiln Artist treasure and ≤3 GCs, for example Jeska's Will and Gamble. |
| High (B4) | Kiki-Jiki + Zealous Conscripts (R). Underworld Breach + LED. Worldly Tutor, Natural Order, Survival, Gaea's Cradle. |

### 2.7 Alesha, Who Smiles at Death: hybrid in rules text, CI `{W,B,R}`
Alesha costs `{2}{R}`, and her attack trigger reads "you may pay {W/B}{W/B}". A generator that reads only `mana_cost` or `colors` gets `{R}`. This is the main **F2** fixture.

| Card | Why it's tricky | Expected |
|---|---|---|
| Swords to Plowshares (W), Dismember (B/P) | Legal **only** through the rules-text identity | **ALLOW**, and at least one W or B card should appear |
| Figure of Destiny (R/W), Rakdos Cackler (B/R), Murderous Redcap (B/R) | Hybrid inside Mardu | **ALLOW** |
| Kitchen Finks (G/W), Deathrite (B/G) | Contain G | **EXCLUDE** |
| Manamorphose (R/G) | Contains G | **EXCLUDE** |
| Syndic of Tithes | Its extort reminder text shows `{W/B}`, but CI = W only (903.4c) | ALLOW. Also assert the computed CI is exactly `{W}`. |

**Banned traps:** Spellbook lists Alesha + Dockside Extortionist + Aggravated Assault lines tagged **B**, so Dockside must never appear even in a high brew. Also Balance, Iona, Karakas, Recurring Nightmare, Griselbrand, Falling Star and Yawgmoth's Bargain.

**Pool sanity:** `id<=brw` returns 18,644 cards, while `id<=r` returns only 7,556. A Mardu pool close to 7.5k means the F2 bug is back.

| Tier | Looks like |
|---|---|
| Low (B2) | Small-creature recursion: power-2-or-less ETB creatures and Sun Titan. GC = 0. |
| Mid (B3) | Karmic Guide + Reveillark + Viscera Seer (3-card, E) and ≤3 GCs, for example Smothering Tithe, Teferi's Protection and Enlightened Tutor. |
| High (B4) | Alesha + Master of Cruelties (2-card, S). Demonic, Vampiric, Imperial Seal, Necropotence, Bolas's Citadel, Gamble, Underworld Breach. **MLD flag:** Armageddon is legal and not a GC, but it's mass land denial, so it's allowed only at B4 and must be flagged. Treat Blood Moon the same way **[tune]**. |

### 2.8 Optional extra rows
- **Bosh, Iron Golem** costs `{8}`, and its text `{3}{R}` makes CI = `{R}` (the CR 903.4 example). Lightning Bolt and Mountain must be allowed, and Spellskite must be excluded.
- **Wilson, Refined Grizzly + Raised by Giants** has CI `{G}`. The deck is 98 + 2 = 100.

---

## 3. Commander-pair fixtures (V8/V9)

| Input | Expected | Rule |
|---|---|---|
| Thrasios + Tymna | legal, CI WUBG, 98 + 2 | 702.124h |
| Thrasios + Kraum, Ludevic's Opus | legal, CI GU ∪ UR = `{G,U,R}` | 702.124h |
| Pir, Imaginative Rascal + Toothy, Imaginary Friend | legal, CI `{G,U}` | 702.124j |
| Thrasios + Pir | **illegal**: "partner" and "partner with" can't be mixed | 702.124f |
| Wilson + Raised by Giants | legal | 702.124k |
| Raised by Giants alone | **illegal**: a Background is never a solo commander | 702.124k |
| Wilson + Tymna | **illegal**: different partner mechanisms | 702.124f |
| Any 3 commanders | **illegal** | 702.124g |
| Banned commander (Golos, Nadu, Leovold) | **illegal** | V4 |

---

## 4. General legality edge cases (apply to every run)

**Count (903.5a, 702.124b)**
- A deck is exactly 100 cards **including** the commander(s): 1 + 99, or 2 + 98.
  - Classic bugs: 99 + commander = 100 is correct; 100 + commander = 101 is wrong.
  - With partners, 99 + 2 = 101 is wrong.
- The commander starts in the command zone (903.6) but counts toward the 100.
- A second copy of the commander in the 99 breaks singleton.
- There is no sideboard or maybeboard (903.5f).
- A companion is **outside** the 100.

**Singleton (903.5b, 201.3b)**
- Check singleton by oracle_id, because interchangeable names count as the same card.
- Unlimited copies: anything with the **Basic** supertype. That includes the five basics, Wastes and the Snow-Covered basics, *subject to 903.5d*: Snow-Covered Plains is illegal under Kozilek.
- Dryad Arbor is **not** basic (Land Creature — Forest Dryad), so it's singleton, and it needs G.
- "A deck can have any number of cards named X" covers 11 cards: Relentless Rats, Shadowborn Apostle, Persistent Petitioners, Rat Colony, Hare Apparent, Slime Against Humanity, Dragon's Approach, Sphinx's Approach, Tempest Hawk, Templar Knight, and Cid, Timeless Artificer.
- Capped cards: **Seven Dwarves ≤7** and **Nazgûl ≤9**. 8 Dwarves or 10 Nazgûl must fail.
- These exceptions still obey color identity. Relentless Rats is illegal under Krenko.
- Parse the exceptions from oracle text. The live query `o:"a deck can have"` returns 13 cards, and a test should assert that count.

**Color identity (903.4-903.4f, 903.5c-d)**
- Identity comes from the mana cost **plus rules-text symbols** (Bosh, Alesha, Iron Myr, Spellskite), plus color indicators and characteristic-defining abilities.
- Back faces count (903.4d): Archangel Avacyn has a W front and R back, so CI = RW.
- Adventures count (903.4e): Kellan, the Fae-Blooded has a red front and a `{1}{W}` adventure, so CI = RW.
- Reminder text is **ignored** (903.4c): Syndic of Tithes is W only.
- Hybrid counts as both colors, and twobrid too: Spectral Procession is W, Reaper King is WUBRG.
- Phyrexian mana counts as its color.
- Devoid cards still carry their cost's identity (Eldrazi Displacer is W).
- Land types (903.5d): a basic land type means that color is required.
- **Rulebreaker** commanders are out of scope; flag them as unsupported rather than mis-validating. Scryfall `o:"Rulebreaker"` returns 8 today, e.g. Seluma, Light of Aysen, which allows Angels of any CI. The CR covers them in 903.5e.

**Companion (702.139, 903.11a)**
- Optional. One card, from outside the game, not counted in the 100.
- Must fit the commander's CI.
- Can't share a name with any card in the starting deck.
- The condition checks the **starting deck including the commander** (702.139b).
- Fixtures:
  - **Lutri, the Spellchaser as a companion is ILLEGAL**: banned as a companion since 2026-02-09.
  - Lutri in the 99 or as commander (CI UR) is **legal** and not a GC.
  - **Yorion, Sky Nomad is never satisfiable**: it needs 20 more cards than the minimum, and the 100 is also the maximum.
  - Lurrus of the Dream-Den fails if the commander's MV is above 2. Meren is MV 4, so it fails.
  - Brew should not output a companion at all unless it's asked for **[tune]**.

**Banlist (V4).** Read `legalities.commander` and keep the 83-card snapshot from brew-domain-v1 as a fallback only. The cards a careless generator is most likely to include are:
- Dockside Extortionist, Mana Crypt, Jeweled Lotus, Recurring Nightmare
- Primeval Titan, Sylvan Primordial, Golos, Paradox Engine
- Leovold, Prophet of Kruphix, Nadu, Hullbreacher, Flash, Tinker, Fastbond, Rofellos
- Griselbrand, Emrakul, the Aeons Torn, Iona, Balance, Upheaval, Sundering Titan
- the five Moxen, Black Lotus, Tolarian Academy, Library of Alexandria, Karakas

Biorhythm is **legal** and is a GC.

---

## 5. Power-trend assertions

Bands come from brew-domain-v1: low = B2 (power score 4-5.5), mid = B3 (6-7.5), high = B4 (8-9).

| Signal | Low (B2) | Mid (B3) | High (B4) |
|---|---|---|---|
| Game Changers | **0** (hard) | **≤3** (hard) | any; expect ≥4 **[tune, soft]** |
| 2-card combos (Spellbook) | **none** (hard) | none tagged R (hard) | allowed; expect ≥1 R or compact line where the pool has one (soft) |
| MLD (Armageddon), chained extra turns | **none** (hard) | **none** (hard) | allowed, **flagged** |
| Tutors | 0-2 | 0-3 | 4-8+ |
| Fast mana (Mana Vault, Grim Monolith, Chrome Mox, Mox Diamond, LED, Ancient Tomb, Lotus Petal, Dark Ritual) | 0-1 (Sol Ring doesn't count) | 1-3 | ≥4 |
| Average nonland MV | ≤3.6 | 3.0-3.4 | ≤3.0 |
| `minimum_bracket` | ≤2 | ≤3 | ≤4 |

**Monotonic tests** for the same commander and conditions:
- `est(low) ≤ est(mid) ≤ est(high)` and `est(high) > est(low)`.
- GC count, tutor count and fast-mana count are non-decreasing across tiers.
- These hold even with EDHREC down. The power signals come from Scryfall `game_changer`, oracle text and Spellbook, not EDHREC.

**Every run asserts:**
- 100 cards.
- Every card has a resolved name, with no UUID-pattern names.
- Singleton holds.
- CI ⊆ the commander identity.
- No banned card.
- The per-row MUST_EXCLUDE set is absent, and every MUST_ALLOW card that appears passes the validator.

To test MUST_ALLOW, put the card in the pool or a deck source, and assert the validator doesn't reject it. Don't require that the generator picks it.

---

## 6. Fixture sets in code-ready form

| Row | Commander(s) | CI | MUST_EXCLUDE | MUST_ALLOW (validator accepts) | BANNED_TRAPS |
|---|---|---|---|---|---|
| 1 | Krenko, Mob Boss | R | Orcish Lumberjack, Etali, Primal Conqueror, Boggart Ram-Gang, Relentless Rats | Gut Shot, Iron Myr, Seven Dwarves (×7) | Dockside Extortionist, Falling Star, Mox Ruby |
| 2 | Meren of Clan Nel Toth | BG | Damn, Murderous Redcap, Kitchen Finks, Leovold, Emissary of Trest | Deathrite Shaman, Dismember, Birthing Pod, Elves of Deep Shadow | Recurring Nightmare, Sylvan Primordial, Fastbond |
| 3 | Korvold, Fae-Cursed King | BGR | Kitchen Finks, Spellskite, Brain Freeze | Rakdos Cackler, Manamorphose, Mayhem Devil | Dockside Extortionist, Primeval Titan, Griselbrand |
| 4 | Kozilek, the Great Distortion | ∅ | Plains, Snow-Covered Plains, Eldrazi Displacer, Spellskite, Iron Myr, Haywire Mite, Mind Over Matter | Wastes (×N), Snow-Covered Wastes, Thought-Knot Seer, Basalt Monolith | Mana Crypt, Sundering Titan, Paradox Engine |
| 5 | Thrasios + Tymna | WUBG | Murderous Redcap, Manamorphose, Gut Shot, Jegantha, the Wellspring | Kitchen Finks, Deathrite Shaman, Spellskite, Thassa's Oracle | Leovold, Emissary of Trest, Prophet of Kruphix, Nadu, Winged Wisdom |
| 6 | Wort, the Raidmother | RG | Kitchen Finks, Figure of Destiny, Murderous Redcap, Plains, Brain Freeze | Boggart Ram-Gang, Manamorphose, Birthing Pod, Gut Shot | Dockside Extortionist, Primeval Titan, Rofellos, Llanowar Emissary |
| 7 | Alesha, Who Smiles at Death | WBR | Kitchen Finks, Deathrite Shaman, Manamorphose | Swords to Plowshares, Figure of Destiny, Murderous Redcap, Syndic of Tithes | Dockside Extortionist, Balance, Iona, Shield of Emeria |
