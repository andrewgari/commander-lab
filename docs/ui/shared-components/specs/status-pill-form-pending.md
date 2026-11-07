# Spec: StatusPill + form pending pattern

Card: t_e8073ef2 (UI audit 2/4, t_64481517) · Author: designer · 2026-10-29
Source audit: `~/.hermes/profiles/secretary/plans/ui-ux-audit-2026-10-29.md`
**Token source of truth:** `ui-foundation-spec.md` (audit card 1/4, t_5c3b8269), mirrored at `~/.hermes/profiles/secretary/plans/ui-foundation-spec.md`. All `--color-*` / `--space-*` / `--radius-*` / `--text-*` / `--font-*` / `--color-focus` names and contrast ratios below are quoted from that spec's §1.1/§2.
Scope: SvelteKit app in `src/web` (Svelte 5 runes). Non-goal: page layouts.

> Assumptions (no check-ins per Andrew's override):
> - **Card 1/4 has landed.** The foundation spec defines the dark HUD token set with WCAG-verified contrast. `StatusPill` and `SubmitButton` consume those tokens **directly** — there is no second `--status-*` / `--btn-*` alias layer (an earlier draft proposed one; it was dropped because the foundation's semantic `--color-*` layer already is the app's token layer, and a second layer only re-introduces the reconciliation burden card 2/4 was asked to close).
> - Still **TBD** (flagged, not blocking): `--color-warning-solid` — foundation ships warning as a **text** token only (`#f0a63a`); a solid warning pill needs a fill token, proposed in §1.6; the `info`/`data` role — no foundation token, kept only as a generic fallback (no audited state maps to it); the visually-hidden utility class name — foundation spec doesn't define it, the a11y spec (`ui-audit-3`) proposes `.visually-hidden`; and **light-theme values** — the audited pages are unthemed light today and the foundation spec is dark-only, so light values must re-pass the same AA checks when defined.
> - The component never references raw hex or HUD names. Coder fills the `--color-warning-solid` gap in `app.css` (card 1/4 follow-up); until then the warning pill renders its `text` variant.

---

## 0. What the audited pages do today

| Page | Today | Problem |
|---|---|---|
| `/drafts` list | `<span class:open class:committed>{draft.status}</span>`, amber `#b9770e` / green `#1e8449` | State is coloured by hue only. `abandoned` has no style at all. Amber on white ≈ 3.4:1 (fails AA). |
| `/drafts/[id]` header | same `.open` / `.committed` | Duplicated in 2 files, with `.meta` (`opacity:.75`, drops contrast) copy-pasted |
| `/games/[id]` h1 | `.win` green / `.loss` red, uppercase text | Win and loss differ by hue only. `draw`/unknown fall through unstyled. |
| `/games` list | `{game.result}` or `result unavailable` buried in `.meta` | Result is unstyled prose in a dim line — not scannable |
| `/games/[id]` cells | `unavailable — reason` repeated in `.unavailable` (`opacity:.6`) | Fails AA (opacity dimming). Counts as a status: `unavailable` |
| `/profile` | `.ok` / `.error` paragraphs for form feedback | Colour only, no `role` (handled by §2 and `ErrorBanner`) |
| `/decks` | "short" cards red-only, have/buy counts | Audit asks for a "Buy 2" pill: `short` status plus a count |

Duplicated classes this spec removes: `.open`, `.committed`, `.win`, `.loss`, `.you`, `.unavailable`, and the status use of `.meta`. (`.meta` as general muted text is typography, not a status — it moves to `--color-text-body`/`--color-muted` with no `opacity`, which is the a11y spec's job, out of scope here and noted in §1.8.)

---

# 1. StatusPill

## 1.1 Purpose

One inline, read-only component that conveys a **state or result** through three redundant channels: **text label + icon shape + colour**. Colour is only ever the third channel. A user with no colour vision, a monochrome print, or `forced-colors` must get the same information.

It is **not** a button, a filter chip, or a toast. It is non-interactive (no `tabindex`, no `role="button"`).

## 1.2 Props

| Prop | Type | Default | Notes |
|---|---|---|---|
| `status` | `StatusKey` (see 1.3) | required | Drives the role (colour family), default label, default icon, default variant |
| `label` | `string` | catalog default for `status` | Visible text. **Always rendered.** Override for context ("Buy 2", "Won 2–1") |
| `icon` | `IconKey \| null` | catalog default for `status` | `null` removes the icon. Never remove it for a status whose sibling is otherwise identical in shape (1.4 rule) |
| `variant` | `'solid' \| 'text'` | catalog default for the role | `solid` = filled chip (saturated fill, white label) for hero/terminal/importance; `text` = coloured label + hairline border for inline use in dense tables. A **density** choice, not a new meaning — the label always carries the specific state |
| `size` | `'sm' \| 'md'` | `'sm'` | `sm` for lists and table cells (12px); `md` for headings (14px, e.g. the `h1` result) |
| `title` | `string` | – | Tooltip (unavailable reason, …). **Never the sole carrier** of information — not keyboard/touch reachable |
| `describedby` | `string` | – | id of an element holding long-form reason; sets `aria-describedby` |
| `class` | `string` | – | Layout-only escape hatch (margin). No colour overrides |

```ts
export type StatusKey =
  // drafts
  | 'draft-open' | 'draft-committed' | 'draft-abandoned'
  // games / results
  | 'game-win' | 'game-loss' | 'game-draw' | 'game-unknown'
  // data / inventory
  | 'unavailable' | 'short' | 'owned'
  // generic
  | 'ok' | 'warn' | 'danger' | 'info' | 'neutral';

export type IconKey =
  | 'dot-open' | 'check' | 'ban' | 'trophy' | 'x' | 'equals'
  | 'help' | 'dash' | 'alert' | 'info';
```

The enum is **closed**. An unknown runtime string (the API can return something new) goes through the `fallback()` helper and renders as `neutral` with the **raw string as the label**. It never silently renders unstyled.

## 1.3 Status catalog (status → label → icon → role → variant)

Icon + label are distinct per sibling group. Within a group, no two statuses share an icon *and* no two share a shape.

| `status` | Default label | Icon (shape) | Role | Default variant | Notes |
|---|---|---|---|---|---|
| `draft-open` | Open | `dot-open` ◯ hollow circle | warn | text | API `status === 'open'`; in progress |
| `draft-committed` | Committed | `check` ✓ | ok | solid | `'committed'`; applied |
| `draft-abandoned` | Abandoned | `ban` ⊘ | neutral | text | `'abandoned'`; currently unstyled |
| `game-win` | Win | `trophy` (SVG glyph) | ok | solid | `result === 'win'` |
| `game-loss` | Loss | `x` ✕ | danger | solid | `'loss'` |
| `game-draw` | Draw | `equals` = | neutral | text | `'draw'` if the parser emits it |
| `game-unknown` | Result unavailable | `help` ? | neutral | text | `result` null; replaces bare "result unavailable" |
| `unavailable` | Unavailable | `dash` — | neutral | text | Replaces every `unavailable — {reason}` cell; reason → `title`/`describedby` + one footnote (audit: "repeated in every cell") |
| `short` | Short | `alert` ▲ | danger | solid | Decks have/buy. Pass `label="Buy 2"` |
| `owned` | Owned | `check` ✓ | ok | solid | Inventory + deck have-count. Use `variant="text"` inline in a long table |
| `ok` / `warn` / `danger` / `info` / `neutral` | OK / Warning / Error / Info / – | `check` / `alert` / `x` / `info` / `dash` | ok / warn / danger / info / neutral | solid / text / solid / text / text | Generic fallbacks; supply `label` |

Icons are inline SVG (16 viewBox, `stroke="currentColor"`, 1.5 stroke) from one local icon set. No icon font, no emoji (emoji render differently per OS and ignore `currentColor`). Glyph art is the coder's pick from the shared set, provided **shape distinctness** holds.

### Rule: non-colour distinctness test
Convert any rendered list of statuses to greyscale. Each distinct status must still be distinguishable by **label text alone** (primary) and by **icon silhouette** (secondary). Statuses that mean opposite things (`win`/`loss`, `committed`/`abandoned`) must have **different icon silhouettes**, not just different colours. A check-vs-x pair is the minimum.

## 1.4 Markup

```svelte
<!-- StatusPill.svelte -->
<span
  class="pill pill--{role} pill--{variant} pill--{size}"
  data-status={status}
  {title}
  aria-describedby={describedby}
>
  {#if icon}
    <svg class="pill__icon" aria-hidden="true" focusable="false" viewBox="0 0 16 16">{iconPath}</svg>
  {/if}
  <span class="pill__label">{label}</span>
</span>
```

Rendered, for `draft-committed` (solid):

```html
<span class="pill pill--ok pill--solid pill--sm" data-status="draft-committed">
  <svg class="pill__icon" aria-hidden="true" focusable="false" viewBox="0 0 16 16">…</svg>
  <span class="pill__label">Committed</span>
</span>
```

A11y rules baked into the markup:
- **The visible label is the accessible name.** The icon is `aria-hidden`. No `aria-label` that repeats or contradicts the text.
- **No `role="status"` on the pill by default** — it would announce every list row. If a pill's value changes live (e.g. a draft flips to Committed after a form action) the **page** announces via the form feedback region (§2.6), not the pill.
- Where the pill is the only text in a cell, prefix context for screen readers: `<td><span class="visually-hidden">Result: </span><StatusPill …/></td>`, or use `describedby`. (A `srPrefix` prop is YAGNI — rejected.) The `.visually-hidden` utility is defined by the a11y spec (`ui-audit-3`) and added to `app.css`; class name **TBD** until that lands.
- Never truncate with ellipsis. The pill wraps below `20rem` (`white-space: normal`, not `nowrap`) so zoom/reflow (WCAG 1.4.10) doesn't clip.
- `@media (forced-colors: active)` → `border: 1px solid CanvasText; background: Canvas; color: CanvasText;`. The icon uses `currentColor`, so shape + text survive.

## 1.5 Visual spec

Flat metro, per the HUD rules in the foundation spec: 1px hairline border, `--radius-sm` (2px), no glow/shadow/gradient. Two variants, both consuming foundation tokens directly.

```css
.pill {
  display: inline-flex; align-items: center; gap: var(--space-1);      /* 4px */
  padding: 0 var(--space-2);                                            /* 8px h padding */
  min-height: 1.5rem;                                                   /* sm; md: 1.75rem */
  border-radius: var(--radius-sm);                                      /* 2px */
  font: var(--weight-bold) var(--text-xs) / 1 var(--font-mono);        /* sm; md uses --text-sm */
  letter-spacing: var(--tracking-label); text-transform: uppercase;    /* CSS-only; DOM text stays readable */
  white-space: normal;
}
.pill__icon { width: 1em; height: 1em; flex: none; }

/* text variant: coloured label on the page surface + decorative hairline */
.pill--text {
  background: transparent;
  border: 1px solid var(--color-border);
}

/* solid variant: saturated fill + white label */
.pill--solid {
  border: 1px solid transparent;                                        /* fill is the edge */
}

/* role → token (ok | warn | danger | neutral | info) */
.pill--ok      { --pill-fg: var(--color-success);  --pill-fill: var(--color-success-solid);  }
.pill--warn    { --pill-fg: var(--color-warning);  --pill-fill: var(--color-warning-solid, var(--color-warning)); } /* solid TBD → text fallback */
.pill--danger  { --pill-fg: var(--color-danger);   --pill-fill: var(--color-danger-solid);   }
.pill--neutral { --pill-fg: var(--color-muted);    --pill-fill: var(--color-surface-raised);  }
.pill--info    { --pill-fg: var(--color-accent);   --pill-fill: var(--color-surface-raised);  } /* info token TBD */

.pill--text   { color: var(--pill-fg); }
.pill--solid  { color: var(--color-on-solid); background: var(--pill-fill); }
```

- `text-transform: uppercase` is CSS only, so the DOM text stays "Committed" for screen readers and copy/paste.
- **No `opacity` anywhere** on the pill or its parent row to "dim" it (that is the audit's AA failure). The `neutral` role + `text` variant is the dim look, achieved with `--color-muted` (6.2:1, AA).
- The `text`-variant border is `--color-border` (`#29323b`, 1.4:1) — **decorative** (the state is carried by text + icon), so it is exempt from the 3:1 non-text requirement (WCAG 1.4.11). Do not use the border to convey state.
- `pill--warn` falls back to the `text` variant when `--color-warning-solid` is unset (the `var()` second argument + the `text` default in the catalog keep it safe).

## 1.6 Token mapping (role → card 1/4 token)

The component references foundation `--color-*` tokens directly. Contrast is the foundation spec's own verified figures (§1.1); the HUD values the audit cited (`#6cbf7a`, `#ff7b6e`, `#f0a63a`, `#8a96a2`) are the same source the foundation lifted from `hud.css`, so the ratios carry over.

| Role | Label / icon (`--pill-fg`) | Fill (`--pill-fill`) | On-fill label | Default variant | Open item |
|---|---|---|---|---|---|
| ok | `--color-success` `#6cbf7a` | `--color-success-solid` `#1e8449` | `--color-on-solid` `#ffffff` | solid | — |
| warn | `--color-warning` `#f0a63a` | **`--color-warning-solid` TBD** (propose `#7a5316`-family dark amber) | `--color-on-solid` | text (until solid lands) | card 1/4 follow-up |
| danger | `--color-danger` `#ff7b6e` | `--color-danger-solid` `#b4443c` | `--color-on-solid` | solid | — |
| neutral | `--color-muted` `#8a96a2` | `--color-surface-raised` `#1a2128` | — | text | — |
| info | **TBD** (no foundation token; `--color-accent` as interim) | `--color-surface-raised` | — | text | no audited state uses it |

Other tokens used: `--color-on-solid` `#ffffff`, `--color-border` `#29323b`, `--space-1`/`--space-2`, `--radius-sm`, `--text-xs`/`--text-sm`, `--font-mono`, `--weight-bold`, `--tracking-label` — all defined in the foundation spec §1.

## 1.7 Contrast requirements (WCAG 2.2 AA)

Hard requirements:
- Label text ≥ **4.5:1** against the pill's own background (solid) and against the page surface (text variant, transparent bg).
- Icon and border (non-text UI) ≥ **3:1** against adjacent colour (1.4.11). The icon sits on the pill bg and carries the label colour (`solid` → white, `text` → `--pill-fg`), so it inherits the label ratio and clears 3:1 comfortably. The `text`-variant border is decorative → exempt.
- The fill vs the page surface does **not** need 3:1 — the state is carried by text + icon, the fill is reinforcement.
- Verified under `prefers-contrast: more` and `forced-colors`.

Computed on the foundation spec's values (sRGB relative luminance). These are the floor for the final tokens; **light-theme values (TBD) must be recomputed before merge**.

**`text` variant** — label = `--color-*` fg on the page surface `--color-surface` `#141a1f` (and on `--color-bg` `#0e1216`):

| Role | Label token | on `#141a1f` | on `#0e1216` | AA? |
|---|---|---|---|---|
| ok | `--color-success` `#6cbf7a` | 7.8 | 8.4 | ✓ |
| warn | `--color-warning` `#f0a63a` | 8.5 | 9.2 | ✓ |
| danger | `--color-danger` `#ff7b6e` | 6.9 | 7.4 | ✓ |
| neutral | `--color-muted` `#8a96a2` | 5.8 | 6.2 | ✓ AA (thinnest) |

**`solid` variant** — label = `--color-on-solid` `#ffffff` on the fill:

| Role | Fill token | white on fill | AA? |
|---|---|---|---|
| ok | `--color-success-solid` `#1e8449` | 4.7 | ✓ (tight — do not lighten the fill or drop below 12px) |
| danger | `--color-danger-solid` `#b4443c` | 5.5 | ✓ |
| warn | `--color-warning-solid` **TBD** | target ≥ 4.5 | TBD |

`neutral` has no solid variant (text only). `info` is TBD. All numbers are the foundation spec's §1.1 figures.

Known hazard: the current page colours `#b9770e` (amber) and `#1e8449` (green) on white do **not** reliably clear 4.5:1 as normal text — they must not be carried into the light theme.

Acceptance is automated: a unit test iterates the catalog roles × variants, resolves the CSS custom properties from the built `app.css`, and asserts the ratios above. Computing it by hand in the PR is not enough.

## 1.8 Usage examples (from the audited pages)

**`/drafts` list** — replaces `<span class:open class:committed>` in `routes/drafts/+page.svelte`:
```svelte
<script lang="ts">
  import StatusPill from '$lib/components/StatusPill.svelte';
  import { draftStatus } from '$lib/status';   // API string → StatusKey, with fallback
</script>
…
<span class="meta">{draft.kind} → {draft.target_name} ·</span>
<StatusPill status={draftStatus(draft.status)} />
{#if draft.result_deck_id}<span class="meta">· built <code>{draft.result_deck_id}</code></span>{/if}
```

**`/drafts/[id]` header** — same call; the `open` derived flag stays (it gates the forms), the pill replaces only the styled span.

**`/games/[id]` h1**:
```svelte
<h1>{title} <StatusPill status={gameResult(game.result)} size="md" /></h1>
```
Win and loss now differ by icon + label; `draw` and unknown are covered; the heading text is unmodified.

**`/games` list** — replaces buried `{#if game.result}{game.result}{:else}result unavailable{/if}`:
```svelte
<StatusPill status={gameResult(game.result)} />
```

**`/games/[id]` tables** — replaces each `<td class="unavailable">unavailable — {reason}</td>`:
```svelte
<td><StatusPill status="unavailable" title={card.unavailable_reason} describedby="why-unavail" /></td>
…
<p id="why-unavail" class="hint">Unavailable: {reasonOnce}</p>   <!-- reason stated once under the table -->
```
If reasons differ per row, keep the reason as visible text in the row and drop the pill's `title`. Do not hide unique information in `title`.

**`/decks` have/buy** — per the audit's "Buy 2":
```svelte
{#if short > 0}<StatusPill status="short" label={`Buy ${short}`} />{:else}<StatusPill status="owned" variant="text" />{/if}
```

**`/profile`** `.ok` "Saved — snapshot #n" is **not** a pill — it is a form-feedback message (§2.6).

Helpers (`$lib/status.ts`), pure and unit-testable:
```ts
import type { StatusKey } from './StatusPill.svelte';

export const draftStatus = (s: string): StatusKey =>
  ({ open: 'draft-open', committed: 'draft-committed', abandoned: 'draft-abandoned' } as const)[s] ?? fallback(s);
export const gameResult = (s: string | null): StatusKey =>
  s == null ? 'game-unknown'
  : ({ win: 'game-win', loss: 'game-loss', draw: 'game-draw' } as const)[s] ?? fallback(s);
```
`fallback(raw)` returns `'neutral'` and logs once in dev; the label for a fallback uses the raw string (unknown value → visible raw text, neutral). Coder picks the exact return shape; the behaviour is fixed.

Note on `.meta`: it is typography, not status. Replace `opacity: .75` with `color: var(--color-text-body)` (10.1:1 on `--color-bg`, foundation §1.1) in the migration card — out of scope here.

## 1.9 Acceptance criteria (StatusPill)

1. Every `StatusKey` renders visible text, plus an icon unless `icon={null}`.
2. For each sibling group (draft, game), icon silhouettes are unique. Win/loss and committed/abandoned use different shapes. Verified by a snapshot test over the catalog + a manual greyscale screenshot in the PR.
3. Contrast: an automated test asserts ≥ 4.5:1 label/bg and ≥ 3:1 icon for every role × variant in dark, and in light once defined. Fails the build if not.
4. No `opacity`, raw hex, or non-`--color-*` colour in `StatusPill.svelte` (lint/grep check).
5. Accessible name equals the visible label; the icon is `aria-hidden`; no extra tab stop. `axe-core` on a fixture page with all statuses reports 0 violations.
6. Unknown API strings render `neutral` with the raw text; `null` result renders `game-unknown`.
7. `forced-colors: active` and `prefers-reduced-motion` need no special handling (the pill has no animation). It stays legible and bordered in forced-colours.
8. At 200% zoom / 320px width the pill wraps and is not clipped.
9. After migration, `.open`, `.committed`, `.win`, `.loss`, `.you`, and the status use of `.unavailable` no longer exist in `drafts/**` and `games/**` route styles.
10. A `/dev` fixture page lists every status in a list, in a table cell, and in an `h1`, in both sizes and both variants.

---

# 2. Form pending pattern

## 2.1 Decision: a documented pattern + one helper + one thin component

**Recommendation: a `createPending()` helper (the behaviour) and a `SubmitButton` component (the presentation), shipped together. This section is the contract.**

Justification:
- The behaviour (submitting flag, double-submit lock, error handoff) is identical across ≥ 9 forms (drafts create/commit/abandon/remove/upsert/stage/unstage, games import, profile override/clear/save/seed/generate/confirm/dismiss). Copy-pasting 15 lines into each is how the current state drifted — so it must be **code**, not prose.
- A **component alone** cannot own it: `use:enhance` attaches to the `<form>`, not the button. A `<SubmitButton>` has no way to set `aria-busy` on the form or to block a second submit from Enter-in-input.
- A **helper alone** leaves the visual part (pending label, spinner, `aria-disabled` styling) copy-pasted into every button, and invites drift in label text.
- So: the helper owns *state and lifecycle*; `SubmitButton` owns *what a pending button looks like and announces*. Each is ~40 lines. No `<Form>` wrapper component — forms have wildly different layouts (inline row, grid, table cell) and a wrapper would force layout decisions (a non-goal).

Rejected: a `<Form>` wrapper (layout coupling) and a pure docs-only pattern (drift).

## 2.2 The helper — `createPending()`

File: `src/web/src/lib/forms/pending.svelte.ts`

```ts
import type { SubmitFunction } from '@sveltejs/kit';

export function createPending(opts: { errorKey?: string; confirm?: string | ((fd: FormData) => string) } = {}) {
  let submitting = $state(false);
  let action = $state<string | null>(null);      // which action is in flight (multi-button forms)

  const enhance: SubmitFunction = ({ action: url, formData, cancel }) => {
    if (opts.confirm) {
      const msg = typeof opts.confirm === 'string' ? opts.confirm : opts.confirm(formData);
      if (!window.confirm(msg)) { cancel(); return; }
    }
    if (submitting) { cancel(); return; }         // double-submit guard (state, not just DOM)
    submitting = true;
    action = url.search.slice(1) || 'default';    // '?/commit' → '/commit'
    return async ({ update, result }) => {
      try { await update({ reset: result.type === 'success' }); }   // keep input on failure
      finally { submitting = false; action = null; }
    };
  };

  return { get submitting() { return submitting; }, get action() { return action; }, enhance };
}
```

Behaviour contract:
- **Single flight per form instance.** A second submit while `submitting` is cancelled. Covers double-click, Enter-key repeat, and a click on a sibling button in the same form.
- `finally` always clears the flag — on `failure`, `error`, and thrown network errors. Never leaves the form stuck.
- `reset` only on `success`, so failed input is **kept** (the user must not retype a Forge log).
- Does not swallow results: `update()` runs SvelteKit's normal path, so the `form` prop (`ActionData`) updates and `invalidateAll` happens.
- Works without JS: every form keeps `method="POST"` and `action="?/x"`. The helper is progressive enhancement only; a no-JS submit still posts and reloads.

## 2.3 The component — `SubmitButton`

File: `src/web/src/lib/components/SubmitButton.svelte`

| Prop | Type | Default | Notes |
|---|---|---|---|
| `pending` | `boolean` | `false` | Pass `p.submitting`. Locks the button (double-submit) |
| `busy` | `boolean` | `= pending` | Shows spinner + `pendingLabel`. In multi-button forms pass `busy={p.action === '/commit'}` so only the pressed button shows its pending label |
| `children` | `Snippet` | required | Idle label ("Open draft") |
| `pendingLabel` | `string` | required | Pending label ("Opening draft…"). **Required** so no button silently keeps its idle label |
| `variant` | `'primary' \| 'danger' \| 'success' \| 'ghost' \| 'link'` | `'primary'` | Replaces `.commit` / `.abandon` / `.link` / `.clear`. Token map in 2.5 |
| `disabled` | `boolean` | `false` | Hard-disabled for non-pending reasons (invalid, nothing to submit) |
| `formaction` | `string` | – | Secondary submit (profile "Clear" → `?/clear`) |
| `type` | `'submit' \| 'button'` | `'submit'` | |
| `class` | `string` | – | Layout only |
| `…rest` | `HTMLButtonAttributes` | – | Passthrough |

### Disabled-button behaviour (important)
Use **`aria-disabled="true"` + a click/submit guard while pending, not the `disabled` attribute**, for the *pending* state:
- A `disabled` attribute drops the button from the tab order and, in some browsers/AT, loses focus — keyboard users get thrown to `<body>` mid-submit, and screen readers may not announce the pending text.
- `aria-disabled` keeps focus and the label readable. `pointer-events` isn't blocked by CSS alone, so the handler must `preventDefault()` when pending (the helper's `cancel()` is the real guard; the component's click guard stops the *native* submit for the no-JS-race window).
- The `disabled` attribute is reserved for the *non-pending* `disabled` prop (required field empty). Acceptable there because focus isn't being held.
- **Siblings** (Commit / Abandon in one `.danger-zone`): all submit buttons take `pending={p.submitting}` for the lock; only the pressed one shows its `pendingLabel` via `busy={p.action === '/commit'}`.

### Markup

```svelte
<button
  type={type}
  class="btn btn--{variant}"
  class:is-pending={pending}
  aria-disabled={pending || undefined}
  disabled={disabled}
  onclick={(e) => { if (pending) e.preventDefault(); }}
  {formaction}
>
  {#if busy ?? pending}
    <svg class="btn__spinner" aria-hidden="true" viewBox="0 0 16 16">…</svg>
    <span>{pendingLabel}</span>
  {:else}
    {@render children()}
  {/if}
  <span class="visually-hidden" role="status">{busy ?? pending ? pendingLabel : ''}</span>
</button>
```

- Button width is held (set `inline-size` from the idle label via `ch`/grid, or stack both labels in an `inline-grid` and `visibility:hidden` the inactive one) so the layout **doesn't jump** when the label changes.
- Spinner is `transform: rotate` only (compositor-friendly), 600ms linear. Under `prefers-reduced-motion: reduce` it is replaced by a static `…`; the text change alone still conveys state.
- Pending style: keep `--btn-*-bg`, swap to a **dashed/hatched border** (not `opacity`), `cursor: progress`. Contrast stays ≥ 4.5:1 — dimming via `opacity` is exactly the audit's AA failure.
- Focus: `:focus-visible { outline: 2px solid var(--color-focus); outline-offset: 2px; }` (foundation `--color-focus` `#ffc266`, 11.8:1). **Kept while pending.**

## 2.4 Form-level a11y contract

```svelte
<script lang="ts">
  import { enhance } from '$app/forms';
  import { createPending } from '$lib/forms/pending.svelte';
  import SubmitButton from '$lib/components/SubmitButton.svelte';
  import ErrorBanner from '$lib/components/ErrorBanner.svelte';   // t_d065be72
  let { form } = $props();
  const p = createPending();
</script>

<form method="POST" use:enhance={p.enhance} aria-busy={p.submitting}>
  <fieldset disabled={p.submitting}> …fields… </fieldset>
  <SubmitButton pending={p.submitting} pendingLabel="Opening draft…">Open draft</SubmitButton>
  {#if form?.createError}<ErrorBanner message={form.createError} onRetry={() => formEl.requestSubmit()} />{/if}
</form>
```

- `aria-busy={p.submitting}` on the `<form>` during flight (Svelte omits the attribute on `false`). It tells AT the region is updating but is **not announced on its own**, so the pending label + live region do the announcing.
- Wrap fields in `<fieldset disabled={p.submitting}>` for forms where editing mid-flight would be wrong (create/import). A disabled fieldset removes inputs from the tab order, which is fine for fields (focus stays on the `aria-disabled` button, **outside** the fieldset). Do **not** disable a fieldset when the button sits inside it. Inline single-row forms (remove/unstage) have no fieldset; the lock is the button only.
- Pending is announced once through a **polite live region** — the `<span class="visually-hidden" role="status">` rendered by `SubmitButton` (always in the DOM, text swaps). Don't also put `role="status"` on the form.
- On completion the result is announced by the feedback region (2.6): success in `role="status"`, failure via `ErrorBanner` (`role="alert"`).

## 2.5 Variant → token map (SubmitButton)

Consumes foundation `--color-*` tokens directly (no `--btn-*` layer).

| Variant | Replaces | background | text | border | AA (text on bg) |
|---|---|---|---|---|---|
| primary | default `button` | `--color-accent` `#f0a63a` | `--color-on-accent` `#17110a` | `--color-accent` | 9.1 ✓ (foundation §1.1) |
| success | `.commit` | `--color-success-solid` `#1e8449` | `--color-on-solid` `#ffffff` | transparent | 4.7 ✓ |
| danger | `.abandon` | `--color-danger-solid` `#b4443c` | `--color-on-solid` `#ffffff` | transparent | 5.5 ✓ |
| ghost | `.clear` | transparent | `--color-text-body` `#b6bfc8` | `--color-border-control` `#707d8a` | 9.4 on `--color-bg` ✓ |
| link | `button.link` | none | `--color-danger` `#ff7b6e` | (underlined) | 7.4 on `--color-bg` ✓ |

Existing `#c0392b`/`#1e8449` fills with white text: **do not carry over** — recompute with the final token pair. White on the foundation `--color-success-solid` `#1e8449` is 4.7:1 (passes, but tight; do not lighten). `--color-on-accent` `#17110a` on `--color-accent` `#f0a63a` is 9.1:1.

Other tokens: `--color-focus` `#ffc266` (focus ring), `--space-*`, `--radius-sm`, `--text-sm`, `--font-sans` — all foundation §1.

## 2.6 Error / success handoff to `ErrorBanner`

Rule: **the action returns `fail(status, { <field>Error: string })`; the page renders it.** The helper never builds UI.

- **Failure** (`result.type === 'failure'`) → `form?.<x>Error` is set by `update()` → render `<ErrorBanner message={…} onRetry={…} />` (`role="alert"`, per t_d065be72) **next to the submit button**, inside the form, below it — not at the top of a long page where the user's eyes aren't.
- After failure: **don't steal focus** — `role="alert"` already announces, and yanking focus from the button the user just pressed breaks keyboard flow. Exception: if the banner is off-screen (rare, long forms), give it `tabindex="-1"` and `scrollIntoView({ block: 'nearest' })`. That is `ErrorBanner`'s concern, not the helper's.
- **Thrown / `result.type === 'error'`** (network, 500) → `update()` would navigate to the nearest `+error.svelte`, which is wrong for a form. The helper's wrapper instead maps it to a failure: `createPending({ errorKey: 'importError' })`; on `error`, the page treats it like a failure with a "Network or server error — your input was kept. Try again." banner + the `ErrorBanner` `onRetry` (re-submit via `formEl.requestSubmit()`). Default key `actionError`.
- **Success** → `reset: true` on create-style forms; render a one-line polite `role="status"` line (e.g. profile "Saved — snapshot #n"). An `ErrorBanner` `severity="success"` variant is **rejected** — success feedback is a one-line status, not a banner, and the sibling spec keeps `ErrorBanner` to warn/error.
- **Multiple forms on one page** (drafts/[id] has 7): each form has its own `createPending()` and its own banner. Inline per-row actions (remove/unstage) share one small banner under the table; page-level actions (commit/abandon) share one banner in the `.danger-zone`. If the server action doesn't return the action name, the coder adds `action: 'commit'` to the `fail()` payload (trivial backend change — flagged to coder, out of designer scope).

## 2.7 `confirm()` (Commit / Abandon)

Out of scope to redesign (the audit flags `confirm()` adjacency separately), but the pending pattern must coexist:
- The confirm check moves **inside** the `SubmitFunction`, **before** `submitting = true` (otherwise cancelling the dialog would lock the form). Helper option: `createPending({ confirm: 'Commit this draft? …' })` (string or `(formData) => string`).
- Commit and Abandon share one `createPending()` (one `.danger-zone`); pressing either locks both; only the pressed one shows its `pendingLabel` (`busy={p.action === '/commit'}`).
- Interim, until a proper confirm dialog exists: `window.confirm` stays; nothing here blocks swapping it later.

## 2.8 Pending label text

Rule: present participle of the idle verb + `…` (single ellipsis char, not three dots), same noun, ≤ 24 chars, sentence case.

| Form | Idle | Pending |
|---|---|---|
| drafts: start | Open draft | Opening draft… |
| drafts/[id]: commit | Commit draft | Committing… |
| drafts/[id]: abandon | Abandon | Abandoning… |
| drafts/[id]: upsert_card | Add / update card | Saving card… |
| drafts/[id]: remove_card | remove | Removing… |
| drafts/[id]: stage_move | Stage move | Staging… |
| drafts/[id]: unstage_move | unstage | Unstaging… |
| games: import | Import & analyze | Importing… (may take a while; show "Parsing log, this can take a few seconds" as a `hint`. **TBD** if the backend emits progress — not assumed) |
| profile: override | Save | Saving… |
| profile: clear | Clear | Clearing… |
| profile: savePlayer | Save as stated | Saving… |
| profile: seedPlayer | Seed from profiles/andrew.json | Seeding… |
| profile: generateProposals | Look for patterns in my feedback | Looking… |
| profile: confirm / dismiss | Confirm / Dismiss | Confirming… / Dismissing… |

## 2.9 Usage example — `/games` import (longest-running form in the audit)

```svelte
<script lang="ts">
  const p = createPending({ errorKey: 'importError' });
  let formEl: HTMLFormElement;
</script>

<form bind:this={formEl} method="POST" use:enhance={p.enhance} aria-busy={p.submitting}>
  <fieldset disabled={p.submitting}>
    <!-- deck, seat, played_at, file, textarea#log_text -->
  </fieldset>
  <SubmitButton pending={p.submitting} pendingLabel="Importing…">Import &amp; analyze</SubmitButton>
  {#if form?.importError}
    <ErrorBanner message="Import failed" detail={form.importError} onRetry={() => formEl.requestSubmit()} />
  {/if}
</form>
```
Result: the textarea keeps its pasted log on failure (no `reset`), the button can't be double-fired, and the failure is announced with a retry.

## 2.10 Acceptance criteria (form pending)

1. While a submit is in flight: the `<form>` has `aria-busy="true"`, the submit button has `aria-disabled="true"`, shows the spinner + `pendingLabel`, **keeps focus**, and a second activation (click / Enter / Space / Enter-in-input) does not send a second request. Test: Playwright with a delayed route, click twice, assert exactly 1 request.
2. After success / failure / network error the flag clears; the button returns to idle; `aria-busy` is removed. Test all three, including a 500 and an aborted fetch.
3. Failure keeps entered values and renders `ErrorBanner` (`role="alert"`) adjacent to the button. A network error renders the same banner, not the global `+error` page.
4. `confirm()` cancel does not lock the form.
5. Multi-button forms: pressing Commit locks Abandon; only Commit shows its pending label.
6. Button width doesn't change between idle and pending (no layout shift: assert bounding box within 1px).
7. Pending state contrasts ≥ 4.5:1 and does not use `opacity` to dim. `prefers-reduced-motion: reduce` shows no rotation; pending text is still present.
8. Screen reader: pending text announced once via the polite live region; the result announced via `role="status"` / `role="alert"`. Manual check with NVDA or Orca + `axe-core` 0 violations.
9. No-JS: with JavaScript disabled, every migrated form still submits via native POST and gets the `form` result on reload.
10. Every `<form method="POST">` in `drafts/**`, `games/**`, `profile/**` uses `createPending` + `SubmitButton`; no raw `<button type="submit">` remains (grep check).
11. Unit tests: `createPending` flag lifecycle (success, failure, error, thrown, confirm-cancel, double-submit).

---

# 3. Dependencies and open items

| Item | Status |
|---|---|
| Card 1/4 tokens (`--color-*`, `--space-*`, `--radius-sm`, `--color-focus`, type/font) — `ui-foundation-spec.md` | **Landed.** Consumed directly; contrast figures quoted from its §1.1. |
| `--color-warning-solid` | **TBD.** Foundation ships warning as text only. Coder adds the fill token to `app.css` (card 1/4 follow-up); until then `warn` renders `text`. |
| `info` / `data` role | **TBD.** No foundation token; `--color-accent` interim. No audited state maps to it. |
| `.visually-hidden` utility class | **TBD name.** Defined by the a11y spec (`ui-audit-3`); add to `app.css`. |
| Light-theme token values | **TBD.** Audited pages are unthemed light today; foundation spec is dark-only. Recompute all contrast before a light theme ships. |
| `ErrorBanner` (t_d065be72) | Used for failure handoff. Needs `onRetry` + `role="alert"` per its spec; if its prop names differ, adapt §2.4/§2.6 only. |
| Server `fail()` payloads — consistent key + optional `action` name | Small backend change, flagged to coder. Not designer scope. |
| Icon set | No shared icon set exists in `src/web` today. Coder adds ~10 inline SVGs in one `Icon.svelte` / `icons.ts`. |
| `.meta` opacity → `--color-text-body` | Migration card (a11y spec). |
| Games import progress | Not assumed. Spec uses an indeterminate pending state only. |

# 4. Decisions made without check-in (reversible)

1. **Consume foundation `--color-*` tokens directly** — no `--status-*` / `--btn-*` alias layer. The foundation spec already is the app's semantic token layer; a second layer only re-introduces the reconciliation card 2/4 was asked to close. (Supersedes the earlier draft's alias-layer decision.)
2. Two pill variants — `solid` (saturated fill + white label) and `text` (coloured label + hairline border) — matching foundation D3 ("text token + solid-fill token, white label"). Variant is a **density** choice, not a new meaning; the `label` always carries the specific state.
3. Status labels are Title case in the DOM, uppercased via CSS, so the DOM stays readable for SR and copy/paste.
4. `aria-disabled` + click guard (not `disabled`) for pending, to preserve keyboard focus; `disabled` reserved for the non-pending `disabled` prop.
5. `createPending` is a Svelte 5 `.svelte.ts` rune module (the project is on runes: `$props`, `$derived`, `$state`).
6. Success feedback is a plain `role="status"` line, not an `ErrorBanner` variant.
7. Closed `StatusKey` enum with a safe fallback, rather than a free-form `color` prop, so colour cannot be the only signal.