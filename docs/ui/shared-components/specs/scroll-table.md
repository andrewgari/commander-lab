# ScrollTable — component spec

Card: t_34d0e9ac (UI audit 2/4, shared components). Source: `ui-ux-audit-2026-10-29.md` themes 2 and 3.
Status: **draft for implementation**. Depends on card 1/4 tokens (not merged at time of writing; see §6).

## 1. Problem

Decks (7-col have/buy), drafts, games, inventory (6-col) and card tables share three defects:

1. No `<caption>`, so screen readers announce an anonymous table.
2. No `scope` on `<th>`, so header/cell association is guesswork.
3. No overflow wrapper, so the table pushes the page past 375px (the whole page scrolls sideways).

ScrollTable fixes all three once, and absorbs the table-specific empty state.

**Non-goals:** page layouts, sorting, pagination, virtualisation, row selection.

**Assumption (no check-in per secretary override):** SvelteKit, Svelte 5 (snippets). If the repo is on Svelte 4, swap `{#snippet}` for named slots; the prop names stay identical.

## 2. API

File: `web/src/lib/components/ScrollTable.svelte`

### Props

| Prop | Type | Default | Notes |
|---|---|---|---|
| `caption` | `string` | required | Accessible name of the table and the scroll region. Required; omitting it is a type error. |
| `captionHidden` | `boolean` | `false` | Visually hides the caption (`.sr-only`) when the page heading already says it. Never use `display:none`. |
| `columns` | `Column[]` | required | Header definitions, see below. |
| `rows` | `T[]` | required | Data. `rows.length === 0` renders the empty state instead of the table. |
| `rowKey` | `(row: T, i: number) => string \| number` | index | Keyed `{#each}`. Pass a stable id for any list that can reorder. |
| `rowHeader` | `string` (column key) | `undefined` | Column whose cell renders `<th scope="row">` (e.g. card name, deck name). |
| `stickyHeader` | `boolean` | `false` | Header row sticks to the top of the scroll container. Needs `maxHeight`. |
| `stickyFirstCol` | `boolean` | `false` | First column sticks while scrolling horizontally. Requires `rowHeader`. |
| `maxHeight` | `string` (CSS length) | `undefined` | Makes the region scroll vertically too. Required for `stickyHeader` to do anything. |
| `density` | `'comfortable' \| 'compact'` | `'comfortable'` | Compact for inventory/card lists. |
| `minWidth` | `string` | `'40rem'` | Min width of the table; below this the wrapper scrolls. Per-table override. |
| `empty` | `Snippet` | default EmptyState | Override empty-state content (see §5). |
| `emptyTitle` / `emptyBody` | `string` | — | Forwarded to the default EmptyState. |
| `class` | `string` | — | Applied to the outer wrapper only. |

```ts
type Column = {
  key: string;                          // stable id, also used as snippet lookup key
  header: string;                       // visible header text (plain string, for a11y name)
  align?: 'start' | 'end' | 'center';   // numbers should be 'end'
  width?: string;                       // optional CSS width/min-width
  headerHidden?: boolean;               // sr-only header for icon/action columns (still a real <th>)
  numeric?: boolean;                    // sets tabular-nums + align end
};
```

### Snippets

| Snippet | Args | Purpose |
|---|---|---|
| `row` | `(row, index)` | Required. Renders the **cells for one row**. ScrollTable owns `<tr>`; the snippet outputs `<td>`s. Use the `Cell`/`RowHeader` helpers (below) so scope is never hand-written. |
| `cell` | `(row, column)` | Optional alternative to `row`: called once per column, for simple tables. |
| `empty` | `()` | See props. |
| `footer` | `()` | Optional `<tfoot>` content (totals). Cells must use `<td>` or `<th scope="row">`. |

Helpers exported from the same module:
`<RowHeader>` renders `<th scope="row">`; `<Cell>` renders `<td data-label={column.header}>`. A `row` snippet must emit exactly `columns.length` cells (dev-mode assertion warns).

## 3. Markup

```html
<div class="st" data-sticky-header data-density="comfortable">
  <!-- 1. Caption lives OUTSIDE the table too, so the region can reference it -->
  <p class="st__caption" id="{uid}-cap">Decks you own — have vs. buy</p>   <!-- .sr-only when captionHidden -->

  <div class="st__scroll"
       role="region"
       aria-labelledby="{uid}-cap"
       tabindex="0">                                   <!-- keyboard-scrollable -->
    <table class="st__table" style="min-width: var(--st-min-width)">
      <caption class="sr-only">Decks you own — have vs. buy</caption>   <!-- see note -->
      <thead>
        <tr>
          <th scope="col">Deck</th>
          <th scope="col" class="num">Have</th>
          <th scope="col" class="num">Buy</th>
          <th scope="col"><span class="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        <tr>
          <th scope="row">Atraxa Superfriends</th>
          <td class="num">92</td>
          <td class="num">7</td>
          <td>…</td>
        </tr>
      </tbody>
    </table>
  </div>
</div>
```

Rules:

- **Caption:** the `<table>` always contains a real `<caption>` (the element is what AT announces as the table name). The visible caption is rendered once: either as the `<caption>` itself (default, `caption-side: top`, styled as a label) or, when `captionHidden`, as `.sr-only`. The region's `aria-labelledby` must point at an element with the same text. Implementation: when visible, give the `<caption>` the `id` and reference it; when hidden, the `.sr-only` caption gets the id. Do **not** render two visible copies. (The sketch above shows the `<p>` for clarity; the implementation should use one node.)
- **Region:** `role="region"` + `aria-labelledby` + `tabindex="0"` on the scroll wrapper only. Always (not conditionally) so layout shift or resize never changes tab order. Rationale: a scrollable container without focusability is unreachable by keyboard (axe `scrollable-region-focusable`).
- **Header scope:** every `<th>` in `<thead>` has `scope="col"`. Every row-header cell has `scope="row"`. Columns with no visible header text (actions, icons) still render a `<th scope="col">` containing `.sr-only` text; never an empty `<th>`.
- **Row header:** one per row, and only if `rowHeader` is set. Pick the cell a user would use to name the row (deck name, card name, game date). If no column qualifies (pure numeric grid), omit it. Do not mark more than one cell per row.
- **No layout tables.** No `role="presentation"` on `<table>`. Never `display:block` on `<table>`/`<tr>` (it strips table semantics in some AT). Responsive reflow is done by scrolling, not by restyling to cards (see §4).
- **Numbers:** zero renders `0`, never blank (audit: inventory zeros). `null`/`undefined` renders an em-dash `—` with `<span class="sr-only">no value</span>`.
- **Color-only status:** ScrollTable stays presentational; status cells use StatusPill (separate spec), not colour alone.

## 4. Behaviour

### Horizontal scroll

- `.st__scroll { overflow-x: auto; overscroll-behavior-x: contain; }`
- Table `min-width` = `minWidth` prop. The page never scrolls horizontally; only the region does.
- Scroll affordance: edge fade via `mask-image` / background-attachment shadows on the sides that have more content (pure CSS, no JS; `background-attachment: local, scroll` trick). Hidden under `forced-colors`.
- Keyboard: Tab focuses region; ←/→/Home/End/PageUp/PageDown scroll natively. No custom key handling.
- Focus: `:focus-visible` outline on `.st__scroll`, 2px, `--focus-ring` with `outline-offset: -2px` so it is not clipped by the container.
- Interactive content inside cells (links, buttons) stays in the normal tab order after the region.

### Sticky header

- `stickyHeader` + `maxHeight`: `thead th { position: sticky; top: 0; z-index: 1; background: var(--surface-1) }`. Sticky only works inside the scroll container, hence `maxHeight` is required; dev warning if missing.
- `stickyFirstCol`: first column `position: sticky; inset-inline-start: 0`, `z-index: 1` (header cell of that column `z-index: 2`). Needs an opaque background and a 1px trailing border so scrolled content does not bleed through.
- No box-shadow soup: a 1px `--border-strong` line marks the stuck edge.

### Responsive

| Viewport | Behaviour |
|---|---|
| ≥ table min-width | Table fills wrapper, no scroll. |
| < table min-width (typical phones, 375px) | Wrapper scrolls horizontally; region stays focusable; first column optionally sticky. |
| ≤ 480px | Density forced to `compact` cell padding if `density="comfortable"` (padding token step down, TBD). |

Deliberate choice: **no stacked-card reflow** on mobile. It breaks column comparison (have vs buy), costs a second markup path, and risks the semantic issues above. Scrolling with a sticky first column is the pattern. Revisit only if the card dossier needs it.

### Motion

None required. Edge fades are static. If a transition is added, `prefers-reduced-motion: reduce` disables it.

## 5. Empty state integration

- `rows.length === 0` → **no `<table>` is rendered.** The scroll region is also dropped (an empty scrollable region is a useless tab stop). Render:

```html
<div class="st st--empty" aria-labelledby="{uid}-cap">
  <p class="sr-only" id="{uid}-cap">{caption}</p>
  <EmptyState title={emptyTitle} body={emptyBody}> <slot name="action"/> </EmptyState>
</div>
```

- Default content is the shared `EmptyState` (separate spec). Callers pass `emptyTitle`, `emptyBody`, and may pass a CTA through the `empty` snippet (audit: empty states are bare text with no CTA, so each migration supplies one).
- Empty ≠ loading. A `loading` boolean is **out of scope** here; pages gate on their own load state and pass `rows` only when ready (pending pattern lives in the forms spec). Flagged as a gap for the 3-spec consistency review.
- Error state is the page's job (ErrorBanner above the table), not ScrollTable's.
- Live region: the empty state is static content; no `aria-live`. If rows become empty after a user action (filtering, deleting), the page announces via its own status message.

## 6. Tokens

Card 1/4 has not delivered a final token list. Names below are **proposed**; anything marked TBD must be reconciled when 1/4 merges. Do not hardcode hex values (audit theme 1).

| Use | Token | Status |
|---|---|---|
| Table surface | `--surface-1` | TBD (1/4) |
| Header surface | `--surface-2` | TBD (1/4) |
| Row hover / focus-within | `--surface-hover` | TBD |
| Zebra (optional, off by default) | `--surface-2` @ 50% | TBD |
| Body text | `--text` | TBD (1/4) |
| Header / caption text | `--text-muted` — must meet 4.5:1 on `--surface-2` | TBD; verify |
| Row dividers | `--border` | TBD (1/4) |
| Sticky edge | `--border-strong` | TBD |
| Focus ring | `--focus-ring` (≥3:1 against adjacent) | TBD (1/4 must provide `:focus-visible`) |
| Cell padding | `--space-2` / `--space-3` (compact / comfortable), horizontal `--space-3` | TBD scale |
| Caption type | `--font-size-sm`, weight 600, `--tracking-wide` | TBD |
| Cell type | `--font-size-base`; numeric `font-variant-numeric: tabular-nums` | TBD |
| Radius | `--radius-md` on `.st` | TBD |
| Min width | `--st-min-width` (component-local, set from prop) | Final (local) |

Opacity must not be used to dim text or rows (audit: fails AA). Use a muted text token instead.
Forced-colors: borders use `CanvasText`/`ButtonBorder` fallbacks; sticky backgrounds use `Canvas`.

## 7. Per-page migration notes

Scope is the table only; page layout is untouched. All pages: drop the page-local table CSS and duplicated `.meta/.error/.open` rules touched by the table; pass `caption` (visible unless a heading directly labels it).

| Page | Columns / keys | `rowHeader` | Options | Empty state | Notes |
|---|---|---|---|---|---|
| **Decks** | deck, have, buy, … (7 cols) | deck name | `minWidth: 52rem`, `stickyFirstCol`, numeric cols `numeric` | "No decks yet" + CTA to import/brew | "Short" cards currently red-only: use StatusPill with text, e.g. a "Buy 2" pill, in the buy cell. Never colour alone. |
| **Drafts** | draft, status, created, actions | draft name | actions col `headerHidden` or labelled "Actions" | "No drafts" + "Start a draft" CTA | Commit/Abandon stay out of this spec (forms/confirm redesign is a later card). Status via StatusPill. |
| **Games** | date, players, result, … | date (or game id) | `minWidth: 40rem` | "No games logged" + CTA to import | "unavailable" repeated per cell → render once as `—` with sr-only text; if an entire column is unavailable, drop the column and say so in the caption/body. Result via StatusPill. |
| **Inventory** | card, set, qty, foil, … (6 cols) | card name | `density="compact"`, `stickyHeader` + `maxHeight` for long lists, `stickyFirstCol` | "Inventory is empty" + import CTA | Zeros render `0`. The search input needs its own `<label>` (outside this component). |
| **Card** | printings / decks-by-name / collection tables | printing or deck name | `captionHidden` where an `<h2>` already names the panel (then `aria-labelledby` still points at the sr-only caption); `minWidth: 32rem` | per-panel body, e.g. "Not in any deck" | Fixed 16rem art that squeezes text is a layout issue, out of scope; ScrollTable just must not widen the page. Multiple tables per page: each gets a unique `uid`. |

Migration order: Inventory (simplest, proves compact + sticky) → Decks → Games → Drafts → Card.

## 8. Acceptance criteria

**Semantics**
- [ ] Every rendered table has exactly one `<caption>` with non-empty text; `caption` prop is required by type.
- [ ] All `<thead>` `<th>` have `scope="col"`; row headers (when `rowHeader` set) have `scope="row"`; no empty `<th>`.
- [ ] Scroll wrapper has `role="region"`, `tabindex="0"`, `aria-labelledby` resolving to the caption text. ids are unique with multiple instances on one page.
- [ ] `rows=[]` renders EmptyState, no `<table>`, no focusable region.
- [ ] Zero values render `0`; null renders `—` with sr-only text.

**Behaviour**
- [ ] At 375px, `document.documentElement.scrollWidth <= innerWidth` on all five pages; the table scrolls inside its region.
- [ ] Region reachable with Tab; arrows/Home/End scroll it; visible `:focus-visible` ring not clipped.
- [ ] `stickyHeader` keeps the header visible during vertical scroll in the region; `stickyFirstCol` keeps the row header visible during horizontal scroll, with no content bleed-through.
- [ ] `captionHidden` hides caption visually only; still announced.

**Visual / tokens**
- [ ] No hardcoded colours, spacing or font sizes; everything via tokens (TBD names reconciled with 1/4 before merge).
- [ ] Text contrast ≥ 4.5:1, focus ring and borders ≥ 3:1, in light and dark. No opacity dimming.
- [ ] `forced-colors` and `prefers-reduced-motion: reduce` checked.

**Tests**
- [ ] Component tests (vitest + testing-library): caption/scope/region attributes, empty state, hidden caption, snippet cell-count warning, unique ids.
- [ ] axe-core on each of the five migrated pages: zero violations for `scrollable-region-focusable`, `th-has-data-cells`, `empty-table-header`, `scope-attr-valid`, `aria-valid-attr-value`, `color-contrast`.
- [ ] Playwright at 375×812 and 1280×800: no page-level horizontal scroll; keyboard scroll works; screenshot of one table per page.

## 9. Open items for the consistency review (t_93e75d00)

1. Token names above are placeholders until 1/4 merges.
2. `EmptyState` must accept `title`, `body` and an action snippet; confirm against its spec.
3. `StatusPill` must render text plus icon/shape, not colour only; ScrollTable depends on it for decks/drafts/games.
4. A `loading` state for tables is deliberately not handled here; decide whether the pending-pattern spec owns it.
5. Confirm Svelte 5 vs 4 (snippets vs slots).
