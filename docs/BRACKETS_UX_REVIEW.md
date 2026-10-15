# BR-7: Designer review of the live bracket UI

**Reference only. Do not merge this doc to main** (repo convention for designer docs).
The small CSS/template fixes in the same PR are meant to ship.

Reviewed: prod v0.4.44 (6d4480b) at `tower:8020`, on Holy Hellfire (`archidekt:417707`), at 1440×900 and 390×844 in headless Chromium (Playwright).
States were driven through the real journal API, then re-evaluated by the live bracket-worker:

| State | How | Result |
|---|---|---|
| Unset | the deck as it is (target `null`) | quiet banner, "Est. B3" chip, no flags |
| Conflicts | `PUT target_bracket: 1` | 2 hard (game_changers, two_card_combos) + 1 advisory, 7 cards flagged |
| Clean | `PUT target_bracket: 3` | `is-ok` banner, "● No conflicts", 0 flags |

The journal was backed up first (`COPY` to `backup:br7:417707`), then restored and the backup deleted.
A forced re-eval came back with the original input hash `5484f11c70c7`, target `null` and 0 conflicts.

## Verdict: PASS, with 8 fixes (all shipped in this PR)

The direction holds up live. Severity always uses shape and colour together, the target chip stays neutral, and hard rows outrank advisory ones.
Flags measured clean at both widths:
- 4px bar plus frame, with no layout shift.
- Every flagged tile is the same height as its row neighbours (299px at 1440, 481px at 390).
- The jump links focus and pulse the card.
- Reduced-motion uses an outline instead of the pulse.
- At 390 the explanation tray has 0 elements past the viewport.

The fixes below are what broke or read badly in the live run.

| # | Where | Problem (live) | Fix | Type |
|---|---|---|---|---|
| 1 | Journal anchor nav + every `#j-*` deep link | **Broken.** `.hud-form-section` is `display: contents`, so `#j-power` has no box. Clicking "Power & bracket" in the anchor nav did nothing (scrollY 0 → 0). The banner's "Set a target bracket" and "Raise target to 3" links landed at the top of the Journal with the bracket row 1045px down (1440) or 1647px down (390). The anchor highlight never moved off "Vision" because the IntersectionObserver watched box-less elements. | `goToSection()` scrolls to the section's `<header>`, offset by the sticky nav. It's used by anchor clicks and by an initial `location.hash`. The observer now watches the headers. Smooth scroll is skipped under reduced motion. | template JS |
| 2 | Journal power tier rail, 390 | "OPTIMIZED" ran into "CEDH". This was the pre-existing issue noted in the BR-6 handoff. | The last tier label is right-aligned at ≤640px. | CSS |
| 3 | Banner conflict rows, 390 | The `HARD RULE` / `ADVISORY` chips stretched into full-width bars once the row stacked, and read like section headers. | `justify-self: start` on the stacked chip. | CSS |
| 4 | Banner, two-card-combo row | "**Two-card combos.** 2 two-card combos (…)" stuttered. | The bold lead now also matches a count-prefixed message: "**2 two-card combos** (…)". | renderer |
| 5 | Banner stat block, no target | "target —" read as truncated. | Now reads "no target". | renderer |
| 6 | Banner sub-line, no target | "nothing is checked" sat next to a power check showing "target 8 · within ±1.5". | Now reads "No target bracket, so no bracket rules are checked." | copy |
| 7 | "How this was decided" tray | `estimated_bracket_reason` repeated the summary as a second paragraph, so the Game Changers list appeared 3 times. The Value column showed "35" and "599.11" while the summary says "35%" and "$599.11". | The duplicate paragraph is dropped. Values now show units (`35%`, `$599.11`). | renderer |
| 8 | Flagged-card caption + Journal hint | At 1440 the caption could wrap to start a line with "· Over Bracket 1". The Journal hint began a sentence with the bare swatch ("▬ marks it on the scale"). | Uses a `&nbsp;` before the `·`. The hint now reads "(computed), marked ▬ on the scale." | template |

Before → after (live prod vs. a local server on this branch, using a snapshot of prod Redis with target 1):

- Conflicts at 390: `screenshots/brackets/br7/conflict_deck_banner_390.jpg` → `screenshots/brackets/br7/after_conflict_deck_banner_390.jpg`
- Journal at 390: `screenshots/brackets/br7/conflict_journal_390.jpg` → `screenshots/brackets/br7/after_conflict_journal_390.jpg`
- Combo row at 1440: `screenshots/brackets/br7/conflict_deck_banner_1440.jpg` → `screenshots/brackets/br7/after_conflict_deck_banner_1440.jpg`
- Deep link at 390, after: `screenshots/brackets/br7/raise_landing_390.jpg` (bracket row in view, `?bracket=3` preselected, savebar "UNSAVED · BRACKET 1 → 3")
- Reference shots of the unset and clean states: `screenshots/brackets/br7/unset_*`, `screenshots/brackets/br7/clean_*`

Measured after the fixes on the local server:
- Anchor click moves scrollY 0 → 853 (1440) and 0 → 1319 (390), and the "Power & bracket" anchor highlights.
- The Raise-target landing puts the bracket row at 192px (1440) and 328px (390).

## Not changed (deliberately)

- **Green "▼ −1.5 · within ±1.5".** The BR-D spec says delta colour means in or out of tolerance, never the sign. It reads fine once you know that, so it stays as specced.
- **Power target shown on a deck with no bracket target.** This is correct: power and bracket targets are independent, which is the Journal's own copy. Fix 6 removes the contradiction in the wording.
- **Card-name buttons are 22px tall** (`--chip-h`) with a 4px gap. That's marginal against WCAG 2.5.8 at 390. It's a kit-wide `hud-cardlink` token, so it's out of scope for a bracket review. Raise it as a HUD kit card if it matters.
- **List view at 390.** The table is 714px inside its `hud-table-wrap` (`overflow-x: auto`), with no page overflow. This is pre-existing and unchanged.

## Follow-ups for the evaluator (not design; BR-4 copy, no rule changes)

These strings come from `analytics/bracket_eval.py`, so I left them alone per the card's non-goals:
- Advisory message: "Estimated Bracket 3 (Upgraded): plays above target Bracket 1" repeats "plays above target" after the title.
  - Suggest: "Estimated Bracket 3 (Upgraded) vs target Bracket 1 (Exhibition)."
- Summary: "… -> Bracket 3 (Upgraded). our estimate 6.5 -> power 6.5/10 vs target 8."
  - It has a lowercase sentence start, ASCII arrows, and a doubled number.
- Signal `our_power_estimate` has effect "power estimate", which only echoes the label.
- The deck's VALUE tile says "no price data" while the price signal reports $599.11. The two use different price sources, which is worth reconciling.
