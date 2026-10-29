# Shared UI components — coder cards

Read `README.md` in this folder first. Decisions D1–D24 and the token map in §3 override the three specs in `specs/`.
Card IDs (`C0`–`C6`) are used for cross-references; each card has a Kanban task whose id is listed in `README.md` §5 once created.

---

## Shared context (repeated in every Kanban card body)

- Repo `andrewgari/commander-lab`. Branch from current `origin/main`. SvelteKit app at `src/web/`, Svelte 5 runes, import alias `#lib/...` (D1, D2).
- Design docs: `docs/ui/shared-components/README.md` (decisions, token map, conflicts) and `cards.md` (this file). Component specs are in `docs/ui/shared-components/specs/`. **Where a spec conflicts with the README, the README wins.**
- Tokens come from `src/web/src/app.css` (card 1/4 A, merged as #392). Use only `--color-*`, `--space-*`, `--text-*`, `--radius-*`, `--dur-*`, `--touch-min`, `--font-*`, `--weight-*`, `--tracking-*`. No raw hex, no `opacity` dimming, no `$lib`.
- Dark theme only (D3). "Contrast" means dark surfaces plus `forced-colors: active`.
- Only card C0 edits `app.css`, `package.json`/`package-lock.json`, `vite.config.ts` and `.github/workflows/ci.yml` (D23). If your card needs a change there, comment on C0 or open a follow-up card.
- Workflow: open a PR, create a tester review card, get `[QA_PASS]` on the final head, then squash-merge yourself once CI is green. `npm run check` and `npm run build` must pass, and the "Review PDF visual regression" check must stay green.
- Changelog: component cards C0–C5 add no `CHANGELOG.md` line (no user-facing change; tick the PR template box). C6 adds one line.

---

## C0 — Foundation primitives for shared components

**Goal.** Land the shared pieces that C1–C5 all need and that none of the specs owns: the new tokens, `.btn`, global `.meta`, the icon set, the JS component-test harness, the `/dev/components` index and the axe runner. Five parallel component PRs can then avoid touching the same files.

**Depends on:** tokens card 1/4 A `t_cffc0b0e` (merged as #392). Nothing else.

**Files**
- Modify `src/web/src/app.css`. Add, without changing any existing token:
  - Tokens (D11): `--color-warning-solid: #8a5a12; --color-info: #7fb3e0; --color-danger-bg: #271f22; --color-warning-bg: #2e2b22; --color-info-bg: #212c36;`
  - `.meta { color: var(--color-muted); font-size: var(--text-sm); }` (D20).
  - The `.btn` system (D12): `.btn` gets `display:inline-flex; align-items:center; gap:var(--space-2); min-height:var(--touch-min); padding:var(--space-2) var(--space-4); border-radius:var(--radius-sm); font:inherit; text-decoration:none; cursor:pointer`. Variants:
    - `--secondary`: `--color-surface-raised` fill, `--color-text` label, `--color-border-control` border. This is today's `button` look.
    - `--primary`: `--color-accent` fill, `--color-on-accent` label.
    - `--success`: `--color-success-solid` fill, `--color-on-solid` label.
    - `--danger`: `--color-danger-solid` fill, `--color-on-solid` label.
    - `--link-danger`: transparent, `--color-danger` underlined text, keeps the 44px box.
  - `.btn[aria-disabled="true"]`: `cursor: progress`, dashed border, no opacity. `.btn` works on both `<a>` and `<button>`.
  - `@media (forced-colors: active)` fallbacks for `.btn` (`ButtonText`/`ButtonFace`/`ButtonBorder`).
- Create `src/web/src/lib/icons.ts`: `export type IconName = 'check'|'x'|'ban'|'trophy'|'equals'|'help'|'dash'|'alert'|'info'|'error'|'dot-open'|'close'|'spinner'` and a map of path data. Each icon is 16-viewBox, stroke-based (`stroke="currentColor"`, 1.5 width).
- Create `src/web/src/lib/components/Icon.svelte`: props `name: IconName`, `size?: string` (default `1em`), `class?`. Always `aria-hidden="true" focusable="false"`.
- Create `src/web/src/routes/dev/components/+page.svelte`: an index with `<h1>Component fixtures</h1>` and links to `/dev/components/{primitives,empty-state,error-banner,status-pill,scroll-table,form-pending}`. The last five 404 until their cards land. That is expected.
- Create `src/web/src/routes/dev/components/primitives/+page.svelte`: a fixture page showing every `.btn` variant as both `<a>` and `<button>`, plus `aria-disabled`; every icon; `.meta` text on `--color-bg`, `--color-surface` and `--color-surface-raised`; and text, muted and rule swatches on each `--color-*-bg` tint.
- Test harness (D22):
  - `package.json` devDependencies: `vitest`, `@testing-library/svelte`, `@testing-library/user-event`, `jsdom`, `axe-core`. Scripts: `"test:unit": "vitest run"`.
  - `vite.config.ts`: add `test: { environment: 'jsdom', include: ['src/**/*.test.ts'], setupFiles: ['src/test-setup.ts'] }` and, only under Vitest, `resolve: { conditions: ['browser'] }` (e.g. `...(process.env.VITEST ? { resolve: { conditions: ['browser'] } } : {})`). Svelte 5 needs its browser build under jsdom. Don't set `conditions` for normal builds, because that replaces Vite's defaults.
  - Create `src/web/src/test-setup.ts`.
  - Create `src/web/src/lib/test/axe.ts`: export `expectNoAxeViolations(container)`, which runs axe-core with tags `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` and the `color-contrast` rule disabled (jsdom can't compute it; the browser runner covers it).
- Create `src/web/tests/components_a11y.py` (Python Playwright, same CLI style as `src/web/tests/a11y_shared.py`):
  - Load `/dev/components`, follow every link that returns 200, inject `src/web/node_modules/axe-core/axe.min.js`, and run axe with the full rule set (contrast included) at 1280×800 and 375×812.
  - Assert `document.documentElement.scrollWidth <= innerWidth` at 375.
  - Assert no element under `main` has computed `opacity < 1`.
  - Re-run axe with `forced_colors='active'` and with `reduced_motion='reduce'`.
  - Accept `--url` (repeatable) so C6 can point it at real pages.
  - Exit non-zero on any failure.
- Create `tests/test_web_shared_components.py` (pytest, static, reusing the contrast helper pattern from `tests/test_web_a11y_shared.py`). Assert:
  - every new token exists with the D11 value;
  - white on `--color-warning-solid` ≥ 4.5;
  - `--color-info` ≥ 4.5 on bg, surface and raised;
  - on each `--color-*-bg` tint: `--color-text` and `--color-text-body` ≥ 4.5, `--color-muted` ≥ 4.5, the hue (`--color-danger`, `--color-warning`, `--color-info`) ≥ 3, and `--color-focus` ≥ 3;
  - `--color-on-accent` on `--color-accent`, white on `--color-success-solid` and white on `--color-danger-solid` are each ≥ 4.5;
  - no `.svelte` under `src/web/src/lib/components/` (excluding `review/` and `review-page/`) contains a 3- or 6-digit hex literal or `opacity: 0.x`.
- Modify `.github/workflows/ci.yml`:
  - add a `web-unit` job (Node 24, `npm ci`, `npm run test:unit` in `src/web`);
  - add `tests/test_web_*.py` (shell glob) to the explicit "Run pytest-style tests" list. This picks up `test_web_a11y_shared.py`, which `unittest discover` doesn't collect today, plus the new file, and lets later cards add `tests/test_web_*.py` files without editing `ci.yml`.

**API.** `Icon` props as above. `.btn` class contract as above. `expectNoAxeViolations(container: HTMLElement): Promise<void>`.

**A11y requirements**
- Icons are always decorative (`aria-hidden`). Meaning is carried by adjacent text.
- `.btn` keeps the global `:focus-visible` ring, has a 44×44 minimum target, and never uses opacity.
- `--link-danger` text meets ≥ 4.5:1 on bg, surface and raised (`--color-danger` is 7.44/6.94/6.43).

**Tests**
- Vitest `src/web/src/lib/components/Icon.test.ts`: renders every `IconName`, root has `aria-hidden="true"`, an unknown name is a type error (`// @ts-expect-error` line), and `expectNoAxeViolations` passes.
- pytest `tests/test_web_shared_components.py` as listed above.
- `components_a11y.py` passes on `/dev/components` and `/dev/components/primitives`.

**Acceptance**
1. `npm run test:unit` runs in CI and passes. `npm run check` and `npm run build` pass.
2. The pytest file is in CI and passes. The contrast numbers in D11 are asserted, not hand-computed.
3. `components_a11y.py --url http://127.0.0.1:5178/dev/components` exits 0 locally. Paste the output in the PR.
4. No existing page changes visually (no existing selector restyled). Review PDF visual regression CI is green.
5. The diff touches only the files listed above.

---

## C1 — EmptyState component

**Goal.** One component for "there is nothing here yet", replacing bare `<p>No … yet.</p>` and `<p class="empty">` text. It says what is missing, what to do next, and optionally offers one action.

**Depends on:** C0 (`.btn`, `Icon`, test harness, `/dev/components`); tokens 1/4 A `t_cffc0b0e` (merged).

**Spec:** `docs/ui/shared-components/specs/empty-state-error-banner.md` §2, as amended by README D7, D8, D9, D12 and token map §3.

**Files (create only)**
- `src/web/src/lib/components/EmptyState.svelte`
- `src/web/src/lib/components/EmptyState.test.ts`
- `src/web/src/routes/dev/components/empty-state/+page.svelte` (fixture)

**Props / API**

| Prop | Type | Default | Notes |
|---|---|---|---|
| `title` | `string` | required | Sentence case, no trailing period. |
| `description` | `string` | — | One sentence. The `children` snippet replaces it for rich content. |
| `variant` | `'page' \| 'inline'` | `'page'` | Page: centred, `--space-8` padding, `--color-surface-raised`, title `--text-xl`. Inline: left-aligned, `--space-4` padding, 1px dashed `--color-border`, title `--text-md`. |
| `headingLevel` | `2 \| 3 \| 4` | page `2`, inline `3` | Never `h1`. |
| `compact` | `boolean` | `false` | Inline only. Title renders as `<p class="empty-state__title">`, with no heading. |
| `ctaLabel` / `ctaHref` / `onCta` | `string` / `string` / `() => void` | — | `ctaHref` renders `<a class="btn …">` and wins when both are set. `onCta` renders `<button type="button" class="btn …">`. |
| `ctaVariant` | `'primary' \| 'secondary'` | page `primary`, inline `secondary` | Maps to C0 `.btn--*`. |
| `filtered` | `boolean` | `false` | Adds `role="status"` to the root. Used when the empty state is the result of a search the user just ran. |
| `icon` | `IconName` | — | Uses C0 `Icon`. 48px for page, 24px for inline. Decorative. |
| `children` / `actions` | `Snippet` | — | `actions` replaces the built-in CTA (max 2 actions). |
| `class` | `string` | — | Margins only. |

Root is a `<div class="empty-state empty-state--{variant}">` for both variants (D9). There is no `aria-labelledby` and no landmark. The title id comes from `$props.id()`. In dev mode, warn on `ctaLabel` without `ctaHref`/`onCta`.

**A11y requirements**
- The title is a real `h{headingLevel}` unless `compact`.
- No focus movement and no live region on mount, except `filtered` → `role="status"`.
- The CTA is a native `<a>`/`<button>`, ≥ 44×44, keeps the global focus ring, and has a label that names the action.
- Description uses `--color-muted`. No opacity.
- No animation required. Any fade must be behind `prefers-reduced-motion: no-preference`.
- At 375px the CTA wraps below the text with no horizontal overflow.

**Tests**
- Vitest `EmptyState.test.ts`:
  - heading level for page and inline, and compact → no heading element;
  - CTA as link (`href` set), as button (`type="button"`, `onCta` fires once on click and on Enter), and none;
  - `ctaHref` wins over `onCta`;
  - `filtered` → `role="status"`;
  - the icon wrapper is `aria-hidden`;
  - the `children` and `actions` snippets render;
  - `expectNoAxeViolations` for each variant.
- Fixture `/dev/components/empty-state` shows: page with link CTA, page with button CTA, inline, inline compact, filtered with a "Clear search" CTA, and an inline variant inside a `--color-surface` panel.
- `src/web/tests/components_a11y.py` (from C0) passes on the fixture: axe including contrast, 375px no-overflow, forced-colors.

**Acceptance**
1. Props, defaults and behaviour match the table above, and the component is typed (no `any` in the props).
2. Vitest and the axe runner pass. `npm run check` passes.
3. No hex, no opacity, and only the tokens in README §3.
4. The diff touches only the 3 files listed.

---

## C2 — ErrorBanner component

**Goal.** One component for load failures and form-action failures. Severity is shown by icon, word and colour, it is announced correctly, and it can offer a retry. It replaces the `<p class="error">` blocks duplicated across pages. It also covers `info` notices.

**Depends on:** C0 (tint tokens, `.btn`, `Icon`, harness); tokens 1/4 A `t_cffc0b0e` (merged).

**Spec:** `docs/ui/shared-components/specs/empty-state-error-banner.md` §3, as amended by README D10, D11, D12, D17 and D18.

**Files (create only)**
- `src/web/src/lib/components/ErrorBanner.svelte`
- `src/web/src/lib/components/ErrorBanner.test.ts`
- `src/web/src/routes/dev/components/error-banner/+page.svelte` (fixture)

**Props / API**

| Prop | Type | Default | Notes |
|---|---|---|---|
| `message` | `string` | required | Says what failed: "Deck data unavailable". |
| `detail` | `string` | — | Technical text, always rendered as text (never `{@html}`). Over 120 chars, or when `detailCollapsed`, it goes in a native `<details><summary>Details</summary>`. Otherwise it renders inline in `--color-text-body`. |
| `detailCollapsed` | `boolean` | `false` | |
| `severity` | `'error' \| 'warning' \| 'info'` | `'error'` | Maps to the tokens `danger` / `warning` / `info` (D10). Visible prefix word "Error:" / "Warning:" / "Notice:" in `<strong>`. Icons: `error` / `alert` / `info`. |
| `onRetry` | `() => void \| Promise<void>` | — | Its presence renders the retry button. |
| `retryLabel` | `string` | `'Try again'` | |
| `retrying` | `boolean` | `false` | Controlled. If `onRetry` returns a promise, the component tracks `retrying` itself until the promise settles. |
| `dismissible` | `boolean` | `false` | Renders a close button with `aria-label="Dismiss {severity word} message"` and the `close` icon. |
| `onDismiss` | `() => void` | — | |
| `open` | `boolean` (`$bindable`) | `true` | |
| `actions` / `children` | `Snippet` | — | `actions` renders after retry. `children` replaces `detail`. |
| `class` | `string` | — | Margins only. |

Visual: `--color-{danger|warning|info}-bg` fill, a 4px left rule in `--color-{danger|warning|info}`, text in `--color-text`, `--radius-sm`, `--space-3`/`--space-4` padding. At 375px the actions wrap below the message. Retry uses `.btn btn--secondary`.

**A11y requirements**
- `error` → `role="alert"`. `warning`/`info` → `role="status"` (implicitly polite). Both with `aria-atomic="true"`. The banner element itself is the live region: callers mount and unmount it rather than toggling `hidden`.
- No `autofocus` and no focus move on mount (D18).
- Retry is a native `<button type="button">`. While retrying it has `aria-disabled="true"` (not `disabled`), keeps focus, ignores extra clicks so it never double-fires, and its label reads "Retrying…". The spinner icon becomes a static "…" under `prefers-reduced-motion: reduce`. A rejected `onRetry` promise is caught (no unhandled rejection) and `retrying` resets.
- Dismiss: `Esc` while focus is inside the banner dismisses it. On dismiss, focus returns to the element focused before the user entered the banner (captured via `focusin.relatedTarget`) if it is still connected, otherwise `document.getElementById('main')` (the layout's `<main id="main" tabindex="-1">`). Never `body`.
- Contrast is covered by C0's tests (text ≥ 4.5 and rule/icon ≥ 3 on each tint). Forced-colors: 1px `CanvasText` border, with the icon and prefix word still visible.
- Enter/exit animation uses only transform/opacity on the container, ≤ `--dur-base`, and only under `prefers-reduced-motion: no-preference`. (Opacity animation is not dimming.)

**Tests**
- Vitest `ErrorBanner.test.ts`:
  - role per severity;
  - prefix word and icon per severity;
  - retry only when `onRetry` is set;
  - async retry sets `aria-disabled` and a second click while pending does not call `onRetry` again;
  - a rejected promise resets the state without an unhandled rejection;
  - `detail` containing `<script>alert(1)</script>` renders as text;
  - a 121-char detail renders as `<details>`;
  - dismiss button name, `Esc` dismiss, and focus restore to the prior element and to the `#main` fallback;
  - `bind:open`;
  - `expectNoAxeViolations` per severity.
- Fixture `/dev/components/error-banner` shows each severity with and without detail, long detail, retry (a 1s async resolve plus an always-reject case), dismissible, and an `actions` snippet. Include a `<main id="main" tabindex="-1">`-like target so the dismiss fallback is testable.
- `components_a11y.py` passes on the fixture.

**Acceptance**
1. Behaviour matches the table and a11y list above, and it is covered by tests.
2. Vitest and the axe runner pass. `npm run check` passes.
3. No hex, no opacity dimming, no `{@html}`.
4. The diff touches only the 3 files listed.

---

## C3 — StatusPill component + status catalog

**Goal.** One read-only inline component that conveys a state or result through a text label, an icon shape and colour, with colour always the third channel. It replaces the colour-only `.open`/`.committed`/`.win`/`.loss`/`.unavailable`/`.buy-tag`/`.pending-tag` spans.

**Depends on:** C0 (`--color-warning-solid`, `--color-info`, `Icon`, harness); tokens 1/4 A `t_cffc0b0e` (merged).

**Spec:** `docs/ui/shared-components/specs/status-pill-form-pending.md` §1, as amended by README D10, D11, D13 and D14.

**Files (create only)**
- `src/web/src/lib/status.ts` (types, catalog, mapping helpers; D14)
- `src/web/src/lib/status.test.ts`
- `src/web/src/lib/components/StatusPill.svelte`
- `src/web/src/lib/components/StatusPill.test.ts`
- `src/web/src/routes/dev/components/status-pill/+page.svelte` (fixture)

**API**

```ts
// status.ts
export type StatusRole = 'success' | 'warning' | 'danger' | 'info' | 'neutral';
export type StatusKey =
  | 'draft-open' | 'draft-committed' | 'draft-abandoned'
  | 'game-win' | 'game-loss' | 'game-draw' | 'game-unknown'
  | 'unavailable' | 'short' | 'owned' | 'in-mail'
  | 'success' | 'warning' | 'danger' | 'info' | 'neutral';
export const STATUS_CATALOG: Record<StatusKey, { label: string; icon: IconName; role: StatusRole; variant: 'solid' | 'text' }>;
export function draftStatus(raw: string): StatusKey;          // open|committed|abandoned → draft-*; unknown → fallback
export function gameResult(raw: string | null): StatusKey;    // null → game-unknown; win|loss|draw → game-*; unknown → fallback
export function fallback(raw: string): StatusKey;             // returns 'neutral'; warns once per raw value in dev
```

Catalog. Within each group, labels and icon silhouettes are unique.

| Key | Label | Icon | Role | Variant |
|---|---|---|---|---|
| draft-open | Open | dot-open | warning | text |
| draft-committed | Committed | check | success | solid |
| draft-abandoned | Abandoned | ban | neutral | text |
| game-win | Win | trophy | success | solid |
| game-loss | Loss | x | danger | solid |
| game-draw | Draw | equals | neutral | text |
| game-unknown | Result unavailable | help | neutral | text |
| unavailable | Unavailable | dash | neutral | text |
| short | Short | alert | danger | solid |
| owned | Owned | check | success | solid |
| in-mail | In mail | dot-open | warning | text |
| success / warning / danger / info / neutral | OK / Warning / Error / Info / — | check / alert / x / info / dash | same | solid / solid / solid / text / text |

`StatusPill.svelte` props:

| Prop | Type | Default |
|---|---|---|
| `status` | `StatusKey` | required |
| `label` | `string` | catalog label. When `status` came from `fallback(raw)`, the caller passes `label={raw}`. |
| `icon` | `IconName \| null` | catalog icon |
| `variant` | `'solid' \| 'text'` | catalog variant |
| `size` | `'sm' \| 'md'` | `'sm'` |
| `title` | `string` | — |
| `describedby` | `string` | — |
| `class` | `string` | — |

Markup per spec §1.4: `<span class="pill pill--{role} pill--{variant} pill--{size}" data-status>` containing `<Icon>` and `<span class="pill__label">`. CSS per spec §1.5, with role names from D10:
- `.pill--warning` fill = `--color-warning-solid` (no fallback needed now).
- `.pill--info` label = `--color-info`.
- `.pill--neutral` = `--color-muted` label on a transparent background, text variant only.

**A11y requirements**
- The visible label is the accessible name. The icon is `aria-hidden`. No `aria-label`, no `role`, no `tabindex`.
- The DOM label stays Title case; uppercase comes from CSS `text-transform` only.
- Wraps (`white-space: normal`) at 320px / 200% zoom and never truncates.
- `title` is never the only carrier of information. Callers put unique reasons in visible text or `describedby`.
- Forced-colors: `1px solid CanvasText`, `Canvas` background, `CanvasText` text. The icon follows via `currentColor`.
- Contrast: solid label ≥ 4.5 on its fill; text-variant label ≥ 4.5 on `--color-bg`, `--color-surface` and `--color-surface-raised`.

**Tests**
- Vitest `status.test.ts`:
  - every `StatusKey` has a catalog entry;
  - group uniqueness: draft, game and deck groups (`short`/`owned`/`in-mail`) have pairwise distinct labels and icons (the non-colour distinctness rule);
  - `draftStatus`/`gameResult` mappings, including `null`, an unknown string → `'neutral'`, and the dev warning firing once.
- Vitest `StatusPill.test.ts`:
  - every key renders visible text and an `aria-hidden` icon;
  - `icon={null}` removes the icon;
  - a `label` override;
  - the accessible name equals the label;
  - no focusable element;
  - `expectNoAxeViolations` over the whole catalog.
- pytest: extend nothing in C0's file. Add `tests/test_web_status_pill.py`. It parses `STATUS_CATALOG` role→variant pairs from `status.ts` (regex) and the token values from `app.css`, and asserts the contrast floors above for every role × variant actually used.
- Fixture `/dev/components/status-pill` shows every status in a list, in a table cell (with the `<span class="visually-hidden">Result: </span>` prefix pattern) and in an `h1`, in both sizes and both variants.
- `components_a11y.py` passes on the fixture, including forced-colors. Attach a greyscale screenshot of the fixture to the PR (manual distinctness check, spec AC2).

**Acceptance**
1. Catalog and helpers exactly as above. Unknown values never render unstyled.
2. Tests pass, including the uniqueness and contrast assertions.
3. `StatusPill.svelte` contains no hex, no `opacity` and no non-`--color-*` colour (grep).
4. The diff touches only the 5 files listed plus `tests/test_web_status_pill.py`. CI picks that file up through C0's `tests/test_web_*.py` glob, so `ci.yml` is not touched.

---

## C4 — ScrollTable component

**Goal.** One table component that always has a caption, `scope`d headers, a keyboard-reachable horizontal scroll region (via the existing `TableScroll`) and a real empty state, so no table ever widens the page at 375px.

**Depends on:** C0 (harness, `.meta`), **C1 EmptyState** (default empty content); tokens 1/4 A `t_cffc0b0e` (merged). Uses the existing `src/web/src/lib/components/TableScroll.svelte` (#395) unchanged.

**Spec:** `docs/ui/shared-components/specs/scroll-table.md`, as amended by README D4, D5, D6, D7 and D8 and token map §3. **The spec's `row` snippet, `RowHeader`/`Cell` exports, `stickyHeader`, `maxHeight` and `footer` are not built.**

**Files**
- Create `src/web/src/lib/components/ScrollTable.svelte`
- Create `src/web/src/lib/components/ScrollTable.test.ts`
- Create `src/web/src/routes/dev/components/scroll-table/+page.svelte` (fixture)
- Do **not** modify `TableScroll.svelte` (pages and `/dev/a11y` use it directly).

**Props / API** (generic `T`, using `<script lang="ts" generics="T">`)

| Prop | Type | Default | Notes |
|---|---|---|---|
| `caption` | `string` | required | Becomes the `<caption>` text and the region's `aria-label` (via `TableScroll label`). |
| `captionHidden` | `boolean` | `false` | `<caption class="visually-hidden">`. Otherwise the caption is visible, `caption-side: top`, `--text-sm`, `--weight-bold`, `--tracking-label`, `--color-text-body`. |
| `columns` | `Column[]` | required | `{ key: string; header: string; align?: 'start'\|'end'\|'center'; numeric?: boolean; headerHidden?: boolean; width?: string }` |
| `rows` | `T[]` | required | |
| `rowKey` | `(row: T, i: number) => string \| number` | index | |
| `rowHeader` | `string` (column key) | — | That column renders `<th scope="row">`. |
| `stickyFirstCol` | `boolean` | `false` | Requires `rowHeader` to be the first column (dev warning otherwise). Sticky cell gets `--color-surface` bg and a 1px `--color-border-control` trailing edge. |
| `density` | `'comfortable' \| 'compact'` | `'comfortable'` | Compact = `--space-1` / `--space-2` padding. At ≤ 480px, comfortable steps down to compact. |
| `minWidth` | `string` | `'40rem'` | Passed to `TableScroll minWidth`. |
| `cell` | `Snippet<[T, Column]>` | — | Custom cell content. Default: `row[column.key]` formatted as below. |
| `empty` | `Snippet` | — | Replaces the default empty content. Should render an `<EmptyState>`. |
| `emptyTitle` / `emptyDescription` | `string` | `'Nothing to show'` / — | Forwarded to the default `<EmptyState variant="inline" headingLevel={3}>`. |
| `class` | `string` | — | Outer wrapper only. |

Default formatting: a number renders as-is (**`0` renders `0`, never blank**). `null`/`undefined` renders `—` plus `<span class="visually-hidden">no value</span>`. `numeric` columns get `text-align: end` and `font-variant-numeric: tabular-nums`.

Markup:

```svelte
{#if rows.length}
  <TableScroll label={caption} {minWidth}>
    <table class="st st--{density}">
      <caption>…</caption>
      <thead><tr>{#each columns}<th scope="col">…</th>{/each}</tr></thead>
      <tbody>{#each rows as row, i (key)}<tr>{#each columns as c}{c.key === rowHeader ? <th scope="row"> : <td>}…{/each}</tr>{/each}</tbody>
    </table>
  </TableScroll>
{:else}
  <div class="st-empty">
    <p class="visually-hidden">{caption}</p>
    {empty ? @render empty() : <EmptyState …/>}
  </div>
{/if}
```

`headerHidden` columns render `<th scope="col"><span class="visually-hidden">{header}</span></th>`, never an empty `<th>`.

**A11y requirements**
- Exactly one `<caption>` per table with non-empty text. Every header has `scope="col"`. Row headers have `scope="row"`. No empty `<th>`.
- The region (from `TableScroll`) is focusable, named, scrollable with arrows/Home/End natively, and its focus ring is not clipped. Add `:global(.table-scroll:focus-visible) { outline-offset: -2px }` scoped under `.st`'s wrapper only if the ring clips. Verify in the fixture.
- Empty: no `<table>`, no focusable region, and no live region (D7). The page announces filter results itself.
- At 375px only the region scrolls: `document.documentElement.scrollWidth <= innerWidth`.
- No `display:block` on `table`/`tr`, no `role="presentation"`, no opacity.
- Forced-colors: cell borders `CanvasText`, sticky bg `Canvas`.

**Tests**
- Vitest `ScrollTable.test.ts`:
  - caption text, and caption hidden → `.visually-hidden`;
  - `scope` on every header;
  - a `rowHeader` cell is a `th[scope=row]`;
  - a `headerHidden` column has sr-only text;
  - `0` renders `0`; `null` renders `—` plus sr text;
  - the `cell` snippet overrides content;
  - `rows=[]` → no `table`, no `[role=region]`, and an EmptyState title is present;
  - the `empty` snippet replaces the default;
  - two instances on one page have distinct region names when their captions differ, and no duplicate ids;
  - `expectNoAxeViolations` for a populated and an empty table.
- Fixture `/dev/components/scroll-table` shows:
  - a 7-column have/buy-style table (`minWidth="52rem"`, `stickyFirstCol`, numeric columns, a cell with a placeholder `<span class="pill">`);
  - a compact 6-column table;
  - an empty table with the default EmptyState;
  - an empty table with an `empty` snippet and a CTA;
  - a `captionHidden` table under an `<h2>`.
- `components_a11y.py` passes on the fixture, including 375px no page overflow. Add Playwright checks to the runner for this fixture only (`--url …/scroll-table` keeps working):
  - Tab reaches the region;
  - ArrowRight increases `scrollLeft`;
  - with `stickyFirstCol`, the first-column cell's `getBoundingClientRect().left` stays the same after scrolling.

**Acceptance**
1. API matches the table above. The deferred props are absent.
2. Tests and the axe runner pass. `npm run check` passes.
3. `TableScroll.svelte` is unchanged, and so are `/dev/a11y` and `tests/test_web_a11y_shared.py`, which still pass.
4. Only tokens from README §3. No hex.
5. The diff touches only the 3 files listed plus additive check functions in `src/web/tests/components_a11y.py`.

---

## C5 — Form pending pattern (`createPending` + `SubmitButton`)

**Goal.** Give every SvelteKit POST form the same in-flight behaviour: a visible and announced pending state, a double-submit lock, input kept on failure, network errors shown in-form instead of on the error page, and `confirm()` that coexists with the lock. The helper owns the behaviour; the button owns the presentation.

**Depends on:** C0 (`.btn`, `Icon` spinner, harness); tokens 1/4 A `t_cffc0b0e` (merged). Error display uses C2 `ErrorBanner` **in pages only** (C6). C5 itself must not import ErrorBanner, so it does not depend on C2.

**Spec:** `docs/ui/shared-components/specs/status-pill-form-pending.md` §2, as amended by README D12, D15, D16, D17, D18 and D19.

**Files (create only)**
- `src/web/src/lib/forms/pending.svelte.ts`
- `src/web/src/lib/forms/pending.test.ts`
- `src/web/src/lib/components/SubmitButton.svelte`
- `src/web/src/lib/components/SubmitButton.test.ts`
- `src/web/src/routes/dev/components/form-pending/+page.svelte` and `+page.server.ts` (fixture with actions `ok`, `slow` (1500ms), `invalid` → `fail(400, { actionError })`, `boom` → `fail(503, …)`, `throws` → thrown error, plus `redirect` → 303 to the same page with `?done=1`)
- `src/web/tests/form_pending.py` (Python Playwright against the fixture)

**API**

```ts
createPending(opts?: {
  confirm?: (ctx: { formData: FormData; action: string }) => string | null; // null = no prompt
  key?: (formData: FormData) => string;            // which row/button is pending
}): {
  readonly submitting: boolean;
  readonly action: string | null;   // 'commit' for ?/commit, 'default' for no action
  readonly key: string | null;
  readonly transientError: string | null;  // set on network/5xx/thrown; cleared on next submit
  enhance: SubmitFunction;          // use:enhance={p.enhance}
  clearError(): void;
}
```

Lifecycle (D16):
1. If `opts.confirm` returns a message and `window.confirm` is false, `cancel()`. The form is **not** locked.
2. If already `submitting`, `cancel()` (a single flight across every form using this instance).
3. Set `submitting`, `action` and `key`, and clear `transientError`.
4. On `success` or `redirect`: `await update({ reset: true })`.
5. On `failure` with status < 500: `await update({ reset: false })`.
6. On `failure` ≥ 500, `type: 'error'`, or a thrown fetch: **do not** call `update()`. Set `transientError = 'Network or server error — your input was kept. Try again.'`
7. `finally`: clear `submitting`, `action` and `key`.

Forms keep `method="POST"` and `action="?/x"`, so a no-JS submit still works.

`SubmitButton.svelte` props:
- `pending: boolean` — locks the button.
- `busy?: boolean` — `$derived(busy ?? pending)` (D16e); controls the pending label and spinner.
- `pendingLabel: string` — required.
- `children: Snippet` — the idle label.
- `variant: 'primary'|'secondary'|'success'|'danger'|'link-danger'` — default `'secondary'` (D12).
- `disabled?: boolean`, `formaction?: string`, `type?: 'submit'|'button'`, `class?`, and `...rest: HTMLButtonAttributes` (passes through `aria-label`).

Markup (D15):

```svelte
<button {type} class="btn btn--{variant}" class:is-pending={pending} aria-disabled={pending || undefined} {disabled} {formaction} onclick={guard} {...rest}>
  <span class="sb__labels"><span class:sb__hidden={isBusy}>{@render children()}</span><span class:sb__hidden={!isBusy} aria-hidden={!isBusy}><Icon name="spinner" class="sb__spin"/> {pendingLabel}</span></span>
</button>
<span class="visually-hidden" role="status">{isBusy ? pendingLabel : ''}</span>
```

Both labels stack in one `inline-grid` cell, and the inactive one is `visibility: hidden`, so the button width never changes. The spinner rotates with `transform` over 600ms. Under `prefers-reduced-motion: reduce` it is replaced by a static "…".

**A11y requirements**
- While pending, the button has `aria-disabled="true"`, **keeps focus**, and stays in the tab order. The `disabled` attribute is reserved for the non-pending `disabled` prop. `guard` calls `preventDefault()` while pending.
- The accessible name while busy is the pending label once. The live-region span is outside the button, so the name is never duplicated (test this).
- Pages set `aria-busy={p.submitting}` on the `<form>` and may wrap fields in `<fieldset disabled={p.submitting}>`, with the button outside the fieldset. This is documented in the fixture.
- No opacity. The pending look is a dashed border plus `cursor: progress` (from C0 `.btn[aria-disabled]`).

**Tests**
- Vitest `pending.test.ts` drives `p.enhance` with fake `{ action: new URL('http://x/?/commit'), formData, cancel }` and a fake `update`. Cases:
  - success → `update({reset:true})` and the flag clears;
  - failure 400 → `update({reset:false})`;
  - failure 503, `type:'error'`, and a thrown error → `update` not called, `transientError` set, and the flag clears;
  - double submit → second `cancel()` called;
  - `confirm` returns false → `cancel()` and `submitting` stays false;
  - `confirm` returns null → no prompt;
  - `key` and `action` are exposed during flight and cleared after.
- Vitest `SubmitButton.test.ts`:
  - idle vs busy label;
  - `aria-disabled` only while pending;
  - a click while pending is `defaultPrevented`;
  - the accessible name while busy equals `pendingLabel` (no duplication);
  - the status span is a sibling;
  - `busy` defaults to `pending`;
  - `aria-label` passes through;
  - variants map to `.btn--*`;
  - `expectNoAxeViolations`.
- Playwright `src/web/tests/form_pending.py` against `/dev/components/form-pending`:
  1. `slow`: double-click plus Enter → exactly 1 POST (count via `page.on('request')`); `aria-busy="true"` on the form during flight; focus stays on the button.
  2. Button bounding box is the same (≤ 1px) idle vs pending.
  3. `invalid` → input values kept.
  4. `boom` and `throws` → `transientError` text visible, the page is not `+error`, and input is kept.
  5. Two buttons in two forms sharing one instance: pressing A locks B, and only A shows its pending label.
  6. `confirm` cancel (`page.on('dialog', d => d.dismiss())`) → no request, and the form is not locked.
  7. With `reduced_motion='reduce'`, the spinner's computed `animation-name` is `none` and the pending text is present.
  8. With `java_script_enabled=False`, the `ok` form submits natively and the page reloads with the result.
- `components_a11y.py` passes on the fixture.

**Acceptance**
1. Contract and lifecycle exactly as above. All 8 Playwright cases and the unit cases pass. Paste the outputs in the PR.
2. No `ErrorBanner` import in C5 files. The fixture renders `p.transientError` and `form?.actionError` as plain `<p role="alert">`.
3. `npm run check` passes. No hex, no opacity.
4. The diff touches only the files listed.

---

## C6 — Migrate pages to the shared components and delete duplicated per-page CSS

**Goal.** Adopt EmptyState, ErrorBanner, StatusPill, ScrollTable and the pending pattern on the decks, drafts, games, inventory and card pages, and delete the duplicated page-local `.meta` / `.error` / `.empty` / `.open` / `.committed` / `.win` / `.loss` / `.unavailable` / `.you` / `.buy-tag` / `.pending-tag` / `.table-scroll` / `.visually-hidden` / `button.commit|abandon|link` styles. Page layouts stay as they are (non-goal).

**Depends on:** C1, C2, C3, C4, C5 (all merged); C0 (global `.meta`, `.btn`, runner); tokens 1/4 A `t_cffc0b0e` (merged).

**Files (modify)**
- `src/web/src/routes/decks/+page.svelte`
- `src/web/src/routes/decks/[id]/+page.svelte`
- `src/web/src/routes/drafts/+page.svelte`
- `src/web/src/routes/drafts/[id]/+page.svelte`
- `src/web/src/routes/drafts/[id]/+page.server.ts` (add `action: '<name>'` to every `fail()` payload; no other change)
- `src/web/src/routes/games/+page.svelte`
- `src/web/src/routes/games/[id]/+page.svelte`
- `src/web/src/routes/inventory/+page.svelte`
- `src/web/src/routes/card/[oracle_id]/+page.svelte`
- `CHANGELOG.md` (one line under `### Changed`, for example: "Web app: empty lists, errors and statuses look and behave the same on every page — errors can be retried, statuses show a label and icon instead of colour alone, wide tables scroll on phones, and buttons show progress while saving.")
- `src/web/tests/components_a11y.py`: only if the runner needs a small change to accept page URLs. It already takes `--url`.

**Out of scope:** profile page (4/4 owns it); review page; the per-page "← home" `<nav>`s; page layout changes; the decks-import route (README X16).

**Per page** (rules: D17 retry, D18 placement, D20 `.meta`, D21 unavailable)

| Page | Changes |
|---|---|
| decks | `data.error` → `<ErrorBanner message="Deck data unavailable" detail={data.error} onRetry={() => invalidateAll()} retryLabel="Reload decks" />`. Empty → `<EmptyState title="No decks yet" description="Import a deck from Archidekt or Moxfield to start analysing it." />` (no CTA, X16). In the have/buy line, replace "· ✅ complete" with `<StatusPill status="owned" label="Complete" />`, and when `buy > 0` add `<StatusPill status="short" label={`Buy ${buy}`} />`; keep the counts text. Delete local `.meta`, `.error`. |
| decks/[id] | Have/buy table → `ScrollTable caption="Deck have/buy breakdown" captionHidden rowHeader="name" stickyFirstCol minWidth="52rem"` with numeric need/have/buy/pending/elsewhere. The Buy cell shows `StatusPill short "Buy N"` when N > 0, else `0`. `pending` and `elsewhere` zeros render `0`. Remove `tr.short` and the sr-only "need to buy" span (the pill carries it). Board lists: `.buy-tag` → `StatusPill short "Buy N"`; `.pending-tag` → `StatusPill in-mail`. Totals "✅ deck complete" → `StatusPill owned "Complete"`. Delete local `.meta`, `table`/`th`/`td`, `tr.short`, `.buy-tag`, `.pending-tag`. |
| drafts | Start form: `const p = createPending()`; `aria-busy`; fields in `<fieldset disabled={p.submitting}>` (the existing "Source decks" fieldset nests inside); `<SubmitButton pending={p.submitting} pendingLabel="Opening draft…">Open draft</SubmitButton>`. Below it: `{#if p.transientError}<ErrorBanner message="Couldn't create draft" detail={p.transientError} onRetry={() => formEl.requestSubmit()} />{:else if form?.createError}<ErrorBanner message="Couldn't create draft" detail={form.createError} dismissible />{/if}`. "No imported decks" → `<EmptyState variant="inline" title="No decks to draft from" description="Import a deck first." ctaLabel="Go to Decks" ctaHref="/decks" />`. `data.error` → ErrorBanner with retry "Reload drafts". Empty list → `<EmptyState variant="inline" title="No drafts yet" description="Pick source decks above and open one." />`. Status → `<StatusPill status={draftStatus(draft.status)} />`. Delete local `.meta`, `.open`, `.committed`, `.error`. |
| drafts/[id] | Header status → StatusPill. Commit/Abandon: one `createPending({ confirm: ({action}) => action === 'commit' ? '<existing commit text>' : '<existing abandon text>' })` shared by both forms (the existing `confirmSubmit` is deleted). Buttons: `SubmitButton variant="success"` and `variant="danger"`, `busy={p.action === 'commit'}` / `'abandon'`, keeping the existing `aria-label`s. Banner in `.danger-zone` for `form?.action === 'commit' \| 'abandon'` or `transientError`. Staged and moves tables → ScrollTable (`rowHeader` = card name column; actions column `headerHidden` "Actions"; cell snippet holds the row form with `SubmitButton variant="link-danger"`, one `createPending({ key })` per table, `busy={p.key === rowKey}`). The `upsert_card` and `stage_move` forms each get their own pending instance plus SubmitButton ("Saving card…", "Staging…") and a banner for their `form?.action`. Empty states → ScrollTable `emptyTitle` "Nothing staged yet" / "No copy moves staged" (description: the existing moves sentence) / "No add suggestions" / "No cut suggestions". `suggestionsError` → `ErrorBanner severity="warning" message="Suggestions unavailable" detail=… onRetry={() => invalidateAll()}`. Adds and cuts tables → ScrollTable. Delete local `.meta`, `.open`, `.committed`, `.error`, `button.link`, `button.commit`, `button.abandon`, `table`/`th`/`td`. |
| games | Import form: `createPending()`, `aria-busy`, `<fieldset disabled>` around the fields, `<SubmitButton pendingLabel="Importing…">Import &amp; analyze</SubmitButton>` plus a `<p class="meta">` hint "Parsing the log can take a few seconds." Banners per D17 (`form.importError` 400 → dismissible; transient → retry `requestSubmit`). The success redirect still works through `update()`. "No imported decks" → the same inline EmptyState as drafts. `data.error` → retry "Reload games". Empty list → inline "No games imported yet" / "Import a Forge log using the form above." Result → `<StatusPill status={gameResult(game.result)} />` (replaces the "result unavailable" text). Delete local `.meta`, `.error`. |
| games/[id] | h1 result → `<StatusPill status={gameResult(game.result)} size="md" />`. `.notice` lines → `<ErrorBanner severity="info" message="Log imported — this is the game it produced." dismissible />` (and the same for duplicate). `.you` → `<StatusPill status="neutral" label="Your deck" icon={null} />`. The three tables → ScrollTable (rowHeader card / turn / card). Unavailable columns per D21: drop a column whose values are all null and add one `<p class="meta">` note under the table naming the columns and the reason. Otherwise use `<StatusPill status="unavailable" />` per null cell, with visible reason text when the reasons differ. Delete local `.win`, `.loss`, `.unavailable`, `.you`, `.meta`, `.table-scroll`, `.visually-hidden`, `.notice`. |
| inventory | `data.error` → retry "Reload inventory". Table → `ScrollTable caption="Inventory by card" captionHidden rowHeader="name" stickyFirstCol density="compact"` with numeric owned/in_collection/in_deck/in_mail (zeros render `0`). A `cell` snippet handles the "+N not owned" note on `name` and joins the `decks` array with ", ". Empty via the `empty` snippet: `q` set → `<EmptyState variant="inline" filtered title={`No cards match “${data.q}”`} ctaLabel="Clear search" ctaHref="/inventory" />`; otherwise `title="Your collection is empty" description="Cards appear here after you sync or import decks."` The GET search form gets no pending UI (D19). Delete local `.error`, `.table-scroll`, `.visually-hidden`. |
| card | Every `<p class="empty">` → `<EmptyState variant="inline" compact title=…>`: "No card art yet", "No oracle text on record", "No printings data yet", "No tags yet", "Not in any deck", "No synergy data yet" (description "Run an interaction rebuild."), "Not owned". "In your decks" table → `ScrollTable caption="Decks containing this card" captionHidden rowHeader="name" minWidth="32rem"`. Notes form → `createPending()` + `<SubmitButton pendingLabel="Saving notes…">Save notes</SubmitButton>`. Move `form.actionError` inside the form after the button, as an ErrorBanner per D17. "Saved." → `<p class="meta" role="status">Saved.</p>`. Tag `.pill` chips stay as they are (they are tags, not statuses). Delete local `.empty`, `.error`, `.meta`, `.table-scroll`. |

**A11y requirements**
- Every migrated table goes through ScrollTable: one caption, scoped headers, a focusable named region, and no page-level horizontal scroll at 375px.
- Every status that is colour-only today now has a text label and an icon.
- Page-data errors use `role="alert"` with retry. Form errors render inside the form next to the button and don't steal focus. Info notices use `role="status"`.
- Pending: `aria-busy` on the form, `aria-disabled` on the button with focus kept, the pending label announced once, and one request per submit.
- No `opacity` dimming, no hex, no colour-only meaning remaining in the 8 files.

**Tests**
- grep gates, run in the PR and pasted. Zero hits across the 8 `.svelte` files for:
  - `class="error"`, `class="empty"`, `class="unavailable"`, `class="notice"`;
  - `class:open`, `class:committed`, `class:win`, `class:loss`, `class:short`;
  - `<table`;
  - `opacity`;
  - a hex literal;
  - `<style>` blocks defining `.meta`, `.error`, `.empty`, `.open`, `.committed`, `.win`, `.loss`, `.unavailable`, `.you`, `.buy-tag`, `.pending-tag`, `.table-scroll` or `.visually-hidden`;
  - `<button type="submit"` inside `method="POST"` forms (card's `<noscript>` GET Switch button is exempt).
- Axe + overflow: run `components_a11y.py --url` for the 8 routes (`/decks`, `/decks/<id>`, `/drafts`, `/drafts/<id>`, `/games`, `/games/<id>`, `/inventory`, `/card/<oracle_id>`) at 1280 and 375:
  - (a) **error state**, with the API stopped (`COMMANDER_API_URL` pointing at a closed port; list pages render ErrorBanner);
  - (b) **populated**, against a local stack (`docker compose up -d`, then `npm run dev` with `COMMANDER_API_URL=http://localhost:8000`).
  - Zero violations, and no page-level horizontal scroll at 375. If no populated deck/draft/game exists locally, create one through the UI and say so in the PR.
- `form_pending.py`-style manual check on drafts start, games import and card notes: one request on double-click, input kept on a 400, no `+error` page on API-down submit. With JS disabled, the forms still post.
- Existing CI (Test & Lint, E2E, Review PDF visual regression) green; `npm run test:unit`, `npm run check`, `npm run build` pass; `/dev/a11y` and `tests/test_web_a11y_shared.py` still pass.

**Acceptance**
1. Every row of the per-page table is done. The grep gates return zero.
2. Axe runner outputs (error and populated states, both widths) are pasted in the PR with zero violations.
3. Pending behaviour is verified on the three named forms, plus the no-JS submit.
4. No layout changes beyond the components' own boxes. Before/after screenshots at 1280 and 375 for each page are in the PR.
5. One CHANGELOG line. The diff is limited to the files listed.
6. Hotspot rule: if the 4/4 redesign cards (t_0c9cc4c9 subtree) have open PRs on drafts/games pages, rebase on them or split this card per page (inventory → decks → games → drafts → card) and note that on the card. Don't silently resolve conflicts against their layout work.
