# EmptyState + ErrorBanner — component spec

Card: t_d065be72 · Source: `ui-ux-audit-2026-10-29.md` (theme 4 "Weak states", theme 2 a11y) · Stack: SvelteKit, Svelte 5 runes (`$props`, snippets) · Location: `src/web/src/lib/components/`

**Non-goals:** page layouts, StatusPill, ScrollTable, form-pending (sibling specs).

## 0. Assumptions (no check-in, per secretary override)

- **Tokens are not final.** Card 1/4 (`app.css` tokens, t_5c3b8269) has not merged. Names below are *proposed* and marked `TBD`; the coder maps them to the final names on merge. Raw hex in these components is forbidden. A token with no final name ships as a CSS custom-property fallback in the component (`var(--color-danger, #c0392b)`) only until card 1/4 lands.
- Svelte 5 snippets are used for slots (`children`, `icon`, `actions`).
- Dark is the default surface; both components must pass AA on light too (token-driven, no per-theme branches in the component).
- Severity and state are never colour-only: icon + text label always accompany colour.

## 1. Token consumption (shared)

| Role | Token (proposed) | Status | Used by |
|---|---|---|---|
| Surface, raised | `--color-surface-2` | TBD | EmptyState page variant bg |
| Ink / text | `--color-text` | TBD | both |
| Muted text | `--color-text-muted` (must be ≥4.5:1; replaces `opacity` dimming) | TBD | EmptyState description |
| Border | `--color-border` | TBD | EmptyState inline dashed border |
| Accent | `--color-accent` | TBD | CTA button, focus ring |
| Danger | `--color-danger` (current hex `#c0392b`) | TBD | ErrorBanner error |
| Warning | `--color-warning` (current `#b9770e`) | TBD | ErrorBanner warning |
| Info | `--color-info` | TBD | ErrorBanner info |
| Danger/warn/info tint bg | `--color-danger-bg`, `--color-warning-bg`, `--color-info-bg` | TBD | ErrorBanner bg |
| Focus ring | `--focus-ring` (`2px solid var(--color-accent)`, offset 2px) | TBD, audit asks for `:focus-visible` | both |
| Space | `--space-2/3/4/6/8` | TBD | both |
| Radius | `--radius-sm` | TBD | both |
| Type | `--text-sm`, `--text-base`, `--text-lg`, `--text-xl`; weights | TBD | both |
| Motion | `--dur-fast` (150ms), `--dur-base` (250ms), `--ease-out` | TBD | ErrorBanner enter/exit |

---

## 2. EmptyState

### 2.1 Purpose
Replace bare-text empties (`<p>No imported decks yet.</p>`, `<p class="empty">no tags yet</p>`) with a consistent block that says *what is missing*, *why/what next*, and offers one action. Distinct from **error** (use ErrorBanner) and **loading** (not this component).

### 2.2 Props

| Prop | Type | Default | Notes |
|---|---|---|---|
| `title` | `string` | required | Short, sentence case, no trailing period. "No decks yet". |
| `description` | `string` | – | One sentence: why empty + what to do. Also accepts `children` snippet for rich content (links). |
| `variant` | `'page' \| 'inline'` | `'page'` | See 2.4. |
| `headingLevel` | `2 \| 3 \| 4` | `2` (page) / `3` (inline) | Caller sets so the outline stays valid. Never renders `h1`. Ignored by `inline` when `compact`. |
| `compact` | `boolean` | `false` | Inline only: title as `<p class="title">`, no heading element (for card-panel empties where a heading would clutter the outline). |
| `icon` | `Snippet` | – | Decorative; wrapper is `aria-hidden="true"`. Omit → no icon. Size via `--icon-size`. |
| `ctaLabel` | `string` | – | Presence of `ctaLabel` + (`ctaHref` or `onCta`) renders the CTA. Label must name the action ("Import a deck"), not "Click here". |
| `ctaHref` | `string` | – | Renders `<a class="btn">`. Wins if both set. Navigation. |
| `onCta` | `() => void` | – | Renders `<button type="button" class="btn">`. For in-page actions. |
| `ctaVariant` | `'primary' \| 'secondary'` | `'primary'` for page, `'secondary'` for inline | |
| `filtered` | `boolean` | `false` | "No results for filter" empty: swaps default tone, expects CTA like "Clear search". No behavioural difference otherwise. |
| `class` | `string` | – | Layout escape hatch (margins only). |

Slots (snippets): `icon`, `children` (replaces `description`), `actions` (replaces built-in CTA; for 2+ actions, max 2).
Dev-time guard: warn in dev if `ctaLabel` without `ctaHref`/`onCta`, or neither title.

### 2.3 Markup

```svelte
<!-- variant="page" -->
<section class="empty empty--page" aria-labelledby={id}>
  {#if icon}<span class="empty__icon" aria-hidden="true">{@render icon()}</span>{/if}
  <svelte:element this={`h${headingLevel}`} id={id} class="empty__title">{title}</svelte:element>
  {#if description}<p class="empty__desc">{description}</p>{:else if children}<div class="empty__desc">{@render children()}</div>{/if}
  {#if actions}<div class="empty__actions">{@render actions()}</div>
  {:else if ctaLabel}
    {#if ctaHref}<a class="btn btn--{ctaVariant}" href={ctaHref}>{ctaLabel}</a>
    {:else if onCta}<button type="button" class="btn btn--{ctaVariant}" onclick={onCta}>{ctaLabel}</button>{/if}
  {/if}
</section>

<!-- variant="inline": same, root is <div class="empty empty--inline">, with role omitted -->
```

Table use: render inside `<tr><td colspan={n}>` (single cell) so the table keeps valid structure; the table keeps its `<caption>` and header row visible.

### 2.4 Variants

| | `page` | `inline` |
|---|---|---|
| Where | Whole list/page has no data (decks, drafts, games lists) | Panel, table body, card section (inventory table, card dossier panels) |
| Root | `<section aria-labelledby>` | `<div>` (no landmark) |
| Layout | centred, max-width ~`40ch`, vertical padding `--space-8`, surface `--color-surface-2` | left-aligned, `--space-4` padding, 1px dashed `--color-border` |
| Title | `--text-xl`, 600 | `--text-base`, 600 |
| Icon | 48px | 24px, optional |
| CTA | primary button | secondary/link style |

### 2.5 Accessibility

- Title is a real heading (not `<strong>`/`<p>`) except `inline compact`. Caller chooses `headingLevel` so the outline has no skips. Default `h2` assumes the page `<h1>` exists.
- **No focus movement on mount.** Empty states are static content, not alerts. Exception: after a user action empties the list (e.g. last item removed) the caller moves focus to the page `h1` or the empty title (`tabindex="-1"` supported via `bind:titleEl`); document in the page, not the component.
- Not `role=status`/`alert` on first render. If the empty state appears as the *result of a filter/search the user just ran*, caller wraps the results region in `aria-live="polite"` (or sets `filtered` which adds `role="status"` to the root).
- CTA is a native `<a>`/`<button>`, ≥44×44 hit area, visible `:focus-visible` using `--focus-ring`.
- Icon is decorative; no meaning conveyed by icon alone.
- Muted text uses `--color-text-muted`, never `opacity`; ≥4.5:1.
- Reduced motion: no animation is required; optional fade-in only under `@media (prefers-reduced-motion: no-preference)`.

### 2.6 Usage (from the audit's pages)

| Page | Today | After |
|---|---|---|
| decks `+page.svelte:16` | `<p>No imported decks yet.</p>` | `<EmptyState title="No decks yet" description="Import a deck from Archidekt or Moxfield to start analysing it." ctaLabel="Import a deck" ctaHref="/decks/import" />` *(href TBD with the import route)* |
| drafts `:22,:64` | `<p class="meta">No imported decks yet — <a>…` / `<p>No drafts yet.</p>` | `<EmptyState variant="inline" title="No decks to draft from" ctaLabel="Import a deck" ctaHref="/decks" />`; list: `title="No drafts yet" description="Pick a deck above and start one."` (no CTA; the form is on the page) |
| games `:32,:68` | same pair | `title="No games imported yet" description="Import a game log using the form above."`; the form-gate empty: same as drafts' |
| inventory `:33` | `No cards{q?…} in the collection.` | `<EmptyState variant="inline" filtered={!!q} title={q ? \`No cards match “${q}”\` : "Your collection is empty"} ctaLabel={q ? "Clear search" : "Add cards"} ctaHref={q ? "/inventory" : …} />` rendered in the table `<td colspan>` |
| card `:30,:42,:53,:80,:93` | `<p class="empty">no tags yet</p>` ×5 | `<EmptyState variant="inline" compact title="No tags yet" />` and likewise "No printings data yet", "Not in any deck", "No oracle text on record", "No card art yet". Compact → no extra headings in the dossier. |

### 2.7 Acceptance criteria

1. Props/slots as 2.2; TypeScript types exported; `variant`, `headingLevel`, CTA branching covered by component tests (CTA as link, as button, none).
2. Renders correct heading level; `compact` renders no heading; `aria-labelledby` points at the title.
3. No hex/px-magic values: all colour/space/type via tokens (TBD fallbacks removed once card 1/4 merges).
4. axe: 0 violations in dark + light, both variants; muted text ≥4.5:1.
5. CTA: keyboard-activatable, visible focus ring, ≥44px target; link variant has `href`, button variant has `type="button"`.
6. Icon wrapper `aria-hidden`; no information lost with CSS off.
7. Works inside `<td colspan>`; table remains valid HTML (checked in test).
8. All five empty sites in the usage table replaced; no `<p class="empty">` remains.
9. Visible at 375px with no horizontal overflow; CTA wraps below text.

---

## 3. ErrorBanner

### 3.1 Purpose
Replace the `.error` paragraph class duplicated in decks, drafts, games, inventory (and review's failed state). Surfaces load failures and form errors with a consistent look, correct announcement, and a **retry**, which the audit notes is missing. One component for both *page data errors* (`data.error`) and *form action errors* (`form?.createError`, `form?.importError`).

### 3.2 Props

| Prop | Type | Default | Notes |
|---|---|---|---|
| `message` | `string` | required | One-line, human, says what failed: "Deck data unavailable". No raw stack/JSON. |
| `detail` | `string` | – | Technical/secondary text (server message, status). Shown in a `<details>` disclosure labelled "Details" when long (>~120 chars) or `detailCollapsed`; otherwise inline muted. Rendered as text, never HTML. |
| `severity` | `'error' \| 'warning' \| 'info'` | `'error'` | Drives colour, icon, **and a visually-visible text prefix** ("Error", "Warning", "Notice") — never colour alone. |
| `onRetry` | `() => void \| Promise<void>` | – | Presence renders the retry button. |
| `retryLabel` | `string` | `'Try again'` | Name the action when helpful: "Reload decks". |
| `retrying` | `boolean` | `false` | Controlled: button gets `aria-disabled="true"`, label "Retrying…" (visually), spinner (reduced-motion: static dots). Parent owns state; if `onRetry` returns a promise the component sets it itself. |
| `dismissible` | `boolean` | `false` | Renders ✕ button. Default **off** for page-data errors, **on** for transient form errors. |
| `onDismiss` | `() => void` | – | Called after dismiss; component also hides itself unless `open` is controlled. |
| `open` | `boolean` (bindable) | `true` | Controlled visibility. |
| `live` | `'assertive' \| 'polite' \| 'off'` | derived | See 3.5. Override only with reason. |
| `class` | `string` | – | Margins only. |

Slots: `actions` (extra buttons after retry, e.g. link to docs), `children` (replaces `detail` for rich content).

### 3.3 Markup

```svelte
<div class="banner banner--{severity}" role={role} aria-live={ariaLive} aria-atomic="true">
  <span class="banner__icon" aria-hidden="true"><!-- ! / ⚠ / i svg --></span>
  <div class="banner__body">
    <p class="banner__msg"><strong class="banner__sev">{sevLabel}:</strong> {message}</p>
    {#if detail}<details class="banner__detail"><summary>Details</summary><p>{detail}</p></details>{/if}
  </div>
  <div class="banner__actions">
    {#if onRetry}<button type="button" class="btn btn--secondary" aria-disabled={retrying} onclick={handleRetry}>{retrying ? 'Retrying…' : retryLabel}</button>{/if}
    {@render actions?.()}
    {#if dismissible}<button type="button" class="banner__close" aria-label="Dismiss {sevLabel.toLowerCase()} message" onclick={dismiss}><svg aria-hidden="true">…</svg></button>{/if}
  </div>
</div>
```

### 3.4 Visual
Flat tinted block (`--color-{sev}-bg`) with a 4px left rule in `--color-{sev}`; text in `--color-text` (not the saturated colour, to hold contrast). Icon + severity word carry meaning. Enter: 150–250ms slide/fade (`transform`, `opacity`) only under `prefers-reduced-motion: no-preference`; otherwise instant.

### 3.5 Accessibility (required)

| Case | Semantics |
|---|---|
| `severity="error"` (default) | `role="alert"` (implicit `aria-live=assertive`, `aria-atomic`). |
| `warning` / `info` | `role="status"` + `aria-live="polite"`. |
| **Present at SSR / page load** (`data.error`) | Live regions announced on server-rendered content are unreliable. Use `role="alert"` only for errors; the page must also have the banner **first in `<main>`** so it is reached in reading order. Component prop `announceOnMount` is not needed; do not set `autofocus`. |
| **Appears after a user action** (form submit fails with `use:enhance`) | Render the banner *always-present container* pattern: parent keeps a persistent `<div aria-live>` wrapper or mounts the banner after the action; announced by `role=alert` on insertion. The banner element is the live region, so mount it, don't toggle `hidden`. |
| Retry | Native `<button type="button">`; accessible name = visible label; while `retrying` use `aria-disabled` (stays focusable, avoids focus loss), ignore clicks, announce "Retrying…" via the button text. On failure again, banner message re-renders (re-announced because it is `role=alert` content change). |
| Focus | Not stolen on mount. On **dismiss**, focus moves to the previous focus target if known, else to the nearest following heading / `main` (`tabindex="-1"`); never to `body`. After successful **retry**, parent moves focus to the refreshed content heading. |
| Dismiss | Button name "Dismiss error message"; `Esc` dismisses when focus is inside the banner. |
| Contrast | Text ≥4.5:1 on tinted bg; 3:1 for the left rule/icon. Verified in light + dark. |
| Detail | `<details>` is native, keyboard operable; detail never required to understand the message. |
| Reduced motion | No spinner animation or slide; static "Retrying…". |

### 3.6 Dismiss behaviour

- Default `dismissible=false` for *load/data* errors (the condition persists until retry/navigation). Dismiss hides the banner only; it does not clear the underlying error state, so a later failure re-shows it (parent re-mounts or sets `open`).
- Form errors (`dismissible`): dismissed automatically cleared when the user edits the form or resubmits (parent resets `form`); no timers/auto-dismiss on errors (WCAG 2.2.1). `info` banners may auto-dismiss only if parent opts in with ≥8s and a pause on hover/focus; **not provided by default**.
- Animated exit ≤150ms, then removed from the DOM.

### 3.7 Usage (from the audit's pages)

| Page | Today | After |
|---|---|---|
| decks `:14` | `<p class="error">Deck data unavailable: {data.error}</p>` | `<ErrorBanner message="Deck data unavailable" detail={data.error} onRetry={() => invalidateAll()} retryLabel="Reload decks" />` |
| drafts `:62` | `Draft data unavailable: …` | same, `retryLabel="Reload drafts"` |
| drafts `:54` | `{#if form?.createError}<p class="error">…` | `{#if form?.createError}<ErrorBanner message="Couldn't create draft" detail={form.createError} dismissible />{/if}` (retry = resubmit the form; no `onRetry`) |
| games `:58,:66` | `Import failed: …` / `Game data unavailable: …` | `message="Game import failed" detail={form.importError} dismissible` / `message="Game data unavailable" … onRetry` |
| inventory `:16` | `Inventory unavailable: …` | `message="Inventory unavailable" detail={data.error} onRetry={() => invalidateAll()}` |
| card (`/card/[oracle_id]`) | no error UI today | per-panel failure inside a dossier panel: `<ErrorBanner severity="warning" message="Couldn't load printings" onRetry={reloadPrintings} />`; whole-card failure: error severity, page-level |
| review (adjacent) | failed review has no Retry | `message="Review failed" detail=… onRetry={rerun} retryLabel="Re-run review"` |

### 3.8 Acceptance criteria

1. Props as 3.2, typed and exported; severity maps icon/colour/prefix word; test per severity.
2. `error` renders `role="alert"`; `warning`/`info` render `role="status"`; asserted in tests.
3. Retry button only when `onRetry`; it is a native `<button type="button">`; `retrying` sets `aria-disabled` and doesn't double-fire; async `onRetry` handled and errors don't throw unhandled.
4. Dismiss: only when `dismissible`; has accessible name; `Esc` works; focus restoration per 3.5 (tested); `open` bindable.
5. `detail` rendered as text (XSS-safe, tested with `<script>` string); long detail collapses into `<details>`.
6. No colour-only meaning: severity word + icon present; axe 0 violations dark+light; contrast ≥4.5:1 text.
7. All hex values removed from `.error` styles; the four duplicated `.error` CSS blocks (decks, drafts, games, inventory) deleted in the migration.
8. Animation uses transform/opacity only and is disabled under `prefers-reduced-motion: reduce`.
9. Works at 375px: actions wrap below message; no horizontal overflow.
10. Test pages: decks, drafts, games, inventory use the component for both `data.error` and form errors.

---

## 4. Cross-component notes (for the coder-cards task)

- **Shared button style:** both use `.btn` (primary/secondary). Needs to exist in `app.css` (card 1/4); otherwise define a minimal `Button` in the same PR — flag, don't duplicate.
- **ScrollTable** should render `EmptyState variant="inline"` in `<td colspan>` for no rows and `ErrorBanner` above the wrapper (not inside) for fetch errors.
- **Forms (pending pattern):** form-level error = `ErrorBanner dismissible`, field-level errors are *not* ErrorBanner (use `aria-describedby` on the input).
- **Decision to confirm, low risk:** import route for decks CTA (`/decks/import`?) is a placeholder; the coder links to the actual import entry.
- Delete `.error`, `.empty`, and the empty-state use of `.meta` per page after migration.
