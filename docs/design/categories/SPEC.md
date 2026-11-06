# Category page: UX spec and components (CT) · v2

Task `t_dabf59d0`, child of `t_f6404946`. Implementation: `t_b54b7ad9`. Taxonomy: Oracle's **categories-v1** (comment #39 on `t_9a3eb6c1`), which this spec follows. v2 replaces my first pass (editable core categories, tag-matching): core is now locked, membership comes from auto-tag, custom categories use an optional *suggestion rule*.
Surface: new page **`/categories`**, sidebar entry next to **Genres**. HUD kit (`static/css/hud.css`), no new tokens.
Mock: `mock.html` (open from disk; loads the real `hud.css` + `pd.css` + `cat.css`). Screenshots: `shots/`.

## TL;DR

- **"The Archetype page" is `/genres`** (`templates/genres.html`). Same shell: registry list left, editor right, same panels, row anatomy and save row.
- **One page, two scopes:** **Global | Deck-specific** switch. Global = definitions and template numbers. Deck-specific = a deck picker and that deck's live counts, plus its own categories.
- **Three tiers, one list.** Core (11 rows in 5 groups, locked), Custom (global), This deck (max **10**, shown `n/10`).
- **Rows show:** count, **template target** and **floor** (`–` where none exists, never invented), a status chip, **★ Commander** when the commander does the job, and **GC** count where Game Changers cluster (Tutors).
- **Interaction is a group header** over Removal · Counterspells · Board Wipes, showing the **distinct** card count.
- **Auto-tag theme tags are not core.** They appear as one-click **Suggested** chips in the deck scope: *+ Sacrifice*.
- **◇ Draft/Improve exists for custom categories only**, same button, states and keys as the pillar assist. Never auto-saved. Can answer *"This is core Ramp. Use it instead?"* Alias names ("Draw", "Wincon") are blocked **before** any request.

## What changed from v1 (and why)

| v1 | v2 | Source |
|---|---|---|
| Core description + tags editable, Reset to default | **Locked.** Only the per-deck **target** is editable | auto-tag, floors and the composition template depend on the definitions |
| 6 core categories | 11 rows + Interaction header, 5 groups | §2 |
| "Match tags" chips | Gone. Membership = auto-tag (core) or user adds (custom). Optional **Suggestion rule** | CAT-0: no fifth classifier |
| Count only | Count + target + floor + status + commander badge + GC | §2, §3, §6 |
| Core/global shown as "inherited, read-only" in deck scope | Core rows are **live** in the deck scope (that is where the counts are) | counts are per deck |
| Free deck-category count | Cap **10**, `n/10` in the group header | §4.1 |
| Assist on every category | Custom only; `duplicate_of`; alias block; `rule` + `flagged` | §4.2, §5 |

## System (all existing)

`hud.page_head`, `hud-panel` ×2, `hud-seg` (scope), `hud-field`, `hud-chip`, `hud-btn`, `hud-kbd`, `hud-card-name` (members), `.gn-*` shape copied as `.ct-*`. Assist = `pd.css` as is (promote into `hud.css` §7 when this lands). New, page-level: `cat.css`, tokens only (~130 lines).

Chip tones carry meaning, never hue alone (every chip has its word): `ok` green · `short` amber · `low` red · `over` grey · `★ Commander` cyan · `GC` amber · `this deck` cyan.

## Wireframes

### A. Global scope

```
│ SCOPE [GLOBAL][DECK-SPECIFIC]                                    11 CORE · 2 CUSTOM    │
├────────────────────────────────────┬─────────────────────────────────────────────────┤
│ REGISTRY               15 categories│ RAMP                                       core │
│ [ Filter ( / )                    ]│ ▌⌖ Core category. Definition is locked: …       │
│ MANA                             3  │ NAME        [ Ramp                  ⌖ locked ]  │
│ ▌Ramp                          10   │ GROUP [Mana]        CARDS COME FROM [autotag ramp]│
│  Makes extra mana…       FLOOR 10   │ DEFINITION  Makes extra mana, puts extra lands… │
│  Fixing                         –   │ TARGET (template) [10]     FLOOR [10]           │
│  Lands                         38   │ Commander MV ≥ 5 adds 2 to the floor. Targets:… │
│ CARDS                            3  │ Members are per deck. Switch to Deck-specific → │
│ INTERACTION  Removal · Counter… 3   │                                                 │
│ DEFENSE 1 · CLOSING 1               │                                                 │
│ CUSTOM · all decks               2  │                                                 │
│  Artifact Removal             3 decks                                                 │
```

Right-hand block per row: global core = **target** (big) + `floor n`; custom = **decks that use it**.

### B. Deck-specific scope

```
│ SCOPE [GLOBAL][DECK-SPECIFIC]  DECK [Edgar Markov ▾]   99 CARDS · 2/10 DECK CATEGORIES │
├────────────────────────────────────┬─────────────────────────────────────────────────┤
│ MANA                             3  │ WIN CONS                                   core │
│  Lands [ok]                    37   │ ▌⌖ Core category. Locked…                       │
│                        TGT 38 · FLR –│ … DEFINITION …                                  │
│  Ramp [low]                     9   │ TARGET FOR THIS DECK [ – ]       FLOOR [ 0 ]    │
│                       TGT 10 · FLR 10│ Your commander does this job, which lowers the │
│ CARDS 3 …  Tutors [2 GC]        4   │ floor by 3.                                     │
│ INTERACTION  Removal · …  10 DISTINCT│ IN THIS DECK · 4                                │
│  Removal [low]  8 · Counterspells ↑ │ ★ COMMANDER  Your commander does this job.      │
│  Board Wipes [short]            3   │ ┌ Impact Tremors ─────────┐                     │
│ DEFENSE · CLOSING Win Cons ★ 4      │ │ Vampire Nighthawk       │                     │
│ CUSTOM · all decks               2  │ └─────────────────────────┘                     │
│ EDGAR MARKOV · this deck only 2/10  │                                                 │
│  Vampire Lords [this deck]      3   │                                                 │
│ SUGGESTED · from this deck's themes │                                                 │
│  [+ Tokens] [+ Sacrifice] [+ Graveyard]                                               │
```

Order, top to bottom: core groups (the health picture), Custom, **This deck n/10**, **Suggested**. The filter and `J/K` reach everything; the scope summary line carries the totals so nobody scrolls to learn "2/10".

### C. Phone (≤900px)

List **or** editor. Tap a row: editor slides in from the right (`translateX(12px)`+fade). **← Categories** returns. Scope bar stacks. Two-up field rows (`.ct-two`) go single column. 40px min tap height. No horizontal scroll at 390px (verified).

## Row anatomy

```
Name  [status] [★ Commander] [2 GC]                     9   ← count (deck) / target (global core) / decks (custom)
1-line description (core) / 2-line (custom)       TGT 10 · FLR 10   ← mono micro caption
```

| Rule | Behaviour |
|---|---|
| `–` | No published number. Never invent one. Tutors, Recursion, Protection, Fixing are count-only |
| **Status chip** | One scale only: **low** if count < floor, else **short** (≥2 under target) / **over** (≥2 over) / **ok**. Uses `composition.delta_status` + autotag `low`; no third scale |
| **No status below 90 cards** | Counts only. Mock: *Half-built (60 cards)* toggle; summary line says *no statuses below 90* |
| **Counterspells** | `↑ with Removal`: the floor 10 covers Removal + Counterspells together, so no second number |
| **★ Commander** | Commander does the job: chip on the row + editor note. The commander is **never in the count** (C1) |
| **GC** | Game Changer count next to the row count (Tutors: `2 GC`). Member cards carry `GC`, `Stax`, `MLD`, `Extra turns` flag chips (`title` spells them out) |
| Floor modifiers | Autotag §7.2 unchanged: commander MV ≥ 5 adds 2 to Ramp; commander does the job = −3. The editor says which applied |
| Interaction header | `10 distinct cards` (union). A card in Removal and Win Cons counts once under the header, once per row |

## Core category editor (locked)

Amber note *Core category. The definition is locked: …*. All read-only text (not `disabled` inputs): **Name** (⌖ locked), **Group**, **Cards come from** (`autotag ramp`, `front-face type line` for Lands), **Definition** (the seed text), **Floor**.
The **only editable field is Target**, and only in the deck scope (per-deck override, stored with `composition:template:{registry_id}`): number input, **Save target**, **Reset to {template}**. Global scope shows the template target read-only with *Switch to Deck-specific →*. Rows with no published target show a plain `–` and no input.
**In this deck · n** lists member cards (card font, flag chips). Removing a card from a core category is a membership action elsewhere; this page does not add or remove core members.

## Custom category editor (global and deck-specific)

| Field | Rule |
|---|---|
| **Name** | ≤32 chars. Unique in scope (a deck category can't reuse a global name in that deck). **Alias check** runs as you type (below) |
| **Applies to** | Read-only: `Global · every deck` / `Deck · {name}` |
| **Group** | Optional: one of the 5 core groups, else *Deck roles (none)* |
| **Serves pillar** | Deck scope only. Optional. **Display link only**, never edits a pillar |
| **Description** | `.pd-f` textarea (400 = `DESC_MAX`), ◇ button in the label row. Hint: *Start with "Cards that…", then say what does not count. Describe the job, not the power level.* |
| **Suggestion rule** | One optional rule: *No rule · Autotag theme (select of the 12) · Kindred type (text) · Type line contains (text)*. Helper: *only fills the Suggested list; never adds cards by itself* |
| **Cards** (deck scope) | Member list + **Suggested by rule** (dashed list, **Add** per card). Accepting is always one card at a time |
| Actions | Save · Reset · `Unsaved` · **Make global** (deck only) · **Delete** (confirm, cards keep their other categories) |

`interaction:<side>:<resource>` rules from Oracle §4.3 are not in the mock's rule picker; they need the vocabulary list from the backend. Same row, one more kind.

### Name checks, in order

1. **Alias collision**, inline under Name, `role=alert`, *before any request*: `That's the core category Card Draw. Use it instead. [Open Card Draw]`. "Interaction"/"Disruption" → *That's the Interaction group…*. The ◇ button goes grey with *Drafting is off while the name is a core category.* Save is blocked on the same rule. Normalisation (Oracle §4.2): lowercase, collapse spaces, ignore one trailing "s". "Counters", "Reanimation", "Artifact Removal" are allowed.
2. **Duplicate** in scope → *A category with that name already exists.*
3. **Cap**: 10 deck categories. At 10, **New** and the Suggested chips are `aria-disabled` with a title; Save says *This deck already has 10 categories. Past that they stop being roles.*

## Auto-description (custom categories only)

Same component as the pillar assist (`t_7f5eff24`): one **◇ Draft** (empty) / **◇ Improve** (has text) button in the Description label row; **Alt+G**; loading = cyan + `aria-busy` + skeleton + **Cancel Esc**; result lands in the **dashed suggestion row** with **Use ⌘↵** (**Replace** for Improve) · **Another** · **✕ Esc**; Improve-Use leaves **Undo**; provenance `◇ Drafted` / `◆ Yours`. Failure never touches the text. Error rows and copy are the pillar spec's verbatim.

Differences for categories:

| Case | UI |
|---|---|
| **Rule returned** | Dashed chip `+ rule autotag:sacrifice` inside the row, a toggle. **Use** applies description **and** the rule, unless the rule is toggled off or you already set one |
| **`duplicate_of`** | Row reads *This is the core category **Ramp**. Use it instead?* **[Open Ramp]** · **Keep mine Esc**. Nothing applied, text untouched |
| **`flagged`** (rule failed validation) | Row meta in amber: *Dropped a rule that isn't allowed: …*. Description still usable |
| **Context line** | Global: *drafted from name + core definitions*. Deck: *name + deck context* (commander, themes, pillars, cards) |
| **Theme suggestion click** | Opens New pre-filled (name, rule) **and fires Draft immediately**. The row appears; nothing is created until Save |
| **Core categories** | No button. Definitions are fixed seed text, never generated |
| **Imported (Archidekt) category, empty description** | Opens with the button and hint *Name it or jot…* ready. Same behaviour |

Style contract (backend, Oracle §5): membership test, starts *Cards that…*, optional *Not: …*, function not power, no card names, ≤400. UI shows the result as is; it doesn't re-police it, but it **does not render model text as HTML** (`textContent`/`esc()`).

## Contract the UI assumes

Backend owns the shape (see Oracle's §4–5); only the page script's `api()` and `describe()` change.

```
GET    /api/categories?deck_id=      → { version, groups:[…], categories:[{ id, scope:"core"|"global"|"deck", group?, name, description,
                                          source?, target?, floor?, count, gc?, commander_does_this?, rule?, pillar_id?, deck_id? }],
                                          deck?:{ cards, commander_mv, categories_used, cap:10 }, suggestions?:[{ theme, rule }],
                                          interaction_distinct? }
POST   /api/categories               { name, description, group?, rule?, pillar_id?, scope, deck_id? }   → 409 {error} on name clash, 422 alias {core_id}, 409 cap
PUT    /api/categories/{id}          custom: any field except scope. core: 403
DELETE /api/categories/{id}          403 for core
PUT    /api/decks/{id}/category-targets/{core_id}   { target } / DELETE to reset
POST   /api/categories/{id}/promote  deck → global
GET    /api/categories/{id}/members?deck_id=   → { members:[{name, flags:["game_changer","stax","mass_land_denial","extra_turns"]}], suggested:[…] }
POST   /api/categories/describe      { name, description?, hint?, group?, deck_id?, avoid?:[str] }
                                     → 200 { success:true, description, rule:str|null, duplicate_of:core_id|null, flagged?:[{rule,reason}], mode }
                                     → 4xx/5xx { success:false, error }   // 422 {error_kind:"alias", core_id} for alias names; no model call
```

`describe` is stateless, never saves. The page performs the **same alias check client-side** so the button greys out without a round trip; the server stays authoritative.

## States

| State | Behaviour |
|---|---|
| Load | `Loading…` in list, editor empty |
| Global, core selected | Locked editor, template numbers |
| Deck, core selected | Locked editor + target input + member list |
| Deck, no categories | Empty block in the deck group (*The 11 core categories already apply…*), Suggested still shown |
| Deck, < 90 cards | Counts, no status chips, summary line says so |
| Deck, 10/10 | New + Suggested disabled with reason |
| New / dirty | Title *New category for {deck}*, `unsaved` in head, `Unsaved` by Reset. Leaving: `confirm()` as Genres does |
| Name = alias | Inline error + link, ◇ grey, Save blocked |
| Assist states | Draft idle / loading / suggestion / `duplicate_of` / rule dropped / error / unavailable (mock bar toggles each) |
| Saved | `Saved.` in `--ok`, selection kept |

## Keyboard

`N` new in this scope · `/` filter (`Esc` clears) · `J`/`K` next/prev row · `Alt+G` Draft/Improve · `⌘/Ctrl+↵` Use the suggestion, else Save · `Esc` dismiss suggestion. Shortcut hints hide on `(hover: none)`.

## Accessibility

- List is a `<ul>` of `<button aria-current>`; group headers are `role=presentation` with visible text. Group counts are text, not colour.
- Scope switch = radiogroup; deck picker = native select; rule picker = native selects.
- Locked values are real text with a word next to the glyph. Not `disabled` inputs.
- Status chips are words (`low`, `short`, `ok`, `over`), flag chips are words (`GC`, `Stax`) with a `title`.
- Suggestion row `role=status` (`alert` on error). The rule chip is a toggle with `aria-pressed`; off = struck through **and** grey.
- Alias error is `role=alert`, tied to the input by `aria-describedby`; the Open link is a real link.
- Disabled-looking buttons use `aria-disabled` and stay focusable (so the reason is reachable).
- AA contrast from existing tokens only. Focus is the HUD 1px signal outline, inset on list rows.
- `prefers-reduced-motion`: animations off; skeleton static; ◇ static.

## Implementation notes (for `t_b54b7ad9`)

- Copy `genres.html` → `categories.html`. Genres' `api()`, `msg()`, `el()` and the **autofill ticket** (race guard) carry over; use the ticket for `describe`, scope switches and deck changes.
- Route `GET /categories` in `app.py`; nav item in `base.html`.
- Core rows come from the backend with `count`, `target`, `floor`; **the page does no counting**. Statuses use the server's numbers so Deep Review and this page agree.
- Don't re-render the editor per keystroke. The mock updates assist pieces in place (`sync()`, `renderSugg()`); `use()` re-renders once because the rule field changes.
- Render the editor **after** making its panel visible on phone: `grow()` reads `scrollHeight` (a 1-line-textarea bug I hit in the mock).
- `Use` via `execCommand('insertText')` in the real page so native Ctrl+Z works (the mock sets the value for simplicity on Use because it re-renders).
- **Required backend fix** (Oracle §4.5): suppression marker so removing Sol Ring from Ramp survives an auto-tag re-run. The UI assumes it.

## Flags

1. **Name collision:** `/api/tags/core` and the Tags page "Core Archetype Tag" already use "core" for archetype-defining tags. Categories' "Core" means built-in. Recommend renaming the Tags-page copy to "Archetype tag" before ship. (Carried from v1.)
2. **`deck_categories:{id}`** (per-card override layer, `docs/DECK_MANAGEMENT_VIEW.md`) overlaps deck-specific categories. Oracle recommends `cat:<id>` entries in `deck_card_tags`. The old layer should be migrated or retired; backend call.
3. **Counting conventions differ:** composition.py counts the commander, autotag doesn't. This page follows autotag (C1). The Deep Review panel will disagree until composition reads these counts (Oracle's later card). Worth a visible note on Deep Review, not on this page.
4. **Seed list** is Oracle's 11 + Interaction header. I did not trim to the three Andrew named.

## Considered, not doing

- **Editable core definitions / per-deck description overrides.** Locked by decision; a deck category with its own name covers it.
- **Free-text tags / colour per category.** Colour already means state; membership is auto-tag or user adds.
- **Separate pages per scope.** 90% shared.
- **Auto-assigning cards by rule.** Rules only suggest.
- **A third status scale, or inventing targets** for Tutors/Recursion/Protection/Fixing.
- **Modal editor.** Persistent right panel, as Genres.
- **Deck-first ordering** in the deck scope. I put core health first; the deck's own categories sit under it. If that proves wrong in use, swapping groups is a one-line order change.

## Reviewer checklist

1. `mock.html` opens from disk, no console errors. **Global** shows 5 groups + Custom; **Deck-specific** (Edgar Markov) shows counts, `TGT/FLR`, status chips, `★ Commander` on Win Cons, `2 GC` on Tutors, `10 distinct cards` on Interaction, `2/10`, Suggested chips.
2. Ramp: everything read-only except **Target** (deck scope). Win Cons: floor drops by 3, commander note. Counterspells: `↑ with Removal`, no inputs.
3. Mock bar **Half-built (60 cards)**: statuses vanish, summary says so. **No deck categories**: empty block + suggestions.
4. New → name `Draw`: inline alias error with *Open Card Draw*, ◇ grey, Save blocked. `Wincons` and `Interaction` likewise. `Counters`, `Artifact Removal` are accepted.
5. Click **+ Sacrifice**: editor opens pre-filled and a Draft appears; **Use** applies text; no category exists until **Save**.
6. **Duplicate of core** then ◇: *Use it instead?* row. **Rule dropped**: amber note. **Fails / Unavailable / Slow**: pillar-spec rows.
7. Vampire Lords: **Suggested by rule** → **Add** moves a card up.
8. 390px: list → tap → editor slides in → **← Categories**. No horizontal scroll.
9. `N`, `/`, `J/K`, `Alt+G`, `⌘↵`, `Esc`.

## Not verified

Mock only. No backend, no screen reader pass, Chromium at 1440 and 390 only. Counts, targets and members are fixtures (Edgar Markov numbers are made up to exercise every status). `interaction:` rules, delete, promote and the 10-cap path are not driven in the mock beyond the checks above; the cap logic exists but I did not screenshot it.

## Files

| File | What |
|---|---|
| `mock.html`, `cat.js`, `cat.css` | Mockup. `cat.css` is the KIT (port it); `.ct-demo` / `.ct-mocknote` at the bottom are mock chrome |
| `pd.css` | Copy of the pillar assist styles; promote into `hud.css` §7, don't duplicate |
| `shots/` | 01 global · 02 deck scope · 03 locked core editor (Win Cons) · 04 theme suggestion loading · 05 theme suggestion · 06 alias blocked · 07 duplicate of core · 08 half-built (no statuses) · 09 custom deck editor + suggested cards · 10 phone list · 11 phone editor + suggestion |
