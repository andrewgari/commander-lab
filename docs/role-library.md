# Starter role-tag library

`analytics/interactions/role_library.json` is the starter library of role tags described in [role-tags-spec.md](role-tags-spec.md) (record shape in section 1.2). It is data only: no code reads it yet. Facets used: **IS** (is X), **TALKS** (mentions X, deliberately overlapping IS), **MAKES** (creates X), **FREE** (functional cuts and concepts).

Each entry carries `label`, `facet`, `target_id`, `query` (exact Scryfall string), `scryfall_count` (total cards returned by the Scryfall API on the `count_checked` date), `example_match` (a card the query returns) and `example_no_match` (a card it excludes). Counts drift as sets release; the queries are the durable part.

Conventions:

- New concept tags use oracle-text regex; `otag:` is used only for names already audited in `analytics/interactions/vocab.py`.
- Possessives in regex use `.` for the apostrophe (`don.t`) because Scryfall oracle text uses a typographic apostrophe.
- `free:destroy-all-vampires` returns 0 cards: no card reads that. It is kept on purpose as a valid but unwinnable role, so `example_match` is null.
- Where a plain template is too broad (`t:artifact` is about 3,900 cards, `c=b` about 5,000), the library entry is narrowed and says so.
- Examples are the top EDHREC result for the match and the top result of `-(query) <loose related query>` for the non-match.

| Id | Facet | Target | Query | Count | Match / No match | What it is |
|---|---|---|---|---|---|---|
| `bird:is` | IS | bird | `t:bird` | 463 | Birds of Paradise / Swan Song | Is a Bird: Creature cards with the Bird subtype. |
| `bird:talks` | TALKS | bird | `o:bird` | 112 | Swan Song / Birds of Paradise | Birds Matter: Cards whose rules text mentions Birds; overlaps Is a Bird on purpose, so a Bird that cares about Birds counts for both. |
| `bird:makes` | MAKES | bird | `o:/creates? [^.]*Bird creature tokens?/` | 56 | Swan Song / Birds of Paradise | Bird Generator: Cards that put Bird creature tokens onto the battlefield. |
| `vampire:is` | IS | vampire | `t:vampire` | 445 | Blood Artist / Accursed Duneyard | Is a Vampire: Creature cards with the Vampire subtype. |
| `vampire:talks` | TALKS | vampire | `o:vampire` | 130 | Charismatic Conqueror / Blood Artist | Vampires Matter: Cards whose rules text mentions Vampires. |
| `vampire:makes` | MAKES | vampire | `o:/creates? [^.]*Vampire creature tokens?/` | 22 | Charismatic Conqueror / Blood Artist | Vampire Generator: Cards that put Vampire creature tokens onto the battlefield. |
| `planeswalker:is` | IS | planeswalker | `t:planeswalker` | 332 | Elspeth, Storm Slayer / Otawara, Soaring City | Is a Planeswalker: Cards with the Planeswalker card type. |
| `planeswalker:talks` | TALKS | planeswalker | `o:planeswalker` | 869 | Otawara, Soaring City / Elspeth, Storm Slayer | Planeswalkers Matter: Cards whose rules text mentions planeswalkers. |
| `planeswalker:makes` | MAKES | planeswalker | `o:/(put\|create)s? [^.]*planeswalker[^.]*onto the battlefield\|put [^.]*planeswalker card[^.]*onto the battlefield/` | 6 | The Eldest Reborn / Elspeth, Storm Slayer | Planeswalker Generator: Cards that put planeswalkers onto the battlefield from elsewhere. |
| `artifact:is` | IS | artifact | `t:artifact t:legendary` | 414 | The One Ring / Sol Ring | Is a Legendary Artifact: Legendary artifacts (plain t:artifact is ~3,900 cards, too broad to be a role, so the type template is narrowed). |
| `artifact:talks` | TALKS | artifact | `o:/whenever [^.]*artifact/` | 463 | Marionette Apprentice / Sol Ring | Artifacts Matter: Cards with triggers that care about artifacts. |
| `artifact:makes` | MAKES | artifact | `o:/creates? [^.]*(Treasure\|Clue\|Food\|Powerstone\|Servo\|Thopter\|Construct\|Blood\|Map\|Incubator\|artifact creature) tokens?/` | 915 | An Offer You Can't Refuse / Sol Ring | Artifact Token Generator: Cards that create artifact tokens (Treasure, Clue, Food, Thopter and similar). |
| `black:is` | IS | black | `c=b t:legendary t:creature` | 386 | Syr Konrad, the Grim / Blood Artist | Is Mono-Black: Mono-black legendary creatures (the full colour set is too large to be a useful role, so this template is narrowed). |
| `black:talks` | TALKS | black | `o:/black (permanents?\|creatures?\|spells?\|cards?)/` | 244 | Jet Medallion / Dark Ritual | Black Matters: Cards whose rules text refers to black permanents, creatures, spells or cards. |
| `black:makes` | MAKES | black | `o:/add [^.]*\{B\}/` | 361 | Bojuka Bog / Command Tower | Makes Black Mana: Cards that produce black mana. |
| `forest:is` | IS | forest | `t:forest -t:basic` | 41 | Breeding Pool / Forest | Is a Forest: Non-basic lands with the Forest land type. |
| `forest:talks` | TALKS | forest | `o:/forests? you control/` | 40 | Quirion Ranger / Command Tower | Forests Matter: Cards that count or reward Forests you control. |
| `forest:makes` | MAKES | forest | `o:/(becomes?\|are\|creates?) [^.]*Forest/ -t:land` | 25 | Ashaya, Soul of the Wild / Thespian's Stage | Makes Forests: Nonland cards that turn lands into Forests or make Forests. |
| `deathtouch:is` | IS | deathtouch | `keyword:deathtouch` | 350 | Baleful Strix / Basilisk Collar | Has Deathtouch: Cards with the deathtouch keyword. |
| `deathtouch:talks` | TALKS | deathtouch | `o:deathtouch` | 588 | Basilisk Collar / Birds of Paradise | Deathtouch Matters: Every card that mentions deathtouch, including ones that only grant or reference it. |
| `deathtouch:makes` | MAKES | deathtouch | `o:/(gains?\|have\|has\|with) deathtouch/ -keyword:deathtouch` | 181 | Basilisk Collar / Baleful Strix | Grants Deathtouch: Cards that give deathtouch to other things without having it themselves. |
| `free:destroy-target-vampire` | FREE | vampire | `o:"destroy target vampire"` | 2 | Cleansing Ray / Charismatic Conqueror | Destroy Target Vampire (literal): Literal phrase match for killing one Vampire; very strict. |
| `free:destroy-target-vampire-loose` | FREE | vampire | `o:/destroy target [^.]*vampire/` | 3 | Victim of Night / Feed the Swarm | Destroy Target Vampire (loose): Spot-kill that can name a Vampire with words in between. |
| `free:destroy-all-vampires` | FREE | vampire | `o:"destroy all vampires"` | 0 | none / Austere Command | Destroy All Vampires: Vampire wipe; no card reads this today, so the 0 count is the honest answer (an unwinnable role). |
| `free:historic-matters` | FREE | historic | `o:/historic/` | 58 | Excalibur, Sword of Eden / Boseiju, Who Endures | Historic Matters: Cards that care about historic spells and permanents (artifacts, legendaries, Sagas); a concept spanning several card types. |
| `free:spot-removal` | FREE | removal | `o:/(destroy\|exile) target [^.]*(creature\|permanent)/` | 1120 | Swords to Plowshares / Austere Command | Spot Removal: Single-target destroy or exile effects that hit creatures or any permanent. |
| `free:spot-removal-selective` | FREE | removal | `o:/(destroy\|exile) target (artifact\|enchantment\|land\|planeswalker)/` | 567 | Boseiju, Who Endures / Feed the Swarm | Selective Spot Removal: Destroys or exiles one permanent of a named non-creature kind. |
| `free:spot-removal-selective-creature` | FREE | removal | `o:/(destroy\|exile) target (attacking\|tapped\|nonblack\|nonwhite\|nonblue\|nonred\|nongreen\|nonartifact\|nonland\|legendary\|flying\|multicolored) creature/` | 126 | Go for the Throat / Feed the Swarm | Selective Creature Removal: Single-target creature removal restricted to a kind of creature. |
| `free:spot-removal-unrestricted` | FREE | removal | `o:/(destroy\|exile) target (nonland )?permanent/` | 163 | Beast Within / Feed the Swarm | Unrestricted Spot Removal: Destroys or exiles one target permanent of any type. |
| `free:destroy-spot` | FREE | removal | `o:/destroy target [^.]*(creature\|permanent)/` | 702 | Beast Within / Swords to Plowshares | Destroy (spot): Single-target destruction; the permanent can come back from the graveyard. |
| `free:exile-spot` | FREE | removal | `o:/exile target [^.]*(creature\|permanent)/` | 419 | Swords to Plowshares / Beast Within | Exile (spot): Single-target exile; the permanent cannot be recurred from the graveyard. |
| `free:bounce-spot` | FREE | removal | `o:/return target [^.]*(creature\|permanent)[^.]* to its owner.s hand/` | 257 | Cyclonic Rift / Beast Within | Bounce (spot): Single-target return to hand; tempo, not permanent removal. |
| `free:bounce-spot-unrestricted` | FREE | removal | `o:/return target (nonland )?permanent to its owner.s hand/` | 78 | Hullbreaker Horror / Snap | Unrestricted Bounce: Returns one target permanent of any type to its owner's hand. |
| `free:wipe-destroy-symmetric` | FREE | wipe | `o:/destroy all [^.]*creatures/ -o:/you control/ -o:/you don.t control/ -o:/target player/` | 132 | Austere Command / Feed the Swarm | Symmetric Destroy Wipe: Destroys every creature, yours included. |
| `free:wipe-destroy-asymmetric` | FREE | wipe | `o:/destroy all creatures you don.t control/ or o:/each opponent sacrifices (a\|all\|each) creatures?/ or o:/destroy all creatures target (player\|opponent) controls/` | 53 | Dictate of Erebos / Austere Command | One-Sided Destroy Wipe: Kills opposing creatures only; most one-sided wipes are worded as 'each opponent sacrifices'. |
| `free:wipe-exile-symmetric` | FREE | wipe | `o:/exile all [^.]*creatures/ -o:/you control/ -o:/you don.t control/ -o:/target player/` | 26 | Farewell / Austere Command | Symmetric Exile Wipe: Exiles every creature, yours included. |
| `free:wipe-exile-asymmetric` | FREE | wipe | `o:/exile (all\|each) [^.]*(you don.t control\|target (player\|opponent) controls\|your opponents control)/` | 4 | Settle the Wreckage / Farewell | One-Sided Exile Wipe: Exiles opposing permanents only; very few cards do this, so the count is small by nature. |
| `free:wipe-bounce` | FREE | wipe | `o:/return all [^.]*(creatures\|permanents\|nonland permanents)[^.]* to their owners. hands/` | 33 | Raise the Palisade / Austere Command | Mass Bounce: Returns a whole group of permanents to hand. |
| `free:tutor-selective` | FREE | tutor | `o:/search your library for an? [^.]*(creature\|artifact\|enchantment\|instant\|sorcery\|planeswalker\|equipment\|aura\|dragon\|elf\|goblin) card/ -t:land -o:/basic land/` | 161 | Enlightened Tutor / Evolving Wilds | Selective Tutor: Finds a card of a named type or subset of the library, not just any card; land fetchers are excluded. |
| `free:tutor-nonselective` | FREE | tutor | `o:/search your library for a card/` | 103 | Demonic Tutor / Evolving Wilds | Non-Selective Tutor: Finds any card in the library, with no type restriction. |
| `free:recursion` | FREE | recursion | `o:/return [^.]*(card\|creature\|permanent)[^.]* from your graveyard to (your hand\|the battlefield)/` | 1150 | Eternal Witness / Bojuka Bog | Recursion: Brings cards back from your graveyard to hand or the battlefield. |
| `free:ramp` | FREE | ramp | `o:/search your library for [^.]*basic land[^.]*onto the battlefield/ or (t:artifact o:/\{T\}: add/) or (t:creature o:/\{T\}: add/)` | 831 | Sol Ring / Command Tower | Ramp: Fetches lands onto the battlefield, or is a mana rock or mana creature. |
| `free:card-draw` | FREE | draw | `o:/(you\|each player\|target player) draws? (two\|three\|four\|x\|that many) cards?/` | 175 | Night's Whisper / Mind Stone | Card Draw: Draws multiple cards in one go. |
| `free:card-draw-repeatable` | FREE | draw | `o:/whenever [^.]*, (you may )?draw a card/` | 507 | Rhystic Study / Mind Stone | Repeatable Card Draw: Triggered draw engines that refill the hand over time. |
| `free:repeatable-treasures` | FREE | concept | `otag:repeatable-treasures` | 205 | Smothering Tithe / Sol Ring | Repeatable Treasure: Cards that make Treasure every turn or trigger. Uses the curated Scryfall Tagger tag. |
| `free:repeatable-sac-outlet` | FREE | concept | `otag:repeatable-sac-outlet` | 909 | Ashnod's Altar / Sol Ring | Repeatable Sacrifice Outlet: Cards that let you sacrifice permanents on demand. Uses the curated Scryfall Tagger tag. |
| `free:mana-rock` | FREE | concept | `otag:mana-rock` | 386 | Sol Ring / Command Tower | Mana Rock: Artifacts that tap for mana. Uses the curated Scryfall Tagger tag. |
| `free:mana-dork` | FREE | concept | `otag:mana-dork` | 443 | Birds of Paradise / Sol Ring | Mana Dork: Creatures that tap for mana. Uses the curated Scryfall Tagger tag. |
| `free:land-ramp` | FREE | concept | `otag:land-ramp` | 624 | Path to Exile / Sol Ring | Land Ramp: Spells that put extra lands onto the battlefield. Uses the curated Scryfall Tagger tag. |
| `free:lands-matter` | FREE | concept | `otag:lands-matter` | 599 | Fabled Passage / Sol Ring | Lands Matter: Cards that reward having or playing lands. Uses the curated Scryfall Tagger tag. |
| `free:landfall-other` | FREE | concept | `otag:landfall-other` | 30 | Archaeomancer's Map / Sol Ring | Landfall Payoff: Cards that trigger when a land enters the battlefield. Uses the curated Scryfall Tagger tag. |
| `free:reanimate-creature` | FREE | concept | `otag:reanimate-creature` | 532 | Reanimate / Sol Ring | Reanimate Creature: Cards that return creatures from a graveyard to the battlefield. Uses the curated Scryfall Tagger tag. |
| `free:flicker-creature` | FREE | concept | `otag:flicker-creature` | 155 | Ephemerate / Sol Ring | Flicker Creature: Cards that exile and return a creature to reuse its enter effect. Uses the curated Scryfall Tagger tag. |
| `free:lifegain-matters` | FREE | concept | `otag:lifegain-matters` | 243 | Aetherflux Reservoir / Sol Ring | Lifegain Matters: Cards that pay off gaining life. Uses the curated Scryfall Tagger tag. |
| `free:repeatable-proliferate` | FREE | concept | `otag:repeatable-proliferate` | 69 | Karn's Bastion / Sol Ring | Repeatable Proliferate: Cards that proliferate counters repeatedly. Uses the curated Scryfall Tagger tag. |
| `free:repeatable-loot` | FREE | concept | `otag:repeatable-loot` | 238 | Geier Reach Sanitarium / Sol Ring | Repeatable Looting: Cards that let you draw then discard over and over. Uses the curated Scryfall Tagger tag. |
| `free:extra-combat` | FREE | concept | `otag:extra-combat` | 45 | Aggravated Assault / Sol Ring | Extra Combat: Cards that grant additional combat phases. Uses the curated Scryfall Tagger tag. |
| `free:graveyard-fuel` | FREE | concept | `otag:graveyard-fuel` | 456 | Underworld Breach / Sol Ring | Graveyard Fuel: Cards that use the graveyard as a resource. Uses the curated Scryfall Tagger tag. |
| `free:extra-land` | FREE | concept | `otag:extra-land` | 160 | Explore / Sol Ring | Extra Land Drop: Cards that put or allow extra lands. Uses the curated Scryfall Tagger tag. |
| `free:cheat-into-play` | FREE | concept | `otag:cheat-into-play` | 267 | Growth Spiral / Sol Ring | Cheat Into Play: Cards that put permanents onto the battlefield without paying their cost. Uses the curated Scryfall Tagger tag. |
| `free:synergy-sacrifice` | FREE | concept | `otag:synergy-sacrifice` | 136 | Mirkwood Bats / Sol Ring | Sacrifice Payoff: Cards that reward sacrificing permanents. Uses the curated Scryfall Tagger tag. |
| `free:token-doubler` | FREE | concept | `otag:token-doubler` | 17 | Doubling Season / Sol Ring | Token Doubler: Cards that double the tokens you create. Uses the curated Scryfall Tagger tag. |
| `free:synergy-token` | FREE | concept | `otag:synergy-token` | 231 | Doubling Season / Sol Ring | Token Payoff: Cards that reward having tokens. Uses the curated Scryfall Tagger tag. |
| `free:mana-doubler` | FREE | concept | `otag:mana-doubler` | 57 | Wild Growth / Sol Ring | Mana Doubler: Cards that double the mana you produce. Uses the curated Scryfall Tagger tag. |
