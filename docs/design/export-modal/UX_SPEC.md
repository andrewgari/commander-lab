# Decklist Export UI: Buttons & Export Modal UX Spec (EX-D)

Task: `t_639f2a63` (Parent: `t_fa34511e`, Downstream wiring: `t_38a1e491`)
Design system: HUD component kit (`static/css/hud.css`, `docs/DESIGN_TOKENS.md`, `static/js/hud.js`)
Class namespace: `ex-` (`docs/design/export-modal/export.css`, to be appended to `static/css/hud.css` or loaded as a partial stylesheet)

**Live interactive mockup:** `docs/design/export-modal/mockup.html` (+ `export.css`)
Switch states via `?s=`:
- `reconstruct-buttons` — Surface A: Reconstruct Step 5 (Draft Results) per-draft buttons, header exported badges, and sticky bar action
- `theory-buttons` — Surface B (Theory doc header + Section 02 Rebuilt Decklist header) & Surface C (Tune tab Change List aside)
- `modal-idle` — Export Modal ready state (both providers selected, options populated)
- `modal-creating` — In-flight creation state (`fieldset disabled`, per-provider spinners)
- `modal-success` — Completed creation on both providers with direct external links
- `modal-partial` — Partial success (1 provider created, 1 provider HTTP 401 token expired with auto-expanded fallback tray)
- `modal-unconfigured` — Unconfigured token state (`501` / env var unset) with manual copy & quick-import fallback tray open

Rendered verification screenshots (`1440×1024` desktop and `390×844` mobile, verified `scrollWidth == clientWidth` with zero horizontal overflow): `docs/design/export-modal/shots/01-*` through `07-*`.

---

## 1. Design System Alignment & Core Principles

1. **Explicit user action only — never overwrites existing decks.**
   Per `docs/SYNC_UPDATE_PUSH.md`, existing linked decks use `Push` (`POST /api/decks/{id}/sync/push`) to overwrite a remote deck. This feature (`EX-D`) is strictly for **creating a brand-new decklist** on the user's Moxfield and/or Archidekt account whenever Commander Lab generates, overhauls, or edits a decklist.
2. **"And / Or" Multi-Provider Selection.**
   Users can create the decklist on **Moxfield**, **Archidekt**, or **both simultaneously** in a single modal interaction, with independent per-provider status rows so one provider succeeding is never rolled back or hidden if the other fails.
3. **Graceful Token / Cloudflare Fallback.**
   Because `MOXFIELD_TOKEN` is a short-lived browser bearer token (Turnstile-gated, `docs/moxfield-api.md` §3) and `ARCHIDEKT_TOKEN` is an optional JWT (`docs/SYNC_UPDATE_PUSH.md`), direct API creation may be unconfigured (`501`) or expired (`401`/`502`). Every export modal includes a collapsible **Manual Copy & Quick-Import Fallback** tray (`<details class="ex-fallback">`) that formats the 100-card decklist in provider-native syntax (`*CMDR*` for Moxfield, `1x Card [Category]` for Archidekt, or plain MTGO) and provides one-click copy + deep links to each provider's New Deck page. When a provider is unconfigured or fails, this tray auto-opens.
4. **Tokens & HUD Primitives Only.**
   All surfaces use `hud.css` tokens (`--surface-0..3`, `--signal`, `--ok`, `--danger`, `--data`, `--font-sans`, `--font-mono`, `--font-card`, `--radius-1`, `1px` hairlines, solid `--scrim` modal). Outbound creation buttons reuse `.sync-btn.sync-btn--push` (amber signal tint) or `.hud-btn--primary` with the upload/external-create SVG icon.

---

## 2. Entry-Point Surfaces & Trigger Buttons

Any surface where Commander Lab builds or edits a decklist exposes an export trigger that opens the shared `#exportDeckModal` (`HUD.openModal('exportDeckModal')`) pre-populated with that surface's deck payload.

### 2.1 Surface A — Reconstruct Step 5 (`templates/reconstruct.html` · `#screenResults`)

Reconstruct generates 1 or 2 complete 100-card draft decks (`currentSession.drafts[idx]`), available both before and after clicking *Accept & Save Deck(s)* (including in Dry Run mode where *Accept* is disabled).

1. **Per-Draft Card Header Action Bar (`.recon-draft-actions`)**:
   Placed in the top-right of each draft card (`#draftsListContainer`), alongside a quick `Copy Text` button:
   ```html
   <div class="recon-draft-actions ex-bar">
       <button type="button" class="hud-btn hud-btn--sm" onclick="copyDraftText(0)" title="Copy 100-card decklist to clipboard">
           <!-- COPY_ICON (13x13) -->
           <span>Copy Text</span>
       </button>
       <button type="button" class="hud-btn hud-btn--sm sync-btn sync-btn--push ex-trigger" onclick="openExportModal({ source: 'reconstruct', sessionId: currentSessionId, draftIndex: 0 })">
           <!-- EXT_ICON (14x14 upload arrow) -->
           <span>Create on Moxfield / Archidekt</span>
       </button>
   </div>
   ```
2. **Created External Badge Links (`.ex-ext-links`)**:
   Once a draft has been exported to Moxfield and/or Archidekt during the session, persistent green status link chips render in the draft panel header (`.hud-panel-meta`):
   ```html
   <span class="ex-ext-links">
       <a class="ex-ext-link" href="https://moxfield.com/decks/..." target="_blank" rel="noopener" title="Created on Moxfield">
           ✓ Moxfield <!-- LINK_ICON -->
       </a>
       <a class="ex-ext-link" href="https://archidekt.com/decks/..." target="_blank" rel="noopener" title="Created on Archidekt">
           ✓ Archidekt <!-- LINK_ICON -->
       </a>
   </span>
   ```
3. **Sticky Bottom Action Bar (`#draftsActionBar` / `.recon-sticky`)**:
   Sits between `Regenerate` and `Accept & Save Deck(s)` so the user can export from the bottom bar without scrolling back up. If the session has 2 drafts, opening the modal from the sticky bar shows the **Draft Selector** segmented control (`Draft 1` | `Draft 2`) at the top of the modal.
   ```html
   <button type="button" class="hud-btn sync-btn sync-btn--push ex-trigger" id="btnExportDrafts" onclick="openExportModal({ source: 'reconstruct', sessionId: currentSessionId, draftIndex: 0 })">
       <!-- EXT_ICON -->
       <span>Export to Moxfield / Archidekt…</span>
   </button>
   ```
4. **Accept Success Modal (`#acceptModal`)**:
   When drafts are accepted into local physical decks, each created deck row includes a secondary `Create on Moxfield / Archidekt` button next to `Open Deck`.

---

### 2.2 Surface B — Theory Overhaul Document (`templates/deck_theory.html` & `templates/partials/theory/decklist.html`)

When Oracle builds a 100-card thematic overhaul (`/deck/{name}/ideas/{doc_id}`):

1. **Top Action Row (`.th-back`)**:
   Placed on the right side of the `.th-back` bar alongside `Delete`:
   ```html
   <div class="th-back">
       <a class="hud-btn hud-btn--sm hud-btn--ghost" href="{{ ideas }}">← All ideas</a>
       <div class="ex-bar">
           <span class="ex-ext-links" id="theoryExtLinks"></span>
           <button class="hud-btn hud-btn--sm sync-btn sync-btn--push ex-trigger" type="button" id="exportTheoryBtn">
               <!-- EXT_ICON -->
               <span>Create on Moxfield / Archidekt</span>
           </button>
           <button class="hud-btn hud-btn--sm hud-btn--danger" type="button" id="deleteBtn">Delete</button>
       </div>
   </div>
   ```
2. **Section 02 Header (`Rebuilt Decklist (100 cards)`)**:
   In `templates/deck_theory.html` (or inside `templates/partials/theory/decklist.html`), a compact secondary trigger sits at `margin-left: auto` on `.hud-section-head` for Section 02:
   ```html
   <button class="hud-btn hud-btn--sm ex-trigger" style="margin-left:auto" type="button" data-export-theory>
       <!-- EXT_ICON -->
       <span>Export Decklist…</span>
   </button>
   ```

---

### 2.3 Surface C — Tune Tab Change List Aside (`templates/deck_tune.html` · `aside.hud-changes`)

When the user accepts swaps or shape fixes on `/deck/{name}/tune`, the `Change list` aside (`aside.hud-changes`) accumulates the edits. Previously it only had `Copy as text` (which copied the delta lines).

Add a primary export button above `Copy changes as text` that applies the accepted swaps/adds to the base 100-card decklist and opens `#exportDeckModal` to create the edited 100-card decklist on Moxfield and/or Archidekt:

```html
<div class="tn-actions">
    <button class="hud-btn hud-btn--primary ex-trigger" type="button" id="exportEditedDeck" disabled>
        <!-- EXT_ICON -->
        <span>Create Edited Deck on Moxfield / Archidekt</span>
    </button>
    <button class="hud-btn" type="button" id="copyText" disabled>
        <!-- COPY_ICON -->
        <span>Copy changes as text</span>
    </button>
    <span class="hud-field-hint" id="moveHint">
        Exports the full 100-card decklist with your accepted swaps applied to a new external deck, or copy the delta as text.
    </span>
</div>
```
- `disabled` when `L.items.length === 0` (enabled as soon as ≥1 swap or add is accepted, matching `#copyText`).

---

## 3. Shared Export Modal (`#exportDeckModal`)

### 3.1 ASCII Wireframe (Desktop `max-width: 42rem` / `672px`)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ CREATE ON MOXFIELD / ARCHIDEKT                                       CLOSE ✕ │
├──────────────────────────────────────────────────────────────────────────────┤
│ ┌───────────────────────────────┬──────────────────────────────────────────┐ │
│ │ DRAFT 1 · KRAUM & TYMNA (100) │ DRAFT 2 · THRASIOS LANDFALL (100)        │ │ ← Only when >1 draft
│ └───────────────────────────────┴──────────────────────────────────────────┘ │
│ ┃ RECONSTRUCTED DRAFT 1 OF 2 · SESSION RECON_49DF99E5251A        (W)(U)(B)(R)│
│ ┃ Kraum & Tymna Spellslinger                        100 CARDS · 2 COMMANDERS │
│ ┃ Kraum, Ludevic's Opus + Tymna the Weaver                                   │
│                                                                              │
│ Target Accounts                                     Select one or both       │
│ ┌────────────────────────────────────┐ ┌────────────────────────────────────┐│
│ │ ┃ [M] Moxfield                  [✓]│ │ ┃ [A] Archidekt                 [✓]││
│ │ ┃ @andrewgari · linked account     │ │ ┃ @andrewgari · linked account     ││
│ │ ┃ Creates a new Commander deck via │ │ ┃ Creates a new Commander deck     ││
│ │ ┃ bulk import with *CMDR* markers. │ │ ┃ with Commander & role categories.││
│ │ ┃ [■ DIRECT API READY]             │ │ ┃ [■ DIRECT API READY]             ││
│ └────────────────────────────────────┘ └────────────────────────────────────┘│
│                                                                              │
│ Decklist Options                                    Applied to new deck      │
│ Deck Title                                    Visibility                     │
│ ┌───────────────────────────────────────────┐ ┌──────────┬────────┬─────────┐│
│ │ Kraum & Tymna Spellslinger [Lab]          │ │ UNLISTED │ PUBLIC │ PRIVATE ││
│ └───────────────────────────────────────────┘ └──────────┴────────┴─────────┘│
│ [✓] Include role & sourcing tags as categories                               │
│ [✓] Link created URL(s) back to this Commander Lab deck                      │
│ [✓] Include generated explanation / primer                                   │
│                                                                              │
│ ┌─ Per-provider status rows (shown during/after submit) ────────────────────┐│
│ │ ┃ [✓ CREATED]  Moxfield · Kraum & Tymna [Lab]      [OPEN ON MOXFIELD ↗]   ││
│ │ ┃              https://moxfield.com/decks/k9Xp2...                        ││
│ ├───────────────────────────────────────────────────────────────────────────┤│
│ │ ┃ [✓ CREATED]  Archidekt · Kraum & Tymna [Lab]     [OPEN ON ARCHIDEKT ↗]  ││
│ │ ┃              https://archidekt.com/decks/9841205                        ││
│ └───────────────────────────────────────────────────────────────────────────┘│
│                                                                              │
│ ▸ MANUAL COPY & QUICK-IMPORT FALLBACK        WORKS WITHOUT API TOKENS · 100  │
│                                                                              │
│ ──────────────────────────────────────────────────────────────────────────── │
│ Explicit user action only · never overwrites   [ CANCEL ] [ ↑ CREATE ON … ]  │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Modal Anatomy & Controls

1. **Draft Picker (`.ex-draft-picker`, conditional)**:
   - Shown only when invoked from a multi-draft Reconstruct session (`drafts.length > 1`).
   - Uses `.hud-seg` radio tabs (`Draft 1 · {name} ({count})` | `Draft 2 · {name} ({count})`). Switching tabs updates the Summary Strip, default Deck Title, and Fallback text preview in place.
2. **Deck Summary Strip (`.ex-summary`)**:
   - Left `2px` `--signal` indicator bar on `--surface-0`.
   - Eyebrow (`.ex-summary-eyebrow`): source context (`RECONSTRUCTED DRAFT 1 OF 2 · SESSION ...`, `THEORY OVERHAUL · DOC ...`, or `TUNE EDITED DECK · 4 SWAPS APPLIED`).
   - Title (`.ex-summary-title`): `--fs-md` semibold sans.
   - Commander line (`.ex-summary-cmdr`): card names in `--font-card` (`EB Garamond`).
   - Meta right column (`.ex-summary-meta`): `HUD.pips(color, "sm")` + card/commander count (`100 cards · 2 commanders`).
3. **Target Accounts Multi-Select Tiles (`.ex-targets` > `.ex-target`)**:
   - Two equal-width checkbox tiles (`grid-template-columns: repeat(2, minmax(0, 1fr))`, stacking to 1 column at `≤640px`).
   - Each tile wraps `<input type="checkbox" name="provider" value="moxfield|archidekt">` + `<label>`.
   - Checked state: `background: var(--signal-dim)`, `border-color: var(--signal-line)`, `box-shadow: inset 2px 0 0 var(--signal)`, monogram badge (`M` / `A`) and checkmark (`✓`) invert to `--signal` fill with `--text-on-signal` ink.
   - Displays the user's linked username from `/api/linked-accounts` (e.g., `@andrewgari · linked account`, or `Account via server token` if no pull-account is linked).
   - Footer capability chip:
     - Configured & ready: `HUD.chip("Direct API Ready", "ok", true)`
     - Token env var unset (`can_create: false`): tile gets `.is-unconfigured` (`border-style: dashed`), chip shows `HUD.chip("No token · Manual paste", "signal", true)`.
     - Last attempt failed with `401`: `HUD.chip("Token expired (401)", "danger", true)`.
4. **Decklist Options (`.ex-options`)**:
   - **Deck Title (`#exDeckName`)**: Text input (`maxlength="100"`), prefilled with:
     - Reconstruct: `{draft.name} [Lab]`
     - Theory: `{head.name} — {meta.theme} [Lab]`
     - Tune: `{deck_name} (Tuned) [Lab]`
   - **Visibility (`name="exVis"`)**: `.hud-seg` radio group (`Unlisted` default, `Public`, `Private`).
   - **Checkboxes (`.ex-checks`)**:
     - `Include role & sourcing tags as categories` (checked by default) — maps Commander Lab role/sourcing tiers to Archidekt `[Category]` brackets and Moxfield card tags.
     - `Link created URL(s) back to this Commander Lab deck` (checked by default when a local deck exists; hidden or disabled with hint *"Available after accepting draft"* on unaccepted Reconstruct drafts).
     - `Include generated explanation / primer` (checked by default when `draft.explanation` or Theory thesis/primer prose is present).
5. **Per-Provider Execution & Result List (`.ex-results`)**:
   - Hidden in `idle` state. Appears as soon as the user clicks `Create on …` (`role="status" aria-live="polite"`, or `role="alert"` on error).
   - Renders one `.ex-result-row` per selected provider:
     - **`.is-busy`**: left bar `--data`, spinning SVG `.sync-spin`, `HUD.chip("Creating…", "data")`, detail line `Importing 100 cards…`.
     - **`.is-ok`**: left bar `--ok`, `HUD.chip("✓ Created", "ok")`, external deck URL, and primary action button `<a class="hud-btn hud-btn--sm hud-btn--primary" target="_blank" rel="noopener">Open on {Provider} ↗</a>`.
     - **`.is-err`**: left bar `--danger`, `HUD.chip("✕ Failed (401)", "danger")`, clear human error message in `--danger` mono, plus two recovery buttons: `Copy & Open {Provider} ↗` (copies provider-formatted text to clipboard and opens the provider's New Deck page in a new tab) and `Retry`.
6. **Manual Copy & Quick-Import Fallback Tray (`<details class="ex-fallback">`)**:
   - Always available at the bottom of the modal body; collapsed by default when both providers report `Direct API Ready`, **auto-expanded (`open`)** when any selected provider is unconfigured (`501`) or fails (`401`/`502`).
   - Format switcher (`.hud-seg`):
     - `Moxfield syntax (*CMDR*)`: `1 Card Name *CMDR*` + mainboard lines (`providers/moxfield.py::build_import_text`).
     - `Archidekt syntax (1x [Category])`: `1x Card Name [Commander{top}]` / `1x Card Name [Role]`.
     - `Plain MTGO (1 Card)`: `1 Card Name` with `Commander` section header.
   - Action buttons:
     - `Copy to Clipboard` (updates `#exCopyFeedback` `aria-live="polite"` with `✓ Copied 100 cards`).
     - `Moxfield New Deck ↗` (`https://www.moxfield.com/decks/personal`)
     - `Archidekt Sandbox ↗` (`https://archidekt.com/decks/new`)
   - Scrollable monospace preview (`<pre class="ex-decklist-box" tabindex="0">`, `max-height: 11rem`).
7. **Modal Footer (`.ex-foot`)**:
   - Left note: `Explicit user action only · never overwrites existing decks`.
   - Right buttons: `Cancel` (`[data-hud-close]`) + Primary submit `#exSubmitBtn`:
     - Both checked: `Create on Moxfield & Archidekt`
     - One checked: `Create on Moxfield` / `Create on Archidekt`
     - None checked: `disabled`, label `Select a provider above`
     - In-flight: `disabled`, spinner + `Creating on {N} provider(s)…`
     - Partial failure: enabled, label `Retry Failed ({Provider})`
     - All succeeded: `disabled`, label `✓ Created on {Providers}` (`Cancel` button label changes to `Done`).

---

## 4. Frontend → Backend Contract for `t_38a1e491` & `t_0c7cfd01`

To make wiring in `t_38a1e491` clean and uniform across Reconstruct, Theory, and Tune, the modal talks to two endpoints (or the equivalent routes implemented by `t_0c7cfd01`):

### 4.1 Capability & Account Preflight (Optional / On Modal Open)
The modal can read provider readiness from `GET /api/export/status` (or piggyback on `/api/linked-accounts` + sync status):
```json
{
  "success": true,
  "providers": {
    "moxfield": {
      "can_create": true,
      "username": "andrewgari",
      "unavailable_reason": null
    },
    "archidekt": {
      "can_create": true,
      "username": "andrewgari",
      "unavailable_reason": null
    }
  }
}
```
If the status endpoint is not yet loaded or returns `can_create: false`, the provider tile renders `.is-unconfigured` and auto-opens `.ex-fallback` so the user is never blocked.

### 4.2 Create External Decklist Request
`POST /api/export/external` (or per-source alias `/api/reconstruct/{session_id}/export`, `/api/decks/{deck_id}/export`):
```json
{
  "providers": ["moxfield", "archidekt"],
  "name": "Kraum & Tymna Spellslinger [Lab]",
  "visibility": "unlisted",
  "include_tags": true,
  "include_description": true,
  "link_to_deck_id": "archidekt:42",
  "source": {
    "kind": "reconstruct | theory | tune | cardlist",
    "session_id": "recon_49df99e5251a",
    "draft_index": 0,
    "deck_id": null,
    "doc_id": null
  },
  "commanders": ["Kraum, Ludevic's Opus", "Tymna the Weaver"],
  "description": "Generated by Commander Lab Reconstruct...",
  "cards": [
    {"name": "Kraum, Ludevic's Opus", "quantity": 1, "is_commander": true, "category": "Commander"},
    {"name": "Sol Ring", "quantity": 1, "is_commander": false, "category": "Ramp"}
  ]
}
```

### 4.3 Response Shape (Per-Provider Results)
```json
{
  "success": true,
  "results": {
    "moxfield": {
      "status": "ok",
      "provider": "moxfield",
      "external_id": "k9Xp2_aKeQjcZVg",
      "remote_url": "https://moxfield.com/decks/k9Xp2_aKeQjcZVg",
      "card_count": 100,
      "linked": true,
      "error": null,
      "code": null
    },
    "archidekt": {
      "status": "error",
      "provider": "archidekt",
      "external_id": null,
      "remote_url": null,
      "card_count": 0,
      "linked": false,
      "code": "push_not_configured",
      "error": "ARCHIDEKT_TOKEN is not set on this server."
    }
  },
  "formatted_text": {
    "moxfield": "1 Kraum, Ludevic's Opus *CMDR*\n...",
    "archidekt": "1x Kraum, Ludevic's Opus [Commander{top}]\n...",
    "mtgo": "Commander\n1 Kraum, Ludevic's Opus\n..."
  }
}
```
*Note for `t_38a1e491`*: Client-side JS can also generate `formatted_text` immediately from the in-memory `cards` array (using the exact format functions in `mockup.html`) so `Copy to Clipboard` and the Fallback Tray work with zero network latency even if the backend is unreachable.

---

## 5. Responsive & Accessibility Requirements

1. **Phone Sheet (`≤640px`)**:
   - `#exportDeckModal` switches to a full-viewport sheet (`padding: 0`, `.hud-modal-dialog` `min-height: 100dvh; border: 0; border-radius: 0`).
   - `.hud-panel-head` sticks to `top: 0`; `.ex-foot` sticks to `bottom: 0` with full-width action buttons (`flex: 1 1 auto`).
   - `.ex-targets` and `.ex-options` collapse to a single column (`grid-template-columns: minmax(0, 1fr)`).
2. **Keyboard & Focus Management**:
   - Handled by `HUD.openModal('exportDeckModal')` / `HUD.closeModal('exportDeckModal')` in `static/js/hud.js`: traps Tab focus inside the dialog, closes on `Escape` or scrim click, and restores focus to the triggering `.ex-trigger` button on close.
   - Provider tiles use native `<input type="checkbox">` with `:focus-visible + label` showing `var(--focus-ring)`.
3. **Reduced Motion**:
   - `@media (prefers-reduced-motion: reduce)` disables transitions on `.ex-target label` and `.ex-ext-link`, and `hud.css` stops `.sync-spin` rotation.
