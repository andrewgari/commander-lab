# Deck Genre taxonomy

Reference for the journal's **Deck Genre** section, where a user records what a
deck *is*: its strategy, its flavor, and its creature types. It is about deck
spirit and self-expression, not power. Power and bracket already have their own
journal fields; nothing here grades or ranks a deck.

The option lists live in **`docs/deck_genre_taxonomy.yaml`**. That file is the
seed data: turn it into constants as-is. This document explains the rules
around it.

```
Deck Genre
├─ Archetype   closed list   primary 0..1 + secondary 0..3   → archetype_preferences (exists)
├─ Genre       open list     0..5, seeded + custom           → deck_genre.genres     (new)
└─ Kindred     open list     0..5, autocomplete              → deck_genre.kindred    (new)
```

## 0. Read this first: archetypes are already half-built

The journal (`analytics/journal.py`, `user_intent.py`,
`schemas/user_intent.schema.json` on `main`) already stores
`archetype_preferences`:

```
primary_archetype     str   default "midrange"   enum ALLOWED_ARCHETYPES (14 ids)
secondary_archetypes  list  enum
excluded_archetypes   list  enum
custom_themes         list  free text, scored by the analytics engine
```

`deck_journal.html` never exposes these fields. So the Genre section should be
the first UI for `archetype_preferences`, not a second copy of it. One source of
truth. The analytics engine (`engine.py` ~L922) and the personality profile
(`personality.py` `ARCH_NUDGE`) already read these fields, so the user's choices
start informing Deep Review for free.

Three gotchas follow from that:

1. **The phantom "Midrange" problem.** `resolve_intent_defaults` fills
   `primary_archetype = "midrange"` and the journal persists the *resolved*
   intent. Every saved journal on disk therefore says Midrange, even though no
   user ever picked it (the UI never offered it). If the Genre section reads
   that field naively, every existing deck shows "Midrange" instead of the empty
   state.
   **Fix:** make `primary_archetype` nullable (default `null`) in the
   dataclass, the resolver, and the JSON schema. Treat a stored value of
   `midrange` with empty `secondary_archetypes`/`excluded_archetypes`/
   `custom_themes` as "never set" → `null`. That migration is safe because no
   UI path could have set it. The engine already falls back with
   `primary_archetype or "midrange"`, so analytics behavior does not change.
2. **New archetype ids need engine patterns or a skip.** The engine scores the
   deck against `ARCHETYPE_PATTERNS[primary]`. An id with no pattern entry
   (e.g. `landfall`) scores 0% and Deep Review reports "Low archetype
   alignment". Before shipping, either add patterns for the new ids or have the
   engine skip theme scoring for ids it has no pattern for. Skipping is the
   smaller change; recommend it.
3. **Do not put genres in `custom_themes`.** The engine text-matches
   `custom_themes` against card text, so "Horror" or "Budget" there would drag
   the consistency score down. Genres and kindred get their own `deck_genre`
   object. Add `deck_genre` to `SECTION_KEYS` in `journal.py` and to
   `known_keys` in `user_intent.resolve_intent_defaults`. Otherwise the resolver
   sweeps it into `metadata`.

## 1. Archetypes (strategy)

30 ids in three display groups: game plan, engine, and table dynamics. The 14
ids marked `existing: true` must stay byte-identical. `counters`, `mill` and
`lifegain` already have engine patterns but were missing from
`ALLOWED_ARCHETYPES`; add them.

Notes on the calls made:

- **`tribal` keeps its id; its label becomes "Kindred (type matters)".** Wizards
  renamed Tribal to Kindred in 2024. The id is stored data and a personality
  nudge key, so only the label changes. Selecting it says "this deck cares
  about creature types". *Which* types goes in the Kindred field. The two are
  independent: a Dragon deck that is really Ramp can list Dragon under kindred
  without picking Kindred as an archetype.
- **Graveyard is a strategy, not a genre.** The task listed it among genres,
  but "uses its graveyard" describes how the deck plays. The spooky flavor of
  a graveyard deck is the `horror` genre. `graveyard` (value/recursion) and
  `reanimator` (cheat fatties back) are both kept because players distinguish
  them.
- **Mill means milling opponents.** Self-mill is Graveyard Value. The
  descriptions say so, so users don't mis-file.
- **No custom archetypes.** The list is closed because the engine and the
  personality model key off these ids. Anything a user wants that isn't on the
  list is a genre (custom allowed there).

## 2. Genres / themes (flavor, vibe, identity)

33 seeded entries in three groups:

- **Flavor & setting:** what the deck is about as a story (Horror, Pirates,
  Heist).
- **Play vibe:** what it feels like at the table (Battlecruiser, Chaos,
  Political).
- **Build identity:** how and why it was built (Precon Upgrade, Budget, Pet
  Deck).

Every genre is descriptive, never evaluative. There is no "Competitive" or
"Casual" entry on purpose; power lives in its own fields.
`budget` describes the build ethos ("cheap on purpose"). The actual price caps
stay in `budget_constraints`.

## 3. Kindred (creature types)

- **Input:** a free-entry tag field with autocomplete against the official
  creature-type list.
- **Source:** `GET https://api.scryfall.com/catalog/creature-types`. It
  returned 351 types on 2026-10-16. Cache it in Redis (e.g.
  `catalog:creature_types`, refresh weekly). Ship a bundled snapshot as an
  offline fallback, because the journal must not break when Scryfall is
  unreachable.
- **Empty field:** show the `suggestions` list (35 types, most played first) as
  quick-pick chips. Every suggestion was checked against the live catalog.
- **Normalization on entry:** trim the input and match case-insensitively.
  Apply the `aliases` map for irregular plurals (Elves → Elf, Mice → Mouse).
  Otherwise strip a trailing `s`/`es` and re-match. Store the canonical
  catalog casing (`Elf`, not `elves`). Dedupe after normalization.
- **Groups:** Party and Outlaws are rules-defined groups. Picking one inserts
  its member types instead of storing the group name, so the data stays clean
  types only.
- **Custom values are allowed.** Users may enter things like "Changeling" or a
  fan nickname. Store the trimmed text (32 characters max). The UI marks it as
  custom with a muted chip. A value counts as custom when it is not in the
  catalog; no separate flag is stored.

## 4. Cardinality and custom values

| Field | Stored at | Count | Custom? | Empty state |
|---|---|---|---|---|
| Primary archetype | `archetype_preferences.primary_archetype` | 0–1, nullable | No | "No archetype set" |
| Secondary archetypes | `archetype_preferences.secondary_archetypes` | 0–3, cannot include primary | No | hidden |
| Genres | `deck_genre.genres` | 0–5 | Yes, max 32 characters | "No genre set" |
| Kindred | `deck_genre.kindred` | 0–5 | Yes, max 32 characters | hidden unless set |

- **Primary plus secondary, yes.** Most Commander decks are a blend
  ("Tokens with an Aristocrats finish"). A single primary keeps the chip row
  readable and gives the engine its anchor. The cap of 3 secondaries stops
  "select everything", which would make the tags meaningless. Picking a
  secondary is only possible once a primary is set. Choosing as primary a
  value that is already a secondary moves it up and out of the secondary list.
- **`excluded_archetypes` stays out of this section.** It is a tuning
  preference ("never suggest stax"), not self-expression. Leave it to the
  tuner UI.
- **Genre storage shape:** `genres: [str]`. Seeded entries are stored as their
  id (`horror`). Custom entries are stored as the user's trimmed text. A value
  is custom when it is not in the seed ids. On save, input matching a seed id
  or label case-insensitively is canonicalized to the seed id, so typing
  "horror" never creates a custom duplicate.
- **New shape:**
  `deck_genre: {genres: [], kindred: []}`. Default is empty, so existing
  decks are unaffected beyond the Midrange cleanup in §0.

### Deck-spirit note: don't add one

The journal already has two free-text fields: **Vision**
(`deck_vision.description`, "what is this deck") and **Notes**
(`freeform_notes`). A third box saying "describe the deck's spirit" would
duplicate Vision and split the same thought across two places.
**Recommendation:** add no new text field. Place the Deck Genre section
directly under Vision, so the Vision text reads as the spirit statement next
to its tags. If Andrew later wants a short tagline for deck cards or lists,
that is a separate, small card.

## Non-goals

- No power or bracket signal derived from any of these fields.
- No auto-detection of archetype or kindred from the decklist. The engine
  already detects archetypes. Pre-filling from it would be a later nice-to-have;
  the user's own words come first.
- No check-in row for genre in the review flow.
- No new archetype engine patterns beyond the "skip unknown ids" guard.
