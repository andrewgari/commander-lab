# Export Modal: Moxfield and Archidekt — Layout & Options Spec

Status: design draft (t_e34ed10d). Scope is the export modal only: destination, format, privacy, and tagging options, plus layout and wireframes. Out of scope: the footer provider buttons (t_691dbd6f, `docs/export-buttons-spec.md`), backend endpoints, code, and tests.

## Assumptions and verified facts

Verified in repo (`main` worktree at `2e2cc4e`):

- `templates/deck.html` has `.modal-overlay` / `.modal-content` (lines 229–284). The card-detail modal (`#cardModal`) is the only modal. Reuse these classes; do not invent a new overlay.
- Design tokens come from `templates/base.html`: `--bg-color #000`, `--border #1f2937`, `--accent #3b82f6`, `--accent-hover #60a5fa`, `--row-hover #0a1120`, `--text-main #d1d5db`, `--text-muted #6b7280`, `--text-heading #fff`. Font Inter; headings EB Garamond.
- `.btn` (secondary), `.btn-primary` (accent fill) already exist in `deck.html`.
- `providers/moxfield.py` excludes sideboard and maybeboard-equivalent categories on import. The Archidekt convention matches. The export must use the same rule.
- `scripts/export_text.py` already emits the MTGO-style text format (`// Commander`, `// Mainboard`, `1 Card Name`). This is the only existing export format.
- `app.py` `sync_tags_to_archidekt` already pushes Lab tags to Archidekt (needs `ARCHIDEKT_SESSION` and `ARCHIDEKT_CSRF`). Tag export to Archidekt has a backend precedent.
- Moxfield has no official API. The provider reads public decks only. Export to Moxfield is therefore unverified and must be treated as a risk (see Open questions).

NOT verified (do not build against these without checking):

- The sync push endpoint `POST /api/decks/{id}/sync/push` (t_4e151174, commit `d67f9ae`) is not an ancestor of `HEAD`. Do not wire the modal's Export action to it until it lands on `main`.
- Whether Archidekt or Moxfield accept a privacy (public / unlisted / private) field through their write paths. The repo contains no privacy handling. The privacy control below is a design option that may be disabled per provider.

## 1. Entry point

- One trigger button, **Export…**, placed in `#footerLinks` after the provider buttons. Style: `.btn`. Icon: upload arrow, 14×14 inline SVG, `currentColor`.
- The modal is opened only from this trigger. Provider buttons stay link-outs.
- Disabled when the deck has no provider target at all (neither `deck.id` nor `deck.moxfield_id`). Tooltip: "Deck has no linked provider."

## 2. Modal content

Single column. Header, four labelled sections, footer.

Sections, top to bottom:

1. **Destination** (required). Radio group, two options: Archidekt, Moxfield.
   - Each option shows the deck state beneath its label: "Linked: archidekt.com/decks/{id}" or "Not linked — will create" or "Unsynced" (Moxfield without `moxfield_id`).
   - Default: the first enabled provider with a link. If none, default to Archidekt.
   - Moxfield option is disabled with a reason when `moxfield_id` is absent: "Requires a Moxfield link. Import the deck from Moxfield first."
2. **Format** (required). Select.
   - Options: `Commander (EDH)` (default). Other formats are not verified in the repo and are not included.
   - Format is a single value per export. Changing destination does not reset format.
3. **Privacy** (optional). Radio group: Public (default), Unlisted, Private.
   - Each option is enabled only if the chosen destination supports it. Until the providers are verified, render all three for Archidekt and show Moxfield as "Public only" with the other two disabled.
   - Helper text under the group: "Applies to the exported deck only. Does not change other copies."
4. **Tagging** (optional). Checkbox group.
   - `Include Lab tags` (default on for Archidekt, disabled for Moxfield). Helper: "Sends Lab tags as Archidekt categories. Uses the existing tag sync." Disabled with reason when `ARCHIDEKT_SESSION` / `ARCHIDEKT_CSRF` are not configured. The configuration state comes from the server; the client must not assume it.
   - `Include Sideboard / Maybeboard` (default off). Off matches the provider import convention (structural categories excluded). Helper: "Off matches the import rule. Commander is always included."
   - Commander is always exported in its own section. Not a toggle.

Footer: `Cancel` (`.btn`, left) and `Export` (`.btn .btn-primary`, right). `Export` is disabled until a destination and format are selected (both have defaults, so in practice only the destination-unavailable case disables it).

## 3. Wireframe — desktop (≥640px), max-width 480px

```
┌─────────────────────────────────────────────────┐
│ Export deck                                 [×] │
│ Ramp-Heavy Competitive                          │
│ ─────────────────────────────────────────────── │
│ DESTINATION                                     │
│  (•) Archidekt   Linked: archidekt.com/decks/…  │
│  ( ) Moxfield    Unsynced — link required       │
│                  (disabled)                     │
│                                                 │
│ FORMAT                                          │
│  [ Commander (EDH)                        ▾ ]   │
│                                                 │
│ PRIVACY                                         │
│  (•) Public   ( ) Unlisted   ( ) Private        │
│  Applies to the exported deck only.             │
│                                                 │
│ TAGGING                                         │
│  [x] Include Lab tags                           │
│  [ ] Include Sideboard / Maybeboard             │
│                                                 │
│ ─────────────────────────────────────────────── │
│ status: idle                  [Cancel] [Export] │
└─────────────────────────────────────────────────┘
```

Status line (`aria-live="polite"`) sits at the footer's left. It shows the state text from section 5.

## 4. Wireframe — mobile (<640px)

Full-screen sheet, not a centered card. Sections stack. Footer buttons are full-width and stacked: `Export` on top, `Cancel` below.

```
┌───────────────────────────┐
│ ← Export deck             │
│ Ramp-Heavy Competitive    │
├───────────────────────────┤
│ DESTINATION               │
│ (•) Archidekt  linked     │
│ ( ) Moxfield   disabled   │
│ FORMAT                    │
│ [ Commander (EDH)      ▾ ]│
│ PRIVACY                   │
│ (•) Public ( ) Unlisted   │
│ ( ) Private               │
│ TAGGING                   │
│ [x] Include Lab tags      │
│ [ ] Sideboard/Maybeboard  │
├───────────────────────────┤
│ [        Export        ]  │
│ [        Cancel        ]  │
└───────────────────────────┘
```

## 5. States

| State | Trigger | Visual | Behavior |
|---|---|---|---|
| Idle | Open modal | Options editable, `Export` enabled | Esc, `Cancel`, or `×` closes without action |
| Invalid | Chosen destination unavailable | Destination shows reason in `--text-muted`; `Export` disabled | Focus stays on the disabled option's group |
| Exporting | Click `Export` | `Export` label becomes "Exporting…" with spinner; all inputs `disabled`; `aria-busy="true"` on the dialog | Esc does not close while exporting. `Cancel` is disabled |
| Success | Server returns OK | Status: "Exported to Archidekt." Green check `#22c55e` (new token; see note) | Modal stays open. Link to the deck appears in the status line. `Export` becomes "Done" and closes the modal |
| Error | Server returns error | Status text in `#ef4444` (new token); `role="alert"` | Inputs re-enabled. Message shows the server reason in plain words, under 24 characters for the button label. `Export` reads "Retry" |
| Cancelled | Esc or `Cancel` mid-export | — | Not supported for this release. Exporting always runs to completion or error |

Color note: `--success` and `--danger` (`#22c55e`, `#ef4444`) are the same tokens proposed in `docs/export-buttons-spec.md`. Add them to `base.html` once, shared. Do not add them twice.

## 6. Interaction and accessibility

- Dialog: `role="dialog"`, `aria-modal="true"`, `aria-labelledby` pointing at the title.
- Focus: on open, focus goes to the first enabled radio in Destination. Focus is trapped inside the dialog. On close, focus returns to the trigger.
- Keyboard: Tab order follows visual order. Arrow keys move within radio groups. Esc closes when idle or in error.
- Overlay click closes when idle. It does not close while exporting.
- Disabled options keep their text visible and carry a `title` and `aria-describedby` reason.
- Minimum touch target 40px on mobile.

## 7. Behavior notes for implementers

- Modal is markup and CSS inside `deck.html`, matching the existing `#cardModal` pattern. No new dependency.
- The payload sent on `Export` is `{destination, format, privacy, include_lab_tags, include_side_maybe}`. Field names are a proposal; the backend contract is out of scope here.
- Defaults are computed on open from the deck record. The client never assumes the Archidekt tag credentials exist; that comes from the server and disables the tag checkbox when absent.
- Do not auto-export on destination change.

## 8. Open questions (block build until answered)

1. **Export semantics.** Does Export create a new deck on the destination, or update the linked deck? The sync-push work (t_4e151174) updates. The modal must say which.
2. **Privacy support.** Confirm the writable privacy field for Archidekt and whether Moxfield accepts any write at all. Moxfield has no public write API in this repo, so Moxfield export may be a text-copy fallback (`scripts/export_text.py` format) instead of a push. If so, the Moxfield option becomes "Copy decklist" and privacy and tagging are removed for it.
3. **Sync-push dependency.** The push endpoint is not on `main` (`d67f9ae` is not an ancestor of `HEAD`). Confirm which branch lands it before wiring Export.
4. **Format list.** Only Commander is verified. Add other formats only with provider confirmation.
5. **Button ownership.** t_691dbd6f owns the footer buttons; this spec adds one trigger next to them. Confirm the order of `Export…` relative to those buttons.

## 9. Acceptance checklist

- [ ] Modal reuses `.modal-overlay` / `.modal-content` and existing tokens; no new overlay pattern.
- [ ] Destination, format, privacy, and tagging sections are specified with defaults, disabled reasons, and provider-specific availability.
- [ ] Desktop (≥640px) and mobile (<640px) wireframes provided.
- [ ] Idle, invalid, exporting, success, error, and cancelled states specified; cancel behavior is explicit.
- [ ] Focus, keyboard, and ARIA behavior specified.
- [ ] Sideboard/maybeboard default matches the import convention.
- [ ] Unverified provider capabilities are listed as open questions, not assumed.
