# Design tokens (HUD)

Source of truth: `static/css/hud.css`, served at `/static/css/hud.css` and
linked from `templates/base.html`, so every page gets it. Page templates
still have their own `<style>` blocks (passed through `{% block extra_css %}`),
and those load after `hud.css`.

Raw colour, size and spacing values belong in the `:root` token block and
nowhere else. Components and pages use `var(--token)`.

## Rules

- The base is dark slate (`--surface-*`), never `#000`.
- `--signal` (amber) is the only warm accent. Use it for status, alerts,
  active nav, focus and selection.
- `--data` (cyan) is the only cool colour. Use it for charts, bars and
  heatmaps.
- Mana colours are semantic. `--mana-r` means red mana and nothing else, so
  never use one for decoration.
- Numbers, labels and stats are set in `--font-mono`. Prose is set in
  `--font-sans`. `body` turns on `tabular-nums`. `--font-card` (EB Garamond)
  is only for card names.
- Corners are 0 to 3px and borders are 1px hairlines. No glow, no blurred
  `box-shadow`, no `backdrop-filter`, no gradients. Focus is a solid 1px
  `--signal` outline.

## Colour

| Token | Value | Use |
|---|---|---|
| `--surface-0` | `#0e1216` | page background |
| `--surface-1` | `#141a1f` | sidebar, panels, cards, inputs |
| `--surface-2` | `#1a2128` | hover rows, panel headers, active nav |
| `--surface-3` | `#222b33` | pressed / selected / tooltip |
| `--line-faint` | `#1c232a` | row separators in dense tables |
| `--line` | `#29323b` | default 1px border |
| `--line-strong` | `#3a4651` | hover / emphasized border |
| `--text-1` | `#e8ebee` | headings, primary values |
| `--text-2` | `#b6bfc8` | body copy |
| `--text-3` | `#8a96a2` | muted, micro-labels, placeholders (AA: 5.8:1 on surface-1; was `#76828e`) |
| `--text-4` | `#4f5a64` | disabled, axis ticks |
| `--signal` / `-strong` / `-dim` / `-line` | `#f0a63a` / `#ffc266` / `#3b2a12` / `#7a5420` | warm accent: text, hover, chip fill, chip border |
| `--text-on-signal` | `#17110a` | text on a solid `--signal` fill |
| `--data` / `-strong` / `-dim` | `#4fb0c6` / `#7fcde0` / `#15333b` | cool chart colour |
| `--data-0` … `--data-5` | surface-2 → `#4fb0c6` | sequential heatmap ramp, solid (no alpha) |
| `--ok` / `--ok-dim` | `#6cbf7a` / `#17301d` | success state |
| `--warn` / `--warn-dim` | = signal | warning (same as the accent on purpose) |
| `--danger` / `--danger-dim` | `#ee6a61` / `#3a1714` | error / destructive (AA on danger-dim; was `#e5584f`) |
| `--neutral` / `--neutral-dim` | text-3 / surface-3 | inactive status |

### Mana (fixed palette)

| Colour | Chart / bar `--mana-x` | Pip face `--mana-x-pip` |
|---|---|---|
| W white | `#e6dcb4` | `#f8f6d8` |
| U blue | `#3f8fd2` | `#c1d7e9` |
| B black | `#9b8c97` | `#bab1ab` |
| R red | `#d9533e` | `#e49977` |
| G green | `#3f9c62` | `#a3c095` |
| C colourless | `#9a948f` | `#cbc2bf` |
| M multicolour | `#c8a94e` | `#e3cf8a` |

Pip glyphs on a `-pip` face use `--mana-ink` (`#15120f`). The `--mana-x`
values are tuned to stay legible as bars on `--surface-*`. Black is a
muted mauve-grey, because a black bar would vanish against the dark base.

## Typography

| Token | Value |
|---|---|
| `--font-sans` | IBM Plex Sans, system fallback |
| `--font-mono` | IBM Plex Mono, ui-monospace fallback |
| `--font-card` | EB Garamond (card names only) |
| `--fs-micro` | 11px: uppercase micro-labels, chips |
| `--fs-xs` | 12px: captions, table meta |
| `--fs-sm` | 13px: dense table cells, nav |
| `--fs-base` | 14px: body |
| `--fs-md` | 16px: panel titles |
| `--fs-lg` | 20px: section headings |
| `--fs-xl` | 26px: page title |
| `--fs-stat` | 32px: stat-tile value |
| `--fs-hero` | 44px: lead stat on a deck surface |
| `--fw-regular/medium/semibold` | 400 / 500 / 600 |
| `--lh-tight/snug/base` | 1.15 / 1.35 / 1.55 |
| `--tracking-label` | 0.08em, for uppercase micro-labels |
| `--tracking-tight` | -0.01em, for large headings |

Fonts load from Google Fonts in `base.html`.

## Spacing (4px grid)

`--sp-1` 4px · `--sp-2` 8px · `--sp-3` 12px · `--sp-4` 16px · `--sp-5` 20px ·
`--sp-6` 24px · `--sp-8` 32px · `--sp-10` 40px · `--sp-12` 48px · `--sp-16` 64px
(plus `--sp-0` and `--sp-px`).

## Borders, corners, focus, motion

| Token | Value |
|---|---|
| `--hairline` | 1px |
| `--stroke` / `-faint` / `-strong` / `-signal` | full `border` shorthands, e.g. `border: var(--stroke)` |
| `--radius-0` / `-1` / `-2` | 0 / 2px (default) / 3px (card-art frames) |
| `--indicator` | 2px, the active-state bar (nav, selected rows) |
| `--focus-ring`, `--focus-offset` | `1px solid var(--signal)`, 1px |
| `--dur-fast` / `--dur-base` / `--ease` | 100ms / 160ms / `cubic-bezier(0.2,0,0,1)` |
| `--dur-slow` / `--stagger` / `--i` | 320ms (entrances) / 40ms per item / per-item index set inline (`style="--i:3"`) |
| `--fs-display` | 4.5rem, reserved for the Lab thesis hero (Phase 3) |
| `--graph-h` | 36rem, interaction-graph plot height (desktop) |
| `--graph-node-min` / `-max` | 5 / 18, node radius px by √degree (unitless: read by `HUD.graph`) |
| `--graph-edge` / `--graph-dim` | `var(--line-strong)` resting edge / 0.14 opacity of non-neighbours while focused |

## Layout and layers

| Token | Value |
|---|---|
| `--sidebar-w` | 232px |
| `--sidebar-w-collapsed` | 56px (icon rail at 900px and below) |
| `--content-max` | 1280px |
| `--content-pad` | 32px (20px at 900px and below) |
| `--bp-tablet` | 900px. Reference only: media queries can't read custom properties, so `@media (max-width: 900px)` is hard-coded. |
| `--z-sidebar` / `-overlay` / `-modal` / `-toast` | 50 / 900 / 1000 / 1100 |

## Legacy aliases (temporary)

Page templates still use the pre-HUD names. `hud.css` maps them onto the new
tokens so pages that haven't been migrated pick up the new palette. Don't use
them in new code. Delete them once no template references them
(`grep -r "var(--accent" templates/`).

| Legacy | Maps to |
|---|---|
| `--bg-color` | `--surface-0` |
| `--nav-bg` | `--surface-1` |
| `--text-main` / `--text-muted` / `--text-heading` | `--text-2` / `--text-3` / `--text-1` |
| `--border` | `--line` (a colour, used as `1px solid var(--border)`) |
| `--accent` / `--accent-hover` | `--signal` / `--signal-strong` |
| `--row-hover` | `--surface-2` |

## Shell markup (base.html)

`body.app-shell` contains `nav.sidebar`, which holds `.brand`,
`.nav-section-label`, `.nav-items > a.nav-item(.active)` and
`.sidebar-footer`, followed by `main.main-content`. Labels that collapse in
the tablet icon rail are wrapped in `.brand-label`, `.nav-label` and
`.sidebar-footer-label`.

## Component sizing tokens

`--pip-size` / `--pip-size-sm` (18 / 14px), `--chip-h` (20px), `--dot-size`
(6px), `--row-h` (32px, dense table row), `--panel-head-h` (40px),
`--bar-track-h` (6px), `--stack-bar-h` (8px), `--heat-cell` (36px),
`--art-ratio` (Scryfall art_crop 626/457), `--card-ratio` (488/680),
`--tile-min` (stat-tile min width), `--col-min` (comparison column min width).

## Component kit

Live reference with sample data: **`/styleguide`**.

- CSS: `static/css/hud.css` sections 5 (components) and 6 (analytics). Every
  class starts with `hud-` so it can't collide with the per-page classes
  (`.badge`, `.stat-card`, `.mana-pip`…) still in un-migrated templates.
- Jinja macros: `templates/partials/hud.html`. Import with
  `{% import "partials/hud.html" as hud %}`.
- JS: `static/js/hud.js`, loaded on every page, exposes `window.HUD`. It
  handles sortable tables and modals. `HUD.pips()`, `HUD.chip()`,
  `HUD.statusChip()`, `HUD.stat()`, `HUD.stack()`, `HUD.barChart()`,
  `HUD.rankList()` and `HUD.art()` return the same markup as the macros, for
  pages that render from fetched JSON (the deck view is built this way).
  Output is escaped.
- `HUD.scryfall(uid, version)` returns `{src, fallback}`. `src` points at the
  `cards.scryfall.io` CDN. Don't use the `api.scryfall.com/...?format=image`
  redirect for grids, because it returns 429 once a 100-card deck loads at
  once. Pass `fallback` to `HUD.art()`, and an `<img data-fallback>` retries
  through the API once if the CDN file is missing.
- Shell (`base.html`): the sidebar marks the active section by path prefix, so
  `/deck/*` lights up "Commander Decks", and it sets `aria-current="page"`.
  There's a Reference group (Changelog, Styleguide) and a skip link. A page
  that fills `{% block crumbs %}` (and optionally `{% block topbar_meta %}`)
  gets an `.app-topbar` breadcrumb strip; pages that don't are unchanged.

| Component | Macro | Root class | Notes |
|---|---|---|---|
| Micro-label | `label(text)` | `.hud-label` | uppercase mono |
| Page header | `page_head(title, eyebrow)` + call block for actions | `.hud-page-head` | |
| Section head | `section_head(title, code, anchor)` | `.hud-section-head` | trailing hairline |
| Stat tile | `stat(label, value, unit, foot, delta, mod)` | `.hud-stat` | mod `hero` / `signal`; wrap in `.hud-stat-strip` |
| Status chip | `chip(text, tone, dot)`, `status_chip(status)` | `.hud-chip` | tones `signal ok danger data neutral ghost`; `STATUS_TONES` maps deck, ownership and repo statuses; `.hud-chip-remove` button for editable tag lists |
| Mana pip | `pips(colors, size)` | `.hud-pip` | `"WUBG"` or a list; `""` gives colourless; size `sm` |
| Colour bar | `stack(segments)` | `.hud-stack` | `[{"color": "W", "value": 12}]` |
| Button | (markup) | `.hud-btn` | `--primary --ghost --danger --sm` |
| Tabs / crumbs | `tabs(items, active)`, `crumbs(items)` | `.hud-tabs`, `.hud-crumbs` | the sidebar stays in `base.html` |
| Panel | `panel(title, meta, mod, foot)` + call block | `.hud-panel` | mod `flush` (tables, lists), `signal` (alert) |
| Key/value | `kv(pairs)` | `.hud-kv` | |
| Empty state | `empty(text)` | `.hud-empty` | |
| Data table | `table(columns, rows)` or call block | `.hud-table` | sortable: `<table data-hud-sortable>` + `<th data-sort="num\|text">`; `<td data-value>` overrides the sort key; `tr.is-selected` |
| Card-art frame | `art(name, img, meta, colors, href, kind, chip)` | `.hud-art` | kind `art` (art_crop) / `card` (full card); caption sits below the art, never on it |
| Bar / curve chart | `bar_chart(bars, ref, hl, height, legend)` | `.hud-chart` | SVG bars in `--data`, `hl` bar in `--signal`, `ref` series as a dashed step line |
| Ranked bars | `rank_list(items, max)` | `.hud-rank` | item `color` uses a mana swatch, `hl` uses signal |
| Comparison | `compare(entities, groups)` | `.hud-compare` | 2–3 columns; row `lead: max\|min` marks the best value |
| Heatmap | `heatmap(rows, cols, matrix, max, self_diag, fmt, hl)` | `.hud-heat` | cells bucketed onto `--data-0..5` (`data-level`) |
| Modal | (markup) + `HUD.openModal(id)` | `.hud-modal` > `.hud-panel.hud-modal-dialog` | flat `--scrim`, no blur; scrim click / `[data-hud-close]` / Esc closes |

### Analytics II (hud.css section 7, AN-4)

Ported from `docs/design/analytics/analytics.css`; spec in
`docs/design/analytics/UX_SPEC.md`. Stats and Tune render from JSON (no
macros). Identity renders from JSON too in AN-4 (simplest path: every pillar
edit needs the rescored payload anyway). Journal (AN-5) gets macros.

| Component | Builder | Root class | Notes |
|---|---|---|---|
| Deck header + tabs | `partials/deck_head.html`: `header(head, tab)`, `crumbs(head, label)`, `freshness()` | `.hud-deckhead`, `.hud-deck-tabs` | every deck tab; tabs carry `data-key` 1–5, keys handled in `static/js/deck_analytics.js` (never while typing) |
| Interaction graph | `HUD.graph(svg, {nodes, edges, engines, commander, commanders, onSelect})` → `{focusEngine, select, search, nodeEl, conns}` | `.hud-graph` (`-engines`, `-stage`, `-toolbar`, `-svg`, `-legend`), `.g-node`, `.g-edge`, `.g-elabel` | needs `static/vendor/d3-force-3.0.0.min.js` (load on `/stats` only); laid out once (300 ticks), frozen |
| Engine row | (markup) | `.hud-engine[aria-pressed]` | name, flow, kind, pillar chip, card count |
| Inspector | (markup) | `.hud-inspector`, `.hud-io`, `.hud-edgelist`, `.hud-members`, `.hud-cardlink` | `aria-live="polite"` |
| Isolated tray | (markup) | `.hud-isolated` | cards with 0–1 edges as `.hud-cardlink` |
| Composition rows | (markup) | `.hud-comp` > `.hud-comp-row` (`-track`, `-fill`, `-target`, `-val`), `.hud-template-edit` | `is-under` = short by ≥ 2 (signal fill) |
| Meter | (markup) | `.hud-meter`, `.hud-meter-legend` | pillar / glue / generic |
| Pillars | (markup) | `.hud-pillars` > `.hud-pillar` (`is-hero is-pinned is-draft is-editing`), `.hud-pillar-add` | max 5; hero spans 2 columns, solid signal |
| Thesis | (markup) | `.hud-thesis`, `.hud-thesis-bar` | one `<em>` phrase in signal |
| Tray | `<details>` | `.hud-tray`, `.hud-tray-row` | rejected pillars |
| Save bar | (markup) | `.hud-savebar` (`is-dirty`) | sticky; `hidden` when clean |
| Combos | (markup) | `.hud-combos` > `.hud-combo`, `.hud-combo-group` | missing piece = `.is-missing` chip |
| Kbd / freshness | (markup) | `.hud-kbd`, `.hud-freshness` | |
| Compact ranked bars | wrap `HUD.rankList()` | `.hud-rank-compact` | narrow panels |
| Journal form | `partials/hud.html`: `form_section(id, title, blurb)`, `seg(name, options, selected, label_id, multi, mod, visible, more_id, id_prefix)`, `money(id, value, label, hint, hint_id)`, `picker_chip(name)` | `.hud-form` > `.hud-form-section`, `.hud-field`, `.hud-seg` (`--multi --exclude --scale`, `.hud-seg-more` / `.hud-seg-rest`), `.hud-scale-tiers`, `.hud-money`, `.hud-toggle`, `.hud-picker` + `.hud-picker-list` (section 10) | server-rendered (AN-5). `seg(visible=4)` puts the rest behind More…, expanded when one of them is selected |
| Check-in | (markup, from `/checkin` JSON) | `.hud-checkin-head`, `.hud-since` (`.up` ok / `.down` signal), `.hud-progress`, `.hud-checklist` > `.hud-check` (`is-current is-done`), `.hud-check-evidence`, `.hud-check-form` (inline Change) | AN-5; `.hud-progress` is `aria-live` |
| Tuner | (AN-6) | `.hud-swap`, `.hud-opts`, `.hud-opt`, `.hud-why`, `.hud-fix`, `.hud-changes` | CSS shipped in AN-4, page pending |

`HUD.barChart(bars, {ref: [...]})` now draws the dashed reference step line
like the `bar_chart` macro (Stats uses it for the template curve).
