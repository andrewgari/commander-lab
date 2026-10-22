# SM-4F: SM-4R findings F1–F19 re-checked against main

Auditor: secretary · Task `t_eec0f785` · 2026-10-21, 10:15–10:30 EDT · Read-only: no code changes, no commits.
Source review: [`SM-4R.md`](SM-4R.md) (PR #225), evidence in [`sm4r/`](sm4r/).

## Build under audit

- **Main audited:** `4ae8e5b16b7137107366c8cdbea52b917b7231ac` (`fix(sim): sanitize opponent pool … (SM-4F / F5) (#263)`).
  The audit started at `45e03ac` (#262). #263 merged mid-audit, so I re-pinned to it. All file:line refs below are at `4ae8e5b`.
- **Is :8020 serving that SHA? No.** `/api/version` = `0.4.157`. I hash-compared the assets served at `http://192.168.50.3:8020` with git blobs:

| Asset | Served md5 | Matches | Same as main `4ae8e5b`? |
|---|---|---|---|
| `static/js/deck_sim.js` | `2539688f…` | `4a0c053` = `45e03ac` | **No** (missing #263's one-line `comboRows` change) |
| `static/js/deck_sim_report.js` | `bc9b1796…` | `4a0c053` … `4ae8e5b` | Yes |
| `static/css/hud.css` | `244c9548…` | `4a0c053` (#261) | **No** (missing #262's two `height: auto` rules) |
| Backend `GET /api/decks/417707/sim` | — | pre-#263 (pool still lists `user_417707` as a precon, names still carry `[SET] [YEAR]`) | **No** |

  In short, :8020 is running about `4a0c053`, which is main minus #262 and #263. Every other finding's code is byte-identical
  between :8020 and main, so the runtime checks are valid for main. F2 and F5 are the exceptions, and their rows explain how they were checked.
- **Runtime method:** a Chromium session against :8020 at 1440×900 and 390×844, plus `curl` on `GET /api/decks/{id}/sim`.
  Decks: Holy Hellfire (417707, saved **Specific** run) and The Return of the King (5054882, saved **Random** run).
  For job states (F4/F8/F9) a fetch wrapper in my browser only faked `GET …/sim/jobs/{id}` and rejected **every non-GET** request client-side.
  Nothing was POSTed, no simulation started, and the localStorage keys were cleared afterwards.

**Independent cross-check (spoton, 10:20–10:27 EDT):** I re-ran these checks on :8020 myself and they agree with the rows below: F1, F2 (injected rules: 75/140 → 0/140 log cells, 6/28 → 0/28 tempo cells overflowing), F3, F4, F7, F8, F9, F10, F11, F14, F16 (Sort select), F19, plus the F5 symptom on the old build and the N1 literal "undefined".

## PR → finding map

| PR | What it did | SM-4R findings touched |
|---|---|---|
| #250 | User-defined Standard pod (`podPanel`, `/api/sim/standard-pod`), UX_SPEC §4.2 updated to 1–5 decks | **F19 fixed**. F10 spec half (UX_SPEC.md:95 now says "1 to 5 opponents") |
| #251 | Random pool toggle (All / Mine) + opponent count | None (the F5 counts copy was reworded, no defect) |
| #253 | `deck_sim_report.js` diagnostics block inserted above sections 01–04 | **None fixed.** Uses `sm-unrec` (`deck_sim_report.js:103`), but only when *telemetry* is missing. The F1 path in `deck_sim.js` is untouched |
| #254 | Focus chips; the notes field moved into `focusSection()` | F9/F13 location moved (`deck_sim.js:207`). **Introduced a regression** (see N1 under F7) |
| #257 | Manage-tab Focus chip editor (`templates/tags.html`) | None |
| #262 | `height: auto` on `.sm-tempo td` / `.sm-log td` at ≤640 | **F2 fixed** |
| #263 | Opponent-pool sanitizer + `comboRows` label order | **F5 fixed** (main only, not deployed) |
| #259 (open, unmerged) | Branch `commander-lab/t_eac061f5-…`. Head `6768ba0` per `gh pr view` at 10:27 EDT (an earlier note cited `577333c`). It touches `static/js/deck_sim.js`, `static/css/hud.css`, `analytics/sim_service.py`, `analytics/sim_diff.py` and `tests/test_sim_4f_fixes.py`, and adds `docs/design/sim/reviews/sm4f/*.png` | Not on main, so it doesn't count here. **Fix tasks must read its diff before starting**: it overlaps t_d02caaf8 and t_01a179a2, and it uses the same `sm4f/` folder name |

## Findings table

| ID | Short title | Status | Evidence (main `4ae8e5b`; runtime result) | Verification | Cause moved | Note |
|---|---|---|---|---|---|---|
| F1 | Random-mode report shows quiet-mode zeros | STILL-PRESENT | `deck_sim.js:343-347` `metricsAvailable()` still tests `!= null`, and RotK's `t5_mana_starvation_rate: 0.0` passes it. The server sends `params.verbose_log: false`, but no `metrics_available` flag exists anywhere in `analytics/` or `app.py`. **Runtime (RotK):** T1–T6 `0% vs 96%…` each `▼ BELOW MATH`, `T5 starvation 0% vs 38% ▼`, tile `Mana used 0%`. | Runtime (:8020) | N | #253 added a second `sm-unrec` (`deck_sim_report.js:103`) for missing telemetry, but sections 02/03 still render zeros. The not-available copy still says "legacy run" (`:398`, `:459`). |
| F2 | Phone game-log cards overlap | FIXED-ON-MAIN | PR #262: `hud.css:3623` `.sm-tempo td {… height: auto …}`, `hud.css:3658` `.sm-log td {… height: auto …}` inside the ≤640 block. **Runtime at 390:** served (pre-#262) CSS gives 75/140 log cells and 6/28 tempo cells overflowing, with seat `td` at 32px. Injecting #262's two rules gives 0/140 and 0/28, with seat `td` at 35.1px. | Runtime (:8020) with main's rules injected (served CSS predates #262) | N | Fixed on main. Re-confirm after the next deploy. |
| F3 | Tempo Read chips ignore polarity | STILL-PRESENT | `deck_sim.js:410-423`: the read is still hardcoded `"worse"` (`r[5]`), and `showChip = sim != null && math != null`. The row's own `lower is better` flag (`r[6] = true`, `:410`) is never read. There is no server `read`/`read_label` (API `tempo_comparison` keys: `land_drops, static, sim, deltas, notes`). **Runtime (HH):** `T5 starvation 15% vs 41% ▼ BELOW MATH`, `Commander first cast T5.73 vs T4 ▼ BELOW MATH`. | Runtime (:8020) | N | Land-drop rows use a client ±0.08 delta (`:403`), not an N-games threshold. Direction is correct there (HH shows `▲ Above math`). |
| F4 | Failed job via localStorage resume vanishes | STILL-PRESENT | `deck_sim.js:756` `if (res.status === "failed") { forget(); load(); return; }`. The failed card copy is still "stays… until this run finishes" (`:320`). Nothing reopens the tray. **Runtime fixture:** `simJob:archidekt:417707` in localStorage plus a poll returning `failed` gives no `#job`, tray closed, `#simError` empty, and the localStorage key removed. | Runtime (:8020, fixture) | N | The `--danger` bar on the failed card is static-only: no `data-phase="failed"` / danger rule exists for `.sm-job`. |
| F5 | Opponent pool leaks raw ids / tags; blank dup label | FIXED-ON-MAIN | PR #263: `sim_service.py:189` drops `user_*` rows from the precon list (this includes the deck's own id). `:190` + `normalize_precon_rows` (`:132`) split `[SET] [YEAR]` into `set`/`year` and give every row a non-blank `label`. `resolve_display_name` (`:112`) maps `sample_mind_seize` → "Mind Seize". `deck_sim.js:124` now checks `disabled` first ("in Seat n"). **Runtime (old build):** the API still lists 5 raw ids incl. `user_417707`, 179 names carry tags, dup label `""`. | Static-only (the :8020 backend and JS predate #263) | Y: from `comboRows` to backend `normalize_precon_rows` / `resolve_display_name` in `analytics/sim_service.py` | Re-run the pool check after deploy. `sample_*` rows are kept, under a readable name. |
| F6 | Win-rate foot lacks Wilson CI; avg unrounded; 390 overflow | STILL-PRESENT | `deck_sim.js:353` foot is the literal `"even odds 25%"`. `:354` prints `pod.avg_total_turns` raw. The API has `summary.win_rate_ci95 = [0.1119, 0.4687]`, which the client never reads. **Runtime:** `even odds 25%`, `20.05 turns`. At 390 the Avg-game tile child overflows by **8px**. | Runtime (:8020) | N | "fastest N" and "85% cast it" foots are also still missing. |
| F7 | Report head has no "Built …"; empty `<time>` indents chips | STILL-PRESENT | `deck_sim.js:518` passes `sim.meta` (which only has `saved_at`) to `reportHead`. `load()` (`:778`) drops the top-level `res.meta`, but `sim_service.py:830` `saved_view` does expose `meta.created_at` (= `saved_at`). **Runtime:** `<time>` text `""`, `datetime=""`, `title=""`; first chip sits **12px** right of the h2. The Specific mode chip `title` is `null`. | Runtime (:8020) | N | **N1 (new, from #254):** `reportHead` uses `focusLine` at `:331` before `var focusLine` is assigned at `:336`. The head prints a literal **"undefined"** (runtime facts text: `…20 gamesundefinedWon 5 of 20`), and the Focus chip never shows. |
| F8 | Job card: focus, clock, ETA, queued track | STILL-PRESENT | No `.focus()` on `#job`. The clock starts from `Date.now()` (`:749`). The ETA is `Math.ceil(left * 0.5)` (`:309`). The phase is only ever `"oracle"`/`"sim"` (`:313`, `:765`). **Runtime fixture:** job `created_at` 211s ago; clock shows `0:00` → `0:05`; `about 7 min left` at 6/20; queued `data-phase="sim"`, track `animation: none`; focus on `BODY`. | Runtime (:8020, fixture) | N | CSS already supports queued (`hud.css:3559` `.sm-job[data-phase="queued"]`). Only the JS needs to emit it. |
| F9 | Run button + notes live during a run | STILL-PRESENT | `deck_sim.js:536` disables only `#simForm fieldset`s. `#runBtn` (`:264`) and `#notes` (`:207`) sit outside any fieldset. **Runtime fixture (running):** fieldsets `[true,true,true]`, `runBtn.disabled=false`, `notes.disabled=false`. | Runtime (:8020, fixture) | Y: notes now render inside `focusSection()` (`deck_sim.js:192-211`, #254), still outside the fieldset | The Focus chips fieldset *is* disabled correctly. |
| F10 | Specific tray not prefilled; 5-seat layout | PARTIAL | Prefill is still missing: `load()` `:787` restores only `mode`/`games`, never `STATE.picks` from `params.opponents`. **Runtime (HH):** 5 Specific seats all `""`, although `params.opponents` holds 3 pool ids. Spec half done: UX_SPEC.md:95 (#250) now documents 1–5 opponents. | Runtime (:8020) | N | Labels still repeat Reckless/Default/Cautious/Reckless/Cautious (`:138`). The 3+2 ragged grid remains. |
| F11 | Rapid 1v1 tile not in spec; orphaned; games not locked | STILL-PRESENT | `deck_sim.js:69` adds the tile. `grep -c Rapid UX_SPEC.md` = 0. `:588-596` forces 5 games but never disables g20/g60. **Runtime 1440:** rapid tile top=400 vs others 309 (own row). After picking Rapid: g5 checked, g20/g60 `disabled=false`. | Runtime (:8020) | N | Product call still open: 4-up vs 2×2. |
| F12 | A11y: detail aria-live, option ids, activedescendant, Show-all focus | STILL-PRESENT | `detail()` `:156-175` emits no `aria-live`. `comboRows` `:125` emits no option `id`. No `aria-activedescendant` is set anywhere. `wireCardGroups` `:562` removes its own button. **Runtime:** `aria-live` null on all 4 `.sm-opp`; 0 options with `id`; 0 `[aria-activedescendant]`. | Runtime (:8020); Show-all focus loss is static-only | N | |
| F13 | ⌘/Ctrl+Enter doesn't submit from notes | STILL-PRESENT | The only `keydown` listener in `deck_sim.js` is the combobox one (`:644`). `#notes` gets only `input` (`:621`). | Static-only | Y: `#notes` is now built in `focusSection()` (`:207`, #254) | |
| F14 | Land-drop panel padding 0; 390 chips hidden; literal N copy | STILL-PRESENT | `hud.css:675` `.hud-panel--flush > .hud-panel-body {padding:0}` beats `hud.css:3575` `.sm-cmp {padding}`. `hud.css:1823` (≤640) `.hud-comp-row .hud-chip {display:none}` isn't overridden by `:3647`. Copy: `deck_sim.js:434` "this many games", `:414` "n of N games", and panel meta `:427` lacks "N games ·". **Runtime:** `.sm-cmp` padding `0px` (1440); land-drop chips `display: none` (390). | Runtime (:8020) | N | |
| F15 | Oracle fallback looks like a real read | STILL-PRESENT | `s1()` `deck_sim.js:369-382` renders `source: "fallback"` exactly like `"oracle"`. **Runtime (HH):** `oracle_review.source="fallback"`, note `ORACLE_KEY not set`; 0 chips in section 01 head. RotK: `timed out after 600s`. | Runtime (:8020) | N | The ops side (Oracle key/timeout) applies to these saved runs. Not re-tested with a new run. |
| F16 | Game-log filter meta, seat sub-line, empty reason, phone Sort | STILL-PRESENT | `wireLog` `:552` reads the meta but never writes to it, and the meta has no `aria-live`. The seat sub-line is `o.ai` (`:486`). `win_reason || ""` (`:491`). The Sort `<select>` (`:506`) has no handler. Loss cells have no `Seat n` (`:483`). **Runtime:** meta `aria-live` null; game #20 "How it ended" empty; seat shows `Cavalry Charge [MOC] [2023]` / `Reckless`; changing the Sort select leaves the row order unchanged. | Runtime (:8020) | Y (partly): a server-side tag splitter now exists (`sim_service.py:101` `split_bracket_tags`, #263) but isn't applied to `pod_breakdown.games[].opponents[].deck` | |
| F17 | Stale note missing full stop; generic reason | STILL-PRESENT | `sim_store.py:24` `STALE_REASON = "Decklist changed since this simulation was run"` (no period) feeds `deck_sim.js:338-339`, which appends `" The numbers…"`. So the text still reads "…was run The numbers". No swap summary is produced. | Static-only (no stale deck available live) | N | The JS fallback string at `:338` has a period, but the server always sends its reason. |
| F18 | "0 cards were drawn too rarely" footnote | STILL-PRESENT | `deck_sim.js:468` uses `(cc.unmatched_cards \|\| []).length`. **Runtime (HH):** "0 cards were drawn too rarely to judge and are left out." | Runtime (:8020) | N | |
| F19 | Standard detail = hardcoded editable pickers | FIXED-ON-MAIN | PR #250: `deck_sim.js:78-93` `podPanel()` renders the user-defined pod (no default) and `:160` mounts it. **Runtime (HH tray, Standard):** "No Standard pod defined… DEFINE STANDARD POD", 0 comboboxes, 0 "Default (…)" placeholders. | Runtime (:8020) | Y: from pickers to `podPanel()` + backend `resolve_pod` (`sim_service.py`) | |

**Totals:** FIXED-ON-MAIN 3 (F2, F5, F19) · PARTIAL 1 (F10) · STILL-PRESENT 15 (F1, F3, F4, F6–F9, F11–F18). There is also one new regression, N1, filed under F7.
**Runtime-verified on :8020:** F1, F3, F4, F6, F7, F8, F9, F10, F11, F12, F14, F15, F16, F18, F19, plus F2 with main's rules injected.
**Static-only:** F5 (main fix not deployed), F13, F17.

## Hand-off to fix tasks

Report rendering → **t_d02caaf8** (F1, F3, F6, F7). The code for these lives in `static/js/deck_sim.js`, **not** `deck_sim_report.js`. #253 didn't touch these paths.

- **F1** · `deck_sim.js:343-347` (`metricsAvailable`), used by `statStrip` `:351`, `s2` `:396`, `s3` `:457`.
  Add a server `metrics_available` (in `analytics/sim_service.py`, e.g. `saved_view`, = `params.verbose_log is not False` and not random), or test `params.verbose_log === false` on the client. When it's false, render one `sm-unrec` panel with the §6.4 copy instead of 02/03 rows. Stat tiles should show "not recorded in random mode", not `0%`.
- **F3** · `deck_sim.js:403`, `:410-423`. Compute the read per metric: use `r[6]` (lower-is-better) for starvation, "Later than math" for commander turn, and threshold by games played. Or add `read`/`read_label` to `tempo_comparison` server-side (`analytics/sim_diff.py`) and render that.
- **F6** · `deck_sim.js:353-354`. Foot `CI {lo}–{hi}%` from `report.summary.win_rate_ci95`; `toFixed(1)` on avg turns. CSS: let the stat unit wrap or shrink at ≤640 (`hud.css` stat strip).
- **F7 + N1** · `deck_sim.js:518`, `:778`, `:327`, `:331/336`. Carry `res.meta.created_at` (or `sim.meta.saved_at`) into `reportHead`, fill `title` with the absolute time, and omit `<time>` when there's no date. Set the Specific mode chip `title` to the deck names.
  **N1:** move `var focusLine = …` (`:335-336`) above `var bits` (`:327`) so the head stops printing "undefined". Add a DOM test.

Job flow → **t_01a179a2** (F4, F8, F9, F10).

- **F4** · `deck_sim.js:756`, `:775-795`, `:311-321`. On `failed`: keep the job in `STATE`, render the failed card with a danger bar, use failure copy (not "stays… until this run finishes"), reopen `#tray` with the submitted `params` (`res.params`), and set `#simError`. Then `forget()`. Needs a `.sm-job[data-phase="failed"]` danger rule in `hud.css`.
- **F8** · `deck_sim.js:747-750` (clock from `job.created_at`), `:306-310` (ETA = `(updated_at − created_at) / games_completed` × left, shown after game 2), `:313` + `:765` (emit `data-phase="queued"`; the CSS at `hud.css:3559` already handles it), `$("job").focus()` after the first render of a new job.
- **F9** · `deck_sim.js:533-537`. Also disable `#runBtn` and `#notes` (or hide `.sm-runbar`) while `jobActive`.
- **F10** · `deck_sim.js:785-788`. Seed `STATE.picks` from `params.opponents` (these are pool ids). 5-across layout at ≥1181 and AI labels are a design call; the spec already says 1–5 (UX_SPEC.md:95).

Low-severity polish (no card yet; the root card **t_eac061f5** lists them as deferred). Suggested grouping for one coder card:

- **F11** · `deck_sim.js:588-596`: disable g20/g60 under Rapid. Tile grid 4-up in `hud.css` `.sm-modes`. UX_SPEC needs a Rapid 1v1 entry (designer).
- **F12** · `deck_sim.js:156-175` (`aria-live="polite"` on `.sm-opp`), `:125` (option `id`s) + `:646-655` (`aria-activedescendant`), `:562` (focus the first revealed row before removing the button).
- **F13** · `deck_sim.js:620-626`: `keydown` on `#notes`, `(e.metaKey||e.ctrlKey) && e.key==="Enter"` → `form.requestSubmit()`.
- **F14** · `hud.css`: `.hud-panel--flush > .hud-panel-body.sm-cmp { padding: var(--sp-4) }`. Inside ≤640: `.sm-cmp .hud-comp-row .hud-chip { display: inline-flex }`. `deck_sim.js:414/427/434`: interpolate N games.
- **F15** · `deck_sim.js:369-382`: when `oracle_review.source === "fallback"`, add a neutral `Oracle unavailable` chip by the section head. Ops: check `ORACLE_KEY` / timeout on the Tower deploy.
- **F16** · `deck_sim.js:545-555` (update the meta text "Showing n of N games" + `aria-live`), `:506` (wire the select to the same sorter as the headers), `:491` (`—` for a null reason), `:483` (`Seat n` on losses). For set/year, reuse `split_bracket_tags` (`sim_service.py:101`) on `pod_breakdown.games[].opponents[].deck` server-side.
- **F17** · `analytics/sim_store.py:24`: end `STALE_REASON` with a period, or have the JS join with `". "`. The swap summary (`n cards swapped (+A, −C)`) needs `sim_store._stale_reason` to diff fingerprints/cards (SM-3 owner).
- **F18** · `deck_sim.js:468`: use `unjudged_n`/`deck_size` if the comparison emits them, otherwise omit the footnote.

Deploy follow-up: :8020 is behind main (missing #262, #263). Re-check F2 and F5 live after the next deploy.

## Closeout status (main after this PR)

The audit table above was taken at 4ae8e5b. Current state:

| Finding | Status | Fixed by |
|---|---|---|
| F1, F3, F6, F7 | FIXED | #264 |
| F2 | FIXED | #262 |
| F5 | FIXED | #263 |
| F4, F8, F9, F10 | FIXED | #265 (F4 failed-card copy and clock: this PR) |
| N1 (literal "undefined" in report head) | FIXED | this PR |
| F19 | FIXED (per audit) | - |
| F11-F18 | DEFERRED (low polish) | - |

Evidence: before/after PNGs for F1-F4 at 1440 and 390 are beside this file (EVIDENCE.md). F4-after predates the failed-card copy change; that change is covered by the node-harness tests only, not re-screenshotted. Nothing deployed or verified on :8020.
