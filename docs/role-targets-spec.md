# Required Roles as "Makes X" / "X Matters"

**Status:** spec (no code) · **Card:** t_dd3e84da · **Date:** 2026-11-04
**Builds on:** [role-tags-spec.md](role-tags-spec.md) (ARCH-2) and [role-library.md](role-library.md) (ARCH-4).
It keeps their core rule (a role tag is one Scryfall query, and the query alone decides
membership). It replaces their facet model, their migration claim and their oversize cap.
**Evidence:** [evidence/role-targets-2026-11-04/](evidence/role-targets-2026-11-04/README.md).
Every Scryfall query string in this document was run against `api.scryfall.com` on
2026-11-04 (291 probes, listed in `scryfall-probes.tsv`). The live-data numbers come from
read-only GETs against the Tower app (v0.4.334).

---

## 0. Summary

1. A **target** X is a thing an archetype cares about. It is one of: a card type, a subtype,
   a color, a land type, a keyword, or a free concept. Type, subtype, land type and keyword
   targets are picked from Scryfall's catalogs, not typed free-hand.
2. Every target offers two roles: **Makes X** (the card is an X, creates an X, or turns
   something into an X) and **X Matters** (the card counts, buffs or triggers on X). There
   is also a third, lower-key option, **Other role for X…**, for one-off queries such as
   "destroy target Vampire".
3. Each role's query is generated from small **clauses** shown as checkboxes, for example
   "is a Bird", "changelings", "creates Bird tokens". The generated query sits in an
   always-visible box that you can edit. Under it, a live preview reads "N copies · M cards
   in *this deck* · K on Scryfall".
4. Tags stack. A Bird that cares about Birds is in both *Makes Birds* and *Birds Matter*.
5. The old `produces:<key>` / `consumes:<key>` roles were already Makes/Matters, just for
   15 fixed targets. Migration is a **read-time relabel** of those 30 roles to
   *Makes <resource>* / *<resource> Matter* under 15 migrated targets. Membership is
   restored to "legacy otag clause ∪ deterministic oracle-rules match". Nothing in Redis is
   rewritten.
6. **The current ARCH-3 migration has regressed live scores.** Two legacy roles exceed the
   5,000-card cap and score as `blocked` = 0. The otag-only queries also dropped the
   oracle-rules members the old tagger counted. Spellslinger's mean coverage across the 58
   live decks fell from 0.662 to 0.052, and the best-matching archetype changed for 35 of
   58 decks. This spec's migration (§5) fixes both. Measured on the same live data, no
   archetype×deck pair scores lower than today, and 53 of 58 decks get back their
   pre-ARCH-3 best match (23 of 58 have it today). **That fix should ship first, on its own,
   ahead of the UI redesign** (§9).
7. The scorer formula (`archetype_match`) is unchanged. What changes is what goes into it:
   there is no oversize exclusion, Scryfall warnings count as errors, legacy roles use the
   union, and blocked or pending roles are excluded from the score instead of counting as 0.
8. Starter library: **partly superseded**. The 21 per-target IS/TALKS/MAKES tags are
   replaced by generated Makes/Matters roles. The 43 functional FREE tags (removal, wipes,
   tutors, draw, ramp, Tagger concepts) are kept as *Other* roles in library v2 (§7).
9. `VOCAB_VERSION` stays at 1 (§5.6).

---

## 1. What the previous specs got wrong

Each item below was checked against code on `origin/main` (5cd47af) or against live data.

| # | ARCH-2 / ARCH-3 / ARCH-4 said or did | What is actually true | Consequence |
|---|---|---|---|
| W1 | "28 of the 30 legacy tags are verbatim mappings and score *identically* before and after migration" (role-tags-spec §3.2). | The old per-card tags were **otag ∪ oracle_rules ∪ LLM** (`tagger.tag_cards` merges all three). The migrated query keeps only the otag part. | Cards counted only by oracle rules dropped out. Example, deck *The Return of the King*: `produces:life_loss` went from 4 cards (Aragorn, the Uniter; Boros Charm; …) to 0, and `produces:mana` from 23 to 20 (City of Brass and others). Across all 58 decks, 124 of the 2,030 archetype role evaluations went from at least one member to none because of this alone. Another 287 are at 0 because of W3. |
| W2 | The gate test `test_migration_score_equality_gate` proves equality. | The test builds a fake Scryfall source that returns exactly the cards the old tagger had, so it passes by construction. | The regression in W1/W3 was never caught. |
| W3 | `TAG_MAX_RESULTS = 5000`: an "oversized" tag is excluded from scoring (§2.6, OQ-4). | `legacy:produces:spells` = `t:instant or t:sorcery` (7,675 today; Tower's cache says 7,644) and `legacy:produces:cards` (5,135; Tower 5,087) are both over the cap. | Both are `blocked` for **every** deck. 15 of the 30 saved archetype/genre records have at least one blocked role. Spellslinger mean coverage is 0.662 before ARCH-3 and 0.052 now; 42 of 58 decks score exactly 0. |
| W4 | "Blocked … never shown as 0." | `archetype_match` adds a blocked role's weight to the denominator and 0 to the numerator. | Blocked roles still drag scores down silently. That contradicts AGENT_RULES rule 4. |
| W5 | Cap rationale: "a role that matches half the card pool carries no information." | Whether a role is informative depends on `min_count` measured against the deck, not on how big the role is in the card pool. "12 instants or sorceries" is a meaningful Spellslinger role. | Two of the most-used legacy roles became unusable. |
| W6 | Five facets: IS / TALKS / MAKES / AMPLIFIES / FREE. | AMPLIFIES has 0 tags in the library or the live registry. IS and MAKES overlap. The editor shows 4 controls (Target, Tag, Facet, Label) before the query. | The model is harder to use than Andrew's two-role model, with no gain. |
| W7 | TALKS seed = `o:<word>`. | `o:` without a regex matches substrings: `o:elf` is 163 cards and `o:/\belf\b/` is 133, because "it**self**" contains "elf". `o:bird` also catches token makers such as Swan Song, which make Birds rather than care about them. | "Matters" sets are noisy. |
| W8 | Library narrowed the IS tags ("plain t:artifact is too broad"). | `artifact:is` is legendary artifacts only (444), `black:is` is mono-black legendary creatures (395), `forest:is` excludes basics (41). `black:talks` = `o:/black (permanents?\|…)/` has no `\b`, so it matches "non**black** creature" (Snuff Out is in its top 5). | "Is X" does not mean X, and Black Matters includes removal spells. |
| W9 | Scryfall 200 = success. | Scryfall returns **200 with `warnings`** when it drops a term. `t:bird ot:mana-rock` returns 463 cards, the same as `t:bird`, with the warning "Unknown keyword 'ot'". | A typo silently widens a role. ARCH-3 stores the result as valid. |
| W10 | (Brief for this card) "verify every `otag:`/`ot:` name". | `ot:` **is not a Scryfall keyword**: alone it returns 400, and combined with other terms it is silently dropped (W9). The real aliases are `otag:` = `oracletag:` = `function:` (all 390 for `mana-rock`). | Only `otag:` is ever emitted. The editor flags `ot:`. |
| W11 | ARCH-2: "All 85 otags … resolve." | `vocab.py` has **97** distinct otags (104 slots). All 97 resolve today (200, total > 0); the thinnest is `affinity-for-tokens` at 1 card. | The safety claim holds. The count was wrong. |
| W12 | Pagination at `~75 ms sleep between pages`. | Scryfall's documented limit for `/cards/search` is **2 requests/second** (500 ms). A 429 locks the client out for 30 s, and repeated 429s can lead to a ban. `role_tags.scryfall_search` also sends no `User-Agent` or `Accept` header, both of which Scryfall requires. | Index builds from Tower risk 429s. This spec's own probing got 429s often from a shared IP (§10). |
| W13 | One long alternation regex per seed. | `o:/\b(other\|…) (planeswalker\|planeswalkers)\b\|…/` returns **400 "Regular expression too complex"**. The same clauses split into separate `o:/…/ or o:/…/` terms return 228. | Templates must emit several OR'd clauses, not one regex. |
| W14 | `tag_query_index` is a single Redis JSON blob. | Every evaluation loads and parses the whole blob. Once broad sets are allowed (W5), entries reach 20k names. | Make it one key per query (§6.3). |
| W15 | `seed_library()` runs inside `ensure_registry()` on every read and adds any library id that is missing. | A library tag the user deleted comes back on the next read. | Deletes don't stick, which goes against Andrew's "everything user-editable" direction. Seed once per library version (§7.3). |
| W16 | "New target" = two `window.prompt()` calls, with the kind typed as free text. | No catalog validation, so a typo produces a target that matches nothing. | Targets are picked from Scryfall catalogs (§2.2). |

What I would do differently, in one paragraph: name the model after what Andrew already
says ("Makes X", "X Matters"). Tie targets to Scryfall's catalogs so the word is always a
real type. Generate queries from visible clauses instead of opaque seeds. Size roles by the
deck (`min_count`) instead of by the card pool. Treat a dropped term as an error. Prove the
migration on live decks instead of a fixture built to pass.

---

## 2. Targets

### 2.1 Kinds

| kind | what X is | where the word comes from | example words |
|---|---|---|---|
| `type` | a card type or supertype | `/catalog/card-types` (traditional types only: Artifact, Battle, Creature, Enchantment, Instant, Kindred, Land, Planeswalker, Sorcery) + `/catalog/supertypes` (Legendary, Snow, Basic) | Creature, Instant, Legendary |
| `subtype` | a creature, artifact, enchantment, spell, planeswalker or battle subtype | `/catalog/creature-types` (377), `artifact-types` (21), `enchantment-types` (13), `spell-types` (6), `planeswalker-types` (99), `battle-types` (1) | Bird, Elf, Treasure, Equipment, Saga, Aura |
| `land_type` | a land subtype | `/catalog/land-types` (18) | Forest, Island, Gate, Desert, Cave |
| `color` | one color | fixed: W U B R G | Black |
| `keyword` | a keyword ability | `/catalog/keyword-abilities` (224) | Flying, Deathtouch |
| `concept` | anything else; no single word | user-named | Historic, Lands, Removal, Tutors |

Andrew's list was type, subtype, color, land type and concept. **Keyword** is kept as a
sixth kind because the live registry and the library already have a Deathtouch target, and
its templates are verified (§4.6). The ARCH-2 kind `enemy` is dropped. "Destroy target
Vampire" is an *Other* role under the Vampires subtype target (§3.3).

Catalog facts verified on 2026-11-04: Bird, Elf and Vampire are only in `creature-types`.
Treasure and Food are in `artifact-types` (and also `keyword-actions`). Saga, Aura and Role
are in `enchantment-types`. Forest is in `land-types`. Historic is in no catalog, so it is
a concept.

A target can have **several words of the same kind** OR'd together, for example
"Instants & sorceries" = `[Instant, Sorcery]`. The migrated Spells target needs this
(§5.2), and Andrew can use it for things like "Elves & Druids".

`t:` matches whole type words, not substrings. All of these agree: `t:ape` = `t:/\bape\b/`
= 45, `t:rat` = `t:/\brat\b/` = 116, and `t:bat` = `t:bat -t:battle` = `t:/\bbat\b/` = 54.
So the 16 prefix pairs in the catalogs (Bat/Battle, Plan/Plane/Planeswalker, Mount/Mountain,
…) never collide in `t:` clauses. In `o:` clauses they would, which is why every generated
`o:` clause wraps the word in `\b…\b` (W7).

### 2.2 Creating a target: what Andrew sees

The Add-Role picker (§3.1) starts with one search box: **"What does this archetype care
about?"** Typing filters one combined list:

```
┌ What does this archetype care about? ─────────────────────────────┐
│ [ bir                                                          ]  │
│                                                                   │
│  YOUR TARGETS                                                     │
│   Birds                         Creature type · 2 roles           │
│  CREATURE TYPES                                                   │
│   Bird                                                            │
│  ARTIFACT TYPES / LAND TYPES / KEYWORDS … (no match)              │
│  ─────────────                                                    │
│   + New concept "bir…"   (for ideas that aren't one card word)    │
└───────────────────────────────────────────────────────────────────┘
```

- Picking a catalog word that isn't a target yet creates the target with no further
  questions. The label defaults to the plural ("Birds"), `kind` and `type_family` come from
  the catalog the word was found in, and the plural comes from the rules in §2.4.
- **+ New concept "…"** creates a `concept` target with no word. Its Makes/Matters queries
  start blank (§4.7).
- Colors appear as five fixed rows when the text matches a color name.
- An existing target with the same word is offered instead of a duplicate. Creating a
  second target with the same (kind, words) is refused.

The catalogs are fetched from `/catalog/*` (the 10 req/s endpoint class) and cached in
Redis for 7 days (`scryfall_catalog:{name}`). If the cache is cold and Scryfall is down,
the box still offers existing targets and **+ New concept**, and shows "Scryfall catalogs
unavailable, so only concepts can be created right now".

### 2.3 Target record

Same Redis key as ARCH-3 (`role_targets`). New fields are optional and filled in at read
time from the old fields (§5.1), so no existing record needs a rewrite.

```jsonc
{
  "id": "bird",                    // slug, stable; never changes when the label does
  "label": "Birds",                // display; editable
  "kind": "subtype",               // type | subtype | land_type | color | keyword | concept
  "words": ["Bird"],               // catalog spelling; color: ["B"]; concept: []
  "plurals": ["Birds"],            // parallel to words; editable (Elf -> Elves)
  "type_family": "creature",       // subtype only: creature|artifact|enchantment|spell|planeswalker|battle
  "source": "catalog",             // catalog | user | library | migrated
  "legacy_key": null,              // migrated targets only: the vocab.py resource key
  "created_at": "2026-11-04T19:00:00+00:00",
  "updated_at": "2026-11-04T19:00:00+00:00"
}
```

Validation: `words` must all be in the catalog for that kind (concept: empty; color: one of
WUBRG). At most 4 words. `MAX_TARGETS` rises from 60 to 200, because targets are now cheap
and catalog-backed. Migrated targets (§5.2) are **synthesized at read time** and never
stored, so they don't count toward the limit.

### 2.4 Plurals

`plurals` is generated as follows: an irregular table first (Elf→Elves, Dwarf→Dwarves,
Wolf→Wolves, Werewolf→Werewolves, Mouse→Mice, Ox→Oxen, Fungus→Fungi, Sorcery→Sorceries,
Cyclops→Cyclopes), then the rules +es after s/x/z/ch/sh, consonant+y→ies, else +s. The
field is editable on the target. Verified case: the Elf/Elves forms (§4.1) behave as
expected. The other irregulars are rules-of-English, not probed.

---

## 3. Roles

### 3.1 The Add-Role picker: what Andrew sees

Archetype editor → **Required roles** → **+ Add role**. Step 1 is the target search box from
§2.2. Step 2 shows the two roles for the chosen target as two large buttons:

```
┌ Roles for Birds  (Creature type) ─────────────────────────────────────────────┐
│                                                                               │
│  ┌──────────────────────────────────┐   ┌──────────────────────────────────┐  │
│  │  Makes Birds                     │   │  Birds Matter                    │  │
│  │  Is a Bird, a changeling, or     │   │  Counts, buffs or triggers on    │  │
│  │  creates Bird tokens.            │   │  Birds.                          │  │
│  │  579 on Scryfall · 9 in Deck ▾   │   │  19 on Scryfall · 2 in Deck ▾    │  │
│  └──────────────────────────────────┘   └──────────────────────────────────┘  │
│                                                                               │
│  Already defined for Birds:  [Bird Generator]  [Destroy target Bird]          │
│  Other role for Birds…                                                        │
└───────────────────────────────────────────────────────────────────────────────┘
```

- The two counts are each role's generated default query, run against Scryfall and against
  the deck chosen in the editor's deck selector (§6.2). Each count loads on its own, and
  while it loads the button shows "…" rather than 0.
- If a role with the same (target, mode) **already exists**, the button reuses that tag
  instead of creating a second one. Its count reflects that tag's current query.
- **Already defined for Birds** lists every other tag on this target, such as library or
  user *Other* roles, as chips. Clicking a chip adds that tag as a role.
- **Other role for Birds…** opens the role editor in Custom mode with a blank query and the
  target's starter queries as one-click fills (§3.3).
- The same tag cannot be added twice to one archetype (rule unchanged from today).

Picking a button adds a role row and opens it in the role editor (§3.2). (The per-deck
numbers in these mock-ups are illustrative. The Scryfall totals are real.)

### 3.2 The role editor (one row of Required roles)

```
┌ Makes Birds ───────────────────────────────────────── min [12]  weight [1.0] ┐
│ Label   [ Makes Birds                                    ]                   │
│ Includes                                                                     │
│   [x] is a Bird                      t:bird                          463     │
│   [x] changelings                    keyword:changeling               63     │
│   [x] creates Bird tokens            o:/creates? [^.]*\bBird\b[^.]*… 62      │
│ Scryfall query                                     generated · [syntax help] │
│ ┌──────────────────────────────────────────────────────────────────────────┐ │
│ │ t:bird or keyword:changeling or o:/creates? [^.]*\bBird\b[^.]*tokens?/   │ │
│ └──────────────────────────────────────────────────────────────────────────┘ │
│ In [The Return of the King ▾]:  9 copies · 9 cards · 579 on Scryfall  [Show] │
│                                                                    [Remove]  │
└──────────────────────────────────────────────────────────────────────────────┘
```

Behaviour:

1. **Clauses** are checkboxes. Each shows its query fragment and its own Scryfall count,
   which loads lazily the first time the row opens and is cached. Toggling a box
   regenerates the query box straight away (pure code, no network) and re-runs the preview.
2. **The query box is always visible and always editable.** Typing in it changes the
   origin label from "generated" to **"edited"** and greys out the clause boxes, because the
   text no longer comes from them. A **Reset to generated** link brings the clauses back
   and replaces the text, after a confirmation that shows the old query.
3. **Preview** (details in §6.2): it runs 600 ms after the last keystroke or clause toggle
   and reads **"N copies · M cards in <deck> · K on Scryfall"**. **Show** expands the
   matched card names; a second link, **show non-matching**, lists the deck cards that
   didn't match. The states are written out in full and never shown as a bare number:
   - `Evaluating…` while requests are in flight.
   - `0 of this deck's 99 cards match`. The deck name and size are always spelled out, so
     a 0 is never ambiguous.
   - **amber** `Scryfall ignored part of this query: Unknown keyword "ot"` (W9). The role
     cannot be saved until the warning is gone.
   - **red** `Scryfall rejected the query: Regular expression too complex` (W13). Same:
     no save.
4. **Label** defaults to "Makes {label}" / "{label} Matter" (with a verb agreement table
   for the few singular labels, e.g. "Black Matters"). It is editable. The label is
   display only.
5. **Min / weight** keep today's ranges (1–40, 0.1–10).
6. Saving writes the tag first and then the archetype, as today, and the tag id is stable
   from then on. If an edited query matches an existing tag's normalized query, the editor
   offers "Use the existing tag *X* instead" so duplicate tags don't pile up.

Collapsed, a row reads `Makes Birds · min 12 · w 1.0 · 9 copies in <deck> ✓`. The ✓ turns
into `short`, `empty`, `blocked` or `pending` with the same colours as §6.4.

### 3.3 The three modes

| mode | label pattern | query source | on every target? |
|---|---|---|---|
| `MAKES` | Makes X | generated from MAKES clauses (§4), or edited | yes (concept: blank until written) |
| `MATTERS` | X Matter(s) | generated from MATTERS clauses (§4), or edited | yes (concept: blank until written) |
| `CUSTOM` | anything ("Destroy target Vampire") | starter, library or user query | yes, via "Other role for X…" |

Tags stack and overlap. Nothing is exclusive, and one card can be in any number of roles.
Each archetype role still points at exactly one tag, so the score breakdown always shows
which query admitted which card (ARCH-2 §1.2, unchanged).

**IS-X lives inside Makes X.** Andrew's example ("a Bird that cares about birds gets both the
IS-Bird tag and the TALKS-ABOUT-Birds tag") works like this: the Bird is in *Makes Birds*
through the "is a Bird" clause, and in *Birds Matter* through the Matters clauses. A role
that is strictly "is a Bird" is *Makes Birds* with the other two clauses unticked. This
keeps the old `produces:` meaning, which was already "is or creates" (vocab.py: artifacts =
"is or creates artifacts", spells = "is (or copies) an instant/sorcery").

**Starter queries for CUSTOM** (one-click fills under "Other role for X…"), carried over
from ARCH-3's `starter_queries`, with word boundaries added:

```
subtype/type:  Destroys X   (Vampire shown)  o:/destroy target [^.]*\bvampires?\b/ or o:/destroy all [^.]*\bvampires\b/   → 3
```

### 3.4 Role tag record

Same Redis key as ARCH-3 (`role_tags`). Old records are read through the mapping in §5.1.

```jsonc
{
  "id": "bird:makes",              // stable; <target>:<mode> for generated, <target>:<slug> for custom
  "target_id": "bird",             // migrated legacy tags: "legacy:<key>" (read-time, §5.2)
  "mode": "MAKES",                 // MAKES | MATTERS | CUSTOM
  "label": "Makes Birds",
  "clauses": ["is", "changeling", "creates"],   // ticked clause ids; [] when query_origin != generated
  "query": "t:bird or keyword:changeling or o:/creates? [^.]*\\bBird\\b[^.]*tokens?/",
  "query_origin": "generated",     // generated | edited | library | migrated
  "template_version": 1,           // version of §4 that generated it; null unless generated
  "explanation": "Is a Bird, a changeling, or creates Bird tokens.",
  "source": "user",                // user | library | migrated
  "created_at": "…", "updated_at": "…"
}
```

The query in `query` is always the exact text that gets evaluated. A **template upgrade**
(§4 changes and `TEMPLATE_VERSION` goes up) never rewrites saved queries. A generated tag
with an older `template_version` shows "Updated template available" on its row, and
applying it is a user action with a before/after preview (same rule as ARCH-2 §1.3).

Archetype and genre `required_roles` keep exactly today's shape: `{tag, min_count, weight}`.

---

## 4. Where each target's query comes from (templates)

The templates live in one pure module, `src/server/domain/analytics/role_templates.py`,
with no network or Redis access: `generate(target, mode, clauses) -> query` and
`clauses_for(target, mode) -> [(id, label, fragment, default_on)]`. The generator joins the
ticked fragments with ` or `, wrapping any fragment that contains a top-level ` or ` in
parentheses. Words are lower-cased inside `o:` regexes (Scryfall regex is case-insensitive)
and always wrapped in `\b` (W7). A clause never contains one long alternation regex (W13).

`TEMPLATE_VERSION = 1` for everything below.

Every count is `total_cards` with `unique=cards` on 2026-11-04. Counts change as sets
release; what stays true is which form expresses which meaning.

### 4.1 `subtype` (creature subtypes)

MAKES clauses:

| id | label | fragment (Bird) | count (Bird) | default |
|---|---|---|---|---|
| `is` | is a Bird | `t:bird` | 463 | on |
| `changeling` | changelings | `keyword:changeling` | 63 | on |
| `creates` | creates Bird tokens | `o:/creates? [^.]*\bBird\b[^.]*tokens?/` | 62 | on |

Generated Makes Birds = `t:bird or keyword:changeling or o:/creates? [^.]*\bBird\b[^.]*tokens?/` → **579**.
`t:bird or <creates>` alone is 516. Adding changelings makes it 579, so all 63 changelings are new to the set.
Changeling is on by default because a changeling *is* every creature type (CR 702.73a). Oracle to confirm the default (§11, Q1).

MATTERS clauses (each is its own `o:/…/` term, W13):

| id | label | fragment (Bird) |
|---|---|---|
| `counts` | counts Birds | `o:/\b(other\|another\|each\|for each\|number of) (bird\|birds)\b/` |
| `you_control` | Birds you control | `o:/\b(bird\|birds)( creatures?)? you control\b/` |
| `spells` | Bird spells/cards | `o:/\bbird (spells?\|cards?\|permanents?)\b/` |
| `triggers` | whenever a Bird… | `o:/\bwhenever (an?\|another\|one or more) (bird\|birds)\b/` |
| `mention` | any other mention (off) | `o:/\b(bird\|birds)\b(?! (\w+ )?creature tokens?)/` |

| target | Makes (3 clauses) | Matters (first 4) | Matters `mention` only |
|---|---|---|---|
| Bird | 579 | 19 | 49 |
| Elf / Elves | 782 | 93 | 134 |
| Vampire | 522 | 58 | 105 |

The first four MATTERS clauses are on by default and the `mention` clause is off.
`mention` catches cards like Mockingbird ("…except it's a Bird in addition…"), which
*make* a Bird rather than care about Birds. That's why it is opt-in. Compare library v1
`bird:talks` = `o:bird` (112), which includes the Bird token makers (W7).

### 4.2 `subtype` (artifact, enchantment, spell subtypes)

Same clauses as §4.1, without `changeling` (it only applies to creatures) and with the
`( creatures?)?` part dropped from `you_control`.

| target | Makes (`is` + `creates`) | Matters `mention` |
|---|---|---|
| Treasure | `t:treasure or o:/creates? [^.]*\bTreasure\b[^.]*tokens?/` → 382 | `o:/\b(treasure\|treasures)\b(?!( \w+)? tokens?)/` → 58 |
| Equipment | `t:equipment or o:/creates? [^.]*\bEquipment\b[^.]*tokens?/` → 658 | `o:/\b(equipment\|equipments)\b(?!( \w+)? tokens?)/` → 424 |

For token-heavy artifact subtypes the counting phrases are thin (Treasure's four phrase
clauses as one regex: 19). So for `type_family = artifact` the `mention` clause is **on** by
default. The lookahead `(?!( \w+)? tokens?)` excludes "create a Treasure token" text,
which is MAKES.

### 4.3 `type`

The type words don't share one Matters phrasing, so each type has its own clause table.
MAKES always starts with `is` = `t:<type>`.

| type | MAKES extra clause | Makes total | MATTERS default | Matters total |
|---|---|---|---|---|
| Creature | `o:/creates? [^.]*creature tokens?/` (2,618) | 20,298 | `o:/\b(other )?creatures you control get\b/ or o:/\bfor each creature you control\b/ or o:/\bnumber of creatures you control\b/` | 809 |
| Artifact | `o:/creates? [^.]*\b(artifact\|Treasure\|Clue\|Food\|Blood\|Map\|Powerstone\|Gold\|Junk\|Incubator\|Servo\|Thopter\|Construct)\b[^.]*tokens?/` (983) | 4,716 | four phrase clauses as §4.1, word `artifact` | 742 |
| Enchantment | `o:/creates? [^.]*\b(enchantment\|Role\|Aura)\b[^.]*tokens?/` (67) | 3,878 | four phrase clauses, word `enchantment` | 262 |
| Instant + Sorcery (two words) | copies: `o:/\bcopy target instant or sorcery\b\|\bcopy (that\|the next) (instant\|sorcery\|spell)\b/` (114) | 7,760 | `o:/\binstant (or\|and) sorcery (spells?\|cards?)\b/ or o:/\bnoncreature spells?\b/ or o:/\bmagecraft\b/ or o:/\bprowess\b/` | 1,013 |
| Instant | copies: `o:/\bcopy (target\|that\|the next) [^.]*\binstant\b/` | 3,953 | four phrase clauses, word `instant`* | 124 |
| Sorcery | copies: `o:/\bcopy target instant or sorcery\b/ or o:/\bcopy (that\|the next) (instant\|sorcery\|spell)\b/` | 3,883 | four phrase clauses, word `sorcery` (plural `sorceries`) | 747 |
| Planeswalker | `o:/(put\|create)s? [^.]*planeswalker[^.]*onto the battlefield\|put [^.]*planeswalker card[^.]*onto the battlefield/` (6) | 337 | four phrase clauses, word `planeswalker` | 228 |
| Land | `is` = `t:land` (1,262) + extra lands: `(otag:extra-land or otag:land-ramp or otag:rampant-growth)` (644) | not probed as one query | `(otag:landfall or otag:landfall-other or otag:lands-matter)` | 619 |
| Legendary, Snow, Basic, Battle, Kindred | generic: `is` + `creates <word> tokens` | measured at preview | four phrase clauses | measured at preview |

Notes:
- \* The Instant Matters count, and the Black/White Matters counts in §4.5, were measured
  with the equivalent single-regex form, which Scryfall accepted for those words. The
  generator always emits the split form. Same semantics, but Planeswalker shows the single
  form can be rejected (W13).
- The generic four-phrase Matters for **Creature** returns 7,185 cards, which is useless:
  almost all rules text says "creature". Hence the dedicated 809-card clause.
- **Land** reuses the verified legacy otags (all 6 resolve, §1 W11). `t:land` alone is
  1,262. The Land target and the migrated *Lands* target (§5.2) are separate on purpose:
  one is generated, the other is the migration.
- The rows marked "measured at preview" use the generic template and are **not verified**
  here. Their syntax is the same as the verified rows, and the editor's preview catches
  errors and warnings before saving.

### 4.4 `land_type`

| id | MAKES (Forest) | | id | MATTERS (Forest) |
|---|---|---|---|---|
| `is` | `t:forest` (43, incl. basics) | | `counts` | `o:/\b(other\|another\|each\|for each\|number of) (forest\|forests)\b/` |
| `fetches` | `o:/search your library for [^.]*\bForest\b/` (57) | | `you_control` | `o:/\b(forest\|forests) you control\b/` |
| `becomes` | `o:/\b(is\|are\|becomes?) (a \|an )?(Forest\|Forests)\b/ or o:/\bevery basic land type\b/` (12) | | `walk` | `o:/\bforestwalk\b/` |

| target | Makes (3) | Matters (3) |
|---|---|---|
| Forest | 112 | 97 |
| Island | 93 | 84 |
| Gate | 35 | 17 |

Basics count. A Forests deck with 15 basic Forests scores 15 copies on *Makes Forests*.
(Library v1 `forest:is` excluded basics, W8.) Fetch text ("search your library for a
Forest card") is MAKES, not MATTERS, so land types have no `spells/cards` Matters clause.

### 4.5 `color`

| id | MAKES (Black) | count | default |
|---|---|---|---|
| `is` | `c:b` (has black in its colors; `c:b` = `c>=b` = 7,047) | 7,047 | on |
| `tokens` | `o:/creates? [^.]*\bblack\b[^.]*tokens?/` | 466 | on |
| `mana` | `o:/add [^.]*\{B\}/` | 361 | off |

MATTERS (Black): `o:/\bblack (permanents?|creatures?(?! tokens?)|spells?|cards?|sources?)\b|\bdevotion to black\b/` → **179** (White, same template: 168).
The `\b` keeps out "nonblack creature" (W8).

`c=b` (mono-black, 5,018) and `id:b` (inside black identity, which includes colorless:
8,027) are deliberately not used. "Is black" means the card is black. Is-black on by
default is near-universal in a black deck, so it is mostly useful with a high `min_count`
or for devotion builds. Oracle to confirm the defaults (§11, Q2).

### 4.6 `keyword`

| id | MAKES (Deathtouch) | count |
|---|---|---|
| `has` | `keyword:deathtouch` | 352 |
| `grants` | `o:/\b(gains?\|have\|has) deathtouch\b/` | 192 |
| `tokens` | `o:/creates? [^.]*tokens? with [^.]*\bdeathtouch\b/` | 18 |

Makes Deathtouch (all three) = 531. Makes Flying = 4,182 (`keyword:flying` 3,297, grants 640).
MATTERS: `o:/\b(creatures?|permanents?) (you control )?with deathtouch\b/` → 12 (Flying: 337).
Multi-word keywords are quoted (`keyword:"first strike"`); that form is not verified here.

### 4.7 `concept`

There's no word, so there's nothing to generate. Makes and Matters both start **blank**,
with the hint "Write the query, or pick a library role below." A concept's queries come
from the user, from library v2 (§7) or from migration (§5.2). Worked examples from
Andrew's brief:

| concept | Makes | Matters |
|---|---|---|
| Historic | `t:artifact or t:legendary or t:saga or o:/creates? [^.]*\b(legendary\|artifact\|Treasure\|Clue\|Food\|Blood\|Map\|Powerstone\|Gold\|Junk\|Incubator\|Servo\|Thopter\|Construct)\b[^.]*tokens?/` → 8,832 (is-historic alone `(t:artifact or t:legendary or t:saga)` → 8,331) | `o:/\bhistoric\b/` → 59 |
| Lands | migrated `produces:lands` (§5.2) | migrated `consumes:lands` |

ARCH-2 refused to score "is historic" (8,185 then) because of the cap. Under this spec it
scores like any other role. With `min_count` 20, Makes Historic separates an
artifacts/legends deck from a creature deck.

---

## 5. Migration from `produces:` / `consumes:`

### 5.1 Read-time mapping of existing records (idempotent, no writes)

Live registry on Tower (2026-11-04):

- 94 role tags: 30 `migrated` and 64 `library`.
- 18 targets.
- 13 archetypes (11 with roles) and 17 genres (all with roles).
- 132 role references, **all** of them legacy `produces:` / `consumes:` strings. No saved
  role points at a library tag.

Every read goes through pure mapping functions. Nothing is written back, so applying them
any number of times gives the same result:

| old field | new field (read-time) |
|---|---|
| tag `facet` IS / MAKES | `mode` MAKES |
| tag `facet` TALKS / AMPLIFIES | `mode` MATTERS |
| tag `facet` FREE | `mode` CUSTOM |
| tag without `query_origin` | `query_origin` = `source` mapped: library→library, migrated→migrated, user/template→edited |
| tag without `clauses` | `clauses` = `[]` |
| target `kind` land | `land_type` |
| target `kind` enemy | `subtype` |
| target `signal` "bird" | `words` ["Bird"], `plurals` from §2.4; color `signal` "b" → `words` ["B"]; null → [] |
| legacy tag (`legacy:<side>:<key>`) `target_id` null | `target_id` "legacy:<key>" (synthesized target, §5.2) |
| archetype role `{"tag": "produces:spells"}` | unchanged string; resolved by the existing `resolve_role_tag` to `legacy:produces:spells` |

`query` is never touched, so **membership of every non-legacy tag is unchanged by
construction**.

### 5.2 The 15 migrated targets

The old vocabulary already was Makes/Matters for 15 fixed targets. The mapping makes that
visible. Each `vocab.RESOURCES` entry becomes a read-time target `legacy:<key>`
(`source: migrated`, not stored and not deletable). Its two legacy tags are shown as its
two roles:

| key | migrated target label | `produces:` → | `consumes:` → | kind / words |
|---|---|---|---|---|
| tokens | Tokens | Makes Tokens | Tokens Matter | concept |
| sacrifice | Sacrifice | Makes Sacrifice (outlets) | Sacrifice Matters | concept |
| death | Creatures dying | Makes Creatures Die | Death Matters | concept |
| etb | Enters the battlefield | Makes ETBs | ETBs Matter | concept |
| counters | +1/+1 counters | Makes Counters | Counters Matter | concept |
| graveyard | Graveyard | Fills Graveyard (Makes) | Graveyard Matters | concept |
| mana | Mana | Makes Mana | Mana Matters (sinks) | concept |
| cards | Cards / draw | Makes Cards (draw) | Cards Matter | concept |
| life_gain | Life gain | Makes Life | Lifegain Matters | concept |
| life_loss | Opponent life loss | Makes Life Loss | Life Loss Matters | concept |
| attacks | Attacking | Makes Attacks | Attacking Matters | concept |
| spells | Instants & sorceries | Makes Instants & Sorceries | Spells Matter | type [Instant, Sorcery] |
| artifacts | Artifacts | Makes Artifacts | Artifacts Matter | type [Artifact] |
| enchantments | Enchantments | Makes Enchantments | Enchantments Matter | type [Enchantment] |
| lands | Lands entering | Makes Lands | Lands Matter | concept |

The labels are display only. The ids (`legacy:produces:spells`, …) and the stored role
strings (`produces:spells`) don't change. In the picker, migrated targets sit under a
**Migrated** heading, and each migrated role row has a chip `migrated from produces:spells`.

The three targets ARCH-3 already stored for the same keys (`artifacts`, `enchantments`,
`lands`, with no tags pointing at them) are left as they are. The user can delete them.

### 5.3 Legacy membership = otag clause ∪ oracle rules

Of the three sources the old per-card tags merged, two are deterministic: the Tagger
otags (now the query) and `oracle_rules.tag_from_oracle` (local regexes over card text).
The third is the LLM pass (`tagger` falls back to it for cards the first two leave
untagged). A legacy role's members are therefore:

```
members(legacy:<side>:<key>, deck) =
      { c ∈ deck : c ∈ index(query) }                                  # the otag union, via Scryfall
    ∪ { c ∈ deck : <key> ∈ oracle_rules.tag_from_oracle(card(c))[side] }  # deterministic, local
```

- `card(c)` uses the existing card-text loader order: `card_interactions:{name}.card` →
  `card_meta:{name}` → `card:{oracle_id}`. That is the chain `commander_roles.load_cards`
  and `insights.card_texts` already use. Once the Postgres cutover lands, the Postgres
  `cards` table (`type_line`, `oracle_text`) fills the same role. No Scryfall call is
  needed, because deck sync already stored the text. A card with no stored text
  contributes only through the query part, and the preview's **Show** list marks it
  "no card text".
- The **LLM pass is not used**. AGENT_RULES rule 5 says numbers must come from deterministic
  code, so a role count that depends on a model's answer is not allowed.
- This applies **only to `legacy:*` ids**. Every other tag is query-only, exactly as ARCH-2
  promised.
- Visible in the UI: under the query box, a migrated row shows a read-only chip
  `+ Commander Lab oracle rules: Instants & sorceries (produces)`, and the preview's
  **Show** list marks each card as `query`, `rules`, or both.
- Editing a migrated tag's query is allowed, but a **legacy role** (the string
  `produces:spells` in an archetype) always means the migrated tag *plus* rules. To drop
  the rules part, use **Convert…** (§5.5).

**`produces:spells` keeps ARCH-3's query** (`t:instant or t:sorcery`). The old
`produce_otags` was empty, so the old tagger counted instants and sorceries through the
oracle rule `"instant" in tl or "sorcery" in tl`. The query and the rule now agree.

### 5.4 The guarantee, and how it was measured

Requirement from the brief: *no saved archetype drops to zero matches.* I replayed all 11
archetypes with roles against all 58 live decks (638 archetype×deck pairs, 2,030 role
evaluations) under three membership rules, plus a fourth for reference (the union using
the full tagger graph, LLM included, in place of local rules; it is in the evidence).
Inputs:

- **Pre-ARCH-3:** each deck's interaction graph (`/api/decks/{id}/interactions`). Its nodes
  carry the merged tagger tags. Tag sources across all 4,931 deck nodes: scryfall 3,964,
  none 767, llm:gemini 199, missing 1.
- **ARCH-3 today:** `/api/decks/{id}/archetype` as served (warm cache).
- **This spec:** ARCH-3's matched cards (blocked roles re-evaluated without the cap) ∪
  `oracle_rules` run locally on oracle text fetched for all 2,452 distinct deck cards.

| archetype | pre-ARCH-3 mean | ARCH-3 today | **this spec** | pairs below today |
|---|---|---|---|---|
| +1/+1 counters | 0.420 | 0.371 | 0.413 | 0 |
| Aristocrats | 0.621 | 0.504 | 0.609 | 0 |
| Group hug | 0.916 | 0.589 | 0.902 | 0 |
| Landfall | 0.619 | 0.539 | 0.619 | 0 |
| Lands Matter | 0.935 | 0.918 | 0.931 | 0 |
| Reanimator | 0.653 | 0.545 | 0.648 | 0 |
| Spellslinger | 0.662 | **0.052** | 0.637 | 0 |
| Stax | 0.893 | 0.747 | 0.893 | 0 |
| Storm | 0.703 | 0.360 | 0.688 | 0 |
| Tokens | 0.515 | 0.353 | 0.503 | 0 |
| Voltron | 0.648 | 0.600 | 0.642 | 0 |

- **0 of 638** archetype×deck pairs score lower than today.
- **0** archetype×deck pairs score 0 where the pre-ARCH-3 score was above 0. (Today: 42
  Spellslinger decks and 2 Tokens decks.)
- Best match equals the pre-ARCH-3 best match for **53 of 58** decks. Today it is 23 of 58.
- 22 of 2,030 role evaluations still have no members where pre-ARCH-3 had some. These come
  from the LLM pass and from card-text differences; with the tagger graph (LLM included)
  in place of local rules the count is 0. They are the measurable cost of rule 5, and none
  of them takes an archetype to 0.
- "Combo" and "Paradox" have no roles and score 0 by definition (unchanged).

The same membership rule applies to genres (17 records, same legacy roles,
`review_metrics.genre_section`). The survey above covers archetypes only.

**Gate for the implementation PR** (replaces the tautological test, W2):

1. Unit test with a fixture of real oracle text, such as Counterspell, Night's Whisper,
   Sol Ring and a land with landfall. The test asserts that legacy membership ⊇ the cards
   `oracle_rules` tags and ⊇ the stubbed otag set. It never asserts equality with a stub
   built from the expected answer.
2. A read-only script, `scripts/role_migration_report.py`, that reproduces the table above
   against a running app. CI can't reach Tower, so it is run by hand and its output pasted
   into the PR. The PR is refused if any pair scores lower than ARCH-3 or any pair drops
   to 0 from above 0.

### 5.5 Optional conversion (never automatic)

Each migrated role row has a **Convert…** link. It opens a dialog: "Replace *Makes Instants
& Sorceries (migrated)* with a role on target…" Andrew picks a target (default: the
matching type target, e.g. Instant + Sorcery). The dialog shows a side-by-side card list
for the selected deck: current members, proposed members, and the differences marked. On
confirm it rewrites **that one archetype's role** to the new tag id. Nothing else changes,
and there is no bulk convert in this spec. ARCH-2's
`scripts/migrate_archetype_roles.py` (dry-run rewrite of all blobs) is **not** built;
read-time resolution makes it unnecessary.

### 5.6 `VOCAB_VERSION` stays 1

The `card_interactions:*` cache, the interaction graph, engines, combos and every other
reader of `produces`/`consumes` are untouched. Legacy roles read `oracle_rules` from card
text and otags from Scryfall. They don't read the cached per-card tags, so no cached record
changes meaning. Bumping the version would retag every card through the LLM for no change
in output. ARCH-2's reasoning (OQ-3) still holds.

---

## 6. Evaluation, preview and the cache

### 6.1 Scoring path (global index ∩ deck)

Unchanged from ARCH-3 in shape: each distinct normalized query has one cached name set,
intersected with deck card names, with copies counted. These things change:

1. **No oversize exclusion.** `TAG_MAX_RESULTS` goes away. A fetch safety cap of 30,000
   names (≈172 pages ≈ 95 s at 0.55 s/page) stays, only to stop runaway queries; above it
   the role is `blocked: "query matches N cards; narrow it"`. Today's broadest generated
   role, Makes Creature, is 20,298.
2. **Warnings are errors.** A 200 response with a non-empty `warnings` array is stored as
   `{error: "Scryfall ignored part of this query: <warning>"}`, so the role is `blocked`
   (W9).
3. **No synchronous fetch in a scoring request.** A cold query makes its role `pending` and
   adds the query hash to `role_tags:index_queue`. The new worker,
   `src/server/workers/role_tag_index.py`, works through that queue and also runs the
   nightly refresh. ARCH-3 fetched inline, which would block a page load for up to 95 s on
   a broad query.
4. **Name mapping (unchanged from ARCH-3, now a stated rule).** A Scryfall result is credited to a deck card only on **exact full-name**
   match: `A // B` matches the deck entry `A // B`. The live decks store all 63 of their
   multi-face cards as the full `A // B` name. Scryfall name search also matches individual
   faces: `!"Swords to Plowshares"` with `t:creature` returns *Emeritus of Truce // Swords
   to Plowshares*. Face-level crediting would therefore count a deck's plain Swords to
   Plowshares as a creature.
5. **Legacy union** (§5.3) for `legacy:*` ids only.

### 6.2 Preview path (deck-scoped)

The editor preview has to come back in a couple of seconds, even for a brand-new 7,000-card
query. A global fetch of that query takes 45 pages, about 25 s. So the preview asks
Scryfall the question **scoped to the deck**:

```
(<user query>) (!"Card A" or !"Card B" or … up to 40 names)
```

Verified: `t:creature (!"Birds of Paradise" or … 23 names)` returned 13 cards in one
request, and the same form with 46 names (925 characters) returned 16. Rules:

- Chunks of ≤ 40 distinct deck names, so a 100-card deck takes ≤ 3 requests. A name
  containing `"` is evaluated through the global path instead; that escaping is not
  verified.
- One more request runs the bare query (page 1 only) for **K on Scryfall**. If the result
  has no `has_more`, page 1 is the full set and goes straight into the global index.
- Results are mapped by exact full name (§6.1 rule 4), so preview and scoring use the same
  matching rule. The test that guards this: for a fixture deck and query, the preview set
  must equal the index∩deck set.
- Cached in `tag_preview:{query_hash}:{deck_fingerprint}` for 1 h, where
  `deck_fingerprint` = sha256 of the sorted name list. Clause counts in the role editor use
  the same cache.
- Cost: about 4 requests ≈ 2.2 s per new (query, deck) pair at 0.55 s spacing, with a
  600 ms debounce and in-flight cancellation when the text changes again.

On **Save**, the query is queued for the global index, and the role shows `pending` in the
archetype list until the worker builds it.

### 6.3 Cache keys and Scryfall etiquette

| key | contents | lifetime |
|---|---|---|
| `tag_query:{hash}` | `{names, total, error, warnings, pages, built_at}` | no TTL; refreshed nightly (replaces the single `tag_query_index` blob, W14; the worker copies old entries over once, then the blob is no longer read) |
| `role_tags:index_queue` | set of hashes waiting for a build | until built |
| `tag_preview:{hash}:{deck_fp}` | `{matched names, total}` | 1 h |
| `scryfall_catalog:{name}` | catalog array | 7 days |

Every role-tag request to Scryfall goes through one limiter:

- At least **550 ms** between `/cards/search` requests (documented limit 500 ms), and
  100 ms for `/catalog/*`.
- Headers `User-Agent: commander-lab/<version> (role-tags)` and
  `Accept: application/json`.
- On 429: honour `Retry-After` with a minimum of 30 s, then **stop the current batch** and
  requeue it. No quick retry loop.

Nightly refresh budget, measured: the 28 distinct legacy queries in use today total 44,009
names = **265 pages ≈ 2.4 minutes**. Queries refreshed within the last 20 h are skipped
(ARCH-3 rule, kept).

### 6.4 Role states

| state | meaning | in the score | shown as |
|---|---|---|---|
| `ok` | have ≥ min_count | counted | ✓ green |
| `short` | 0 < have < min_count | counted | `3 of 4` amber |
| `empty` | valid query, 0 deck matches | counted (0) | `0 of this deck's 99 cards` amber |
| `pending` | index not built yet | **excluded** | `evaluating…` grey |
| `blocked` | syntax error, warning, over fetch cap, Scryfall unreachable | **excluded** | error text red, plus last good count if any |

---

## 7. Scorer and starter library

### 7.1 `archetype_match` (and `genre_match`)

The formula is unchanged: `coverage = Σ wᵢ·min(haveᵢ/needᵢ, 1) / Σ wᵢ`, copies counted,
4 dp, ties on name then id. The signature is unchanged too:
`archetype_match(deck, archetypes, r, source, tag_sets)`. These things change:

1. The sums run over **evaluable** roles only (`ok`/`short`/`empty`). `pending` and
   `blocked` roles are left out of both numerator and denominator (fixes W4).
2. Each result row gains `coverage_basis: {evaluated_weight, total_weight}` and
   `partial: bool`. If every role is pending or blocked, `coverage` is `null` and the row
   renders "unavailable: <reason>" (AGENT_RULES rule 4).
3. **Auto-selection** (`resolve_for_deck`) only considers non-partial archetypes. Partial
   ones are still listed, with "2 of 4 roles evaluated", but a half-evaluated archetype
   can't win by default.
4. Membership per §5.3 for `legacy:*`, and per §6.1 rules 2 and 4 for everything else.
5. `review_metrics._graph_tag_sets` (the no-Redis fallback that rebuilds legacy sets from
   graph tags) switches to the same otag ∪ oracle-rules rule, so offline reviews agree
   with live scoring.

`analytics/personality.py` `score_deck` is a different function and is not touched (same
warning as ARCH-2 §4.0).

### 7.2 Library: superseded in part, extended in part

`src/server/domain/analytics/interactions/role_library.json` becomes **version 2**:

| v1 content | count | v2 fate | why |
|---|---|---|---|
| IS / TALKS / MAKES tags for bird, vampire, planeswalker, artifact, black, forest, deathtouch | 21 | **retired**: listed in `retired_ids` | the generator now produces Makes/Matters for any target; the v1 queries had the narrowing and substring bugs in W7/W8 |
| FREE tags (removal ×8, wipes ×5, Vampire removal ×3, tutors ×2, draw ×2, recursion, ramp, historic-matters, 20 otag concepts) | 43 | **kept** as `mode: CUSTOM`, same ids, same queries | they are functional cuts Andrew asked for (spot vs selective removal, symmetric vs one-sided wipes, tutor selectivity) and every one of them is verified: 42 return 200 with counts within 20 cards of the v1 figures (sets released since 2026-10-25), and `free:destroy-all-vampires` returns 404 = 0 cards, kept on purpose |
| targets (15) | 15 | 7 word targets get `words`/`plurals` filled in; 8 concept targets kept | |
| — | — | **added**: one *Matters* role per concept that has an obvious one, e.g. Historic Matters `o:/\bhistoric\b/` (59) replacing `free:historic-matters` (`o:/historic/`, 59: same set, now with `\b`) | |

The 20 `otag:` names the library uses are a subset of the 97 legacy otags, and all resolve.
`role-library.md` is regenerated from the v2 JSON with fresh counts by the implementation
PR.

### 7.3 Seeding

- Replace the every-read `seed_library()` call (W15) with a once-per-version flag:
  `role_tags:library_seeded:v2`. When the flag is missing, add only the ids that are new in
  v2 and set the flag. After that, a user's deletes stick.
- `retired_ids` are hidden at read time from the picker and tag lists when the stored copy
  is **unreferenced** by any role and **unedited** (`updated_at` equals the library's
  date). A retired tag that is referenced or edited stays visible with a chip "retired from
  the starter library". It is never deleted automatically.

---

## 8. API changes (shape only)

| endpoint | change |
|---|---|
| `GET /api/role-targets/search?q=` | **new**: existing targets + catalog words + colors, grouped (§2.2) |
| `POST /api/role-targets` | body `{kind, words, label?}`; words validated against the catalog |
| `POST /api/role-tags/generate` | **new**, pure: `{target_id, mode, clauses?}` → `{query, label, clauses:[{id,label,fragment,default_on}], template_version}` |
| `POST /api/role-tags/preview` | body adds optional `clauses: true` (also return per-clause counts); response adds `total_scryfall`, `warnings`, `evaluated_by: deck_scope\|index`, per-card `via: query\|rules\|both` |
| `GET /api/archetypes/{id}/score?deck=` | rows add `state` `pending`; archetype adds `partial`, `coverage_basis`; `coverage` may be `null` |
| `GET /api/archetypes`, `GET /api/role-tags` | tags/targets carry the read-time fields from §5.1 |

Storage stays in Redis, next to `archetypes`/`genres`. `redis-postgres-blockers.md` already
lists `archetypes`, `genres`, `role_tags` and `role_targets` (B13/B15/B18/B19) as registries
with no table yet, under its proposed generic `registry(name, data jsonb)` table, and
`tag_query_index` as derived. This spec adds no new durable keys. The new `tag_query:*`,
`tag_preview:*`, `role_tags:index_queue` and `scryfall_catalog:*` keys are all derived and
rebuildable, and the key-mapping doc should list them as such when the implementation lands.

---

## 9. Order of work

This section is a sequencing constraint, not card decomposition (that is a non-goal for this
card).

1. **Regression fix first, as a standalone PR:** §5.3 legacy union, §6.1 rules 1–2, §6.3
   limiter/headers and §7.1 rules 1–3. It repairs live scores today (Spellslinger, Storm,
   Group hug and the 12 genres listed in the evidence) with no UI change, and it is the
   prerequisite for everything else.
2. Then: templates module (§4) → catalog-backed targets (§2) → picker and role editor
   (§3) → deck-scoped preview (§6.2) → library v2 and seeding (§7.2–7.3) → Convert dialog
   (§5.5).

The legacy Jinja editor (`src/web/templates/archetypes.html`, `genres.html`) is the only
place roles are edited today. Whether step 2 is built there or as the SvelteKit
archetypes page (the "parity tail" in `ARCHITECTURE.md` §8) is up to the secretary when
decomposing. The UI in §3 is framework-neutral.

---

## 10. Verification notes

- **291 Scryfall probes**, all `unique=cards`, run on 2026-11-04 and stored with status, total
  and any warning in `evidence/role-targets-2026-11-04/scryfall-probes.tsv`. Outcomes: 288
  returned 200, 2 returned 400 (`ot:mana-rock`; the one-regex planeswalker form), and 1
  returned 404 (`o:"destroy all vampires"`). One 200 carried a warning (`t:bird ot:mana-rock`).
- **Rate limiting was real.** This machine shares a VPN exit IP, and from about 17:00 EST
  almost every second request got a 429 with "DO NOT IGNORE THIS WARNING". The probes
  worked only at one request per 20 s with 4–5 minute back-offs. That is the case for the
  single limiter and the "stop the batch on 429" rule in §6.3.
- **Live data** comes from read-only GETs against the Tower app v0.4.334: `/api/archetypes`,
  `/api/genres`, `/api/role-tags`, `/api/decks`, and per-deck `/archetype` and
  `/interactions`. These are served from caches and cost no Scryfall calls. Oracle text
  for the 2,452 distinct deck cards came from `/cards/collection` (33 requests, 0 not found).
- **Not verified, and flagged where used:** the generic-template rows marked "measured at
  preview" (§4.3); multi-word `keyword:"…"`; irregular plurals other than Elf; escaping `"`
  inside `!"…"`; Scryfall's maximum query length (46 names / 925 characters worked); and
  the genre-side score table (§5.4 measured archetypes only).

---

## 11. Open questions

Each has a recommendation. **Q1–Q3 are Magic questions for the Oracle** (AGENT_RULES rule
10); the answers adjust clause defaults only, not the model.

- **Q1 · Changeling in Makes <creature type>.** Recommend **on** (CR 702.73a: a changeling
  is every creature type). Alternative: off, because changelings rarely define a tribe deck.
- **Q2 · Color Makes defaults.** Recommend is-color **on**, creates-color-tokens **on**,
  adds-mana **off** (fixing isn't a color theme). Is "Makes Black" useful at all, or should
  color targets offer only "Black Matters"?
- **Q3 · Historic Makes.** Recommend is-historic ∪ creates historic tokens (8,832). Should
  legendary *tokens* count (they are historic, CR 700.6)? The template includes them.
- **Q4 · Pending roles in auto-selection.** Recommend excluding partial archetypes from
  auto-selection (§7.1 rule 3). Alternative: allow them when evaluated weight ≥ 75 %.
- **Q5 · Fetch cap of 30,000.** Recommend keeping it as a runaway guard only. Nothing a
  sane role needs comes close; Makes Creature is the largest at 20,298.
