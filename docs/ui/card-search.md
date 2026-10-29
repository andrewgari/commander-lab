# Card search to add (shared SvelteKit module)

Status: design. No code in this doc's PR.
Scope: every place a user searches for a card in order to **add** it to a list
(brew, deck management, reconstruct, draft staging, future pages).
Out of scope: filtering a list that is already on the page (inventory table,
deck list). That stays page-local and gets its own later design.

Reference UIs (Andrew's screenshots, 2026-10-29):

- **Moxfield**: an `Add card` area with a **Card search** button (opens the
  full search with filters) next to a **Quick add** input
  (`Sol Ring (Ctrl + ')`) and a settings gear.
- **Archidekt**: one wide input, `Find and add cards to main deck…`, with a
  search button and an `Advanced Search · EDHRecs · Tips` link row.

This design combines them: one bar with a quick-add input that names the target
board, plus a **Card search** button that opens the filter panel.

**Amendment (Andrew, 2026-10-29): "Can be commander".** Brew's first search
step picks the commander. The module therefore ships a reusable **Can be
commander** filter and a `commander` preset that turns it on, prominently, in
that flow only. Eligibility is real commander eligibility (§3.1), not
`t:legendary`. Ordinary add-card searches are not restricted: the filter is off
unless a host opts in.

---

## 1. What exists today (origin/main @ 0f3d21e)

| Where | What it does | Reuse? |
|---|---|---|
| `src/web/src/routes/inventory` | GET form `?q=` → `/api/v2/inventory?q=` (name substring, Postgres) | **No.** This filters a list. Out of scope. |
| `src/web/src/routes/decks/[id]` | Read-only boards and Have/Buy. No add path. | Adopter (§7, step 2) |
| `src/web/src/routes/drafts/[id]` | Add form needs a hand-typed **oracle id** + name + qty + board → `?/upsert_card` → `POST /api/v2/drafts/{id}/cards` | Adopter. Its board list (`commander/main/sideboard/maybeboard`) and its action shape (`oracle_id, name, qty, board`) define the `onadd` payload. |
| `src/web/src/lib/components/review/CardImage.svelte` + `lib/cardImage.ts` | Same-origin card art via `/api/card-image` (proxies `/api/v2/card-images`) | **Yes**, for result thumbnails |
| `src/web/src/routes/api/card-image/+server.ts` | Pattern for a SvelteKit server route that proxies FastAPI, so the browser never talks to Scryfall | **Yes**, as the pattern for the two new proxy routes |
| `GET /api/cards/autocomplete` (`domain/inventory/card_search.py`) | Name-only suggestions (Scryfall, cache envelope, degraded local fallback). Returns `name, uid, image, type_line, color_identity, colors, keywords, mana_cost` | **Yes**, behind quick-add. It returns no `oracle_id`; see §5.3. |
| `static/js/card_autocomplete.js` (Jinja) | Combobox behavior: sequence guard, debounce, ARIA listbox, `commitOnEnter:"first"`, separate rows for error / empty / degraded | **Port the behavior** into `QuickAdd.svelte`. Do not import the file. |
| `infra/providers/raw_paste.py` | `^(\d+)\s*[xX]?\s+(.+)$` quantity grammar | **Yes**, the same grammar for quick-add (§4) |

The existing pages give the module three things: the add payload, the image
proxy, and the autocomplete endpoint. None of them has a structured card filter,
so the filter set and the search endpoint are new.

---

## 2. Module layout

```
src/web/src/lib/components/card-search/
  CardSearch.svelte        # composite: the bar (QuickAdd + "Card search" button) + panel
  QuickAdd.svelte          # combobox input, qty parsing, Enter commits
  CardSearchPanel.svelte   # drawer/sheet: FilterBar + ResultGrid + paging
  FilterBar.svelte         # the eight filters (§3), incl. Can be commander
  ResultGrid.svelte        # thumbnails + badges + add buttons
  query.ts                 # pure: Filters <-> URLSearchParams, defaults, isEmpty
  presets.ts               # pure: named presets (v1: `commander`, §3.1) -> default props + filters
  quickAdd.ts              # pure: parseQuickAdd("2x Sol Ring") -> {qty, name}
  types.ts                 # Filters, CardHit, AddRequest, SearchResponse
src/web/src/routes/api/card-search/+server.ts        # proxy -> /api/v2/cards/search
src/web/src/routes/api/card-autocomplete/+server.ts  # proxy -> /api/cards/autocomplete
```

`query.ts`, `presets.ts` and `quickAdd.ts` have no Svelte or DOM imports, so they can be
unit-tested with `node:test`, as `tests/pillar_describe_client.test.ts` is.

### 2.1 Component API (Svelte 5 runes, callback props)

```ts
// types.ts
export type Board = 'commander' | 'main' | 'sideboard' | 'maybeboard' | 'considering';
export type Color = 'W' | 'U' | 'B' | 'R' | 'G' | 'C';
export type OwnedFilter = 'any' | 'owned' | 'not_owned';

export interface CardHit {
  oracle_id: string;
  name: string;
  mana_cost: string;
  cmc: number;
  type_line: string;
  color_identity: Color[];
  rarity: string;             // rarity of the printing Scryfall returned
  oracle_text: string;
  image: string;              // pass to cardImage(name), never hotlinked
  owned_qty: number;          // in_collection + in_deck + in_mail (never not_owned)
  free_qty: number;           // in_collection: owned and not bound to a deck
  can_be_commander: boolean;  // §3.1 eligibility; always present, drives the `Commander` badge
  pairing: 'partner' | 'partner_group' | 'partner_with' | 'friends_forever'
         | 'choose_background' | 'background' | 'doctors_companion' | 'doctor'
         | null;              // §3.1.3; from partner_profile; null = cannot take a partner
}

export interface AddRequest { card: CardHit; qty: number; board: Board; }

export type PresetName = 'commander';
export interface Preset {         // presets.ts; a preset only sets DEFAULTS
  filters: Partial<Filters>;      // e.g. { commander: true, ci_mode: 'exact' }
  boards: Board[];
  placeholder: string;
  fixedQty?: 1;                   // hides the qty stepper and ignores "2x" in quick-add
  promoteFilter?: 'commander';    // render this filter as a chip in the bar itself
}
```

```svelte
<CardSearch
  onadd={(req: AddRequest) => Promise<void>}   // required; reject with Error(message) to show it inline
  boards={['main', 'sideboard', 'maybeboard']} // target choices; first one is the default
  identity={['W','U','B']}                     // commander identity; omit when there is no commander
  inList={Map<oracle_id, qty>}                 // badges "In deck ×N"; host-owned state
  ownedDefault="any"                           // initial owned filter
  hotkey={true}                                // Ctrl+' focuses quick-add (Moxfield)
  placeholder="Add cards to main…"             // defaults to `Add cards to ${board}…`
  preset="commander"                           // optional; see below and §3.1
  pairWith={CardHit}                           // optional, commander preset only: narrow to legal partners of this card
/>
```

**Presets.** `preset` applies a named bundle of defaults from `presets.ts`.
Explicit props win over the preset. v1 has one preset:

```ts
// presets.ts
export const PRESETS: Record<PresetName, Preset> = {
  commander: {
    filters: { commander: true, ci_mode: 'exact' },
    boards: ['commander'],
    placeholder: 'Search commanders…',
    fixedQty: 1,
    promoteFilter: 'commander',
  },
};
```

- With the `commander` preset, a **Can be commander** toggle chip sits in the
  bar itself, between the quick-add input and the **Card search** button. It
  is on, visibly pressed, and one click turns it off. It is the same filter as
  in the panel (§3), so the two always agree.
- `ci_mode: 'exact'`: when you are picking a commander, clicking `B G` means
  "a Golgari commander", not "anything within Golgari". The user can switch
  the mode back.
- With no `preset`, the filter exists in the panel (off) and nothing about
  ordinary add-card search changes.
- `pairWith` is set by the host after the first commander is picked (§3.1.3).
  It is ignored without the `commander` preset.

Rules:

- **The host owns the list.** The component never writes to the API. It calls
  `onadd`, awaits it, then shows `Added 2× Sol Ring` or the rejection message in
  an `aria-live` status line. Format rules (singleton, commander slot) belong to
  the host, which rejects with a readable message. The component does not
  duplicate those rules.
- **`identity`** sets the color filter to *within identity* by default. The user
  can turn that off. Any result outside the identity shows an `Outside identity`
  chip.
- **`inList`** is display-only: a badge, and the add button reads `+1 (have N)`.
  It does not block adding.
- The component holds no other state. Filters persist while the page is mounted
  (open/close the panel keeps them). They are **not** synced to the page URL,
  because this is a picker, not page navigation.

---

## 3. Filter set

These are the filters Andrew named: color, type, mana value, rarity, oracle
text, owned / not owned, plus name, plus **Can be commander** (amendment). All
are ANDed together. Multi-select values inside one filter are ORed.

| Filter | Control | Param | Scryfall translation |
|---|---|---|---|
| Name | text | `name` | each token → `name:<tok>` |
| Color (identity) | W U B R G C toggle chips + mode seg `within / exact / includes` | `ci=wub&ci_mode=within` | `id<=wub` / `id=wub` / `id>=wub`; `C` alone → `id=c` |
| Type | chips `Creature Instant Sorcery Artifact Enchantment Planeswalker Land Battle` (OR) + free text for subtype/supertype | `type=creature,artifact&type_text=elf` | `(t:creature or t:artifact) t:elf` |
| Mana value | chips `0 1 2 3 4 5 6 7+` (OR) | `mv=1,2,7+` | `(mv=1 or mv=2 or mv>=7)` |
| Rarity | chips `common uncommon rare mythic` (OR) | `rarity=rare,mythic` | `(r:rare or r:mythic)`; matches if **any printing** has that rarity |
| Oracle text | text; `"quoted phrase"` stays one term | `oracle` | each term → `o:<term>` / `o:"phrase"` |
| Owned | seg `Any / Owned / Not owned` | `owned=any\|owned\|not_owned` | server-side only (§5.2) |
| Can be commander | toggle chip, first in the panel; in the bar too under the `commander` preset | `cmdr=1` (absent = off) | `is:commander`; with `pair=<oracle_id>` see §3.1.3 |

Always applied, with no control: `legal:commander` (and `game:paper`).

The panel's filter count and **Clear filters** treat `cmdr` like any other
filter, with one exception: under the `commander` preset, **Clear filters**
resets to the preset defaults, so the toggle stays on. Turning it off is
always one explicit click.

The panel's footer shows the translated Scryfall query as **Open on Scryfall ↗**
(the role of Archidekt's "Advanced Search" link). The translation is visible and
can be debugged without reading code.

Deferred, not in v1: card color as distinct from identity (`c:`), price, set,
keywords, power/toughness, tags/roles, sort controls (v1 sorts by name).

### 3.1 Commander eligibility

#### 3.1.1 Source of truth: Scryfall `is:commander`

`cmdr=1` translates to Scryfall's `is:commander`. The existing
`/api/brew/commanders` scope already uses it (`card_search.py:184`). It was
checked live against Scryfall on 2026-10-29, combined with
`legal:commander`:

| Case | Example | `is:commander`? | Note |
|---|---|---|---|
| Legendary creature | Atraxa | yes | 3,414 of the 3,497 hits; the other 83 are not creatures |
| "can be your commander" | Aminatou, the Fateshifter (planeswalker) | yes | 45 hits have a Planeswalker face (some are creature-front DFCs) |
| Planeswalker that is a creature off the battlefield | Grist, the Hunger Tide | yes | has no "can be your commander" text; `is:commander -t:creature -t:vehicle -t:spacecraft -t:background -o:"can be your commander"` returns Grist and nothing else |
| Background | Agent of the Iron Throne | yes | 30. Legal only paired with a Choose a Background commander (§3.1.3) |
| Legendary Vehicle | Esika's Chariot, Shorikai | yes | 27 non-creature Vehicles |
| Legendary Spacecraft **with** P/T | Hearthhull, the Worldseed | yes | 5 paper-legal (22 before `legal:commander`; the rest, such as Candela, are digital-only) |
| Legendary Spacecraft **without** P/T | The Eternity Elevator | **no** | correct |
| Partner, Partner with, Partner—group, Friends forever, Choose a Background, Doctor's companion, Doctor | Akiri; Amy Pond; Abby; Cecily; Abdel Adrian; Ace; Arcade Gannon | yes | they are legendary creatures; pairing is §3.1.3 |
| Meld result / back face only legendary | Brisela; Akki Lavarunner // Tok-Tok | **no** | correct: the front face is what's in the command zone |
| Banned as commander | | excluded by `legal:commander` | |

That is why the filter is `is:commander` and never a type query.
`t:legendary t:creature legal:commander` (3,448 cards) misses 83 eligible
cards (Vehicles, Spacecraft, Backgrounds, walkers) and includes 34 that are
not eligible (flip and meld backs).

**Oracle consult.** These edge cases go to the oracle profile for a rules
verdict (card t_6ccb9548: Vehicles, Spacecraft P/T, Grist, Backgrounds, meld
and back-face legends, pairing cross-cases). If oracle finds a case where
Scryfall's `is:commander` differs from the Commander rules, it is recorded
here as a post-filter. Until then, the rule is: **Scryfall decides**. CS-1
does not wait on the verdict, because a post-filter is additive.

#### 3.1.2 Paths that do not call Scryfall

Two paths cannot ask Scryfall per request: **owned** mode (§5.2, local only)
and the **degraded** fallback (§5.1). Both use the same eligibility, in this
order:

1. **Eligible set** (primary). Redis set `cards:commander_eligible` of
   `oracle_id`s, built by paging `is:commander legal:commander game:paper`
   (about 20 pages of 175; one `unique=cards` pull with the 100 ms spacing).
   TTL 24 h, rebuilt lazily on first use after it expires. A partial pull
   (any page fails) is **never** written. That is the same "failure is never
   cached" rule as §5.1.
2. **Predicate** (only when the set is missing and cannot be built). A new
   pure function `is_commander_eligible(card)` in
   `domain/cards/commander_eligibility.py`, over the Postgres `cards` row.
   It looks at the **front face** of `type_line` (split on ` // `):
   - `legalities.commander == "legal"`, **and** one of:
   - `Legendary` and `Creature`;
   - oracle text contains `can be your commander`;
   - `Legendary` and subtype `Background`;
   - `Legendary` and subtype `Vehicle` or `Spacecraft`.
   It has a known gap: `cards` stores no power/toughness, so a P/T-less
   Spacecraft (The Eternity Elevator) and a Grist-style walker are
   misclassified. That is acceptable only because this is the last
   fallback, and such results carry `degraded: true`.

Do **not** reuse the two existing helpers. Both disagree with Scryfall:
`deck_validator.CardInfo.can_be_commander` (legendary creature or "can be your
commander") rejects Vehicles and Backgrounds, and
`commander_roles.card_flags` is a display heuristic. Aligning the validator is
a separate follow-up, after the oracle verdict, not part of CS-1.

The autocomplete endpoint's commander scope (`commanders_only=true`) already
asks Scryfall `is:commander`, but its local fallback is
"legendary-only". CS-1 switches that fallback to steps 1–2 above, so the
quick-add and panel paths agree.

#### 3.1.3 Second commander (`pairWith` / `pair`)

When the first pick can take a partner, the host passes it as `pairWith`,
and the component sends `pair=<oracle_id>` along with `cmdr=1`. The server
reads the first card's pairing ability, reusing
`deck_validator.partner_profile`, and ANDs one narrowing clause:

| First card has | Second-card clause (Scryfall) |
|---|---|
| Partner (plain) | `o:/^partner$/` (63 live) |
| Partner—`<group>` | `o:"Partner—<group>"` |
| Partner with `<name>` | `!"<name>"` |
| Friends forever | `o:"friends forever"` (`kw:"friends forever"` returns nothing on Scryfall, so use `o:`) |
| Choose a Background | `t:background` |
| is a Background | `kw:"choose a background"` |
| Doctor's companion | `t:doctor` |
| is a Doctor | `kw:"doctor's companion"` |
| none of these | `pair` is rejected with 400 "This commander can't have a partner"; the host does not offer a second slot |

The first card is excluded from its own results. In owned or degraded mode,
the same clause runs as SQL over `oracle_text`, `keywords` and `type_line`.
In every mode, the server then post-filters the page with
`deck_validator.check_partner_pair(first, hit)`, so a `pair` result is
always a legal pair, not just a likely one. The Scryfall clause only keeps the
upstream page relevant.

With `pairWith` set, the **Can be commander** chip is shown pressed and
**disabled** ("Partner search shows legal partners only"), because `pair`
requires `cmdr=1`. The only way to pick a second commander is from a
pair-filtered result.

A **Background** is eligible (`is:commander`), so it shows in the first
search with a `Needs a partner` chip. If the user picks one first, the second
search narrows to Choose a Background creatures, and the host does not accept
a lone Background (§6.1).

---

## 4. Quick-add behavior

Quick-add is the fast path for a user who already knows the card name.

1. **Focus**: click, or **Ctrl+'** from anywhere on the page when `hotkey`
   is set. That is Moxfield's binding. It does not collide with browser or OS
   shortcuts.
2. **Parse** the input with `parseQuickAdd`, the `raw_paste.py` grammar:
   `2 Sol Ring`, `2x Sol Ring`, `2X Sol Ring` → qty 2. A bare name → qty 1.
   Only the name part is sent to autocomplete.
3. **Suggest**: debounce 200 ms, minimum 2 characters, `limit=8`, via
   `/api/card-autocomplete`. A sequence guard drops stale responses. Rows show a
   small image, name, mana cost, and the owned count when known.
4. **Commit**: **Enter** adds the highlighted row, or the **top row** when
   nothing is highlighted (`commitOnEnter:"first"`; this fixed the Brew
   bug). **Tab** completes the name without committing. **Esc** closes the
   list, and a second Esc clears the input. ArrowUp/Down move the highlight.
5. **After commit**: the input clears and **keeps focus**, so the user can type
   the next card right away. A status line reads `Added 2× Sol Ring to main`.
6. **Target board**: a small board select sits next to the input and defaults
   to `boards[0]`. The placeholder names the board, as in Archidekt's
   "…to main deck".
7. **States**: `Searching…`, `No matches`, `Search failed — retry`, and
   `Card search is offline — local matches only`. These are four separate rows.
   An error never looks like "no matches" (the autocomplete incident lesson).
8. **Paste**: a multi-line paste is **not** handled in v1. The component
   shows "Paste a list via Import" and adds nothing.

The **Card search** button (next to the input) opens the panel and seeds the
Name filter with whatever is typed in the input.

**Quick-add while Can be commander is on.** Suggestions come from the
autocomplete's commander scope (`/api/card-autocomplete?commanders_only=true`,
already backed by `is:commander`). With `pairWith` set, quick-add uses
`/api/card-search?cmdr=1&pair=…&name=<typed>&page_size=8` instead, because the
autocomplete has no pairing clause. Turning the chip off switches back to the
normal card scope on the next keystroke. With `fixedQty: 1` (the `commander`
preset), a `2x` prefix is ignored, not added twice. The status line reads
`Commander: Atraxa, Praetors' Voice`.

### 4.1 Panel behavior

- **Layout**: a right-hand drawer at ≥1024 px (width about 32rem), so the
  host's list stays visible as cards land in it. A full-screen sheet at
  390 px. Esc or the close button returns focus to the **Card search** button.
- **Live search**: debounce 300 ms after any filter change. If every filter is
  empty, it shows a hint and makes no request.
- **Results**: a grid of `CardImage` thumbnails. Under each: name, mana cost,
  and the badges `Own 2 · 1 free` / `Not owned` / `In deck ×1` /
  `Outside identity`. Under the `commander` preset, also `Partner`,
  `Background`, `Needs a partner` (a Background) or `Vehicle` /
  `Planeswalker` when the commander is not a creature. In ordinary searches,
  a `Commander` badge marks eligible cards (from `can_be_commander`) without
  filtering anything. Each card has a **+** button that adds 1 to the selected
  board. A qty stepper appears on hover/focus. Clicking the image opens the
  existing card dossier route in a new tab.
- **Paging**: 60 per page, **Load more** (no infinite scroll). The header shows
  the total when Scryfall provides one.
- **States**: loading skeleton, empty ("No cards match. Loosen a filter."),
  error with Retry, and a degraded banner when `degraded: true`.

---

## 5. Data source: Scryfall, called only by the server

The browser talks only to SvelteKit. SvelteKit talks only to FastAPI. Only
FastAPI talks to Scryfall. This is the same chain as `/api/card-image`
(ARCHITECTURE.md §2).

```
CardSearch ──fetch──▶ /api/card-search (SvelteKit +server.ts)
                          └─▶ GET /api/v2/cards/search (FastAPI, app/api/cards.py)
                                 ├─ domain/cards/search_query.py   filters → Scryfall q (pure)
                                 ├─ infra/providers/scryfall.py     /cards/search, unique=cards
                                 └─ infra/db/repositories.py        owned/free counts by oracle_id
```

### 5.1 `GET /api/v2/cards/search` contract (new; built in follow-up CS-1)

Query params: the ones in §3 (including `cmdr` and `pair`), plus `page`
(1-based) and `page_size` (default 60, max 100). `pair` without `cmdr=1` is
a 400. Every `CardHit` carries `can_be_commander`, filled from the
eligible set (§3.1.2) whether or not `cmdr` is on, so ordinary results can
show the badge without being filtered. It also carries `pairing`, from
`deck_validator.partner_profile` over the card's oracle text and keywords.

```json
{
  "results": [CardHit, ...],
  "page": 1, "has_more": true, "total": 412,
  "query": "legal:commander id<=wub (t:creature) o:\"draw a card\"",
  "source": "scryfall|cache|local", "degraded": false
}
```

- The cache policy is copied from `card_search.py`: an envelope
  `{cached_at, results}` keyed on the **normalized Scryfall query + page**.
  Non-empty results live 24 h, empty results 60 s, and **a result produced by a
  failure is never cached**. Ownership counts are **not** cached; they are
  re-joined on every request, because they change when the user adds a card.
- Rate limit: at most 1 upstream call per request (2 in the not-owned case,
  §5.2), with the existing 100 ms spacing.
- When Scryfall fails, the same filters run against the local Postgres
  `cards` mirror (name/type/oracle `ilike`, cmc, identity subset;
  rarity through `printings`), and the response sets `degraded: true`. The
  endpoint never raises to the page.

### 5.2 Owned / not owned

Scryfall knows nothing about ownership, so each mode is handled differently:

- **any**: Scryfall results, each annotated with `owned_qty`/`free_qty` from
  one `card_instances` aggregate over the page's oracle ids.
- **owned**: query **local Postgres only**: `cards` joined to `card_instances`
  with owned statuses, filters applied in SQL. Every owned card already has a
  `cards` row (FK), so this is complete, fast, and costs no Scryfall call.
- **not_owned**: take Scryfall pages and drop rows with `owned_qty > 0`. Fetch
  at most one extra upstream page to fill a short page, and set `has_more`
  honestly. `total` is `null` in this mode, because Scryfall's count includes
  owned cards.

`not_owned` placeholder instances never count as owned. This is the same rule
as inventory totals.

### 5.3 Identity and the autocomplete gap

Hosts add by `oracle_id` (draft staging requires it; Postgres keys on it). The
panel's results carry `oracle_id`. The existing autocomplete returns only
`uid`, the Scryfall **printing** id. So CS-1 also adds `oracle_id` to the
autocomplete `_shape_card` output. That is an additive field: the Jinja
consumers ignore unknown keys, and `/api/brew/commanders` is unchanged. Until
that ships, quick-add resolves a picked name to a `CardHit` with
`/api/card-search?name=<exact>&page_size=1`.

---

## 6. Pilot: Brew construct (Andrew's call)

The pilot host is the **Brew construct** step: a brewed virtual deck that the
user edits by adding cards, before *Accept / Make physical*. This is the
"search for a card to add" flow Andrew uses most. It also exercises every
filter: identity comes from the commander, and owned vs not owned matters,
because brew is owned-first.

The pilot needs a host and a write path, and neither exists yet:

- Brew's UI is Jinja (`templates/brew.html`, `brew_deck.html`). There is
  no `src/web/src/routes/brew`.
- Brew sessions live in Redis (`domain/draft/brew.py`). The draft's
  `cards` list (`{name, quantity, tier}`) has no add/remove endpoint.

The pilot is therefore deliberately thin. **`/brew/[session_id]`** in
SvelteKit shows the commander, the brewed card list grouped by type (qty, tier
chip owned-donor / owned-inventory / buy, remove), and `<CardSearch>` with:

```svelte
<CardSearch boards={['main', 'maybeboard']} identity={session.color_identity}
  inList={byOracleId(draft.cards)} ownedDefault="owned" onadd={addToBrew} />
```

- `ownedDefault="owned"` follows brew's *owned-first* intent. A
  `best_build` session passes `"any"`.
- `addToBrew` posts to the new brew card endpoint (CS-4). It rejects
  non-basic duplicates with "Already in the brew (singleton)".
- Accept, export, Make physical, and abandon remain on the legacy Jinja page.
  The pilot links there ("Finish on the brew page →"). Porting the rest of
  brew is a separate effort.

### 6.1 Step 1: pick the commander (`/brew/new`, amendment)

Brew's first search picks the commander, so the pilot starts there. Today
that step is the Jinja `/brew` page (`brew.js`, a `commandersOnly`
autocomplete plus a "+ Add partner" button that matches keywords). The pilot
adds a thin SvelteKit **`/brew/new`**:

```svelte
<CardSearch preset="commander" onadd={pickCommander} hotkey={true} />
{#if canPair(picked[0])}
  <CardSearch preset="commander" pairWith={picked[0]} onadd={pickPartner}
              placeholder="Add a partner…" hotkey={false} />
{/if}
```

- The **Can be commander** chip is **on by default here and only here**. The
  user sees it pressed in the bar and can turn it off with one click (for
  example, to look up a card by name). With it off, `pickCommander` still
  rejects an ineligible pick using the hit's `can_be_commander` (computed by
  the server, §5.1): "Not a legal commander: Sol Ring". The filter is a
  convenience; the host's check is the gate.
- `pickCommander` / `pickPartner` hold at most two picks in page state. The
  partner comes only from the `pairWith` instance, whose results the server
  has already checked with `check_partner_pair` (§3.1.3), so the host does no
  pairing logic of its own. `canPair` is true when the first hit has
  `pairing != null`, which replaces `brew.js`'s keyword regex.
- **A lone Background** cannot start a brew. The start button reads "A
  Background needs a Choose a Background commander" until a partner is
  picked.
- The panel opens with no colors chosen. Clicking colors searches with
  `ci_mode=exact` (the preset default). Results default to `owned=any`: a
  commander you don't own yet is still a valid brew target, and the `Own 1`
  badge shows which ones you have.
- Below the pick: build mode (`owned_first` / `best_build`) and name, then
  **Start brew**. That calls the existing `POST /api/brew/start` with
  `commanders: [names]`, then `POST /api/brew/{id}/build`, then goes to
  `/brew/[session_id]` (§6). The same sequence as `brew.js:generate()`. No
  new endpoint.
- Donors, themes, power level and notes are not in the pilot. A link "More
  options (donors, themes) on the classic brew page →" goes to `/brew`. The
  Jinja page keeps its own picker until brew is fully ported.

In the construct step (§6), the commander preset is **not** used: the
`<CardSearch>` there is an ordinary add-card search, restricted by the
commander's identity and nothing else.

---

## 7. Adoption order

| # | Page | Host change | Props | Depends on |
|---|---|---|---|---|
| 1a | **Brew commander pick** `/brew/new` (pilot) | new thin route (§6.1) | `preset="commander"`, then `pairWith` for the partner | CS-1, CS-3 |
| 1b | **Brew construct** `/brew/[session_id]` (pilot) | new thin route (§6) | `identity`, `ownedDefault` owned/any, boards main+maybe; **no** preset | CS-1, CS-3, CS-4 |
| 2 | **Deck management** `/decks/[id]` | add an "Add cards" bar above the boards; `onadd` → a v2 deck add-card endpoint (Postgres `deck_cards`; the legacy `POST /api/decks/{id}/cards` is Redis-registry) | `identity=deck.color_identity`, boards main/side/maybe/considering, `inList` from boards | pilot verified; CS-7 endpoint; coordinate with open PR #379 (deck-local intent, the same `decks.py`) |
| 3 | **Reconstruct** must-include picker | rides on the reconstruct SvelteKit port (not started; today `templates/reconstruct.html`) | `ownedDefault="owned"`, single board `main`, `onadd` adds to the preferences must-include list | reconstruct port |
| — | Draft staging `/drafts/[id]` (opportunistic) | replace the oracle-id form with `<CardSearch>`; `onadd` → existing `?/upsert_card` | boards from the current select | CS-3 only; can go any time after the pilot |

Not adopting: `/inventory` and the deck-list filters (non-goal). The Jinja pages
keep `card_autocomplete.js` until each page is ported.

---

## 8. Proposed follow-up cards (one deliverable each)

| Id | Assignee | Deliverable | Acceptance |
|---|---|---|---|
| CS-1 | coder | `GET /api/v2/cards/search` (§5) incl. `cmdr`/`pair` (§3.1) + `oracle_id` added to the autocomplete shape + the `cards:commander_eligible` set and `is_commander_eligible` fallback; the autocomplete's commander-scope local fallback uses them | pytest: translation table in §3 (one case per row + combos); `cmdr=1` → `is:commander`, absent → no `is:commander` in `query`; one `pair` case per §3.1.3 row, plus the 400s (`pair` without `cmdr`, first card with no pairing ability); `pair` results pass `check_partner_pair` (plain Partner never returns a Partner—group card); `can_be_commander` and `pairing` set on every hit with `cmdr` off; owned mode + `cmdr=1` returns an owned Esika's Chariot and an owned Background and excludes an owned Brisela and an owned non-legendary creature (fixture); a partial eligible-set pull is not written; owned/not_owned/any against fixture Postgres; Scryfall failure → `degraded:true`, nothing cached; ownership never cached |
| CS-2 | coder | `query.ts` + `presets.ts` + `quickAdd.ts` + `types.ts` + the two SvelteKit proxy routes | `node:test` for Filters↔params round-trip (incl. `cmdr`, `pair`), the qty grammar (`2 x`, `2x`, `2X`, bare, `0` rejected), and presets: no preset → `cmdr` absent; `commander` → `cmdr=1`, `ci_mode=exact`, boards `['commander']`; an explicit prop overrides the preset; Clear filters under the preset keeps `cmdr=1`; `npm run check` green |
| CS-3 | coder | `CardSearch` components (§2, §4) + a `/dev/card-search` harness page with a fixture `onadd`, showing **two** instances: one plain, one `preset="commander"` | screenshots at 1440 and 390 attached to the card, both instances; keyboard path (Ctrl+' → type → Enter → focus stays) works in the harness; the commander instance shows the pressed **Can be commander** chip in the bar, one click turns it off and the panel's toggle follows; the plain instance has no chip in the bar and its panel toggle is off; quick-add `2x Atraxa` in the commander instance adds 1 |
| CS-4 | coder | brew draft card add/remove: `POST /api/v2/brew/{session_id}/cards` `{oracle_id, name, qty}` and `DELETE …/cards/{oracle_id}`; recompute the tier from pools; reject non-basic duplicates (409) | pytest over a Redis fixture session; accept_draft still produces the same deck for an unedited session |
| CS-5 | coder | pilot routes `/brew/new` (§6.1) and `/brew/[session_id]` (§6) | **/brew/new**: the Can be commander chip is on at load; picking Atraxa and starting lands on `/brew/[id]` with Atraxa as commander; Shorikai (Vehicle) and Grist are findable and pickable; Brisela and Sol Ring are not offered with the chip on, and are rejected inline with it off; Akiri → partner search shows only Partner cards; Abdel Adrian → partner search shows only Backgrounds; a lone Background cannot start. **/brew/[id]**: no commander chip in the bar, and the panel toggle is off (a non-legendary card such as Sol Ring is found); add 3 cards via quick-add and 2 via panel on a real session; they appear in the list with correct tiers; Accept on the legacy page includes them |
| CS-6 | tester | live verification of the pilot at 1440 and 390 | pass/fail report with screenshots: every filter changes the results; owned/not-owned are correct against inventory; error/degraded states shown |
| CS-7 | coder | deck management adoption: v2 deck add-card endpoint + `/decks/[id]` bar | after CS-6 passes and #379 lands |
| CS-8 | coder | reconstruct must-include adoption | after the reconstruct SvelteKit port exists |

CS-1 and CS-2 can run in parallel. CS-3 needs CS-2. CS-4 is independent. CS-5
needs CS-1, CS-3, and CS-4. CS-6 needs CS-5.

---

## 9. Decisions and risks

- **Callback props, not events.** Svelte 5 convention, and `onadd` must be
  awaitable so a host can reject.
- **Owned mode never calls Scryfall.** It is the most-used mode for brew, and it
  keeps working during a Scryfall outage.
- **Rarity is per-printing.** A card counts as rare when any printing is rare
  (Scryfall's semantics). This is noted in the filter's tooltip.
- **Not-owned paging is approximate.** Pages can come back shorter than 60
  when much of the result set is owned. This is accepted for v1. `total` is
  hidden in that mode rather than wrong.
- **Hotkey scope.** Ctrl+' binds only while a `CardSearch` with `hotkey` is
  mounted. Two instances on one page: only the first binds it.
- **Commander eligibility comes from Scryfall `is:commander`, not a type
  query** (amendment). Live counts on 2026-10-29: `t:legendary t:creature`
  misses 83 eligible cards and includes 34 ineligible ones. Local paths use a
  cached copy of the same set, and fall back to a predicate with known gaps.
  The oracle verdict (t_6ccb9548) can add post-filters.
- **The commander filter is opt-in per host.** Only `preset="commander"`
  turns it on. Ordinary add-card searches never filter on it; they only show
  a `Commander` badge.
- **The filter is not the gate.** The chip can be turned off, so the host
  still checks `can_be_commander` before starting a brew, and partners come
  only from server-checked `pair` results. `POST /api/brew/start` itself does
  not validate commanders today. This design does not add that; the pilot
  relies on the picker.
- **Risk: three eligibility implementations disagree.** The validator
  (`CardInfo.can_be_commander`), the display flags (`commander_roles`) and
  `reconstruct.html`'s check all differ from `is:commander` (they reject
  Vehicles or accept any Background). This design does not change them. A
  follow-up aligns the validator after the oracle verdict, so that a commander
  the picker offers cannot fail validation later.
