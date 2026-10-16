# Deck Genre UX spec (GN-D): the Journal's Genre section

Status: design, ready to build. Mockups and docs only; no app code changed.
Builds on: `docs/DECK_GENRE_TAXONOMY.md` + `docs/deck_genre_taxonomy.yaml` (t_fbad3834; copied onto
this branch so everything is in one place). Consumed by: t_c1f47124 (implementation).
Base: `origin/main` @ b4ca96e (v0.4.4x, RV-4). **Build on `origin/main`**, not the local checkout's
`fix/sync-legacy-key-guard`, which predates the Journal.

| File | What |
|---|---|
| `docs/design/genre/index.html` | Interactive mockup. Opens from disk, loads the real `static/css/hud.css`. Dashed **mockup bar** = state picker (not product UI). |
| `docs/design/genre/genre.js` | Reference behaviour. `TagField` is written to be ported (ES5, plain DOM, same style as `deck_journal.html`). |
| `docs/design/genre/genre.css` | **KIT** block = CSS to port into `hud.css` §10 (Journal). **MOCK** block = mock chrome, don't port. |
| `docs/design/genre/taxonomy.js`, `creature_types.js` | Fixtures: the yaml as JSON; Scryfall's 351 creature types (2026-10-16). |
| `docs/design/genre/shoot.sh` | Re-shoots `docs/screenshots/genre/{state}_{1440,390}.png`. |

States (`index.html?state=…`): `empty`, `set`, `full`, `search`, `kindred`, `browse`.

---

## 0. What I looked at first

- `templates/deck_journal.html`: the Journal is **one always-editable form** (`hud-form`, a 13rem
  header column + fields), sticky anchor nav, one sticky save bar, dirty tracking over whole
  top-level keys, and a check-in that can open a section inline. There is no per-section
  view/edit toggle, and adding one would break that pattern.
  So **edit mode = the form section**, and **view mode = a compact chip strip in the deck header**,
  which every deck tab renders (`partials/deck_head.html`), including the Journal itself.
- Existing kit pieces reused: `hud.form_section`, `hud-field`, `hud-picker` + `hud-picker-list`
  (the pet-card combobox), `hud-chip` tones, `hud-btn--sm --ghost` "More… N", `hud-field-hint`,
  the save bar.
- **Name collision on main:** `/genres` (sidebar "Genres", RV-1) is the Deep Review *genre
  registry*: mechanical genres with card roles (Aristocrats, Storm, Voltron…), and the Review tab's
  "Genre" panel is computed from it. The taxonomy's "genres" are flavor (Horror, Budget, Pet Deck).
  Calling those "Genre" in the Journal would put "Genre: Horror" next to Review's
  "Genre: Aristocrats". Hence §1's labels.

## 1. Placement and naming

```
Journal  ─ anchor nav:  VISION · GENRE · WIN CONS · POWER & BRACKET · BUDGET · PETS & NOVELTIES · PILLARS
┌──────────────┬──────────────────────────────────────────────────────────┐
│ Vision       │ Intent · Vision textarea · Target win turn                │
├──────────────┼──────────────────────────────────────────────────────────┤
│ Genre   NEW  │ Archetype  [★ ARISTOCRATS ×][☆ TOKENS ×] Add another… 2/4 │
│ blurb        │   + Voltron + Spellslinger …        hint · Deep Review read│
│              │ Themes     [Horror / Gothic ×][Budget ×] Add another… 2/5 │
│              │   + Precon Upgrade + Pet Deck …                      hint │
│              │ Kindred    [Zombie ×][Vampire ×]        Add another… 2/5 │
│              │   + Party +4  + Outlaws +5  + Elf + Goblin … More… 25 hint │
├──────────────┼──────────────────────────────────────────────────────────┤
│ Win cons     │ (unchanged)                                               │
```

- New `hud.form_section("j-genre", "Genre", …)` **directly after `j-vision`** (taxonomy decision:
  Vision is the sentence, Genre is the tags; no separate deck-spirit note).
- Anchor nav gets `<a href="#j-genre">Genre</a>` second.
- Field labels: **Archetype**, **Themes**, **Kindred**. The section title stays "Genre" (Andrew's
  word). It holds the archetype, which is the user's claim of what Review's computed "Genre"
  measures. The flavor list is labelled **Themes** in the UI and stored as `deck_genre.genres`
  (storage name per the taxonomy; the label is UI only).
- Section blurb: "What the deck is: how it plays, what it's about, which creature types it loves.
  Vision above is the sentence; these are the tags."

## 2. Data contract (unchanged from the taxonomy; repeated so the UI is unambiguous)

| Field | Payload key | UI value | Max | Custom |
|---|---|---|---|---|
| Archetype | `archetype_preferences.primary_archetype` + `.secondary_archetypes` | one ordered list; index 0 = primary | 4 total (1 + 3) | no |
| Themes | `deck_genre.genres` | seed id (`horror`) or custom text | 5 | yes, ≤ 32 chars |
| Kindred | `deck_genre.kindred` | catalog spelling (`Elf`) or custom text | 5 | yes, ≤ 32 chars |

`collect()` adds two keys (`genre.js` → `toPayload`):

```js
archetype_preferences: { primary_archetype: arch[0] || null, secondary_archetypes: arch.slice(1),
                         excluded_archetypes: extras.excluded_archetypes, custom_themes: extras.custom_themes },
deck_genre: { genres: themes, kindred: kindred }
```

- `excluded_archetypes` and `custom_themes` are **not shown** here. Carry them through untouched in
  `extras`, the way `custom_win_conditions` is carried today.
- Add both keys to `KEYS` (dirty tracking) in `deck_journal.html`. `fill()` sets the three fields
  from `j.archetype_preferences` / `j.deck_genre`.
- Archetype ids are always lowercase snake ids. Custom themes and kindred keep the user's trimmed
  text, with inner whitespace collapsed to single spaces.

## 3. Components

### 3.1 `TagField` (new kit component; one implementation, three configs)

Anatomy (all three fields):

```
Label                                                   n / max   ← .hud-tagfield-head (+ .hud-tagfield-count)
┌ .hud-picker ───────────────────────────────────────────────────┐
│ [chip ×] [chip ×] … <input role=combobox placeholder=…>         │ ← chips then input, wraps
└─────────────────────────────────────────────────────────────────┘
  ┌ .hud-picker-list (listbox, floats, max-height 20rem) ┐          ← on type / ArrowDown / click
ADD  [+ quick] [+ quick] … [More… N]                             ← .hud-tagfield-picks (hidden at max)
hint text                                                         ← .hud-field-hint
(Archetype only) Deep Review's read: Aristocrats 72% · Tokens 54% · Open Review
```

| Config | Archetype | Themes | Kindred |
|---|---|---|---|
| options | 30 archetypes, 3 groups | 33 genres, 3 groups | 351 creature types (flat) |
| max | 4 (first = primary) | 5 | 5 |
| custom | no | yes | yes |
| placeholder (empty) | `Search 30 archetypes…` | `Search or add a theme…` | `Search 351 creature types…` |
| placeholder (some) | `Add another…` | same | same |
| placeholder (full) | `4 of 4 · remove one to add` | `5 of 5 · …` | `5 of 5 · …` |
| quick picks (first N not yet picked) | 6 of: tokens, aristocrats, voltron, spellslinger, counters, ramp, combo, control, graveyard, blink | 6 of: budget, precon_upgrade, pet_deck, battlecruiser, horror, meme, political, flavor_first | groups Party/Outlaws, then 8 of the 35 yaml `suggestions` + `More… N` (rest of the 35) |
| hint | How the deck plays. The first is the primary (★); up to 3 more. Feeds Deep Review and the tuner. | Flavor, vibe, why you built it. Up to 5; type your own if it's not listed. Labels only: never scored. | Creature types the deck cares about. Up to 5. Picking Kindred as an archetype is separate and optional. |

The option lists come from the yaml constants. The 351 creature types come from Redis
`catalog:creature_types` (fall back to the bundled snapshot). Render them all into the page with
`| tojson`, the same way `CARDS` is rendered for the pet picker. That's about 4 KB and needs no extra
request or loading state.

### 3.2 Chips (inside the field)

| Value | Classes | Look |
|---|---|---|
| Primary archetype | `hud-chip hud-chip--signal hud-tagchip` + `<span class="hud-chip-star is-on">★</span>` | amber, uppercase mono |
| Secondary archetype | `hud-chip hud-chip--data hud-tagchip` + `<button class="hud-chip-star">☆</button>` | cyan (same as `hud-seg--multi` checked), uppercase mono |
| Theme / kindred (seed or catalog) | `hud-chip hud-chip--ghost hud-tagchip` | outline, sentence case |
| Custom theme / kindred | `hud-chip hud-chip--ghost hud-chip--custom hud-tagchip` | **dashed** border, muted text, `title="Custom theme"` / `"Custom: not an official creature type"` |

Every chip ends in the existing `hud-chip-remove` (×, `aria-label="Remove {label}"`). The label sits
in `.hud-tagchip-text` (ellipsis if the column is narrower than the chip). Seeded chips carry the yaml
`desc` as `title`.

### 3.3 Listbox rows

- Seeded fields: group heading rows (`li.hud-picker-group`, `role="presentation"`), then options
  with the label on line 1 and the yaml `desc` on line 2 (`<small>`). The matched substring is
  wrapped in `<mark>` (amber text, no background).
- Already-picked options stay listed but `aria-disabled="true"` with " · added".
- Custom row (Themes and Kindred, only when the query isn't an exact seed or catalog match):
  `li.is-create`: **+** Add "{query}" as a custom theme / creature type.
- Note rows (`li.hud-picker-note`, not selectable): at max, "Limit reached (N). Remove one
  first."; Kindred with an empty query, "Type to search 351 creature types, or use a quick pick
  below."; Archetype with no match, "No archetype matches "x". Archetypes are a fixed list: try a
  theme instead."

### 3.4 Header strip (view mode): `#deckGenre.hud-genre-strip` in `.hud-deckhead-meta`

```
Holy Hellfire
(W)(B)(R)  ■ HAVE  100 CARDS · 35 LANDS  [Aristocrats] (Zombie) (Horror / Gothic) [Tokens / Go-Wide] (+2)
                                          └ filled = archetype   └ outline = kindred/theme        └ link
```

- Order: primary archetype, first kindred, first theme, then the remaining secondaries, kindred
  and themes. One of each comes first because "Aristocrats · Zombie · Horror" says the most.
- Desktop shows 4 chips + `+N`; phone (≤ 640px) shows 2 + `+N`. Render all chips with
  `data-rank="1..4"` plus two "more" chips (`.hud-chip-more-desktop` = items−4,
  `.hud-chip-more-phone` = items−2). CSS hides by breakpoint, so no resize JS is needed.
- Archetypes use `hud-chip--neutral` (filled). Kindred and themes use `--ghost`, custom ones
  `--custom`. The whole strip is **sentence case** so the row reads as one line of tags. A target is
  a choice, not a status, so the strip is never amber (same rule as the bracket chip, BR-D §1.1).
- `+N` is an `<a class="hud-chip hud-chip--neutral">` to `/deck/{name}/journal#j-genre`, with
  `title` = the hidden labels, comma-separated, and `aria-label="N more genre tags: …. Open the
  Journal."`. Chips are max 14rem with an ellipsis, and `title="Archetype: Tokens / Go-Wide"`.
- Empty: the strip is `hidden` and the header shows nothing. No nag; the Journal handles the empty
  state.
- Data: `_deck_head_context` adds `genre = {archetype:[ids], kindred:[…], themes:[…]}` from
  `journal:deck:{registry_id}` (one GET; wrap it in try/except like the other head fields), with
  labels resolved server-side. On the Journal page, a successful Save re-renders `#deckGenre` from
  the saved journal (`stripHtml` in `genre.js`), so the header never shows unsaved values.

## 4. Interactions

**Adding a value** works four ways: a quick pick (one tap), typing then Enter, clicking or tapping
a listbox row, or (Kindred only) a group button.

- **Opening the listbox:** it opens on typing, ArrowDown, or a pointer click in the input. It does
  *not* open on plain focus, so tabbing through or focus returning after × doesn't pop a 30-row
  list. On Archetype and Themes, an empty query lists every option grouped, which works as browse.
- **While typing**, the first enabled option is active (`aria-activedescendant`), so Enter takes
  the best match. ↑/↓ move and skip disabled rows. Esc closes. After a pick, the input clears and
  the list closes (same as the pet picker).
- **Ranking for seeded lists:** label prefix, then label/id substring, then description substring
  ("sacrifice" finds Aristocrats). Results stay inside their groups.
- **Kindred search:** normalize first (taxonomy §3: trim, case-insensitive, `aliases`, strip
  trailing `es`/`s`). If the normalized value is in the catalog, it is the top row, with a "from
  "elves"" note when the spelling changed. Then come prefix matches, then substring matches, 8 rows
  max. The custom row appears only when nothing normalizes to a catalog type.
- **Canonicalization:** themes typed as a seed id or label (case-insensitive) store the seed id
  and never create a custom duplicate. Duplicates (case-insensitive) aren't added; the existing chip
  flashes (`.is-flash`, 700 ms) and the live region says "{X} is already added".
- **Custom text:** the input has `maxlength=32`. Trim, collapse spaces, store the text as typed.

**Archetype primary rules** (one ordered list, index 0 = primary):

```
add      → no primary yet ? becomes primary : appended as secondary
☆ on X   → X becomes primary; the old primary becomes the FIRST secondary (total unchanged)
× on ★   → the first secondary is promoted to primary (announced); none left → primary = null
```

So "a secondary needs a primary" holds by construction, and the cap is simply 4 chips.

- **Kindred groups** (Party +4, Outlaws +5): a tap inserts every member type not already present,
  never the group name. The button shows how many it would add. It is `disabled` when that's 0 or
  more than the free slots, with `title` "Party adds Cleric, Rogue, Warrior, Wizard (needs 4 free
  slots, 3 left)".
- **At max:** the quick-pick row hides, the count turns amber, the placeholder changes, and typing
  shows only the limit note. The input stays enabled so Backspace still works.
- **Removing:** use ×, or Backspace in an empty input (removes the last chip). Focus returns to the
  input.
- **Keyboard order:** chips (☆, ×), then the input, then the quick picks. Tab order stays native;
  there are no custom key traps. The page's ⌘/Ctrl+↵ save keeps working. j/k/y/c are already
  ignored while typing (`A.inTextField`).
- **Screen readers:** the field is `role="group"`, labelled by its label and described by its hint.
  A polite live region (`.hud-sr`) announces: "Added Aristocrats as primary", "Added Tokens as
  secondary", "Added "Space western" (custom)", "Removed Aristocrats. Tokens is now primary",
  "Tokens is now primary. Aristocrats moved to secondary", "Added Party: Cleric, Rogue, Warrior,
  Wizard", and "Limit reached: 4 archetypes". The primary chip carries hidden text "Primary: ".
- **Save and Discard:** these are the page's existing ones. The save bar's dirty summary adds
  " · genre" when either key is dirty (like " · power 7 → 8"). Discard runs `fill(base)`. A server
  400 shows in `#saveError` as today; the client rules above should make that rare.

**Deep Review read** (Archetype only, a `hud-field-hint.hud-tagfield-read`): if
`GET /api/decks/{id}/review` returns a doc, show its `sections[type=genre].data`:
"Deep Review's read (computed from the cards): **Aristocrats** 72% · **Tokens** 54% · Open Review",
linking to `/deck/{name}/review`. Append " · stale" if `review.stale`. Hide it when there's no
review or no `primary`. It is informational only. There is no mismatch warning, because registry
genres and archetype ids aren't mapped (see §8). This is the Journal's usual "your target next to the
computed estimate" pattern, and it is the one place the two "genre" ideas meet.

## 5. States

| State | Header strip | Section |
|---|---|---|
| **Empty** (new deck, or no journal) | hidden | 0/4, 0/5, 0/5, empty placeholders, quick picks visible, no Review read. *Mock: `empty`.* |
| **Phantom Midrange** (journals saved before this ships) | hidden | Must look **empty**. The backend migration nulls the default `midrange` (taxonomy §0); the UI just renders `null`. |
| **Typical** | 4 chips + `+2` | *Mock: `set`.* |
| **Full / long / custom** | 4 (2 on phone) + `+10` / `+12` | All caps reached: amber counts, no quick picks, long labels wrap per chip, dashed custom chips. *Mock: `full`.* |
| **Searching** | — | grouped matches + custom row (*`search`*: "noir" → Crime / Heist / Noir + Add "noir"); alias (*`kindred`*: "elves" → Elf from "elves"); browse (*`browse`*: empty query on Archetype). |
| **Catalog unavailable** | — | No visible change: the bundled snapshot serves the list. |
| **Saving error** | unchanged | existing `#saveError` text in the save bar |

## 6. Phone (≤ 640px)

- The form is already one column (header above fields, from the 900px rule). Nothing new is needed
  for the layout.
- In the picker, the input takes a full row (`min-width: 100%`) under the chips, so chips never get
  squeezed by the input.
- Touch sizes: chips 1.75rem tall; × and ☆ at least 1.5rem; quick picks 2rem (the kit's ≤ 640
  bump is 2.25rem for full-row buttons; these are inline and wrap).
- The listbox stays a floating panel at the field's width (as the pet picker does). Descriptions
  are visible in it, which matters because there's no hover for `title` on phones.
- Header strip: 2 chips + `+N`.
- Screens: `docs/screenshots/genre/*_390.png`.

## 7. Style-guide notes

- Tokens only. Port the **KIT** block of `genre.css` into `hud.css` §10 (Journal):
  `.hud-sr`, `.hud-tagfield*`, `.hud-tagchip*`, `.hud-chip--custom`, `.hud-chip-star`,
  `.hud-tagopt*`, `.hud-picker-group`, `.hud-picker-note`, `.hud-genre-strip`, `a.hud-chip`.
  Add a row for each to `/styleguide` next to the pet picker.
- Colour meaning: amber is reserved for *selection* (primary chip, active row, focus, full count).
  Cyan marks secondary selections (matching `hud-seg--multi`). Dashed means user-made. Mana colours
  are never used.
- Type: archetype chips in the field are uppercase mono (fixed vocabulary, like the seg labels).
  Themes and kindred are sentence case. Card-name Garamond is **not** used here; the tag field
  overrides the pet picker's `--font-card`.
- `.hud-tagfield .hud-picker input` is pinned to the chip height (1.5rem), so the placeholder sits
  on the chip baseline.

## 8. Out of scope, and the open question for Andrew

- Not in scope: a check-in row (taxonomy non-goal), deck-list filters by genre, auto-suggesting
  values from the decklist, and `excluded_archetypes` UI (that belongs to the tuner).
- **Open (doesn't block the build):** there are now two archetype-like lists. One is the journal's
  30 archetype ids (engine-facing, `archetype_preferences`). The other is the user-editable `/genres`
  registry (13 seeds that Deep Review labels decks from). This design keeps them separate and shows
  Review's read beside the user's archetype. If Andrew wants one list, the Archetype field could
  source its options from the registry later. The TagField takes any grouped option list, so the
  UI wouldn't change; only the data source and the engine mapping would.

## 9. Build checklist (t_c1f47124)

1. `deck_journal.html`: add the section after Vision, the anchor link, and two containers per
   field. Port `TagField`, `toPayload` → `collect()`, `fill()`, `KEYS`, the dirty summary, and the
   post-save strip refresh.
2. `app.py` journal route: pass `genre_options` (yaml constants) + `creature_types` to the template.
   `_deck_head_context`: add `genre`.
3. `partials/deck_head.html`: the `#deckGenre` strip macro (server render, same markup as
   `stripHtml`).
4. `hud.css` §10: KIT block. `styleguide.html`: samples.
5. Tests: payload round-trip (null primary, primary-only, 1+3, custom theme/kindred, alias
   normalization "Elves"→"Elf", >max rejected with 400). For the header: an empty journal renders no
   strip, and a phantom-midrange journal renders no strip.
