# Auto Tag: Tag Taxonomy, Intent Rules and Fixtures

Spec version **autotag-v1**, 2026-10-17. Author: Oracle (kanban t_e6983ff1).

Consumers:
- t_ab0599d4: the tagging engine and `POST /decks/:id/auto-tag`
- t_5af29988: storage (per-deck-membership tags, `source`, `version`)
- t_49d1bec9: the feature root

This is an advisory spec, not code. You can implement it without knowing Magic: every rule is a regex or a count over Scryfall card fields.

Every threshold is a **tunable heuristic** unless a source is cited. All card text was fetched from Scryfall on 2026-10-17.

---

## 0. Principles

1. **Conservative.** A tag must change how someone builds the deck. If in doubt, leave it off. An untagged card is a valid result.
2. **Intent in context.** A card's tags depend on the deck around it, so the same card can carry different tags in two decks. Storage is already per deck membership (t_5af29988).
3. **Deterministic first.** The rule engine below is the source of truth. The fixtures in §8 must pass with no LLM involved. An LLM pass is optional and restricted (§6.4).
4. **Fixed vocabulary.** The engine never emits a tag outside §1.
5. **Describe, never judge.** Tags say what a card does. They don't score quality or recommend cuts, and they don't use popularity (EDHREC), price or salt.
6. **User tags win.** Auto-tag replaces only `source: auto` tags. It never edits `source: user` tags and never writes to the Deck Journal.

---

## 1. Taxonomy (22 tags)

Tag IDs are lowercase snake_case.

Kindred takes a parameter: `kindred:<subtype>`, with the subtype lowercase and singular, e.g. `kindred:goblin`. Kindred is the current official name for what players call Tribal.

### 1.1 Function tags (10): the job the card does

| ID | Label | One-line definition | Examples (oracle text checked) |
|---|---|---|---|
| `ramp` | Ramp | Makes extra mana, puts extra lands onto the battlefield, or reduces spell costs. | Sol Ring, Sakura-Tribe Elder, Etherium Sculptor |
| `card_advantage` | Card Advantage | Draws more than one card, or draws repeatedly. A one-off cantrip is not enough. | Harmonize, Skullclamp, Enchantress's Presence |
| `removal` | Removal | Answers one opposing permanent: destroy, exile, damage, -X/-X, shuffle-away, edict or fight. | Swords to Plowshares, Chaos Warp, Ravenous Chupacabra |
| `board_wipe` | Board Wipe | Answers many permanents at once. | Wrath of God, Blasphemous Act, Pernicious Deed |
| `counterspell` | Counterspell | Counters a spell or ability on the stack. | Counterspell, Arcane Denial |
| `tutor` | Tutor | Searches the library for a nonland card. | Demonic Tutor, Goblin Matron |
| `recursion` | Recursion | Returns cards from a graveyard to hand or battlefield, or lets you cast them from there. | Eternal Witness, Animate Dead, Meren of Clan Nel Toth |
| `protection` | Protection | Keeps your own things alive: hexproof, shroud, indestructible, protection, phasing, or fogs. | Lightning Greaves, Heroic Intervention, Spore Frog |
| `finisher` | Finisher | Ends the game, or turns a board into lethal: alternate win, mass pump, extra combats, poison, or repeated drain. | Craterhoof Behemoth, Impact Tremors (go-wide), Blood Artist (aristocrats) |
| `mana_fixing` | Mana Fixing | Produces two or more of the deck's colors. | Command Tower, Exotic Orchard, Arcane Signet (3+ color decks only) |

### 1.2 Theme tags (12): the synergy the card belongs to

| ID | Label | One-line definition | Examples (oracle text checked) |
|---|---|---|---|
| `lands_matter` | Lands Matter | Rewards land drops (landfall), extra land plays, or lands in the graveyard. | Lotus Cobra, Tireless Tracker |
| `tokens` | Tokens | Makes creature tokens repeatedly, or pays off going wide. | Krenko, Mob Boss; Legion Warboss; Young Pyromancer |
| `counters` | +1/+1 Counters | Places, multiplies or pays off +1/+1 counters, or proliferates. | Hardened Scales, Ajani's Pridemate, Setessan Champion |
| `sacrifice` | Sacrifice / Aristocrats | A sacrifice outlet, a payoff for creatures dying, or fodder that sacrifices itself. | Viscera Seer, Blood Artist, Ashnod's Altar |
| `graveyard` | Graveyard | Fills your own graveyard, or uses it as a resource (flashback, escape, counting cards there). | Grisly Salvage, Stitcher's Supplier |
| `spellslinger` | Spellslinger | Pays off casting instants and sorceries. | Guttersnipe, Young Pyromancer |
| `artifacts` | Artifacts | Pays off, makes or counts artifacts. In an artifact deck, plain artifacts and Treasure makers count too. | Etherium Sculptor, Darksteel Forge, Whirler Rogue |
| `enchantments` | Enchantments | Pays off casting or controlling enchantments. In an enchantment deck, plain enchantments count too. | Enchantress's Presence, Sigil of the Empty Throne, Setessan Champion |
| `kindred:<type>` | Kindred: <Type> | Cares about one creature type, or is that type in a deck built around it. | Goblin Chieftain, Goblin Matron |
| `lifegain` | Lifegain | Gains life repeatedly, or pays off gaining life. | Soul Warden, Ajani's Pridemate |
| `voltron` | Voltron | Suits up one creature: Equipment or Aura buffs, and equip payoffs. | Bonesplitter, Puresteel Paladin |
| `blink` | Blink | Exiles your own permanents and returns them, to reuse enters-the-battlefield effects. | Ephemerate, Conjurer's Closet |

The list is deliberately short.
- Archetypes such as "Control", "Stax" and "Combo" are not tags. They belong in the Journal's Archetype field, which the user sets.
- Stax, mass land denial and extra turns appear as **flags** (§6.5), not tags.

---

## 2. Inputs and normalisation

### 2.1 Inputs

| Field | Source | Used for |
|---|---|---|
| `name`, `type_line`, `oracle_text` (or `card_faces[].oracle_text`), `keywords[]`, `produced_mana[]`, `cmc`, `game_changer` | Scryfall card object | All rules |
| The same fields for each commander (including a partner or background) | Deck `Commander` category | Commander-centered themes, kindred candidates, floor modifiers |
| The deck's `color_identity` | Union of the commanders' `color_identity` | Mana Fixing thresholds |
| The main deck | Every category except `Commander`, `Sideboard`, `Maybeboard`, `Considering` | Card tags, theme support |
| Journal `Themes` / `Kindred` (optional) | Deck Journal (Deck Genre section) | Forced active themes (§5.4) |

Terms used below:
- **Land**: the front face's type line contains `Land`. A modal double-faced card with a nonland front is a nonland card.
- **N**: the number of nonland cards in the main deck, not counting the commanders.
- **Identity size** `|CI|`: the number of colors in the deck's color identity.

### 2.2 Normalisation (run before any rule)

Apply these steps in order to produce `text` and `paragraphs[]`:

| Step | Action | Why |
|---|---|---|
| N1 | Join all faces' oracle text with `\n`. | Double-faced, split and adventure cards |
| N2 | Lowercase everything. | |
| N3 | Delete reminder text: every `\([^)]*\)`. | **Critical.** Treasure reminder text contains "{T}, Sacrifice this token: Add one mana". Left in, it fakes Ramp and Sacrifice hits. |
| N4 | Replace each of these with `~`: the card's full name; its short name (the part before the first comma, for legendary names); and `this (creature\|artifact\|enchantment\|land\|card\|spell\|permanent\|equipment\|aura\|vehicle\|token)`. | Separates self-references from "a creature" |
| N5 | Split into paragraphs on `\n`. A paragraph that starts with `•` is appended to the previous one. | Keeps modal choices with their cost (e.g. Breya) |
| N6 | Strip a leading ability word: `^[a-z' -]+ — ` (e.g. `landfall — `, `constellation — `). Keyword detection uses Scryfall `keywords[]` instead. | |
| N7 | Collapse whitespace. | |

### 2.3 Paragraph kinds

- **REP (repeatable).** Either of:
  - the paragraph starts with `whenever` or `at the beginning`; or
  - it is an activated ability. That means it matches `^[^:"]*:`, and the part before the colon contains `{` or one of `sacrifice`, `tap`, `untap`, `discard`, `exile`, `pay`, `remove`.
- **ONESHOT.** Any paragraph that is not REP. This includes `when ~ enters` triggers and spell text.
- **STATIC.** A ONESHOT paragraph on a permanent with no `when` and no `until end of turn`. Only TK-4 uses it.

All regexes are Python `re.search` over a single paragraph, unless the rule says "card" (all of `text`).

---

## 3. Rule catalog (Pass 1: base evidence)

Each rule has a **weight**: 2 = strong, 1 = weak.
- A tag's **base score** is the sum of the weights of the distinct rules that fired for it, capped at 4. Each rule counts once.
- `scope` limits which card types a rule applies to.

The YAML below is the catalog.
- Patterns are Python regex.
- They are written in YAML single quotes, where a doubled `''` stands for one apostrophe.

```yaml
ramp:
  - {id: RAMP-1, w: 2, scope: nonland, re: '\{t\}(, [^:]*)?: add '}                                   # rocks, dorks
  - {id: RAMP-2, w: 2, scope: nonland, re: 'search your library for [^.]*\bland cards?\b[^.]*(onto|on) the battlefield'}
  - {id: RAMP-3, w: 2, scope: nonland, re: 'you may play (an )?additional lands?'}
  - {id: RAMP-4, w: 2, scope: nonland, re: '\b(spells?|[a-z]+ spells) you cast costs? \{\d+\} less'}  # cost reducers
  - {id: RAMP-5, w: 2, scope: nonland, re: '\bcreate[^.]*\btreasure tokens?'}
  - {id: RAMP-6, w: 1, scope: nonland, re: '\bsacrifice (a|an|another|two|three|x) [^:]*: add \{'}     # sac-for-mana; see C-RAMP-1
  - {id: RAMP-7, w: 1, scope: instant_or_sorcery, re: '\badd \{'}                                    # rituals
  - {id: RAMP-8, w: 2, scope: nonland, kind: REP, re: '^(whenever|at the beginning)[^.]*, add '}     # triggered mana (Lotus Cobra)

card_advantage:   # subject guard: the match must follow start, ". ", ", ", ": ", "then ", "you " or "you may "
  - {id: CA-1, w: 2, re: '(^|[.,:] |\bthen |\byou (may )?)draw (two|three|four|five|six|seven|x|that many|\d+) cards'}
  - {id: CA-2, w: 2, kind: REP, re: '(^|[.,:] |\bthen |\byou (may )?)draw a card'}
  - {id: CA-3, w: 2, re: '\bdraw cards equal to'}
  - {id: CA-4, w: 2, all_of: ['exile the top', 'you may (play|cast)']}                               # impulse draw
  - {id: CA-5, w: 1, kind: ONESHOT, re: '(^|[.,:] |\bthen |\byou (may )?)draw a card'}               # cantrip
  - {id: CA-6, w: 1, any_of: ['\b(scry|surveil) \d'], or_all_of: ['(look at|reveal) the top', 'into your hand']}  # selection

removal:
  - {id: RM-1, w: 2, re: '\b(destroy|exile) (up to \w+ )?(another )?target [a-z ,-]*?(creature|artifact|enchantment|planeswalker|permanent|battle)s?\b(?! you (control|own))'}
  - {id: RM-2, w: 2, re: '\bdeals? (x|[2-9]|\d{2,}) damage to (any target|target creature|target [a-z ]*creature|target planeswalker)'}
  - {id: RM-3, w: 1, re: '\bdeals? 1 damage to (any target|target creature)'}                        # see C-RM-1
  - {id: RM-4, w: 2, re: '\btarget creature [^.]*gets -(x|\d+)/-(x|\d+)'}
  - {id: RM-5, w: 2, all_of: ['\btarget\b', '(shuffles? it into (its owner''s|their) library|on the (top|bottom) of (its owner''s|their) library)']}
  - {id: RM-6, w: 2, re: '\b(target player|target opponent|each opponent) sacrifices (a|an|one|two)\b'}    # edicts
  - {id: RM-7, w: 2, re: '\bfights? (target|another target|up to one target)|deals damage equal to its power to (target|another target|any target)'}
  - {id: RM-8, w: 1, re: '\breturn (up to one )?target (creature|nonland permanent|permanent|artifact|enchantment)(?! you control)[^.]*to (its|their) owners?''?s?''? hands?'}  # bounce

board_wipe:       # set attribute one_sided=true if the paragraph contains "you don't control" or "your opponents control"
  - {id: BW-1, w: 2, re: '\b(destroy|exile) (all|each) (other )?(nonland |nontoken |non[a-z]+ )?(creatures?|artifacts?|enchantments?|permanents?|planeswalkers?)\b'}
  - {id: BW-2, w: 2, re: '\bdeals? (x|\d+) damage to each (other )?creature'}
  - {id: BW-3, w: 2, re: '\b(all|each) (other )?([a-z]+ )?creatures?[^.]* gets? -(x|\d+)/-(x|\d+)'}
  - {id: BW-4, w: 2, re: '\breturn all [^.]*to (their|its) owners?''? hands?'}

counterspell:
  - {id: CS-1, w: 2, re: '\bcounter target [a-z ]*?(spell|ability)'}

tutor:
  - {id: TU-1, w: 2, re: '\bsearch your library for (a|an|up to (one|two|three|x)) (?![^.]*\b(land|forest|island|swamp|mountain|plains)\b)[^.,]*?cards?\b'}

recursion:
  - {id: RC-1, w: 2, all_of: ['\bcards? (from|in) (your|a) graveyard', '\b(return|put)\b', '\b(to|onto) the battlefield|\b(to|into) your hand']}
  - {id: RC-2, w: 2, re: '\byou may (cast|play) [^.]*from your graveyard'}

protection:
  - {id: PR-1, w: 2, re: '\b(equipped creature|enchanted creature|(other )?creatures you control|(other )?permanents you control|artifacts you control|target (creature|permanent) you control|your commander|you) (has|have|gains?|gets?) [^.]*\b(hexproof|shroud|indestructible|protection from)'}
  - {id: PR-2, w: 2, re: '\bprevent all combat damage|\b(target|each|all) [a-z ]*you control phases? out|\byou (have|gain) hexproof'}
  - {id: PR-3, w: 2, re: '\bcounter target [^.]*spell[^.]*that targets [^.]*you control'}

finisher:
  - {id: FN-1, w: 2, re: '\byou win the game|\b(each|target) opponents? loses? the game'}
  - {id: FN-2, w: 2, re: '\badditional combat phase|\bextra combat'}
  - {id: FN-3, w: 2, keywords: [infect, toxic], or_re: '\bpoison counters?'}
  - {id: FN-4, w: 2, re: '\bcreatures you control [^.]*get \+(x|\d+)/\+(x|\d+)[^.]*until end of turn'}  # overrun
  - {id: FN-5, w: 2, re: '\bdeals? (x|[3-9]|\d{2,}) damage to each opponent|\beach opponent loses (x|[3-9]|\d{2,}) life'}
  - {id: FN-6, w: 1, kind: REP, re: '\bdeals? (x|\d+) damage to (each opponent|any target|target player|target opponent)|\b(each opponent|target player|target opponent) loses (x|\d+) life'}  # see C-FN-1

mana_fixing:      # computed, not regex. colors(c) = set(produced_mana) & set("WUBRG")
  - {id: MF-1, w: 2, scope: nonland, when: '|CI| >= 3 and (RAMP-1 or RAMP-8 fired) and |colors(c) & CI| >= 2'}
  - {id: MF-2, w: 2, scope: nonland, when: '|CI| >= 3 and RAMP-2 fired and text contains "basic land"'}
  # lands: see §4.4 rule L-2

lands_matter:
  - {id: LM-1, w: 2, re: '^whenever (a|one or more) lands? (you control )?enters?', or_keywords: [landfall]}
  - {id: LM-2, w: 2, re: '\byou may play (an )?additional land|\bplay lands from your graveyard|\bland cards? from your graveyard|\bfor each land you control|\bsacrifice a land\b'}
  - {id: LM-3, w: 1, when: 'RAMP-2 fired'}                                                          # land ramp; see C-LM-1

tokens:
  - {id: TK-1, w: 2, kind: REP, re: '\bcreate [^.]*\bcreature tokens?'}
  - {id: TK-2, w: 2, re: '^whenever (a|another|one or more) (nontoken )?([a-z]+ )?creatures? (you control )?enters?|\bfor each creature you control|\b(creature )?tokens you control'}
  - {id: TK-3, w: 1, kind: ONESHOT, re: '\bcreate [^.]*\bcreature tokens?'}
  - {id: TK-4, w: 1, kind: STATIC, re: '\b(other )?([a-z]+ )?creatures you control get \+\d+/\+\d+'}   # anthem

counters:
  - {id: CT-1, w: 2, any_of: ['\+1/\+1 counters? would be put', '\bproliferate', '\bfor each \+1/\+1 counter', '\bwith (a|one or more) \+1/\+1 counters? on (it|them)'], or_rep_re: '\bput (a|one|two|three|x|that many|[a-z]+) \+1/\+1 counters? on'}
  - {id: CT-2, w: 1, kind: ONESHOT, re: '\bput (a|one|two|three|x|[a-z]+) \+1/\+1 counters? on|\benters with [^.]*\+1/\+1 counters?', or_keywords: [mentor, evolve, outlast, bolster, adapt, modular, graft, training, backup, riot]}

sacrifice:
  - {id: SAC-1, w: 2, re: '^[^:]*\bsacrifice (a|an|another|two|three|x|one or more) [^:]*:'}         # outlet: the cost sacrifices something other than ~
  - {id: SAC-2, w: 2, re: '\bwhenever [^.]*\b(a|another|one or more)( nontoken)?( [a-z]+)? creatures?( you control)? (dies|die|is put into a graveyard)|\bwhenever you sacrifice'}
  - {id: SAC-3, w: 1, scope: creature, re: '\bsacrifice ~[^:]*:'}                                   # fodder that sacrifices itself; see C-SAC-1

graveyard:
  - {id: GY-1, w: 2, all_of: ['your library', 'into your graveyard'], or_re: '\bmills? (a|one|two|three|four|five|x|\d+|that many) cards?(?![^.]*(target (player|opponent)|each opponent))', or_keywords: [surveil]}
  - {id: GY-2, w: 2, keywords: [flashback, escape, unearth, embalm, eternalize, disturb, dredge, delve, jump-start, retrace, scavenge, encore, aftermath]}
  - {id: GY-3, w: 2, re: '\bfor each [^.]*cards? in your graveyard|\b\w+ or more [^.]*cards? in your graveyard', or_keywords: [threshold, delirium]}

spellslinger:     # TYPE THEME (§5.2)
  - {id: SS-1, w: 2, re: '\bwhenever you cast (an|a|your first) (instant|sorcery|noncreature)|\binstant (and|or) sorcery spells?\b|\bcopy target (instant|sorcery)', or_keywords: [magecraft]}
  - {id: SS-T, w: 1, type_has: [Instant, Sorcery]}

artifacts:        # TYPE THEME
  - {id: AR-1, w: 2, re: '\bartifacts? you control|\bartifact spells?|\bwhenever (an|another|one or more) (nontoken )?artifacts?\b|\bfor each artifact|\bsacrifice (a|an|another|two|three|x) (nontoken )?artifacts?|\buntapped artifacts?|\bartifact creatures? you control'}
  - {id: AR-2, w: 2, re: '\bcreate [^.]*\bartifact [^.]*tokens?'}
  - {id: AR-T, w: 1, type_has: [Artifact]}
  - {id: AR-X, w: 0, re: '\bcreate [^.]*\b(treasure|clue|food|blood|map|gold|powerstone|incubator) tokens?|\binvestigate\b'}  # context only: C-AR-2

enchantments:     # TYPE THEME
  - {id: EN-1, w: 2, re: '\benchantments? you control|\benchantment spells?|\bwhenever (an|another) (nontoken )?enchantment|\bfor each enchantment|\baura spells?', or_keywords: [constellation]}
  - {id: EN-T, w: 1, type_has: [Enchantment]}

kindred:          # evaluated once per candidate subtype T (§5.3)
  - {id: KD-1, w: 2, re: '\b{T}\b|\b{T_plural}\b'}    # the card text names T; use an irregular-plural map (elf→elves, dwarf→dwarves, wolf→wolves, ...)
  - {id: KD-T, w: 1, when: 'type_line subtypes contain T, or keywords contain changeling'}

lifegain:
  - {id: LG-1, w: 2, re: '\bwhenever you gain life|\bif you would gain life|\byou gain life for the first time|\bfor each 1 life you gained|\bcreatures you control have lifelink', or_rep_re: '\byou gain (x|\d+) life'}
  - {id: LG-2, w: 1, kind: ONESHOT, re: '\byou gain (x|\d+) life', or_keywords: [lifelink]}

voltron:          # TYPE THEME
  - {id: VO-1, w: 2, re: '\b(equipped|enchanted) creature gets \+(x|\d+)/\+|\bequipment you control|\bwhenever an equipment|\bequip \{0\}|\bequip abilities|\bfor each (aura|equipment)|\battach (target|an?) equipment'}
  - {id: VO-T, w: 1, type_has: [Equipment, Aura]}

blink:
  - {id: BK-1, w: 2, re: '\bexile (up to (one|two|x) )?(another )?target [a-z ]*(creature|permanent|artifact)s? you (control|own)[^.]*,? then return (it|them|that card|those cards) to the battlefield'}
  - {id: BK-2, w: 1, scope: creature, re: '^when ~ enters'}                                       # creature with an ETB effect; see C-BK-1
```

Notes for implementers:
- **Exclusions are part of the rule.**
  - RM-1 must not fire on `exile target creature you control`. That is Blink.
  - TU-1 must not fire on land searches. That is Ramp.
  - The CA rules must not fire on `an opponent draws a card` (Smothering Tithe).
- A self-sacrifice (`sacrifice ~`) never satisfies SAC-1, because SAC-1 needs one of `a`, `an`, `another`, `two`, `three`, `x` or `one or more` after `sacrifice`.
- Self-recursion (`return ~ from your graveyard`) does not satisfy RC-1. That is deliberate.

---

## 4. Intent-in-context rules (Pass 2)

### 4.1 Which signals drive context

| Signal | Where it comes from | What it drives |
|---|---|---|
| **Commander** | Commander oracle text and type line | Commander-centered themes (§5.2); kindred candidate types (§5.3); health-floor modifiers (§7) |
| **Color identity** | Union of the commanders' identities | `mana_fixing`: a nonland card needs \|CI\| ≥ 3, a land needs 2 deck colors; `produced_mana ∩ CI` |
| **Dominant themes** | The active theme set from Pass 1.5 (§5) | Every C-rule below; theme gating (§6.1) |
| **Card type + oracle text** | The card itself | Base rules (§3) |
| *Not used* | EDHREC, price, salt, popularity | Tags describe function, not strength |

### 4.2 Context rule table

Context rules run once the active themes are known. A bonus adds to a tag's score, capped at 4.

A **filler** tag is one that reaches 2 **only because of** a context bonus, because its base score was below 2. Fillers can only use a limited slot (§6.2).

| ID | Applies to | Condition (deck context) | Effect | Intent it encodes |
|---|---|---|---|---|
| C-RAMP-1 | RAMP-6 (sac-for-mana) | `tokens` or `sacrifice` active | +1 `ramp` | Ashnod's Altar and Skirk Prospector only ramp when the deck feeds them |
| C-RM-1 | RM-3 (1-damage ping), when SAC-1 fired in the **same paragraph** | `tokens` or `sacrifice` active | +1 `removal` | A sac outlet that deals damage is removal only in a fodder shell (Goblin Bombardment in Krenko) |
| C-FN-1 | FN-6 (repeatable small drain or ping) | any of `tokens`, `sacrifice`, `spellslinger`, `lifegain`, `lands_matter` active | +1 `finisher` | Impact Tremors, Blood Artist and Guttersnipe only kill with a high volume of triggers |
| C-SAC-1 | SAC-3 (creature that sacrifices itself) | `sacrifice` active | +1 `sacrifice` | Sakura-Tribe Elder and Spore Frog are fodder in aristocrats |
| C-LM-1 | LM-3 (land ramp) | `lands_matter` active | +1 `lands_matter` | Sakura-Tribe Elder triggers landfall |
| C-TK-1 | TK-3 (one-shot tokens), TK-4 (anthem) | `tokens` active | +1 `tokens` | Anthems and token makers support go-wide |
| C-AR-1 | AR-T (is an artifact) | `artifacts` active | +1 `artifacts` | Sol Ring counts in an artifact deck |
| C-AR-2 | AR-X (makes Treasure, Clue, Food, ...) | `artifacts` active | +2 `artifacts` | Treasure makers are Ramp everywhere, and also Artifacts in artifact decks |
| C-EN-1 | EN-T (is an enchantment) | `enchantments` active | +1 `enchantments` | |
| C-SS-1 | SS-T (is an instant or sorcery) | `spellslinger` active | +1 `spellslinger` | |
| C-KD-1 | KD-T (has subtype T) | `kindred:T` active | +1 `kindred:T` | A plain Goblin counts in a Goblin deck |
| C-LG-1 | LG-2 (one-shot lifegain) | `lifegain` active | +1 `lifegain` | |
| C-CT-1 | CT-2 | `counters` active | +1 `counters` | |
| C-VO-1 | VO-T (Equipment or Aura) | `voltron` active | +1 `voltron` | |
| C-BK-1 | BK-2 (ETB creature) | `blink` active | +1 `blink` | |

C-AR-2 can create a filler `artifacts` tag even when the card's base `artifacts` score is 0.

### 4.3 The same card in different decks (reference examples)

| Card | Deck without the context | Deck with the context | Rule |
|---|---|---|---|
| Sakura-Tribe Elder | `ramp` | Aristocrats: `ramp`, `sacrifice`. Lands deck: `ramp`, `lands_matter`. | C-SAC-1, C-LM-1 |
| Smothering Tithe | `ramp` | Artifact deck: `ramp`, `artifacts` | C-AR-2 |
| Pitiless Plunderer | `ramp` (plus `sacrifice` if that theme is active) | Artifact + sacrifice deck: still `sacrifice`, `ramp`. The `artifacts` filler can't take a third slot. | RAMP-5, SAC-2, §6.2 |
| Dockside Extortionist | `ramp` | Artifact deck: `ramp`, `artifacts`. Goblin deck: `ramp`, `kindred:goblin`. | C-AR-2, C-KD-1 |
| Ashnod's Altar | *(no tags)*. An outlet with no fodder is off-plan, and saying nothing is the honest signal. | Fodder deck: `sacrifice`, `ramp` | C-RAMP-1 |
| Goblin Bombardment | *(no tags)* | Tokens deck: `removal`, `finisher`. Sacrifice deck: `sacrifice` plus one filler (`removal`). | C-RM-1, C-FN-1 |
| Viscera Seer | *(no tags)* | Aristocrats: `sacrifice`. **Never** `removal`: it only sacrifices your own creatures. | SAC-1 |
| Sol Ring | `ramp` | Artifact deck: `ramp`, `artifacts` | C-AR-1 |
| Arcane Signet | `ramp` (1–2 colors) | 3+ colors: `ramp`, `mana_fixing` | MF-1 |
| Command Tower | *(no tags)* in a mono-color deck | `mana_fixing` in 2+ colors | L-2 |

Dockside Extortionist is **banned in Commander** (Scryfall, checked 2026-10-17). The tagger still tags it, because legality is a separate check. It is left out of the fixtures.

### 4.4 Lands

Lands never get `ramp`. A land gets **at most 2 tags**: `mana_fixing`, plus at most one other tag.

| Rule | Condition | Result |
|---|---|---|
| L-1 | The type line contains `Basic`. | No tags |
| L-2 | \|colors(produced_mana) ∩ CI\| ≥ 2. If `produced_mana` is missing, use any of these instead: the text contains `any color`; the add clauses name 2 or more deck colors; or the land fetches a `basic land` or two basic land types in a 2+ color deck. | `mana_fixing` |
| L-3 | **Utility land**: it has a paragraph that is not a pure mana ability (`^\{t\}: add [^.]*\.$`), not an enters-tapped clause, and not an `as ~ enters` choice. A mana ability with an extra cost, such as `{t}, sacrifice a creature: add`, counts as utility. | Run every function and theme rule from §3, **except** `ramp` and `mana_fixing`. Theme gating (§6.1) applies. Keep the single highest-scoring tag; break ties by priority (§6.3). |
| L-4 | **Typed land**: the type line contains `Artifact`, `Enchantment`, or the subtype T of an active `kindred:T`. | That theme tag, if the theme is active. It uses the one non-fixing slot. |
| — | Any other land | No tags |

Lands are excluded from N and from theme support counts.

---

## 5. Deck themes (Pass 1.5): the "dominant themes" signal

Deck themes are computed from the Pass 1 **base** scores, before any context bonus, so the logic isn't circular. They are final: Pass 2 doesn't recompute them.

### 5.1 Support

For a non-type theme T (`tokens`, `counters`, `sacrifice`, `graveyard`, `lands_matter`, `lifegain`, `blink`):

- `support(T)` is the number of nonland cards in the main deck, not counting commanders, whose base score for T is at least 2.
- **Graveyard bridge:** for `graveyard`, a card also counts if its base `recursion` score is at least 2. The bridge only affects support. A recursion card still gets `recursion`, not `graveyard`.

### 5.2 Activation thresholds

| Theme kind | Support formula | General threshold | Commander-centered threshold |
|---|---|---|---|
| Non-type themes | count of cards with base ≥ 2 | `max(3, ceil(0.12·N))` | `max(2, ceil(0.06·N))` |
| Type themes (`artifacts`, `enchantments`, `spellslinger`, `voltron`) | the count of cards with a **text** rule scoring ≥ 2 (AR-1/AR-2, EN-1, SS-1, VO-1), plus 0.5 × the count of cards that match only by type (AR-T, EN-T, SS-T, VO-T) | `max(3, ceil(0.20·N))` | `max(2, ceil(0.06·N))` |
| `kindred:T` | cards with KD-1 or KD-T (each card counted once) | `max(3, ceil(0.30·N))`, lowered to `max(3, ceil(0.20·N))` if the commander's text names T | (built into the lowered threshold) |

Type themes have a higher bar, and type-only cards get half credit. Most decks run 8–12 mana rocks and equipment, and they shouldn't become "artifact decks" by accident.

A theme is **active** if either:
- support meets the general threshold; or
- the theme is **commander-centered** and support meets the commander-centered threshold.

A theme is **commander-centered** when any commander has a base score of at least 2 for it. Two special cases:
- For `graveyard`, a commander's base `recursion` score of at least 2 also counts.
- For kindred, it means the commander's text names T.

Worked thresholds for a typical 99-card deck with N = 62:

| Threshold | Value |
|---|---|
| Non-type general | 8 |
| Type general | 13 |
| Commander-centered | 4 |
| Kindred | 19, or 13 when the commander names the type |

### 5.3 Kindred candidate types

Only these subtypes are evaluated as `kindred:T`:
1. creature subtypes on any commander;
2. creature subtypes named in a commander's oracle text;
3. any creature subtype carried by at least 30% of N.

At most 2 kindred themes can be active. Keep the 2 with the highest support.

### 5.4 Journal override

A Journal `Themes` or `Kindred` value may match a tag label or ID, case-insensitively. Examples: "Goblins" → `kindred:goblin`, "Aristocrats" → `sacrifice`.

A matching theme is active with `path: journal`, whatever its support. The Journal is read, never written. The fixtures have no Journal.

---

## 6. Conservatism and selection (Pass 2, per card)

### 6.1 Theme gating

A theme tag stays on a card **only if that theme is active** for the deck (§5). This applies to commanders too. Function tags are never gated.

This rule is what keeps a deck's tags small. For example, Thopter Foundry makes tokens, but it isn't tagged `tokens` in a deck without a tokens theme.

### 6.2 Evidence threshold and caps

1. **Minimum evidence:** score ≥ **2**. That takes one strong rule, two different weak rules, or one weak rule plus a context bonus.
2. **At most 3 tags per card**, counting all auto tags. User tags don't count toward the cap.
3. **At most 2 tags per land** (§4.4).
4. **Filler slot:** filler tags (§4.2) can only bring a card up to **2** tags in total. They never take the third slot. For example, Arcane Signet in a 4-color artifact deck already has `ramp` and `mana_fixing`, so its filler `artifacts` tag is dropped.
5. A card with zero tags is a valid result.

### 6.3 Selection algorithm

```
for each card in main deck + commanders:
    if land: apply §4.4 and continue
    cand = {tag: score  for tags with final score >= 2 after gating (§6.1)}
    strong = [t in cand if base_score(t) >= 2]
    filler = [t in cand if base_score(t) <  2]
    sort strong by (score desc, PRIORITY asc)   # commanders: theme tags before function tags, then the same key
    tags = strong[:3]
    for t in sorted(filler, key=PRIORITY):
        if len(tags) < 2: tags.append(t)
    emit tags
```

`PRIORITY` runs from first to last (earlier wins):
1. Function tags: `ramp`, `card_advantage`, `removal`, `board_wipe`, `counterspell`, `finisher`, `tutor`, `protection`, `recursion`, `mana_fixing`.
2. Theme tags: `kindred:*`, `tokens`, `sacrifice`, `artifacts`, `enchantments`, `counters`, `graveyard`, `spellslinger`, `lands_matter`, `lifegain`, `voltron`, `blink`.

The function order follows how much each role matters in a deck template. The Command Zone template (ep. 658, 2025-02-18) leads with ramp, card advantage and disruption.

Tests compare card tags as **sets**. The order only matters when tags are trimmed.

### 6.4 Optional LLM pass (not used in the v1 fixtures)

If the project's LLM client is used:
- It may only **veto** a rule tag, giving a reason, or **promote** a tag whose final score is exactly 1. It may never add a tag that scored 0.
- Validate its output: tags must come from §1, and the caps (§6.2) and theme gating (§6.1) still apply. Discard anything invalid silently and log it.
- Record its evidence as `"llm"`.
- The fixtures in §8 must pass with the LLM switched off.

### 6.5 Table flags (not tags; never counted toward caps)

The creed says these are "allowed, never hidden", so every card carries `flags[]`:

| Flag | Detect |
|---|---|
| `game_changer` | Scryfall `game_changer == true` |
| `extra_turns` | `\btakes? an extra turn\|\bextra turn after this one` |
| `mass_land_denial` | `\bdestroy all lands\|\beach player sacrifices [^.]*lands` |
| `stax` | `(don't\|doesn't) untap during\|\b(each player\|your opponents\|players) can't\|\bcosts? \{\d+\} more to cast` |

---

## 7. Deck-level output

### 7.1 Deck tags

1. Take every active theme (§5.2, §5.4).
2. Rank them by `path` first: journal, then commander-centered, then general. Break ties by `support / threshold_used`, highest first, then by `PRIORITY`.
3. Emit at most **5**. A typical deck has 2–3. Never pad: if fewer themes qualify, emit fewer. Zero is valid.
4. Deck tags are theme tags only. Function tags appear in the health hints, not as deck tags.

### 7.2 Role-count health hints

Health hints run only when the main deck has at least 90 cards, commanders included. Below that the output is `[]`. That covers the fixtures and partial lists.

A count is the number of nonland main-deck cards, not counting commanders, whose final tags include the tag. Each card counts once per tag.

| Hint key | Counts | Base floor | Source |
|---|---|---|---|
| `ramp` | `ramp` | 10 | TCZ template 10; EDHREC mean 12.7 |
| `card_advantage` | `card_advantage` | 7 | EDHREC draw mean 7.6 (rounded down); TCZ 12 |
| `interaction` | `removal` + `counterspell` | 10 | TCZ targeted 12; EDHREC removal 8.5 + counters 2.4 |
| `board_wipe` | `board_wipe` | 2 | EDHREC sweepers 2.7; TCZ mass 6 |
| `finisher` | `finisher` | 2 | heuristic |

Sources:
- The Command Zone ep. 658, *Commander Deckbuilding Template for the New Era*, 2025-02-18.
- MtGDS, *The Deck of Many Things*, EDHREC, 2022-07-05.

Each base floor is the lower of the two published references. These are the same floors as the Single Card Review spec, so the two features agree.

**Floor modifiers.** Apply every one that fits. A floor never goes below 0.
- If the highest commander MV is 5 or more, the `ramp` floor goes up by 2 (heuristic).
- If a commander's own final tags include the hint's tag, that floor drops by 3. For example, a commander tagged `card_advantage` lowers the card_advantage floor.
- If `tokens` is active, the `board_wipe` floor is 0. A go-wide deck shouldn't be told to wipe its own board.

**Status:** `low` if the count is below the floor, otherwise `ok`.

The display string is `"<Label>: <count> (<status>; floor <floor>[, <reason>])"`. Example: `"Ramp: 9 (low; floor 12, commander MV ≥ 5)"`.

### 7.3 Output schema

```json
{
  "version": "autotag-v1",
  "cards": [
    {"name": "Goblin Bombardment", "is_commander": false,
     "tags": [
       {"tag": "removal",  "score": 2, "filler": true, "evidence": ["RM-3", "C-RM-1"]},
       {"tag": "finisher", "score": 2, "filler": true, "evidence": ["FN-6", "C-FN-1"]}],
     "flags": []}
  ],
  "deck_tags": [
    {"tag": "kindred:goblin", "support": 4, "threshold": 3, "path": "commander"},
    {"tag": "tokens", "support": 2, "threshold": 2, "path": "commander"}
  ],
  "health": []
}
```

- `path` is one of `journal`, `commander` or `general`.
- Storage writes each tag with `source: "auto"`, `version: "autotag-v1"` and a timestamp.
- A re-run replaces only that deck's `source: auto` rows.
- **Suppression (recommended):** when a user deletes an auto tag, store a suppression marker so the next run doesn't add it back. If storage can't do this yet, document it as a known limitation.

---

## 8. Worked examples (test fixtures)

These are mini-decks, so N is small (10–11). Every percentage threshold falls back to its minimum: 3 for general, 2 for commander-centered. Health hints are `[]` because every deck has fewer than 90 cards.

Oracle text is verbatim from Scryfall `/cards/named` (2026-10-17), reminder text included, so that step N3 is exercised. `produced_mana` is shown where a rule reads it.

**Legality and Game Changers**
- All fixture cards are Commander-legal. A Scryfall `-legal:commander` search over them returned no results.
- Smothering Tithe and Demonic Tutor are on the Game Changer list (checked one by one). An `is:gamechanger` search over the other fixture cards returned no results.
- The engine should read `game_changer` from the card data, not from this table.

### 8.1 Fixture A: Krenko, Mob Boss (mono-R, Goblin tokens). N = 11.

`/` marks a paragraph break.

| # | Card | Type | Oracle text |
|---|---|---|---|
| C | **Krenko, Mob Boss** | Legendary Creature — Goblin Warrior | {T}: Create X 1/1 red Goblin creature tokens, where X is the number of Goblins you control. |
| 1 | Sol Ring | Artifact | {T}: Add {C}{C}. |
| 2 | Skirk Prospector | Creature — Goblin | Sacrifice a Goblin: Add {R}. |
| 3 | Goblin Bombardment | Enchantment | Sacrifice a creature: This enchantment deals 1 damage to any target. |
| 4 | Impact Tremors | Enchantment | Whenever a creature you control enters, this enchantment deals 1 damage to each opponent. |
| 5 | Goblin Chieftain | Creature — Goblin | Haste (This creature can attack and {T} as soon as it comes under your control.) / Other Goblin creatures you control get +1/+1 and have haste. |
| 6 | Goblin Matron | Creature — Goblin | When this creature enters, you may search your library for a Goblin card, reveal that card, put it into your hand, then shuffle. |
| 7 | Legion Warboss | Creature — Goblin Soldier | Mentor (Whenever this creature attacks, put a +1/+1 counter on target attacking creature with lesser power.) / At the beginning of combat on your turn, create a 1/1 red Goblin creature token. That token gains haste until end of turn and attacks this combat if able. |
| 8 | Chaos Warp | Instant | The owner of target permanent shuffles it into their library, then reveals the top card of their library. If it's a permanent card, they put it onto the battlefield. |
| 9 | Blasphemous Act | Sorcery | This spell costs {1} less to cast for each creature on the battlefield. / Blasphemous Act deals 13 damage to each creature. |
| 10 | Skullclamp | Artifact — Equipment | Equipped creature gets +1/-1. / Whenever equipped creature dies, draw two cards. / Equip {1} |
| 11 | Lightning Greaves | Artifact — Equipment | Equipped creature has haste and shroud. (It can't be the target of spells or abilities.) / Equip {0} |
| L | Mountain | Basic Land — Mountain | ({T}: Add {R}.) |

**Pass 1.5: themes.** General threshold = `max(3, ceil(1.32))` = 3. Commander-centered threshold = `max(2, ceil(0.66))` = 2.

| Theme | Support | Active? |
|---|---|---|
| `kindred:goblin` | Skirk Prospector, Goblin Chieftain, Goblin Matron, Legion Warboss = **4**. Krenko's text names Goblin, so the threshold is `max(3, ceil(2.2))` = 3. | **Yes** (commander) |
| `tokens` | Impact Tremors (TK-2), Legion Warboss (TK-1) = **2**. Krenko's TK-1 makes it commander-centered. | **Yes** (commander) |
| `sacrifice` | Skirk Prospector (SAC-1), Goblin Bombardment (SAC-1) = 2. That's below 3, and Krenko has no sacrifice rule. | No |
| `artifacts` | 0 text + 0.5 × 3 type-only (Sol Ring, Skullclamp, Lightning Greaves) = 1.5 | No |
| `voltron` | 0.5 × 2 (Skullclamp, Lightning Greaves) = 1 | No |
| `enchantments` / `spellslinger` | 0.5 × 2 = 1 each | No |

**Expected card tags**

| Card | Expected tags | Evidence | Note |
|---|---|---|---|
| Krenko, Mob Boss | `kindred:goblin`, `tokens` | KD-1+KD-T; TK-1 | |
| Sol Ring | `ramp` | RAMP-1 | AR-T gated off (artifacts inactive) |
| Skirk Prospector | `kindred:goblin`, `ramp` | KD-1+KD-T; RAMP-6+C-RAMP-1 (filler) | SAC-1 gated off (sacrifice inactive) |
| Goblin Bombardment | `removal`, `finisher` | RM-3+C-RM-1 (filler); FN-6+C-FN-1 (filler) | Both are fillers, so it stops at 2 tags |
| Impact Tremors | `tokens`, `finisher` | TK-2; FN-6+C-FN-1 (filler) | |
| Goblin Chieftain | `kindred:goblin`, `tokens` | KD-1+KD-T; TK-4+C-TK-1 (filler) | The anthem counts in a go-wide deck |
| Goblin Matron | `kindred:goblin`, `tutor` | KD-1+KD-T; TU-1 | |
| Legion Warboss | `kindred:goblin`, `tokens` | KD-1+KD-T; TK-1 | CT-2 (mentor) is weak and gated off |
| Chaos Warp | `removal` | RM-5 | |
| Blasphemous Act | `board_wipe` | BW-2 | RAMP-4 must **not** fire: `~ costs` is not `spells you cast` |
| Skullclamp | `card_advantage` | CA-1 | |
| Lightning Greaves | `protection` | PR-1 | Reminder text is stripped |
| Mountain | *(none)* | L-1 | |

**Expected deck tags:** `kindred:goblin` (support 4 / threshold 3, commander), then `tokens` (2/2, commander).

**Health:** `[]`. **Flags:** none.

### 8.2 Fixture B: Meren of Clan Nel Toth (BG, aristocrats and recursion). N = 10. CI = {B, G}.

| # | Card | Type | Oracle text |
|---|---|---|---|
| C | **Meren of Clan Nel Toth** | Legendary Creature — Human Shaman | Whenever another creature you control dies, you get an experience counter. / At the beginning of your end step, choose target creature card in your graveyard. If that card's mana value is less than or equal to the number of experience counters you have, return it to the battlefield. / Otherwise, put it into your hand. |
| 1 | Sakura-Tribe Elder | Creature — Snake Shaman | Sacrifice this creature: Search your library for a basic land card, put that card onto the battlefield tapped, then shuffle. |
| 2 | Viscera Seer | Creature — Vampire Wizard | Sacrifice a creature: Scry 1. (Look at the top card of your library. You may put that card on the bottom.) |
| 3 | Blood Artist | Creature — Vampire | Whenever this creature or another creature dies, target player loses 1 life and you gain 1 life. |
| 4 | Pitiless Plunderer | Creature — Human Pirate | Whenever another creature you control dies, create a Treasure token. (It's an artifact with "{T}, Sacrifice this token: Add one mana of any color.") |
| 5 | Eternal Witness | Creature — Human Shaman | When this creature enters, you may return target card from your graveyard to your hand. |
| 6 | Spore Frog | Creature — Frog | Sacrifice this creature: Prevent all combat damage that would be dealt this turn. |
| 7 | Grisly Salvage | Instant | Reveal the top five cards of your library. You may put a creature or land card from among them into your hand. Put the rest into your graveyard. |
| 8 | Ravenous Chupacabra | Creature — Beast Horror | When this creature enters, destroy target creature an opponent controls. |
| 9 | Pernicious Deed | Enchantment | {X}, Sacrifice this enchantment: Destroy each artifact, creature, and enchantment with mana value X or less. |
| 10 | Demonic Tutor | Sorcery | Search your library for a card, put that card into your hand, then shuffle. |
| L | Phyrexian Tower | Legendary Land | {T}: Add {C}. / {T}, Sacrifice a creature: Add {B}{B}. (`produced_mana: [B, C]`) |
| L | Command Tower | Land | {T}: Add one mana of any color in your commander's color identity. (`produced_mana: [B, G, R, U, W]`) |
| L | Forest | Basic Land — Forest | ({T}: Add {G}.) |

Meren's RC-1 match falls inside paragraph 2, which contains `card in your graveyard`, `return` and `to the battlefield`.

**Pass 1.5: themes.** General threshold = 3. Commander-centered threshold = 2.

| Theme | Support | Active? |
|---|---|---|
| `sacrifice` | Viscera Seer (SAC-1), Blood Artist (SAC-2), Pitiless Plunderer (SAC-2) = **3**. Sakura-Tribe Elder and Spore Frog don't count: they're SAC-3, weight 1. | **Yes**. It meets the general threshold, and Meren's SAC-2 also makes it commander-centered. |
| `graveyard` | Grisly Salvage (GY-1) + Eternal Witness (RC-1, via the bridge) = **2**. Meren's RC-1 makes it commander-centered. | **Yes** (commander) |
| `lifegain` | Blood Artist (LG-1, repeatable) = 1. Meren has none. | No |
| `kindred:*` | The candidates are Meren's subtypes: Human 2, Shaman 2, each needing 3. Vampire is 2/10, below the 30% bar to become a candidate. | No |

**Expected card tags**

| Card | Expected tags | Evidence | Note |
|---|---|---|---|
| Meren of Clan Nel Toth | `sacrifice`, `recursion` | SAC-2; RC-1 | Commander, so the theme comes first |
| Sakura-Tribe Elder | `ramp`, `sacrifice` | RAMP-2; SAC-3+C-SAC-1 (filler) | TU-1 must **not** fire (it's a land search). MF-2 doesn't fire, because \|CI\| = 2. |
| Viscera Seer | `sacrifice` | SAC-1 | CA-6 (scry) only scores 1. **Not** removal. |
| Blood Artist | `sacrifice`, `finisher` | SAC-2; FN-6+C-FN-1 (filler) | LG-1 gated off (lifegain inactive) |
| Pitiless Plunderer | `sacrifice`, `ramp` | SAC-2; RAMP-5 | No `artifacts`, because that theme is inactive. Reminder text is stripped. |
| Eternal Witness | `recursion` | RC-1 | No `graveyard` tag: the bridge counts toward support only |
| Spore Frog | `protection`, `sacrifice` | PR-2; SAC-3+C-SAC-1 (filler) | |
| Grisly Salvage | `graveyard` | GY-1 | CA-6 only scores 1. RC-1 must **not** fire. |
| Ravenous Chupacabra | `removal` | RM-1 | |
| Pernicious Deed | `board_wipe` | BW-1 | SAC-3 is creature-only, and this is an enchantment |
| Demonic Tutor | `tutor` | TU-1 | Flag `game_changer` |
| Phyrexian Tower | `sacrifice` | L-3 → SAC-1 | L-2: {B,C} ∩ {B,G} = {B}, so no fixing |
| Command Tower | `mana_fixing` | L-2 ({B,G}) | |
| Forest | *(none)* | L-1 | |

**Expected deck tags:** `sacrifice` (support 3 / threshold 2, commander), then `graveyard` (2/2, commander).

**Health:** `[]`. **Flags:** Demonic Tutor → `game_changer`.

### 8.3 Fixture C: Breya, Etherium Shaper (WUBR, artifacts). N = 10. CI = {W, U, B, R}.

| # | Card | Type | Oracle text |
|---|---|---|---|
| C | **Breya, Etherium Shaper** | Legendary Artifact Creature — Human | When Breya enters, create two 1/1 blue Thopter artifact creature tokens with flying. / {2}, Sacrifice two artifacts: Choose one — / • Breya deals 3 damage to target player or planeswalker. / • Target creature gets -4/-4 until end of turn. / • You gain 5 life. |
| 1 | Smothering Tithe | Enchantment | Whenever an opponent draws a card, that player may pay {2}. If the player doesn't, you create a Treasure token. (It's an artifact with "{T}, Sacrifice this token: Add one mana of any color.") |
| 2 | Sol Ring | Artifact | {T}: Add {C}{C}. |
| 3 | Arcane Signet | Artifact | {T}: Add one mana of any color in your commander's color identity. (`produced_mana: [B, G, R, U, W]`) |
| 4 | Etherium Sculptor | Artifact Creature — Vedalken Artificer | Artifact spells you cast cost {1} less to cast. |
| 5 | Thopter Foundry | Artifact | {1}, Sacrifice a nontoken artifact: Create a 1/1 blue Thopter artifact creature token with flying. You gain 1 life. |
| 6 | Whirler Rogue | Creature — Human Rogue Artificer | When this creature enters, create two 1/1 colorless Thopter artifact creature tokens with flying. / Tap two untapped artifacts you control: Target creature can't be blocked this turn. |
| 7 | Swords to Plowshares | Instant | Exile target creature. Its controller gains life equal to its power. |
| 8 | Counterspell | Instant | Counter target spell. |
| 9 | Darksteel Forge | Artifact | Artifacts you control have indestructible. (Effects that say "destroy" don't destroy them. Artifact creatures with indestructible can't be destroyed by damage.) |
| 10 | Ashnod's Altar | Artifact | Sacrifice a creature: Add {C}{C}. |
| L | Seat of the Synod | Artifact Land | {T}: Add {U}. (`produced_mana: [U]`) |
| L | Command Tower | Land | {T}: Add one mana of any color in your commander's color identity. (`produced_mana: [B, G, R, U, W]`) |
| L | Island | Basic Land — Island | ({T}: Add {U}.) |

**Pass 1.5: themes.** Type-theme general threshold = `max(3, ceil(2.0))` = 3. Non-type general threshold = 3. Commander-centered threshold = 2.

| Theme | Support | Active? |
|---|---|---|
| `artifacts` | Text matches: Etherium Sculptor (AR-1), Thopter Foundry (AR-1, AR-2), Whirler Rogue (AR-1, AR-2), Darksteel Forge (AR-1) = 4. Type-only: Sol Ring, Arcane Signet, Ashnod's Altar = 0.5 × 3 = 1.5. Total **5.5**. Smothering Tithe doesn't count, because AR-X is context-only. | **Yes**. It meets the general threshold, and is commander-centered too (Breya has AR-1 and AR-2). |
| `sacrifice` | Thopter Foundry (SAC-1), Ashnod's Altar (SAC-1) = **2**. Breya's SAC-1 makes it commander-centered. | **Yes** (commander) |
| `tokens` | Thopter Foundry (TK-1) = 1. Breya and Whirler Rogue are TK-3 (one-shot, weight 1), so Breya isn't centered on tokens. | No |
| `lifegain` | Thopter Foundry (LG-1, repeatable) = 1. Breya's LG-1 makes it commander-centered, but 1 < 2. | No |
| `enchantments` / `spellslinger` | 0.5 (Smothering Tithe) / 1.0 (Swords to Plowshares, Counterspell) | No |
| `kindred:*` | Human (Breya's subtype): Whirler Rogue = 1. Artificer is 2/10 = 20%, below the 30% bar to become a candidate. | No |

**Expected card tags**

| Card | Expected tags | Evidence | Note |
|---|---|---|---|
| Breya, Etherium Shaper | `artifacts`, `sacrifice`, `removal` | AR-1+AR-2+AR-T; SAC-1; RM-4 | FN-6+C-FN-1 is a filler, and the card already has 3 tags, so it's dropped. LG-1 and TK-3 gated off. |
| Smothering Tithe | `ramp`, `artifacts` | RAMP-5; AR-X+C-AR-2 (filler) | CA-2 must **not** fire (`an opponent draws`). Flag `game_changer`. |
| Sol Ring | `ramp`, `artifacts` | RAMP-1; AR-T+C-AR-1 (filler) | Compare Fixture A, where Sol Ring is `ramp` only |
| Arcane Signet | `ramp`, `mana_fixing` | RAMP-1; MF-1 (\|CI\| = 4, colors ∩ CI = 4) | The filler `artifacts` is dropped: a filler can't take the third slot |
| Etherium Sculptor | `artifacts`, `ramp` | AR-1+AR-T; RAMP-4 | |
| Thopter Foundry | `artifacts`, `sacrifice` | AR-1+AR-2+AR-T; SAC-1 | TK-1 and LG-1 gated off |
| Whirler Rogue | `artifacts` | AR-1+AR-2 | TK-3 and BK-2 are weak and gated off |
| Swords to Plowshares | `removal` | RM-1 | `its controller gains life` is not lifegain |
| Counterspell | `counterspell` | CS-1 | |
| Darksteel Forge | `artifacts`, `protection` | AR-1+AR-T; PR-1 | Reminder text is stripped |
| Ashnod's Altar | `sacrifice`, `ramp` | SAC-1; RAMP-6+C-RAMP-1 (filler) | Only one filler fits, and `ramp` beats the `artifacts` filler on PRIORITY |
| Seat of the Synod | `artifacts` | L-4 | L-2: {U} only, so no fixing |
| Command Tower | `mana_fixing` | L-2 ({W,U,B,R}) | |
| Island | *(none)* | L-1 | |

**Expected deck tags:** `artifacts` (support 5.5 / threshold 2, commander), then `sacrifice` (2/2, commander).

**Health:** `[]`. **Flags:** Smothering Tithe → `game_changer`.

### 8.4 Health-hint unit fixtures (counts in, hints out)

These call the health function directly with made-up counts. Every row assumes a 100-card deck unless noted.

| # | Input | Expected |
|---|---|---|
| H1 | Commander MV 6; ramp = 9 | `{key: ramp, count: 9, floor: 12, status: low}`. Text: `"Ramp: 9 (low; floor 12, commander MV ≥ 5)"` |
| H2 | `tokens` active; board_wipe = 0 | `{key: board_wipe, count: 0, floor: 0, status: ok}` |
| H3 | Commander's final tags include `card_advantage`; card_advantage = 5 | `{key: card_advantage, count: 5, floor: 4, status: ok}` |
| H4 | removal = 6, counterspell = 3 | `{key: interaction, count: 9, floor: 10, status: low}` |
| H5 | finisher = 1 | `{key: finisher, count: 1, floor: 2, status: low}` |
| H6 | Main deck of 14 cards (any counts) | `[]` |

### 8.5 Invariant tests

1. No card has more than 3 auto tags. No land has more than 2, and no land has `ramp`.
2. No tag appears that isn't in §1.
3. If a card has a theme tag, that theme is in the deck's active set.
4. A deck has at most 5 deck tags.
5. **Determinism:** with the LLM off, the same input gives byte-for-byte the same output.
6. **Idempotency:** running twice gives the same result, and `source: user` tags survive unchanged.
7. **Cross-deck:**
   - Sol Ring is `ramp` in Fixture A and `ramp`, `artifacts` in Fixture C.
   - Command Tower is `mana_fixing` in Fixtures B and C, and has no tags in a mono-color deck.
8. **Reminder text:** without step N3, Pitiless Plunderer would wrongly pick up RAMP-1 and SAC-1 hits from "Sacrifice this token: Add". With N3, it must not.

---

## 9. Config (single source of tunables)

```yaml
autotag:
  version: autotag-v1
  evidence_threshold: 2
  score_cap: 4
  max_tags_card: 3
  max_tags_land: 2
  filler_max_total: 2
  max_deck_tags: 5
  max_kindred_themes: 2
  theme_thresholds:
    general_pct: 0.12
    type_theme_pct: 0.20
    commander_pct: 0.06
    kindred_pct: 0.30
    kindred_named_by_commander_pct: 0.20
    general_min: 3
    commander_min: 2
    type_only_weight: 0.5
  mana_fixing_nonland_min_colors: 3
  health:
    min_deck_size: 90
    floors: {ramp: 10, card_advantage: 7, interaction: 10, board_wipe: 2, finisher: 2}
    big_commander_mv: 5
    big_commander_ramp_bonus: 2
    commander_does_job: -3
    tokens_wipe_floor: 0
  llm: {enabled: false, may_veto: true, may_promote_score: 1}
```

Any change to a rule, a threshold or the taxonomy bumps `version`. The fixtures in §8 then have to be recomputed.

---

## 10. Sources

- **Scryfall** `/cards/named?format=text` and `/cards/named` JSON, fetched 2026-10-17, for every card named in this spec: oracle text, type line, `produced_mana`.
- **Scryfall search**, 2026-10-17:
  - `is:gamechanger` showed that Smothering Tithe and Demonic Tutor are Game Changers.
  - `banned:commander !"Dockside Extortionist"` showed that Dockside is banned.
  - `-legal:commander` and `is:gamechanger` over the other fixture cards returned no results.
- **The Command Zone** ep. 658, *Commander Deckbuilding Template for the New Era*, 2025-02-18: https://www.youtube.com/watch?v=OSNV6224cHg
- **MtGDS**, *The Deck of Many Things*, EDHREC, 2022-07-05: https://edhrec.com/articles/the-deck-of-many-things-card-frequencies-by-type-and-function-across-edh
- The floors match the Single Card Review spec, §1.5 (t_d9db48e8).
