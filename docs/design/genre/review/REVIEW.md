# Deck Genre UI review: PR #145 vs mockups (GN-R)

Task t_d54c718e. Reviewed head `e495450` (branch `wt/t_c1f47124`). Not a PR comment; the aggregator (t_85a2a7d8) turns this into one.

## Verdict

**PASS WITH FIXES.** No blockers. The implementation is a faithful port of the mockup (computed styles match on every kit element). The deviations are behaviour, not paint: autocomplete ranking/markup, missing duplicate feedback, focus handling, and touch targets. Mobile layout holds at 375 / 414 / 768 with zero overflow.

Counts: **0 blocker · 6 should-fix · 7 nit**.

## What I compared against

| Source | Where |
|---|---|
| Mockups (the design deliverable) | `docs/design/genre/index.html` (+ `genre.css`, `genre.js`), shots `docs/screenshots/genre/{empty,set,full,search,kindred,browse}_{1440,390}.png` |
| Spec | `docs/DECK_GENRE_UX_SPEC.md` (in the PR) |
| PR description | PR #145 body; it links the same spec and mockup. No Figma. No separate issue. |

Mockups exist, so I compared against them directly rather than falling back to the design system.

## How I ran it

PR head checked out in this worktree. Real FastAPI app on `FakeRedis` (same recipe as the tester's note), seeded decks: empty, partial (1 archetype), set (typical), full (4 archetypes, 5 themes incl. a custom one, 5 kindred incl. a custom one), a legacy phantom-midrange deck, and scratch decks for editing. Headless Chromium via Playwright at 1440, 768 (touch), 414 (touch), 375 (touch). Computed styles diffed between mockup and live page.

Limits: Deep Review read was checked with a **stubbed** `/api/decks/*/review` response (no real review in the fake store). The on-screen keyboard was **simulated** with a short viewport (375x440, 414x470), not a real device. Chromium only.

## State-by-state comparison (all four requested states)

| State | Result | Evidence |
|---|---|---|
| **Empty** (new deck) and **legacy phantom-midrange deck** | Match. Placeholders, 0/4 · 0/5 · 0/5, quick picks, hints and header (no strip) are all as spec. The legacy deck is indistinguishable from empty. | `p-empty_1440-mock-vs-impl.png`, `p-empty_390-mock-vs-impl.png`, `state-empty-1440.png`, `state-legacy-empty-1440.png` |
| **Partially filled** (1 archetype, others empty) | Match. Placeholder flips to "Add another…", quick pick for the used archetype is removed, header strip shows one chip. | `state-partial-1440.png` |
| **Set / typical** (2+2+2) | Match. Amber primary, cyan secondary, outline themes/kindred; strip order archetype, kindred, theme. | `p-set_1440-mock-vs-impl.png`, `p-set_390-mock-vs-impl.png` |
| **Fully filled** (4 / 5 / 5, long + custom) | Match. Counts turn amber, quick picks hide, cap placeholder "4 of 4 · remove one to add", dashed custom chips, long labels wrap per chip. | `p-full_1440-mock-vs-impl.png`, `p-full_390-mock-vs-impl.png` |
| **Editing** (dirty, autocomplete open, promote, remove, group add) | Dirty bar reads "UNSAVED · GENRE", header only updates after Save, reload persists. Deviations D1 to D7 live here. | `state-editing-1440.png`, `state-editing-dirty-1440.png`, D-figures below |
| **Deep Review read** | Renders as spec ("computed from the cards", `%`, ` · stale`, Open Review) and is hidden with no review. Stub-verified. | `state-review-hint-1440.png` |

Token-level parity (mock vs impl, computed): font family/size/weight/tracking/transform, colour, bg, border, padding, height, radius all identical for label, count, picker, input, primary chip, secondary chip, ☆, ×, quick pick, hint, section title and blurb. The only measured difference is field height (163px mock vs 137px impl) and that is the Review read line the mock shows and the impl correctly hides.

Contrast (measured, AA): label 15.7, count 9.2, hint 6.2, placeholder 6.2, amber chip 6.7, cyan chip 5.3, ghost chip 9.4, custom chip 5.8, strip chip 4.8. One miss: D9.

Copy, labels, placeholders, hints, iconography (★ ☆ × +): all match the spec text exactly.

## Deviations

Severity key: **blocker** breaks the feature or corrupts data. **should-fix** real defect, fix before or right after merge. **nit** polish.

### D1. should-fix: Search highlight splits the option label onto separate lines
`<mark>` is a child of an `li` that is `display:flex; flex-direction:column` (hud.css ~L1928). Every text run and the `<mark>` become separate flex items, so "Crime / Heist / **Noir**" renders on two lines and "**Spel**lslinger" renders as "Spel" over "lslinger". **The mockup has the same defect** (see `search_1440.png`); it was my bug and the port faithfully copied it.
- Figure: `d01-mark-splits-label.png` (mock left, impl right)
- Fix: wrap the label in `<span class="hud-tagopt-label">` (inline) inside the `li`; keep the column layout on the `li`, put `<mark>` inside the span. Same fix in `genre.js` so the mock stops lying. `hl()` in `static/js/deck_genre.js` is the only producer.

### D2. should-fix: Enter on a partial query picks the wrong archetype
On "spel" the active row is **Ramp / Big Mana** (description contains "spells"), not Spellslinger. Enter silently sets Ramp as primary (verified: chips `ramp ★`). Cause: ranking is correct per group, but the active row is the first enabled row *across* groups, and "Game plan" sorts before "Engine / mechanic". Spec §4 says the first enabled option is the best match; groups break that promise.
- Figure: `d02-spel-enter-picks-ramp.png`
- Fix: when a query is present, activate the best-ranked option globally (lowest rank, then group order), not the first in DOM order. Alternatively order groups by their best rank while a query is active. Add a test: query "spel" + Enter yields `spellslinger`.

### D3. should-fix: Typing a duplicate gives no feedback
Type "elf" with Elf already chosen, press Enter: nothing happens. The row is greyed ("already added") but never active, so Enter is a no-op; `add()`'s flash + "X is already added" announcement is unreachable. The live region keeps its previous text ("Added Elf"), which is now wrong. Same for archetype and themes. Spec §4 promises a 700ms flash on the existing chip and a polite announcement.
- Figure: `d03-duplicate-no-feedback.png`
- Fix: let a duplicate row be selectable-but-inert: on Enter, call the flash + `say(label + " is already added")` path and keep the list closed. Also clear/replace the live text on every keypress path so stale messages never persist.

### D4. should-fix: After ☆ "make primary", keyboard focus lands on the new primary's × (Remove)
Verified: `document.activeElement` is "Remove Tokens / Go-Wide". The primary chip has no ☆ (its ★ is a non-focusable span), so the code focuses its remove button. A keyboard user who presses Enter/Space twice deletes the archetype they just promoted. Destructive control under the cursor after a constructive action.
- Figure: `d04-focus-lands-on-remove.png`
- Fix: keep focus on a neutral target: make the primary's ★ a focusable `button aria-pressed="true" aria-disabled="true"` (also fixes D8), or return focus to the input. Announcement already works ("X is now primary. Y moved to secondary").

### D5. should-fix: × and ☆ hit areas are 11x12px (desktop and **tablet**) and 24x24px (phone)
The touch bump is `@media (max-width: 640px)`, so a 768px tablet (touch) gets the desktop 11x12 targets (fails WCAG 2.5.8 AA 24px minimum, and is miserable by finger). On the phone it reaches exactly 24x24 (passes AA, below the 44 recommendation), and ☆ is the *only* way to change the primary. Chips wrap with the × of one chip next to the ☆ of the next.
- Figures: `d05-touch-targets-768.png`, `d05-touch-targets-375.png`
- Fix: key off input type, not width: `@media (pointer: coarse)` target 2.75rem via padding or an `::after` hit area (keeps the 1.75rem chip height). On fine pointers grow to at least 24x24 with the same pseudo-element trick; no visual change.

### D6. should-fix: Phone autocomplete can open off-screen / under the sticky save bar
The listbox is `position:absolute` under the field and never scrolls itself into view. At 375x812 the Kindred list spans y 764 to 817 (viewport 812): clipped, and it floats over the sticky save bar so Save/Discard are hidden while it is open. With a simulated keyboard (375x440) the Archetype list is 156px tall and the Themes list overlays the save bar. Theme/archetype lists (up to 20rem) are worse.
- Figure: `d06-phone-dropdown-vs-keyboard-savebar.png`
- Fix: on open (touch only), `scrollIntoView({block:'start'})` the field with the sticky-bar offset; cap list height to `visualViewport.height - fieldBottom - savebarHeight` and flip above the field if it won't fit; or lift `.hud-picker-list` z-index below the savebar and let it scroll. No dropdown overflow at 375/414/768 horizontally (clean).

### D7. should-fix: Typos become custom kindred silently; the only cues are a dashed border and a `title`
"drgon", "wizzard", "undead", "fairy" (catalog has "Faerie") commit as custom chips on Enter because the "+ Add "drgon" as a custom creature type" row is the active row when nothing matches. The chip cue is a dashed border plus `title="Custom: not an official creature type"`. `title` does not exist on touch, is not read by most screen readers, and the SR label is just the text. There is no "did you mean". A 32-char custom theme truncates at 375px with no way to read it.
- Figure: `d07-custom-typo-feedback.png`
- Fix: (a) when there is a near-match (edit distance 1 to 2, or alias), show "Did you mean Dragon?" as the first row and make *it* active, custom row second; (b) add `<span class="hud-sr">Custom, </span>` to custom chips; (c) show a small visible "custom" micro-label in the listbox create row on phone; (d) add `aria-label` including the full text for truncated chips.

### D8. nit: Primary-archetype affordance is a bare ☆ glyph with a `title`
Discoverable on desktop (hover title, amber ★ chip, hint "first is the primary (★)"), thin on touch: no tooltip, hint says ★ while the control is ☆. Not wrong; it is the least obvious interaction in the section.
- Fix: phone hint copy "Tap ☆ to make it primary." and a visible "Primary" text on the amber chip at 640px and below; consider aria-pressed per D4.

### D9. nit: ☆ glyph contrast is 4.44:1 on the cyan chip (AA text needs 4.5)
- Figure: `d09-focus-rings.png` (also shows the focus ring: visible, 1px amber, but tiny on an 11px target; grows with D5)
- Fix: `.hud-chip-star:not(.is-on) { color: var(--text-2) }`.

### D10. nit: Party / Outlaws disabled with the reason only in `title`
At 2 chips Party (+4) and Outlaws (+5) are disabled; the explanation ("needs 4 free slots, 3 left") is a `title` on a disabled button, which never fires on touch and often not on hover.
- Figure: `d10-kindred-more-375.png`
- Fix: use `aria-disabled` + a visible one-line note under the quick picks ("Party needs 4 free slots"), or leave the button enabled and explain on tap.

### D11. nit: Backspace on an empty input removes the last chip with no undo
Fast, power-user friendly, spec-sanctioned. On phone soft keyboards it is easy to hit by accident, and removing the primary promotes the next archetype silently apart from the live region.
- Fix: keep, but add an inline "Undo" (3s) next to the count, or only enable on fine pointers.

### D12. nit: About 28 tab stops through the Genre section
Every quick pick is a tab stop (6 + 6 + 10 + More). Keyboard order itself is correct (chips, input, picks, next field) and has no traps.
- Fix: roving tabindex on each quick-pick row (one stop, arrows move), per the ARIA toolbar pattern.

### D13. nit: Custom text accepts any punctuation
`<b>x</b>` and `Bob's` are accepted as custom kindred (rendered escaped, so no XSS). Fix: restrict custom kindred to letters, spaces, hyphens, apostrophes server-side (400 with a reason), keep themes free-text.

## Invalid kindred input: what happens today (summary)
| Input | Result |
|---|---|
| "elves", "wolves", "mice", "dwarves", "werewolves", "foxes" | Canonicalised (Elf, Wolf, Mouse, Dwarf, Werewolf, Fox) with a "from “elves”" note. Good. |
| "vampir" | Vampire first, custom row second. Good. |
| "fairy", "drgon", "wizzard", "undead" | Custom chip on Enter (D7). |
| "elf" with Elf present | Silent no-op (D3). |
| 40 chars | `maxlength=32` clamps to 32, accepted as custom. |
| 6th item | Limit note in the list, never reaches a 400 from the UI. |

## Mobile layout verdict

**Pass. Ship-able on layout; fix D5 and D6 for touch quality.**
- No horizontal overflow at 375, 414 or 768 (`scrollWidth == clientWidth` on all; no element in `#j-genre` outside the viewport). Full state, empty state and the expanded Kindred "More…" (35 quick picks, 10 rows) all reflow.
- Tag lists wrap per chip, long labels ellipsise (`.hud-tagchip-text`), the input takes its own row under the chips (`min-width:100%` at ≤640) so chips are never squeezed. 
- Header strip: 4 + `+10` at 768, 2 + `+12` at 375 and 414, as spec. No wrap trouble.
- Quick picks 32px tall on phone, "More…" 32px high. Chips 28px. × / ☆ 24px (D5).
- Autocomplete: right width (field width), correct z-order over following fields, but not viewport-aware (D6).
- Tablet 768: single column, touch device still gets desktop-size × / ☆ (D5).
- Note, app-wide not Genre: at 375 the 55px icon rail leaves a 277px field; fine here, tight for long chips.
- Figures: `mobile-full-375-414-768.png`, `mobile-empty-375-414.png`, `mobile-header-strip-375-414-768.png`.

## Adversarial pass: what held up
- Labels: all three comboboxes have a real `<label for>`; `role=combobox`, `aria-controls`, `aria-expanded`, `aria-activedescendant`; group is labelled and described by the hint; live region announces add / promote / remove; primary chip carries hidden "Primary:".
- Focus order is natural; no custom traps; Esc closes the list; list does not open on plain focus.
- Legacy phantom-midrange deck renders as empty (no strip, 0/4). Save, reload and strip re-render all correct.
- Console: no JS errors in any run.

## Not done / needs another pair of eyes
- Real device keyboard behaviour (D6 is simulated).
- Review-read hint with a real saved Deep Review (stubbed).
- PR currently reports `mergeable: CONFLICTING` against main (was clean when the implementer finished). Not a design issue; flag for the aggregator/author.

## Files
All figures are in this folder (`docs/design/genre/review/`). Repro server script lives in the task scratch dir, not committed.
