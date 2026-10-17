# CR-D adversarial UX pass

Card `t_54629bee`. Method: I played a hostile user against `review.html` (keyboard only,
phone width, slow network, wrong deck type, a user who closes things mid-flight), then
probed it in headless Chromium: 18 states × digital/physical × 320/390/768px for horizontal
overflow, a 44px touch-target sweep at 390px, a Tab-order walk, a `prefers-reduced-motion`
animation count, a long-name stress test, and a full scripted live run with `j` / `a` keys.

Result: **13 defects found and fixed in the mockup/spec, 8 open items for the coder or
Andrew.** Nothing here is a blocker for building.

## A. Found and fixed

| # | Defect | How I found it | Fix |
|---|---|---|---|
| A1 | `HUD.openModal` does not move focus into the dialog, trap Tab, or return focus. After opening, `document.activeElement` was `<body>` and Tab walked the page behind the scrim. | Tab-order probe | `CardReview.enter(modal)`: focus the title, trap Tab, restore focus on close. Verified: focus lands on `H3.hud-panel-title`, 40 Tabs stay inside, `j`+`a` work. Spec 9.2 says to put it inside `HUD.openModal` (benefits card detail too). **This is an existing kit gap, not just ours.** |
| A2 | Streaming repaint replayed every entrance animation on every token (each argument flashed), and dropped focus and scroll. | Reading the live run | Keyed repaint (`data-k`): elements already on screen get `animation: none`; focus and scroll restored. Verified mid-run: existing args report `animation: none`. |
| A3 | The screen-reader live region sat inside the repainted body, so the verdict announcement died with the running→verdict repaint (`live: undefined` at the end of the run). | Live-run probe | Region moved to the dialog shell (`data-cr-live`), outside the repaint. |
| A4 | Four amber buttons stacked in the replacement list. When everything is primary, nothing is. | Looking at the 1440 shot | Only the current row's pick button is amber (first row, then the `j`/`k` cursor). Others neutral. |
| A5 | Menu actions as `<button>` rendered with browser-default grey backgrounds (`.hud-menu-item` is styled for `<a>`). | Screenshot | `button.hud-menu-item` reset in `card-review.css`. |
| A6 | `R` keycap inside the amber primary button was unreadable (`--text-2` on `--signal`). | Screenshot | `.hud-btn--primary .hud-kbd` uses `--text-on-signal`. |
| A7 | `On list` / `Queued` chip stretched to full column width and mismatched the button height. | Screenshot | Centered, 2rem tall. |
| A8 | `.is-skipped` dimmed the whole row at 45%, including `Restore`, below AA. | Contrast reasoning | Dim only thumb + body; buttons stay full strength. (Body at 45% is deliberate "set aside", same as Tune's dismissed rows.) |
| A9 | Pending stepper labels used `--text-4` (~2.3:1). | Contrast reasoning | `--text-3`. Pending vs done is carried by the bar and the `✓`, not by dimming the label. |
| A10 | Touch targets under 44px on phone: `← Card detail` and `Close` (32px), `Oracle text` summary (14px). | 44px sweep at 390px | 44px minimums in the sheet. Sweep now returns `[]`. |
| A11 | The failed-ruling state still said `JUDGE · Deliberating` in amber. | Screenshot of `err-badruling` | Reads `JUDGE · No ruling`, not active. |
| A13 | **Keyboard menu is broken when many cards share one `.hud-menu`.** `HUD.initMenus` binds the panel's key handler once *per trigger*, so with 12 triggers on one panel, `ArrowDown` fires 12 times and lands back on item 0 (focus stayed on "Card detail"). | Keyboard walk: `⋯` → `Enter` → `ArrowDown` → `Enter` opened Card detail instead of Review | Mockup renders one menu per card. For the app, either do the same or guard the binding in `initMenus` (`if (panel.__hudKeys) return;` before attaching panel listeners). Verified after the fix: `ArrowDown` lands on **Review card**, `Enter` opens the review. Spec 9.2 #4. |
| A12 | Empty replacements header read "0 like-for-like · 0 function change"; a stray "02" section number with no "01". | Screenshots | Both removed. |

## B. Attacks that held up (no change needed)

- **Horizontal overflow:** none at 320, 390 or 768px, in any of 18 states, either deck kind.
  The probe's only hit is the animated indeterminate track bar, which is clipped by its own
  `overflow: hidden` (false positive).
- **Long card name** (79 characters) with a 180-character rationale at 390px: wraps, no overflow.
- **Reduced motion:** `document.getAnimations()` is empty in the running state.
- **Close mid-run (Esc):** modal closes; the script keeps running (D6, server job in the real app). Focus returns to the card that opened it (verified: `R` on a focused frame, `Esc`, focus back on that frame).
- **Wrong deck type:** flipping Digital/Physical re-renders the scope line, action labels,
  ownership facts and the confirmation strip. No digital string leaks into physical.
- **Colour-blind:** ruling = word + colour, side = label + bar, kind = glyph + word, ownership = glyph + word.
- **Commander:** blocked with a reason; the entry button is `aria-disabled`, not hidden.

## C. Open items

| # | Risk | Owner | Recommendation |
|---|---|---|---|
| C1 | **"Swap into deck" has no write path.** The app can't write to Archidekt. A user will assume it did. | **Andrew** (the one question in the spec) | v1 queues onto the Tune change list; the button hint and the confirmation strip say so. Spec 8.4. |
| C2 | **Two scores for one card.** Tune shows a "weak score" (0 to 1, higher = weaker). This shows "strength" (0 to 100, higher = stronger). The same card can look contradictory. | backend + Andrew | Derive strength from the same inputs, or at least keep the bands consistent (Tune "weak 0.67" should land in Review "Weak"). Don't ship two unreconciled numbers. |
| C3 | **Arguments can cite numbers the evidence doesn't contain.** A model saying "12%" when EDHREC said 14% is the fastest way to lose trust, and the UI shows both side by side. | backend | Validate numeric claims against the evidence before streaming (spec 8.5). |
| C4 | **Pets.** Tune never suggests cutting a pet. Review would happily argue to remove one. | backend + designer | Allowed, with a note; replacements hidden until asked (spec 7.3). Needs the pet flag in the review payload. |
| C5 | **Concurrent reviews.** User starts A, closes, starts B. One oracle, so B queues behind A and the clock lies. | backend | One active review per deck. A second request returns `queued` and the job panel says "Queued behind {card}". Not designed in the mockup. |
| C6 | **Cost.** Each run calls two sub-agents plus a judge. `Re-run` and `Show more` are one click. | backend | Server-side cache by deck fingerprint + card (like RV-3), and a short cooldown on `force`. Cost is already logged under `/api/interactions/runs`. |
| C7 | **Physical, owned-elsewhere replacement.** "In Dawnbringer" still gets `Add to want/buy list`. Arguably right (you'd buy a second copy or rob another deck), but it's a judgement call. | Andrew | Kept as-is; the fact is visible. Revisit if it annoys. |
| C8 | **Right-click hijack.** Overriding `contextmenu` on frames takes away "Open image in new tab". | coder | Don't `preventDefault` when `Shift` is held (spec 2.2). Not exercised by the mockup, so untested. |

## D. Not tested (be honest)

- No real API: transport, SSE reconnect and poll fallback are specified (8.1) but not built.
  `Play live` is a script.
- No screen reader (VoiceOver, NVDA). Roles, labels and the live region are by construction
  and by DOM inspection only.
- No real phone. 390px is a viewport, `hasTouch` emulated; no iOS Safari `100dvh` check.
- Contrast was checked by reasoning against the documented token values, not measured with a tool.
- The grid result marker (spec 2.4) and the pet/basic-land blocked states are specified, not mocked.
