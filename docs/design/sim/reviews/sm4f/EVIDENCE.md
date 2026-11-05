# SM-4F acceptance evidence (tester, t_6fe8e69a)

**Builds compared.** The before build is `4a0c053`, the commit just before #262. The after build is `f715cc5`, current main, which includes #262 F2, #263 F5, #264 F1/F3/F6/F7 and #265 F4/F8/F9/F10.

**How it was run.** Each build ran under local uvicorn against a scratch Redis copy of the prod snapshot, so no prod state was touched. Screenshots were taken with Playwright Chromium at 1440×900 and 390×844.

**Data.**
- F1 uses RotK (`archidekt:5054882`), a 20-game random-pool report.
- F3 uses Holy Hellfire (`417707`), where T5 starvation is 15% in the sim vs 41% in the math.
- The saved docs predate #264, so for the after build I recomputed `comparison` with the new `sim_diff.build_sim_comparison`. This is what a fresh run would save.
- F4 uses a failed job (`sm4f-failed-job`) seeded in Redis and restored from `localStorage['simJob:archidekt:417707']`.

| ID | Verdict | Before (4a0c053) | After (f715cc5) |
|---|---|---|---|
| F1 | **PASS** | Six land-drop rows show `0% vs 96…23%` with ▼ Below math, T5 starvation `0%` and Mana used `0% vs 100%` have chips, and the strip tile reads `0%`. 8 Below-math chips, no `sm-unrec` panel in section 02. | Section 02 is the "Not recorded for random pods" panel only, with 0 Below-math chips. The strip shows `n/a · not recorded in random mode` and the win-rate foot `95% CI 11–47%` (F6). |
| F2 | **PASS** (390). The bug is 390-only; 1440 is unchanged as expected. | At 390, `td` height is a fixed 32px. 81 of 168 cells overflow, and the Seat-3 tag draws over Seat 4 and "Cautious" over "all opponents have lost". | Cell heights are 35 and 52px, 0 of 168 cells overflow, and the screenshot shows no visual overlap. |
| F3 | **PASS** | T5 starvation `15% vs 41%` shows amber ▼ BELOW MATH, and Commander `T5.73 vs T4` also shows ▼ BELOW MATH. | T5 starvation shows green ▲ BETTER THAN MATH, Commander shows ▼ LATER THAN MATH, and Mana used `57% vs 74%` shows ▼ BELOW MATH. |
| F4 | **PARTIAL** (see note) | Nothing renders: no job card, the tray stays closed and localStorage is wiped. | The FAILED card renders with a `--danger` bar (rgb 238,106,97). The tray re-opens with Specific mode, seats 2–4, 5 games, the notes and the focus chips restored, and the error shows under the notes (`#simError`). The card survives a reload. |
| F8 | **PASS** | The clock counts from page load (`0:03` for a job created 3.5 min earlier), the ETA is a constant (`about 1 min left`), queued uses `data-phase=sim` with a static track, and focus stays on BODY. | The clock counts from `created_at` (`3:34`), the ETA is computed (`about 3 min left`), queued uses `data-phase=queued` with a `job-slide` animation, and submit moves focus to `#job`. |
| F9 | **PASS** | After submit, `notes` and `runBtn` stay live. | After submit, mode, games, focus, notes and the Run button are disabled. The visible `specificopp*` combobox inputs are inside the collapsed tray and cannot be filled. |

**F4 note.** The failed card still uses the generic line "Your saved report stays exactly as it is until this run finishes. You can leave this page." (`deck_sim.js:341`). SM-4R F4 lists this copy as wrong for the failed state. UX_SPEC §5 has no failed-specific body line, so this is a minor copy gap and not a spec violation. The behavior is otherwise fixed.

**Not in scope, seen in passing.**
- The report-head chip row renders the literal text `undefined` on both builds. In `reportHead` (`deck_sim.js:346`), `focusLine` is used before its `var` assignment. This is pre-existing and came in with #254.
- On the after build, the failed card's clock shows `0:00`.

**UX_SPEC.md.** No diff from `0d01a56` (the SM-4R baseline) or from `4a0c053` to `f715cc5`. PASS.

**Files.** `F{1..4}-{before,after}-{1440,390}.png`. F8 and F9 use `F8F9-sm4f-{run,q}-{8301=before,8302=after}-1440.png` and `F9-after-submit-{8301,8302}-1440.png`.
