# Shared UI components — reconciliation and coder plan

Card: t_93e75d00 (UI audit 2/4, root t_64481517). Audit: `~/.hermes/profiles/secretary/plans/ui-ux-audit-2026-10-29.md`.
Written against `origin/main` @ `18a4f77` (2026-10-29).

Inputs, copied verbatim into `specs/` so they are versioned (they only lived in unmerged worktrees):

| Spec | Card | Covers |
|---|---|---|
| `specs/empty-state-error-banner.md` | t_d065be72 | EmptyState, ErrorBanner |
| `specs/status-pill-form-pending.md` | t_e8073ef2 | StatusPill, form pending (`createPending` + `SubmitButton`) |
| `specs/scroll-table.md` | t_34d0e9ac | ScrollTable |

**Where a spec conflicts with §2 below, this README wins.** The implementation cards are in `cards.md`.

## 1. What is already on main

The specs were written before most of the foundation landed. Coders must build on what is there, not re-create it.

| Landed | PR | Relevance |
|---|---|---|
| `src/web/src/app.css`: `--color-*`, `--space-*`, `--text-*`, `--radius-*`, `--dur-*`, `--touch-min`, global `:focus-visible` (`--color-focus`), reduced-motion block, base `button`/`input`/`th`/`td` styles | #392 (card 1/4 A) | Token source of truth. Every "TBD (1/4)" name in the specs maps to one of these (§3). |
| `.visually-hidden`, `.visually-hidden-focusable`, `.table-scroll`, `.text-link` utilities | #395, #401 | `.visually-hidden` is the final name (resolves the StatusPill TBD). |
| `src/web/src/lib/components/TableScroll.svelte` (`label`, `minWidth`, `children`; focusable named region) | #395 | ScrollTable composes it (D4). |
| Layout shell: skip link, `<header>` + primary nav, single `<main id="main" tabindex="-1">` | #393, #401 | ErrorBanner dismiss can return focus to `#main`. |
| Per-page a11y passes: captions + `scope="col"` + scroll regions on decks/[id], drafts/[id], games/[id], inventory, card; `role="alert"` on every `.error`; `.meta` opacity → `--color-muted`; decks "short" rows get sr-only "need to buy N" | #394, #397, #398, #399, #400 | The migration card replaces these stop-gaps with components; it does not redo them. |

Not on main: no `.btn` classes, no icon set, no JS unit-test runner (only pytest + Python Playwright), no `--color-warning-solid`, no info or tint tokens.

## 2. Decisions (settled here; every card body repeats the ones it needs)

| # | Decision | Why |
|---|---|---|
| D1 | Paths: components in `src/web/src/lib/components/`, helpers in `src/web/src/lib/`, import alias `#lib/...` (not `$lib`, not `web/src/...`). | Repo convention (32 `#lib` imports, 0 `$lib`). ScrollTable spec had the wrong root. |
| D2 | Svelte 5 runes + snippets only. Unique ids come from `$props.id()` (SSR/hydration stable, Svelte ≥5.20; repo is on 5.57). | Confirmed in source. |
| D3 | Dark theme only. Every "light and dark" acceptance item means dark + `forced-colors: active`. | Foundation is dark-only; no light theme exists. A light theme recomputes contrast in its own card. |
| D4 | **ScrollTable composes `TableScroll`** for the region (`label={caption}` → `aria-label`), instead of the spec's `aria-labelledby` wiring. `TableScroll` stays as the low-level wrapper. | Same accessible name, no second region implementation. |
| D5 | **ScrollTable is column-driven.** It owns `<tr>`, iterates `columns`, and renders `<th scope="row">` for the `rowHeader` column and `<td>` otherwise. The only cell hook is an optional `cell(row, column)` snippet (default: formatted `row[column.key]`). The spec's `row` snippet, `RowHeader`/`Cell` helper exports, `data-label`, and dev cell-count assertion are dropped. | A `.svelte` file can't export helper components. Owning the cells means `scope` can never be hand-written wrong. |
| D6 | ScrollTable ships `caption`, `captionHidden`, `columns`, `rows`, `rowKey`, `rowHeader`, `stickyFirstCol`, `density`, `minWidth`, `cell`, `empty`, `emptyTitle`, `emptyDescription`, `class`. **Deferred:** `stickyHeader`, `maxHeight`, `footer`. | No migrated page needs them. Vertical scroll inside a region on phones is worse than page scroll. |
| D7 | **Empty table = no `<table>`, no region.** ScrollTable renders `EmptyState variant="inline"` (or the `empty` snippet) under a sr-only caption. The EmptyState spec's `<td colspan>` pattern and its acceptance item #7 are dropped. | The two specs disagreed. An empty focusable region is a dead tab stop, and an empty header row is noise. |
| D8 | EmptyState text prop is **`description`**. ScrollTable forwards **`emptyTitle` / `emptyDescription`** (the spec's `emptyBody` is renamed). CTAs in tables go through the `empty` snippet, which renders a full `<EmptyState>`. | One name for one concept. |
| D9 | EmptyState root is a `<div>` for both variants, never `<section aria-labelledby>`. | A named `<section>` is a region landmark; an empty state shouldn't add a landmark. |
| D10 | Token families everywhere are **`success` · `warning` · `danger` · `info` · `neutral`**. StatusPill's internal role classes and generic `StatusKey`s use these names (spec `ok`→`success`, `warn`→`warning`). ErrorBanner's public `severity` stays `'error' \| 'warning' \| 'info'` (user-facing words) and maps error→danger tokens. | The specs mixed `ok/warn`, `error/danger`. Matching token names removes a translation layer. |
| D11 | New tokens, added **only by card C0**, with contrast computed on the landed values (sRGB, WCAG 2.x): `--color-warning-solid #8a5a12` (white 5.91:1) · `--color-info #7fb3e0` (8.44 bg / 7.87 surface / 7.30 raised) · `--color-danger-bg #271f22` · `--color-warning-bg #2e2b22` · `--color-info-bg #212c36` (the tints are 12% of the hue over `--color-surface`; on them `--color-text` ≥11.8, `--color-text-body` ≥7.59, `--color-muted` ≥4.69, the hue rule ≥6.37 and `--color-focus` ≥8.86). | Closes the three spec TBDs (warning fill, info role, banner tints). With the fill token, a solid warning pill is now allowed. |
| D12 | **One `.btn` system in `app.css` (C0):** `.btn` + `--primary` (accent fill) · `--secondary` (today's `button` look) · `--success` · `--danger` · `--link-danger` (underlined `--color-danger` text, 44px target). EmptyState CTA: page → primary, inline → secondary. ErrorBanner retry → secondary. `SubmitButton` default variant → **secondary**. The spec's `ghost` and `link` variants are dropped. | Both specs needed `.btn` and neither owned it. A secondary default keeps the migrated pages looking the same. Nothing in scope used `ghost`; `link-danger` is what remove/unstage need. |
| D13 | **One icon set (C0):** `src/web/src/lib/icons.ts` + `Icon.svelte`, inline SVG at 16 viewBox with `stroke="currentColor"`, always `aria-hidden`. Names: `check x ban trophy equals help dash alert info error dot-open close spinner`. | StatusPill, ErrorBanner and SubmitButton would otherwise each add one. |
| D14 | StatusPill catalog types and helpers live in **`src/web/src/lib/status.ts`** (`StatusKey`, `IconName` re-export, `STATUS_CATALOG`, `draftStatus()`, `gameResult()`, `fallback()`), not exported from the `.svelte` file. One status is added: **`in-mail`** (label "In mail", icon `dot-open`, warning, text), for decks/[id] `.pending-tag`. The deck/card group becomes `short` ▲ / `owned` ✓ / `in-mail` ◯, three distinct shapes. | Plain TS is unit-testable without rendering. "In mail" is a status that is colour-only today, and the spec missed it. |
| D15 | **`SubmitButton`'s live region is a sibling of the `<button>`, not a child.** The component renders `<button>…</button><span class="visually-hidden" role="status">…</span>`. | In the spec markup the status text sat inside the button, so the accessible name became "Opening draft… Opening draft…". |
| D16 | `createPending()` contract fixes: (a) on `result.type === 'error'` or a `failure` with status ≥500, do **not** call `update()` (that renders `+error.svelte`); set `p.transientError` to "Network or server error — your input was kept. Try again." and keep the input. (b) For other failures call `update({ reset: false })`; on success, `update({ reset: true })`; redirects go through `update()`. (c) One instance may enhance several `<form>`s (Commit + Abandon; every row's remove form), and the lock is shared. `p.action` (`'commit'`, from `?/commit`) and an optional `p.key` (from `opts.key(formData)`, e.g. the row's oracle id) tell `SubmitButton` which button shows its pending label. (d) `confirm` receives `{ formData, action }` and returns a message, or `null` for no prompt, so one instance can hold different Commit and Abandon prompts. The spec's `errorKey` option is replaced by `transientError`. (e) `busy` is `$derived(busy ?? pending)`, not a prop default that reads another prop. | The spec's sample code called `update()` on every result, which contradicts its own text. |
| D17 | **Retry rule for form banners:** `onRetry` (→ `formEl.requestSubmit()`) only when the failure is transient (`p.transientError`). Validation and 4xx failures render `ErrorBanner dismissible` with no retry, because resubmitting unchanged input fails again. Page-data errors (`data.error`) always get `onRetry={() => invalidateAll()}`. | The two specs disagreed (retry always vs. never). |
| D18 | Form banners go inside the `<form>`, after the submit button, outside any `<fieldset disabled>`. Page-data banners go first in the page content, before the section they describe. Neither steals focus on mount. On dismiss, focus returns to the element that had focus before the user entered the banner (tracked via `focusin.relatedTarget`) if it is still connected, otherwise `#main`. | Merges the placement and focus rules from both specs into one implementable rule. |
| D19 | The pending pattern is **POST forms only**. Table loading is the page's job; ScrollTable has no `loading` state. GET forms (inventory search, card printing switch) are navigation and get no pending UI. | Agreed by all three specs; written down once. |
| D20 | `.meta` becomes **one global utility in `app.css`** (`color: var(--color-muted); font-size: var(--text-sm)`), matching what #399/#400 already ship. The migration deletes every page-local `.meta` block. The StatusPill spec's `--color-text-body` suggestion is not used. | Main already uses `--color-muted` (6.24:1 on bg, AA). A global class removes the duplication the audit flagged without touching 40+ call sites. |
| D21 | games/[id] "unavailable" cells: if **every** row in a column is unavailable, drop the column and add one note under the table ("Drawn and Stuck in hand are unavailable: {reason}"). If only **some** rows are unavailable, render `StatusPill status="unavailable"` in those cells, plus visible reason text when the reasons differ, or one footnote referenced by `describedby` when they are the same. | ScrollTable said "drop the column" and StatusPill said "pill per cell". Both rules hold, each in its own case. |
| D22 | Tests: **vitest + @testing-library/svelte + jsdom + axe-core** for components (C0 adds the runner and an `npm run test:unit` CI step); pytest static guards for token contrast and the hex/opacity ban; a Python Playwright + axe runner (`src/web/tests/components_a11y.py`) for rendered contrast and 375px overflow, run locally by coder and tester (same model as `src/web/tests/a11y_shared.py`). | The repo has no JS test runner. Python Playwright is the established browser harness. |
| D23 | Collision avoidance: only C0 edits `app.css`, `package.json`, `vite.config.ts` and `ci.yml`. Each component card owns its component file, its `*.test.ts`, and its own fixture route `src/web/src/routes/dev/components/<name>/+page.svelte`. C0 creates the `/dev/components` index, which already links all five fixture routes, and the axe runner finds them from that index. Component cards add no CHANGELOG line (no user-facing change); the migration card adds one. | Five parallel PRs must not touch the same file. |
| D24 | Coder cards depend on tokens card 1/4 **A** (t_cffc0b0e, merged as #392), not on the 1/4 root t_5c3b8269. That root is still open, waiting on unrelated children. | The tokens these cards use are on main. Waiting on the root would stall the work for no technical reason. |

## 3. Token name map (spec proposals → landed tokens)

| Spec name (any of the three) | Use | Landed / new token |
|---|---|---|
| `--surface-1`, page surface | table/pill page bg | `--color-surface` (`--color-bg` for the page body) |
| `--surface-2`, `--color-surface-2`, `--surface-hover` | header, raised, hover, sticky cell bg | `--color-surface-raised` |
| `--text`, `--color-text` | strong text | `--color-text` |
| `--text-muted`, `--color-text-muted` | muted text | `--color-muted` |
| body copy | body text | `--color-text-body` |
| `--border` | dividers | `--color-border` |
| `--border-strong` | sticky edge, control borders | `--color-border-control` |
| `--focus-ring` | focus | global `:focus-visible` (2px `--color-focus`, offset 2px). For scroll regions, use `outline-offset: -2px` so the ring isn't clipped. |
| `--color-danger` / `--color-warning` | label/rule | same names (landed) |
| `--color-info` | info label/rule | **new** `#7fb3e0` (D11) |
| `--color-*-bg` | banner tint | **new** `--color-danger-bg` / `--color-warning-bg` / `--color-info-bg` (D11) |
| `--color-warning-solid` | warning fill | **new** `#8a5a12` (D11) |
| `--space-2/3/4/6/8` | spacing | same names (landed; also `-1`, `-5`, `-10`) |
| `--font-size-sm`, `--text-sm` / `--text-base` / `--text-xl` | type | `--text-sm` / **`--text-md`** (there is no `--text-base`) / `--text-xl` |
| `--tracking-wide` | caption letter-spacing | `--tracking-label` |
| `--dur-fast` (150ms), `--dur-base` (250ms) | motion | `--dur-fast` (120ms), `--dur-base` (200ms), landed values |
| `--radius-sm` / `--radius-md` | radius | same names (landed) |
| `--icon-size` | EmptyState icon | component-local custom property |
| `--st-min-width` | ScrollTable min width | the existing `--table-min-width` (set by `TableScroll`'s `minWidth`) |

## 4. Conflicts found between the specs (and their resolution)

| # | Conflict | Specs | Resolved by |
|---|---|---|---|
| X1 | Table empty state: no table vs `<td colspan>` inside the table | ScrollTable §5 vs EmptyState §2.3/AC7 | D7 |
| X2 | Empty text prop: `emptyBody`/`body` vs `description` | ScrollTable §2 vs EmptyState §2.2 | D8 |
| X3 | Severity/role names: `ok/warn/danger` vs `error/warning/info` vs token `success/warning/danger` | StatusPill §1.2 vs ErrorBanner §3.2 vs app.css | D10 |
| X4 | Token names: ScrollTable and EmptyState/ErrorBanner use pre-merge proposals (`--surface-1`, `--text-muted`, `--focus-ring`, `--text-base`, …) | ScrollTable §6, EmptyState §1 | §3 map |
| X5 | Form error retry: always `onRetry` → `requestSubmit()` vs "no `onRetry`, resubmit the form" | Pending §2.4/§2.6 vs ErrorBanner §3.7 | D17 |
| X6 | `SubmitButton` `role="status"` span inside the `<button>` corrupts the accessible name | Pending §2.3 markup | D15 |
| X7 | `createPending` sample calls `update()` on `type:'error'` (renders `+error.svelte`), contradicting its own §2.6 | Pending §2.2 vs §2.6 | D16 |
| X8 | `.btn` needed by EmptyState, ErrorBanner, SubmitButton; no card owns it; variant sets differ (`primary/secondary` vs `primary/danger/success/ghost/link`) | EmptyState §4, Pending §2.5 | D12 |
| X9 | ScrollTable "exports `<RowHeader>`/`<Cell>` from the same module" (not possible for a `.svelte` file); `row` + `cell` duality | ScrollTable §2 | D5 |
| X10 | ScrollTable reimplements the region that `TableScroll` (#395) already provides | ScrollTable §3 vs main | D4 |
| X11 | games/[id] unavailable: drop the column vs a pill in every cell | ScrollTable §7 vs StatusPill §1.8 | D21 |
| X12 | `.meta` replacement: `--color-text-body` vs `--color-muted` (already shipped) | StatusPill §1.8 vs #399/#400 | D20 |
| X13 | `import type { StatusKey } from './StatusPill.svelte'` and `$lib` alias | StatusPill §1.8 | D1, D14 |
| X14 | Paths `web/src/lib/...` vs `src/web/src/lib/...` | ScrollTable §2 | D1 |
| X15 | Acceptance asks for "light and dark" contrast; no light theme exists | all three | D3 |
| X16 | Decks empty-state CTA `href="/decks/import"`: the web app has no deck-import route (the API has `POST /api/v2/imports/archidekt`, but no page) | EmptyState §2.6 | No CTA on the decks empty state for now; the description says what to do. Gap flagged for 4/4 (t_0c9cc4c9). |
| X17 | Pending acceptance #10 covers `profile/**`; the migration scope is decks/drafts/games/inventory/card | Pending §2.10 vs task scope | Profile adopts the components in 4/4's profile-editor redesign (t_0c9cc4c9). Not in these cards. |
| X18 | `EmptyState` `section` landmark | EmptyState §2.3 | D9 |

No conflict: all three agree that colour is never the only signal (icon + text), that nothing uses `opacity` dimming, that there's no focus stealing on mount, that error severity means `role="alert"` and the rest `role="status"`, and that `aria-disabled` (not `disabled`) is used while pending or retrying.

## 5. Card set and dependency graph

```
t_cffc0b0e (1/4 tokens, merged #392)
        │
        C0  Foundation primitives: tokens, .btn, .meta, Icon, test harness, /dev/components index
        ├── C1  EmptyState ──────────┐
        ├── C2  ErrorBanner          │
        ├── C3  StatusPill           ├──► C6  Migration: decks, drafts, games, inventory, card
        ├── C4  ScrollTable ◄─ C1    │
        └── C5  Form pending ────────┘   (C6 depends on C1–C5)
```

C1, C2, C3 and C5 run in parallel after C0. C4 waits for C1, because its default empty state is `EmptyState`. C6 waits for all of them.

Hotspot: C6 touches 8 route files, so it is one card per the brief. If it collides with other UI work (4/4 redesigns touch drafts, games and profile), split it per page, in the order inventory → decks → games → drafts → card.

Gaps outside these cards: profile page adoption (X17), a deck-import route for the decks empty CTA (X16), and running `components_a11y.py` / `a11y_shared.py` in CI (they are local today).
