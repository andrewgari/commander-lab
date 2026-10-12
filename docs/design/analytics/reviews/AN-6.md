# AN-6R: Designer review of the live Journal, check-in and Tune

Card: `t_ace127ec`. Reviewed against `docs/design/analytics/UX_SPEC.md` §3.3,
§3.4, §4 and §5 on live prod `http://192.168.50.3:8020` (a74c0ab, v0.4.16),
Holy Hellfire, at 1440×900 and 390×844 (mobile, DPR 2), plus 1024 and 1920
for the Tune breakpoint. I used Chromium over CDP and measured findings in the
DOM.

**Prod data was read-only.** Prod only got GET page loads. The interactive
parts (starting a check-in, `y`/`c`/`j`/`k`, Change, Accept/Undo on Tune) ran
against a local app on the coder's scratch Redis copy (`an6-redis`, copied
from prod), so Holy Hellfire's real journal, review history and tuner state
have no writes from this review.

## Verdict: PASS, with 9 small fixes shipped in this PR

The Journal form, the check-in and Tune all follow the spec's flow and colour
rules. One finding was a real usability bug: on a normal 1440 desktop, every
Tune swap row was too cramped to read (R3). It's fixed here, and none of the
fixes needed a backend change. AN-7 has no fix list to apply.

### What I checked and it holds

| Spec | Result |
|---|---|
| §3.0 tabs, keys 1–5, Tune badge | ok. `Tune 17` = 4 fixes + 12 swaps + 1 pet notice, hidden until the first run |
| §3.3.1 form: 13rem header column, stacks ≤ 900 | ok (208px + fluid; 1 col at 390) |
| §3.3.1 sticky anchor nav highlights section in view | ok (`.jr-anchor`, sticky top 0, `is-on`) |
| §3.3.1 segs are native radios/checkboxes, More… with `aria-expanded` | ok. Exclude = struck through on `--danger-dim` |
| §3.3.1 tier derived, active tier `--signal` | ok. Rail alignment was off, see R5 |
| §3.3.1 money hints "Global default $X · overridden", "Owned cards cost $0" | ok |
| §3.3.1 pet picker combobox (deck list only), chip remove labelled | ok (`Remove Dragon Tempest`) |
| §3.3.1 savebar sticky + `beforeunload` while dirty | ok |
| §3.3.2 header, since row, progress `aria-live` | ok. Downs are `--signal`, never red |
| §3.3.2 evidence lines computed (budget %, over-cap cards, weakest pet) | ok |
| §3.3.2 `y` answers and advances, `c` expands the form inline and focuses it | ok (3 of 8 after `yyy`; `c` focuses `#noBudget`) |
| §3.3.2 Finish → Tune, enabled with unanswered rows | ok (`next` = `/deck/{name}/tune`) |
| §3.4 filter bar: Owned first default, sleeved toggle, caps + source + price age | ok |
| §3.4 D5/D6/D7: keep-free cell, soft-cap justify, hidden hard count, one pet row | ok |
| §3.4 Accept → `--ok-dim` + Accepted/Undo; change list, spend, on-plan, "Pull X from" | ok |
| §3.4 keys `j k a b x h`, ignored in text fields | ok (`jj a k` walked/accepted/moved as specced) |
| §3.4 weak table ≤ 640 keeps Card / Weak score / action | ok |
| §5 contrast on Tune + Journal | 2 misses, fixed (R1, R2). Everything else ≥ 4.5:1 |
| §5 reduced motion | ok (`.hud-swap` transition 0s, 0 running animations) |
| §5 390px: no page h-scroll | ok on Journal, check-in and Tune (scrollWidth 390) |

## Fixes shipped (this PR)

| # | Where | Bug (measured) | Fix |
|---|---|---|---|
| R1 | `hud.css` `.hud-tab-count` | Tab badges ("Identity **4**", "Tune **17**") were `--text-4`, **2.67:1**. | `--text-3` (the D9 AA colour). |
| R2 | `hud.css` `.hud-swap-arrow` | The → was `--text-4`, 2.31:1. | `--text-3`. |
| R3 | `hud.css` `.hud-swaps` container query | **The main one.** At 1440 the Tune main column is 630px (the 20rem change list takes the rest), so the 4-column swap row gave each option **128px**. 12 of 20 option source lines truncated ("OWNED · IN EVERYTHI…", "HASTE AND EXTRA COM…"). Every gapline and hard-cap line wrapped to 62px, with its chip dropped onto its own line. That was the coder's "chips wrap at narrow widths" note, except it also hit the default desktop. | `container-type: inline-size` on `.hud-swaps`. When a row is ≤ 52rem wide, the cut and actions share the top line and the options take the full width below. At ≤ 30rem the actions get their own full-width line. Now each option is **376px at 1440** (345 at 1024); truncations are **1 of 22**; every gapline fits on one 36px line. At 1920 the row is 1166px wide and keeps the original 4-column layout. |
| R4 | `hud.css` `.hud-check-actions` | "✓ Still true" sat 53px further left on the Pillars row (because "Open Identity" is wider than "Change") and on Card changes (a single "✓ Seen"). | A fixed 2-slot grid (7rem / 7.5rem), right-aligned. All 8 rows now share x=1145. On a phone the two slots split the row 50/50. |
| R5 | `hud.css` `.hud-scale-tiers` | The tier rail (`4fr 2fr 2fr 2fr`, 25rem) ended 9px short of the scale, so "Optimized" started 6px left of the 7 button. | The rail is now exactly as wide as the scale's labels (10 × 2.5rem + 9 dividers), offset 1px for the border. Casual / Focused / Optimized / cEDH line up with 1–4 / 5–6 / 7–8 / 9–10 within 1px, at 1440 and 390. |
| R6 | `hud.css` savebar during a check-in | Two sticky bars stacked at the bottom of the viewport, each with an amber primary ("Finish → Tune" and "Save journal"). | While a check-in is open, the journal savebar is sticky only when there are unsaved edits (`.is-dirty`). Otherwise it sits at the end of the form. |
| R7 | `hud.css` Tune change list | `.hud-changes li` (a 3-column grid) also matched the "Pull X from {deck}" moves list, so each move was squeezed into a 20px column and wrapped one word per line (147px tall). | `.tn-moves li` is a plain block now: 26px. |
| R8 | `hud.css` Tune change list | A long source label ("from Uncharted: Lara's Legacy") squeezed the card name to 33px, so "Mithril / Coat" broke across lines. | The source drops under the name (2 columns: op + name/source). |
| R9 | `analytics/tuner.py` + `deck_tune.html` copy | (a) The soft-cap note said "Shown because the gap is big; … the gap is under 0.25", which contradicts itself. (b) The shape-fix advice started with "3 short of the template's 38", repeating the `−3 · 35/38` beside it. (c) The hard-cap line read "hidden · over hard cap $40" next to a red `hard cap` chip, so the same words appeared twice and the chip looked like it was labelling the visible $39.58 buy. (d) The "why" line sat after the hard-cap note, so the reason for the pick ended up below the hidden-buy line. | (a) "Kept owned: it's over your $15 soft cap, and breaking the soft cap takes a gap of 0.25 or more." (b) The advice is just the card list ("Owned, sleeved elsewhere: …"). (c) `1 better buy hidden` plus the chip `over hard cap $40` (danger). (d) The why line goes straight under the gap verdict. Accept/Buy instead also get an `aria-label` naming the card ("Accept: Kaalia, Zenith Seeker"). |

All 9 are presentation only: they don't change any score, threshold or API
field. `tests/test_deck_tune_ui.py` now asserts the new hard-cap chip text.

Screenshots (in `reviews/an6r/`):
- Before: `tune-top-1440.png`, `tune-swap-soft-hard.png`, `tune-390-swap-soft-hard.png`, `checkin-1440.png`.
- After: `after-tune-swaps-1440.png`, `after-tune-softcap-1440.png`, `after-changes-1440.png`, `after-checkin-1440.png`, `after-checkin-390.png`, `after-tune-swap-390.png`.

## Answers to the coder's questions

### AN-6 (t_d2847d10, comment 675)

1. **Score scale + `MIN_UPGRADE 0.05`.** Approved. Scoring the cut and the
   options on the same three inputs is what makes `gap` honest, and 0.05
   stops "+0.01 sidegrade" noise. Add `MIN_UPGRADE` to the spec's constants
   list next to `GAP_WORTH` and `GAP_SOFT_OVERRIDE` (done in TUNER.md
   already, so no spec change needed beyond this note).
2. **Keep yours in the empty buy cell.** Approved. It's the right home: the
   cell already means "the buy you're not getting", and the `keep yours`
   (ok) gapline gives the number. Keep both.
3. **Hard-cap chip `danger`.** Correct. §2.2 wins over my mock; the mock's
   `neutral` was a slip. R9c also moved the "$40" into the chip so it reads
   as one statement.
4. **Apply hidden, Copy as text + "Pull X from {deck}".** Approved. That is
   exactly §3.4's "hide, don't disable". The physical-moves list is a good
   addition, and R7 fixes its layout.
5. **"See N owned" lists sleeved-elsewhere and filters swaps.** Approved.
   Free cards first, then sleeved elsewhere, is the right order. The number
   on the button ("See 128 owned") is big, but it's honest: it's the
   filtered candidate count, and the chips cap at 12 + "N more".
6. **Role rule (ramp/draw/removal/wipe cuts need a same-row add; they pick
   first).** Approved. Otherwise the tuner would trade a removal spell for a
   dragon and make the shape fixes worse.
7. **Tab badge from the cached last run, hidden until first run.**
   Approved. That's better than computing on every render. The spec says
   "hide when 0", and "unknown" hides too.
8. **Decisions last until the next finished review.** Approved. That's what
   "won't return this review" means.
9. **One Gemini call per deck, numbers inserted by code, cost logged.**
   Approved. R9a only changes the code-written tail, never the LLM sentence.

### AN-5 (t_1bce112e, comment 673)

1. **Finish → Tune.** Done by AN-6: the button reads "Finish → Tune" and
   `next` is `/deck/{name}/tune`. Verified.
2. **Tune tab (key 5).** Done by AN-6. Verified.
3. **`.hud-picker-list` listbox.** Approved. Surface-2 plus the 2px signal
   indicator matches the engine list and the check-in rows.
4. **Change moves the real form section inline.** Approved. One copy of the
   form is the right call. The Save change / Cancel pair inside the row is
   clear, and R6 removes the competing sticky bar.
5. **Pillars row → "Open Identity".** Approved. R4 fixes the button
   misalignment it caused.
6. **Global cap defaults $30/$10, no UI yet.** Fine for now. Don't add a
   global-defaults editor to the Tune filter bar: the caps line already
   links to `journal#j-budget`, and global defaults are a set-once thing.
   If Andrew wants one, it belongs on a settings page (new card, not AN-7).
7. **y/c/j/k hint hidden ≤ 640.** Approved. Same rule as the graph toolbar.
8. **Cheapest priced printing when the default has no USD.** Approved.
   It's a budget tool, and the cheapest real copy is the honest number.

## Notes, not fixes (no action unless Andrew wants it)

- N1. **On-plan in the check-in "since" row.** Locally (scratch Redis, no
  embedder) it reads `98% → 61%` with 0 card changes, because the last
  review's snapshot came from prod, where the embedder is up, and the
  current value was computed without it (`fit.status: embedder_unavailable`).
  On prod both numbers come from the embedder, so this won't show there. If
  it ever does, the fix is backend: don't diff on-plan when either side was
  computed in fallback. That's not a UI card.
- N2. **The picked option is marked only by a 2px top bar plus the
  verdict chip.** That's per spec, and the chip says it in words. The new
  `aria-label` names the card for screen readers. A "picked" tick would be
  more explicit, but it would be a new kit element, so I'm leaving it.
- N3. **The soft-cap justification is long on a phone** (6 to 8 lines,
  coder's note). It's the "why you're not getting the better card"
  paragraph, so it earns its space. R9a made it shorter and no longer
  contradictory.
- N4. **Pet chips in the journal use `--font-card` (EB Garamond).** That's
  deliberate: card names are always the card face. It's not a font
  fallback.
- N5. **Long owned-source deck names still truncate**, with the full text in
  `title`. That's 1 of 22 at 1440 now (was 12 of 20). Same call as AN-4R
  N5: the deck names are long by nature.

## Verify

```
Tune, 1440×900:  .hud-opt width ≈ 376px; .hud-opt-src truncated ≤ 1;
                 every .hud-gapline height 36px; scrollWidth == innerWidth
Tune, 1920:      .hud-swap keeps 4 columns (cut | → | options | actions)
Tune, 390:       scrollWidth 390; actions full-row; chips left-aligned on wrap
Tune change list: #moves li height ≈ 26px; card names never break
Check-in, 1440:  first .hud-check-actions button left edge identical on all 8 rows
Journal power:   tier rail spans == scale label spans (±1px) at 1440 and 390
Contrast:        0 text nodes < 4.5:1 on /tune and /journal?review=1
```
