# Export Buttons: Moxfield and Archidekt

Status: Draft spec (t_691dbd6f)
Scope: The export action buttons for the decklist generation view (`templates/deck.html`, `#footerLinks`). This spec covers the two buttons, their states, icons, and placement. It does not cover modals, dropdowns, code, or tests.

## 1. Current state (verified in repo)

- `templates/deck.html` `buildFooterLinks()` renders provider buttons into `#footerLinks`.
- Existing button class: `.btn` (ghost/outline). `.btn-primary` (filled accent) is defined but not used in this footer.
- Archidekt: rendered as an outbound `<a target="_blank" class="btn">` to `https://archidekt.com/decks/{deck.id}`, with an external-link icon.
- Moxfield: an outbound `<a>` to `https://moxfield.com/decks/{moxfield_id}` when `deck.moxfield_id` exists. When it does not, a disabled `<button class="btn">` reads "Moxfield (Unsynced)".
- `app.py` has no export endpoint. Its only Moxfield/Archidekt references are provider enablement (`ENABLED_PROVIDERS`) and URL import. The sync pull/push endpoints (`POST /api/decks/{id}/sync/pull|push`) come from t_4e151174 and may not be on `main` yet. Confirm before wiring any button to them.

Consequence: the two current buttons are navigation links, not actions. The loading, success, and error states below apply only if the buttons trigger a server action (push/export). For link-out buttons, use the Idle and Disabled states only.

## 2. Design tokens (from `templates/base.html` and `deck.html`)

| Token | Value | Use |
|---|---|---|
| `--bg-color` | `#000000` | Button idle background |
| `--row-hover` | `#0a1120` | Hover background |
| `--border` | `#1f2937` | Idle border |
| `--accent` | `#3b82f6` | Primary fill, hover border, focus ring |
| `--accent-hover` | `#60a5fa` | Primary hover fill |
| `--text-main` | `#d1d5db` | Idle label |
| `--text-heading` | `#ffffff` | Primary label |
| `--text-muted` | `#6b7280` | Disabled label |
| Font | Inter 500, 0.875rem | Labels |
| Radius | 4px | All buttons |

Add two tokens only if needed: `--success: #22c55e` and `--danger: #ef4444`. Both are new and must be documented in `base.html` if used. Do not add them without the implementer's sign-off.

## 3. Button variants

- **Primary** (`.btn .btn-primary`): filled `--accent`, white text. Use for the single recommended export in a view. Use Archidekt as the default primary only when `archidekt` is the first enabled provider. Otherwise do not promote either button.
- **Secondary** (`.btn`): outline on `--bg-color`, `--text-main` label. This is the default for both buttons.
- **Disabled**: `opacity: 0.5`, `cursor: not-allowed`, `aria-disabled="true"`. Use when the deck is unsynced. Keep the reason visible in the label or a `title`.

Recommended default: both buttons secondary. Promote one to primary only if a product decision is made. Do not decide this in code.

## 4. Icons

- Link-out (current): the existing 14×14 external-link SVG (`stroke-width 2`, `currentColor`).
- Loading: a 14×14 spinner, a 1.5px arc rotating 0.8s linear infinite, `currentColor`. Replace the external-link icon while loading.
- Success: a 14×14 check (`polyline 20 6 9 17 4 12`), `#22c55e`. Shown for 2.5s, then reverts to idle.
- Error: a 14×14 alert (circle plus vertical line), `#ef4444`. Stays until the user clicks again.

Icons are inline SVG, matching the existing nav-icon pattern. Do not add an icon font.

## 5. States

| State | Label | Icon | Visual | Interaction |
|---|---|---|---|---|
| Idle | "Archidekt" / "Moxfield" | external link | Secondary style | Clickable |
| Hover | same | same | Background `--row-hover`, border `--accent` | Cursor pointer |
| Focus | same | same | 2px `--accent` outline, offset 2px | Keyboard: Enter/Space |
| Loading | "Exporting…" | spinner | Secondary, `aria-busy="true"` | Not clickable (`disabled`) |
| Success | "Exported" | check (green) | Secondary, border `#22c55e` | Reverts to idle after 2.5s |
| Error | "Export failed — retry" | alert (red) | Secondary, border `#ef4444` | Click retries |
| Disabled | "Moxfield (Unsynced)" | none or external link | `opacity 0.5` | Not clickable; `title` explains why |

Status text is announced to screen readers through an `aria-live="polite"` region (one per button group). Errors use `role="alert"`.

Label length: keep the error label under 24 characters so it fits on mobile.

## 6. Placement

- Container: `#footerLinks` (`.links-container`), at the bottom of the deck view, below the primer section.
- Order (left to right): EDHREC, Archidekt, Commander Salt, Moxfield. Keep the existing order. Do not reorder for visual balance.
- Gap: 0.5rem, wrapping allowed. On narrow screens (<640px), buttons wrap to full width in a 2-column grid.
- Do not place export buttons inside the card grid or the header.

## 7. Behavior notes for implementers

- Idle, hover, and focus are CSS-only. Loading, success, and error need a JS state machine per button: `idle → loading → (success → idle | error → idle)`.
- A click in loading state is ignored.
- If the action is link-out only, skip loading, success, and error entirely.
- Do not block the page or show a modal for export. Feedback stays on the button.

## 8. Acceptance checklist

- [ ] Both buttons use `.btn`; primary variant is documented but not applied by default.
- [ ] Idle, hover, focus, loading, success, error, and disabled states are specified above.
- [ ] Icons are inline SVG at 14×14 with `currentColor` except status colors.
- [ ] Placement stays in `#footerLinks`; order unchanged.
- [ ] Disabled Moxfield state preserved for unsynced decks.
- [ ] Status colors are documented as new tokens if used.

## 9. Open questions

1. Do the buttons trigger a push/export, or stay link-outs? This decides whether loading, success, and error are needed. Current code: link-out only.
2. Which branch carries the sync push endpoint (t_4e151174)? It is not on `main` in this worktree.
3. Should Moxfield and Archidekt be primary or secondary? Spec default is secondary for both.
