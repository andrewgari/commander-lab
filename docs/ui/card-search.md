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
  FilterBar.svelte         # the seven filters (§3)
  ResultGrid.svelte        # thumbnails + badges + add buttons
  query.ts                 # pure: Filters <-> URLSearchParams, defaults, isEmpty
  quickAdd.ts              # pure: parseQuickAdd("2x Sol Ring") -> {qty, name}
  types.ts                 # Filters, CardHit, AddRequest, SearchResponse
src/web/src/routes/api/card-search/+server.ts        # proxy -> /api/v2/cards/search
src/web/src/routes/api/card-autocomplete/+server.ts  # proxy -> /api/cards/autocomplete
```

`query.ts` and `quickAdd.ts` have no Svelte or DOM imports, so they can be
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
}

export interface AddRequest { card: CardHit; qty: number; board: Board; }
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
/>
```

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
text, owned / not owned, plus name. All are ANDed together. Multi-select values
inside one filter are ORed.

| Filter | Control | Param | Scryfall translation |
|---|---|---|---|
| Name | text | `name` | each token → `name:<tok>` |
| Color (identity) | W U B R G C toggle chips + mode seg `within / exact / includes` | `ci=wub&ci_mode=within` | `id<=wub` / `id=wub` / `id>=wub`; `C` alone → `id=c` |
| Type | chips `Creature Instant Sorcery Artifact Enchantment Planeswalker Land Battle` (OR) + free text for subtype/supertype | `type=creature,artifact&type_text=elf` | `(t:creature or t:artifact) t:elf` |
| Mana value | chips `0 1 2 3 4 5 6 7+` (OR) | `mv=1,2,7+` | `(mv=1 or mv=2 or mv>=7)` |
| Rarity | chips `common uncommon rare mythic` (OR) | `rarity=rare,mythic` | `(r:rare or r:mythic)`; matches if **any printing** has that rarity |
| Oracle text | text; `"quoted phrase"` stays one term | `oracle` | each term → `o:<term>` / `o:"phrase"` |
| Owned | seg `Any / Owned / Not owned` | `owned=any\|owned\|not_owned` | server-side only (§5.2) |

Always applied, with no control: `legal:commander` (and `game:paper`).

The panel's footer shows the translated Scryfall query as **Open on Scryfall ↗**
(the role of Archidekt's "Advanced Search" link). The translation is visible and
can be debugged without reading code.

Deferred, not in v1: card color as distinct from identity (`c:`), price, set,
keywords, power/toughness, tags/roles, sort controls (v1 sorts by name).

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

### 4.1 Panel behavior

- **Layout**: a right-hand drawer at ≥1024 px (width about 32rem), so the
  host's list stays visible as cards land in it. A full-screen sheet at
  390 px. Esc or the close button returns focus to the **Card search** button.
- **Live search**: debounce 300 ms after any filter change. If every filter is
  empty, it shows a hint and makes no request.
- **Results**: a grid of `CardImage` thumbnails. Under each: name, mana cost,
  and the badges `Own 2 · 1 free` / `Not owned` / `In deck ×1` /
  `Outside identity`. Each card has a **+** button that adds 1 to the selected
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

Query params: the ones in §3, plus `page` (1-based) and `page_size`
(default 60, max 100).

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

---

## 7. Adoption order

| # | Page | Host change | Props | Depends on |
|---|---|---|---|---|
| 1 | **Brew construct** `/brew/[session_id]` (pilot) | new thin route (§6) | `identity`, `ownedDefault` owned/any, boards main+maybe | CS-1, CS-3, CS-4 |
| 2 | **Deck management** `/decks/[id]` | add an "Add cards" bar above the boards; `onadd` → a v2 deck add-card endpoint (Postgres `deck_cards`; the legacy `POST /api/decks/{id}/cards` is Redis-registry) | `identity=deck.color_identity`, boards main/side/maybe/considering, `inList` from boards | pilot verified; CS-7 endpoint; coordinate with open PR #379 (deck-local intent, the same `decks.py`) |
| 3 | **Reconstruct** must-include picker | rides on the reconstruct SvelteKit port (not started; today `templates/reconstruct.html`) | `ownedDefault="owned"`, single board `main`, `onadd` adds to the preferences must-include list | reconstruct port |
| — | Draft staging `/drafts/[id]` (opportunistic) | replace the oracle-id form with `<CardSearch>`; `onadd` → existing `?/upsert_card` | boards from the current select | CS-3 only; can go any time after the pilot |

Not adopting: `/inventory` and the deck-list filters (non-goal). The Jinja pages
keep `card_autocomplete.js` until each page is ported.

---

## 8. Proposed follow-up cards (one deliverable each)

| Id | Assignee | Deliverable | Acceptance |
|---|---|---|---|
| CS-1 | coder | `GET /api/v2/cards/search` (§5) + `oracle_id` added to the autocomplete shape | pytest: translation table in §3 (one case per row + combos); owned/not_owned/any against fixture Postgres; Scryfall failure → `degraded:true`, nothing cached; ownership never cached |
| CS-2 | coder | `query.ts` + `quickAdd.ts` + `types.ts` + the two SvelteKit proxy routes | `node:test` for Filters↔params round-trip and the qty grammar (`2 x`, `2x`, `2X`, bare, `0` rejected); `npm run check` green |
| CS-3 | coder | `CardSearch` components (§2, §4) + a `/dev/card-search` harness page with a fixture `onadd` | screenshots at 1440 and 390 attached to the card; keyboard path (Ctrl+' → type → Enter → focus stays) works in the harness |
| CS-4 | coder | brew draft card add/remove: `POST /api/v2/brew/{session_id}/cards` `{oracle_id, name, qty}` and `DELETE …/cards/{oracle_id}`; recompute the tier from pools; reject non-basic duplicates (409) | pytest over a Redis fixture session; accept_draft still produces the same deck for an unedited session |
| CS-5 | coder | pilot route `/brew/[session_id]` (§6) | add 3 cards via quick-add and 2 via panel on a real session; they appear in the list with correct tiers; Accept on the legacy page includes them |
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
