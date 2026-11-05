# Archetype Description — Enhanced Editing UX Spec

Status: design spec, ready for implementation (card t_f9db9d7a)
Scope: `/archetypes` registry page editor + registry list rendering, and the archetypes API.
Out of scope: composition presets (`templates/composition_presets.html` has the same plain-textarea pattern — reuse this spec's component there later, but don't touch it in this card).

---

## 1. Current state (as of main @ 7a19655)

- **Editor**: `templates/archetypes.html:62-65` — a fixed `<textarea id="arDesc" rows="4" maxlength="1200">`. No preview, no counter, no hint, no auto-grow.
- **List rendering**: `templates/archetypes.html:119` — `a.description` injected via `textContent` into `.ar-item-desc`. Plain text, newlines collapse.
- **Validation**: `analytics/archetypes.py:42,147-152` — `MAX_DESCRIPTION = 1200`, `desc.strip()`, plain string, HTTP 400 via `ArchetypeError`.
- **Storage**: single Redis JSON list key `archetypes`; `description` is a plain string field. No schema versioning.
- **Display surfaces for the description**: the registry list on `/archetypes` is the *only* place descriptions render today. No other template shows them.

### Existing house patterns this spec reuses (do not invent new ones)

| Pattern | Where | What |
|---|---|---|
| `pf-count` char counter | `templates/deck.html:161`, `static/css/hud.css:3288-3289` | `n / max` mono counter, `.is-over` turns `--danger` |
| `hud-field` + `hud-field-hint` | `templates/archetypes.html:66-68` | label + hint stack |
| `ar-msg` status line | `templates/archetypes.html:26-28,72` | `role="status"`, `.is-error` / `.is-ok` |
| `hud-tab` segmented toggle | `templates/deck.html` decklist views | two-option toggle with `aria-current` |
| Server markdown render | `app.py:466-481` (`render_release_body`) | `markdown` pkg + `bleach` allow-list, both already dependencies |
| HTML sanitizer | `analytics/deck_writeup/sanitize.py` (`sanitize_primer_html`) | tag allow-list, drops script/style/etc. |
| `.deck-prose` | deck page Briefing panels | prose container styling for rendered markdown |

## 2. Locked decisions

1. **Format: a markdown subset, stored as source.** The stored `description` stays the raw markdown string in the same Redis field — **no schema change, no migration**. Supported syntax: paragraphs (blank line), `**bold**`, `*italic*`, `` `code` ``, `-`/`1.` lists (a list may directly follow an intro line, GitHub-style), single newlines as `<br>` (`nl2br`). No headings, links, or images. Raw HTML: only the sanitizer's allow-listed formatting tags (`b, strong, em, i, code, …`) pass through; everything else (`script`, `img`, event handlers, unknown tags) is stripped.
2. **Render server-side, once.** New helper in `analytics/archetypes.py` (or a tiny shared module): `render_description_html(desc)` = `markdown.markdown(desc, extensions=["nl2br"])` → `sanitize_primer_html(html, known_cards=None)`. Reusing the primer sanitizer keeps one allow-list in the app; its allowed tags (`p, ul, ol, li, b, strong, em, i, code, br, h3, h4, span`) cover the subset.
3. **API is additive.** `GET /api/archetypes` items gain a read-only `description_html` field alongside `description`. New `POST /api/archetypes/render-preview` with body `{description}` returns `{html}` for the editor preview — same render path, so preview == final. `POST/PUT /api/archetypes` keep accepting the markdown source string unchanged.
4. **Length: raise hard cap 1200 → 4000 chars** (markdown source). Soft guidance: aim ≤ 600 (1–3 short paragraphs). 4000 fits the "enhanced/longer" goal without inviting essays; registry list only ever shows an excerpt (§5) so long entries don't break layout.
5. **Preview is a Write/Preview toggle, not split-screen.** The editor column is ~half a 900px+ grid; side-by-side would cramp both. Preview renders live (debounced) while the Preview tab is active.
6. **No client-side markdown library.** `static/vendor/` has none; the debounced server round-trip guarantees parity and avoids shipping a JS parser.

## 3. Editor spec (Write tab)

```
┌─ Editor panel (existing #arEditor hud-panel) ─────────────────────────┐
│ Name                                                                  │
│ [____________________________________________________________]        │
│                                                                       │
│ Description                                  [ Write | Preview ]      │
│ ┌───────────────────────────────────────────────────────────────┐     │
│ │ Grindy **midrange** deck that wins through attrition.         │     │
│ │                                                               │     │
│ │ Key engines:                                                  │     │
│ │ - `Yawgmoth, Thran Physician` loops                           │     │
│ │ - sacrifice outlets for *card draw*                           │▲    │
│ │                                                               ││    │
│ │                                                               │▼    │
│ └───────────────────────────────────────────────────────────────┘     │
│ Markdown: **bold**, *italic*, `code`, lists. Aim for ≤ 600 chars.     │
│ Registry list shows the first paragraph only.           412 / 4000    │
│                                                                       │
│ Required roles  … (unchanged)                                         │
│ status message line (existing #arMsg)                                 │
│ [Save]  [Reset]                                    [Delete]           │
└───────────────────────────────────────────────────────────────────────┘
```

### Structure

Replace the current `.hud-field` description block (`archetypes.html:62-65`) with:

```html
<div class="hud-field ar-desc-field">
    <div class="ar-desc-head">
        <label for="arDesc">Description</label>
        <div class="hud-tabs ar-desc-tabs" role="group" aria-label="Description edit mode">
            <button type="button" class="hud-tab active" id="arDescWriteTab" aria-current="page">Write</button>
            <button type="button" class="hud-tab" id="arDescPreviewTab">Preview</button>
        </div>
    </div>
    <textarea id="arDesc" rows="4" maxlength="4000"
              aria-describedby="arDescHint arDescCount"></textarea>
    <div class="ar-desc-preview hud-prose" id="arDescPreview" role="region"
         aria-label="Description preview" hidden></div>
    <span class="hud-field-hint" id="arDescHint">
        Markdown: **bold**, *italic*, `code`, lists. Aim for ≤ 600 characters.
        The registry list shows the first paragraph only.
    </span>
    <span class="pf-count" id="arDescCount">0 / 4000</span>
</div>
```

- **Auto-grow**: textarea grows with content from `rows=4` (min-height ~6.5rem) up to `max-height: 40vh`, then scrolls internally. Use CSS `field-sizing: content` where supported with a 5-line JS fallback (`input` listener → `style.height = 'auto'; style.height = scrollHeight + 'px'`, clamped). Never shrinks below 4 rows. Animate nothing — height changes are instant.
- **Counter**: `pf-count` pattern, `n / 4000`, updates on `input`. Adds `.is-over` (and sets `aria-invalid="true"` on the textarea) when over 4000. Native `maxlength="4000"` also hard-stops typing; the counter is the visible affordance. Counter has `aria-live="off"` (screen readers get the hint, not keystroke spam).
- **Hint**: static `hud-field-hint`, always visible, linked via `aria-describedby`.

### Keyboard

- `Ctrl/Cmd+B` / `Ctrl/Cmd+I`: wrap selection in `**` / `*` (toggle off if already wrapped). Standard textarea selection math; keep undo intact by using `setRangeText` + `selectionStart/End`, not `execCommand`.
- `Ctrl/Cmd+Enter`: submit the form (same path as Save).
- `Tab` moves focus normally — **no** tab-inserts-spaces. `Esc` does nothing destructive (Reset is the escape hatch, and it refills from the last-loaded record, which is today's behavior).
- Write/Preview toggle buttons are plain `<button>`s: reachable by Tab, activated by Enter/Space. Toggling moves focus to the preview region (Preview) or back to the textarea (Write).

## 4. Preview tab

```
┌─ Description ────────────────────────────[ Write | Preview ]─────────┐
│ ┌───────────────────────────────────────────────────────────────┐     │
│ │ Grindy midrange deck that wins through attrition.             │     │
│ │                                                               │     │
│ │ Key engines:                                                  │     │
│ │  • Yawgmoth, Thran Physician loops                            │     │
│ │  • sacrifice outlets for card draw                            │     │
│ └───────────────────────────────────────────────────────────────┘     │
│ Rendered as it will appear.                             412 / 4000    │
└───────────────────────────────────────────────────────────────────────┘
```

- On switching to Preview: `POST /api/archetypes/render-preview {description}` → inject `html` into `#arDescPreview`. While the Preview tab stays active, re-render **debounced 400 ms** after typing stops (typing only happens in Write, so in practice this fires when the user toggles back and forth after edits — cheap).
- Empty description → preview shows `<p class="hud-muted">Nothing to preview yet.</p>`.
- Preview request failure → preview shows `.hud-empty` "Preview unavailable — your text is safe and saving still works." Never blocks Save.
- Styling: reuse `.deck-prose`/`hud-prose` typography inside a bordered box matching the textarea's footprint (same min-height, `max-height: 40vh`, scroll).
- The preview pane is **not** a live region (`aria-live` unset); the tab switch itself is the announcement. Loading state sets `aria-busy="true"` on the pane.

## 5. Registry list rendering

The list stays dense. Enhanced descriptions render as an **excerpt**:

```
┌─ Registry ────────────────────────────┐
│ ● Yawgmoth Attrition        4 roles   │
│   Grindy midrange deck that wins      │
│   through attrition…                  │
├───────────────────────────────────────┤
│ ● Storm                    3 roles    │
│   Spell-slinger combo deck.           │
└───────────────────────────────────────┘
```

- Item shows `description_html`'s **first block only** (server can ship `description_excerpt_html` = first `<p>`/`<ul>` block, or client slices `description_html` before the second block-level tag — server's choice, prefer server).
- CSS clamp as backstop: `.ar-item-desc { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }`.
- No "read more" control — clicking the item already opens the full text in the editor, where Preview shows it fully rendered.
- Empty description: render nothing (today's `|| ''` behavior).

## 6. Validation & error states

| Case | Client | Server |
|---|---|---|
| Over 4000 chars | counter `.is-over`, `aria-invalid="true"`; on submit attempt, inline error and no request | 400 `"Description is longer than 4000 characters."` |
| Empty description | allowed (optional field, unchanged) | allowed (`""`) |
| Save failure (network/400/500) | existing `#arMsg` `.is-error`, keep editor contents (today's behavior) | unchanged |
| Preview failure | inline `.hud-empty` note in the pane, Save unaffected | n/a |

Error copy reuses the server message verbatim in `#arMsg` (existing pattern at `archetypes.html:183`). Field-level: add `.is-error` styling hook on the textarea (`border-color: var(--danger)`) while `aria-invalid` is set; cleared on next `input` that validates.

## 7. Backward compatibility & migration

- **No data migration.** The `description` field keeps its current type (string) and storage location. Existing descriptions are plain text ≤ 1200 chars, which is valid markdown.
- **One intentional rendering change**: `nl2br` preserves single newlines that the old `textContent` list rendering collapsed. Existing multi-line plain-text entries will show line breaks where they were typed. This is a display improvement, not data loss; flag it in the PR description.
- **API**: `description` unchanged in shape; `description_html` / `description_excerpt_html` are additive. No consumer of `GET /api/archetypes` besides `archetypes.html` exists today (verified: only template referencing registry items).
- **Old limit grandfathered**: anything ≤ 1200 passes the new 4000 cap trivially.
- **Sanitization happens at render, never at storage**: source is preserved, output is safe. Non-allow-listed raw HTML (`script`, `img`, event handlers, …) in existing or new descriptions is stripped; allow-listed formatting tags pass through (see §2.1).

## 8. Implementation change points (checklist for the coder)

1. `analytics/archetypes.py` — `MAX_DESCRIPTION = 4000`; add `render_description_html()` (markdown → `sanitize_primer_html`); add excerpt helper.
2. `app.py` — include `description_html` (+ excerpt) in `GET /api/archetypes` items; add `POST /api/archetypes/render-preview`.
3. `templates/archetypes.html` — new description field block (§3 markup), Write/Preview toggle JS, auto-grow, counter, preview fetch, keyboard shortcuts, `.ar-item-desc` renders excerpt HTML (set via `innerHTML` from the *sanitized* server field only — never from raw `description`).
4. `static/css/hud.css` — `.ar-desc-head`, `.ar-desc-tabs`, `.ar-desc-preview`, textarea `.is-error`, excerpt line-clamp. All spacing/type/color via existing HUD tokens; no new hex values.
5. `tests/test_archetypes.py` — extend: 4000 cap enforced, markdown rendered + sanitized (script stripped), render-preview endpoint, legacy plain-text description round-trips unchanged.

### Explicit non-goals

- No rich-text/WYSIWYG widget, no toolbar buttons (shortcuts + hint suffice for this audience), no client markdown parser, no `@codemirror`, no autosave, no per-archetype card-name hover spans (`known_cards=None` — archetypes are global, not deck-bound).

## 9. Acceptance mapping

- **Editing**: §3 — auto-growing multi-line textarea, markdown subset, Write/Preview toggle, live preview (§4).
- **Validation**: §6 — hard cap, inline + server errors, `aria-invalid`, counter.
- **Rendering**: §2/§5 — server-rendered sanitized HTML in list excerpt and preview.
- **Backward compatibility**: §7 — no migration, legacy plain text renders correctly, additive API.

## 10. Quick verification script (manual, post-deploy on :8020)

1. Create an archetype with a 3-paragraph markdown description (bold, italic, code, list, one newline mid-paragraph). Save. Expect: counter tracked input; preview matched final list excerpt; registry v bumped.
2. Reload; edit it. Expect: source markdown back in the textarea (not HTML); Preview re-renders.
3. Paste 5000 chars. Expect: counter red at 4001 (maxlength blocks further typing), submit shows the 4000-char error if forced via API.
4. Enter `<script>alert(1)</script>**ok**`. Expect: preview and list show only "**ok**" bolded, no alert.
5. An archetype created before this change (plain text, e.g. "Midrange value pile.\nWins late.") renders unchanged in meaning, newlines now preserved.
6. Keyboard only: Tab to toggle → Preview → Ctrl+Enter saves → status announced.
