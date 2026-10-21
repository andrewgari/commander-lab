# Simulate tab: UX spec (SM-D)

Deck page, 8th tab: `/deck/{name}/sim`, key `8`. HUD kit only. Built by SM-4 (`templates/deck_sim.html`, `templates/partials/sim/*.html`, a `sm-*` block at the end of `static/css/hud.css`).
Backend contract: SM-3 (`/api/decks/{id}/sim`, `.../sim/run`, `.../sim/jobs/{job_id}`). Simulator: `forge_sim_daemon` (SM-1).

**Live mockup:** `docs/design/sim/mockup.html` (+ `sim.css`, the exact new CSS). Open from disk; `?s=` switches state:
`empty` · `empty-random` · `empty-specific&open=1` · `running` · `oracle` · `fresh` · `fresh&tray=1` · `random` · `stale&tray=1` · `offline`.
Every number, Oracle sentence and win/loss in it is sample data. Shots: `docs/design/sim/shots/<state>_1440.png` and `_390.png`.

Mirrors the Review tab on purpose: same page rhythm (header, job card, `hud-doc` with numbered sections), so Simulate reads as Review's runtime twin.

---

## 0. Two things to decide before SM-4 starts

| # | Finding | Why it matters | Recommendation |
|---|---|---|---|
| 1 | **Random mode records no curve or card metrics.** The daemon runs random pods with `-q` quiet logs, so the parser fields come back as `0` (the smoke report shows every `land_drop_rate` at `0.0` and `commander_never_cast_rate` at `1.0`). | Rendering those as real numbers would tell Andrew his commander is never cast. SM-4's acceptance asks for "static-vs-sim comparison" on a random run, which can't be true today. | **Fix at the source:** random mode already runs one JVM per game, so it can log verbose and aggregate (slower, but sections 02 and 03 then work in every mode). **Until then** the UI shows a *Not recorded for random pods* panel in 02 and 03 and `n/a` in two stat tiles (§6.2). The spec supports both: the UI keys off `metrics_available`, never off the mode name. Needs a card to the daemon owner (triage). |
| 2 | Report must carry `metrics_available` (bool) for curve and cards. | The UI must not guess from zeros. | In the SM-3 contract (§10). |

---

## 1. System

| | |
|---|---|
| Reused | `hud-doc` `hud-doc-head` `hud-doc-section` `hud-section-head` `hud-stat-strip` `hud-callout` `hud-panel` (+`--flush`, `--signal`) `hud-tray` `hud-job` `hud-seg` `hud-field` `hud-comp-*` `hud-table` (+`data-hud-sortable`, `hud-sort`) `hud-chip` `hud-empty` `hud-rv-prose` `hud-doc-index` `hud-cp-name` + `HUD.cardRefAttrString` |
| New (all in `sim.css`) | `sm-modes/sm-mode` radio tiles · `sm-opp/sm-seats` opponent detail · `sm-combo*` grouped combobox · `sm-tray` · `sm-job` (determinate bar) · `sm-cmp` `sm-card` `sm-unrec` · `sm-log` (+phone card layout) · `sm-tempo` (+phone cards) |
| Colour meaning | **Amber** = selected / needs attention (selected tile, stale, "below math", dead draws). **Cyan** = the simulated value (`--data` fill). **White tick** = static math / "as expected". **Green** = win, up to date. **Never red** for a bad sim number: red (`--danger`) is for errors only. |
| Verdicts | The app computes every read (`worse` / `in_line` / `better`, bucket membership). The UI prints what it is given. Chips carry glyph + words (`▼ Below math`), never hue alone. |
| Tokens only | No new colours, radii, shadows, gradients. One new custom property: `--p` (0–1 fill) set inline on `.sm-job`. |
| Type | Prose `--font-sans`; numbers and labels `--font-mono`; card names `--font-card` (via `hud-cp-name`). Deck/opponent names are not cards: sans. |

---

## 2. Deck header tab

`templates/partials/deck_head.html`: append to `tabs`:

```jinja
{"key": "sim", "label": "Simulate", "href": base ~ "/sim", "n": "8", "count": none},
```

Also in the same PR:
- `static/js/deck_analytics.js`: key test `/^[1-7]$/` → `/^[1-8]$/`; comment "keys 1-8".
- `deck_head.html` header comment: "data-key 1-8", add "Simulate (8) with SM-4".
- No badge. A badge for "run in flight" is tempting; skipped (the job card is the signal, and a badge needs a cross-page poll).
- Phone: the tab strip already scrolls inside itself and scrolls the active tab into view (`deck_analytics.js`). 8 tabs at 390px confirmed: no page overflow.
- Not added in this PR: the tab would 404 until SM-4 lands the route.

---

## 3. Page anatomy

```
Header (title, chips) · tabs [… Review 7 | SIMULATE 8]
┌ offline banner (only when the simulator is unreachable) ───────────┐
┌ BUILDER ───────────────────────────────────────────────────────────┐  empty: open panel · saved: collapsed tray
┌ JOB CARD (only while a run is active) ─────────────────────────────┐
┌ REPORT (hud-doc) ──────────────────────────────────────────────────┐
│ head: Built · Up to date|Stale · mode · games · Won X of N         │
│ test-notes callout · stale note                                    │
│ stat strip (win rate · avg game · commander cast · mana used)      │
│ index 01 02 03 04                                                  │
│ 01 Oracle review   02 Curve & tempo   03 Cards   04 Game log       │
└────────────────────────────────────────────────────────────────────┘
```

Order is fixed. The tray sits above the report and the report is never hidden or replaced until a new run finishes (§5). Column is `hud-doc` max-width 60rem for both builder and report, same as Review.

Header has **no action button** (unlike Review). Reason: Review has one action; Simulate has a form. In the saved state the tray bar is the call to action; in the empty state the open form is.

---

## 4. Builder

### 4.1 States

| State | Container | Default | Button |
|---|---|---|---|
| Empty (no report) | `hud-panel--signal`, open, titled **Run a simulation**, meta "No simulation saved yet". Footer lists what you get (01–04). | Standard pod · 20 games · notes empty | **Run simulation** |
| Saved | `<details class="hud-tray sm-tray">`, **closed**. Summary: `▸ RUN NEW SIMULATION` left, `Last run: Random each game · 20 games` right. | Opens prefilled with the last run's mode, game count and (specific) opponents. **Notes start empty** (they are per run). | **Run new simulation** |
| Run active | Tray stays closed; its form is `<fieldset disabled>` (55% opacity). | n/a | none shown (form hidden while disabled) |
| Failed run | Tray **re-opens**, inputs as submitted, error under the notes. | kept | Run again (same label) |
| Simulator offline | Tray body replaced by the banner (§9). | n/a | none |

### 4.2 Controls (in order)

**1. Opponents** (`fieldset` + `radiogroup`): three tiles, one row ≥901px, stacked ≤900px. Selected tile: amber fill, 2px amber left bar, `✓` after the name. Each tile = name, one-line description, mono footer flag.

| Value | Name | Description | Footer flag |
|---|---|---|---|
| `standard` | Standard pod | Your saved pod of 1 to 5 decks (Redis `sim:standard_pod`). Same opponents every game; no default, empty state prompts to define one. | Full curve + card metrics |
| `random` | Random each game | Three new opponents on every game, from Forge precons and your synced decks. | Game log + outcomes only *(drop this flag once decision 1 ships)* |
| `specific` | Specific | You pick 1 to 5 opponents from precons and synced decks. Fixed for the run. | Full curve + card metrics |

Under the tiles, one detail block (others `hidden`), `aria-live="polite"` so a switch is announced:

- **Standard:** seat list of the user-defined pod with an Edit pod form (synced decks; Forge precons opt-in, never preselected). Deleted/invalid decks are flagged and block the run. The report records the pod in `params.pod`.
- **Random:** one paragraph, no controls: "Fresh pod every game. Three distinct decks drawn at random from **179**: 154 Forge precons plus 25 of your synced decks. {Deck} and its mirrors are left out. The log in section 04 shows which pod each game got." Numbers come from `/opponents` (`pool.total`, `precons`, `synced`). Deliberately **no pod preview**: a preview would imply the pods are fixed.
- **Specific:** three comboboxes labelled `SEAT 2 · RECKLESS`, `SEAT 3 · DEFAULT`, `SEAT 4 · CAUTIOUS` (AI profile follows the seat). Prefilled from last run.

**Combobox** (ARIA 1.2 pattern, `sm-combo`): `<input role="combobox" aria-expanded aria-controls aria-autocomplete="list">` + `<ul role="listbox">`.
- Groups, sticky headers: **Forge precons · 154**, **Your synced decks · 25**. Rows: name left, `SET YEAR` (precons) or status (decks) right, mono.
- Type to filter (case-insensitive, substring, match highlighted amber). Empty result: "No deck matches “x”."
- Keys: ↓/↑ move (wrap), Enter picks, Esc closes and keeps the typed text out (restores the previous pick), Tab picks nothing and moves on. Opens on focus or typing, not on hover.
- A deck already in another seat shows in the list, disabled, with `in Seat 2` on the right. The user's own deck and its mirrors are not in the list.
- No-JS fallback: the same field is a native `<select>` with `<optgroup>`s.
- Listbox max-height 17rem, scrolls inside itself, `z-index: var(--z-overlay)`.

**2. Games**: `hud-seg` radios: `20 games · ~10 min` (default) · `60 games · ~30 min`. Side note: "More games = tighter numbers. 20 is enough to spot a pattern, 60 to trust it." Estimate is ~30 s a game (from the daemon smoke runs); if SM-3 reports a better `sec_per_game` the labels use it.

**3. Test notes** (optional): `<textarea maxlength="500" rows="3">`, placeholder *e.g. Watch if Kaalia gets removed before attacking, or if colors brick on T4*. Hint: "Oracle answers this directly in section 01." Counter `0 / 500`, turns amber at ≥450. ⌘/Ctrl+Enter submits.

**4. Run bar**: primary button (min 11rem, 36px) + hint "Runs on the Forge server, ~30 s a game. You can leave this page; the report saves itself when it finishes."

### 4.3 Validation and submit

| Case | Behaviour |
|---|---|
| Specific, a seat empty | Button stays enabled. On submit: that combobox gets `is-invalid` (red border) + `Pick a deck for Seat 3.` under it (`role="alert"`); focus goes to the first empty one. No request sent. |
| Specific, duplicate | Prevented by the disabled rows; if it slips through, same inline error. |
| Notes > 500 | `maxlength` stops it. |
| Success | `POST /sim/run` → `202 {job_id}`. Tray closes, fieldset disables, job card appears and takes focus (`tabindex="-1"`), job id saved to `localStorage["simJob:{deck}"]` so a reload or another visit resumes it (same as Review). |
| 409 busy, **same deck** | Follow the existing job: "A simulation for this deck is already running; following it." |
| 409 busy, **other deck** | The daemon runs one job at a time across all decks. Inline error: "The simulator is busy with another deck's run (game 4 of 20). Try again in a few minutes." Button re-enabled. |
| 400 / network | Inline `sm-error` text (the server's message, ≤300 chars) or "Couldn't reach Commander Lab. Check the connection and try again." |

No confirm dialog: a run is cheap, non-destructive, and the saved report survives it.

---

## 5. Live job (`hud-job`, `.sm-job`)

```
SIMULATING                                                  3:31      ← label (phase) · clock
Simulating game 7 of 20                                               ← title
█████████████░░░░░░░░░░░░░░░░░░░░░░                                   ← determinate track
7 / 20 games · about 6 min left                                       ← count + ETA
Your saved report from Oct 17 stays exactly as it is until this run finishes. You can leave this page.
```

| Phase (`job.status`) | Label | Title | Track |
|---|---|---|---|
| `queued` | Queued | Waiting for the simulator… | indeterminate (existing `job-slide`) |
| `running` | Simulating | Simulating game **X** of **N** (`X = games_completed + 1`, capped at N) | determinate: `--p = games_completed / games_total`, `scaleX`, `--dur-slow` step |
| `oracle` | Writing Oracle review | All N games done. Oracle is reading the log. | indeterminate |
| `done` | Done | Done. Loading the report… | hidden, then `location.reload()` (Review's pattern) |
| `failed` | Failed | `Couldn't finish the simulation: {error ≤300}` | hidden; **Dismiss** button |

- Clock: `m:ss` from `created_at`, ticks every 1 s. ETA = `(N − X) × mean seconds per finished game`, shown after game 2; "about 6 min left", rounded up to the minute, "under a minute" below 60 s.
- Poll every 2.5 s; on network loss: "Lost contact; retrying…" and back off ×3 (Review's behaviour). A job that hasn't updated in 15 min is shown as failed ("stopped responding"). 404 = "This job is gone".
- Placement: under the tray, above the report, `margin-bottom: --sp-6`.
- The "saved report stays" line only appears when a report exists. In the empty state the message reads "The report appears here when it finishes."
- Failed run: left bar turns `--danger`, saved report untouched, tray re-opens (§4.1).
- **No Cancel.** The daemon has no cancel endpoint. Not faking one.

---

## 6. Saved report (`hud-doc#doc`, `data-state="fresh|stale"`)

### 6.1 Head

```
SIMULATION · SAVED
Holy Hellfire
BUILT 3H AGO  ■ UP TO DATE  [STANDARD POD] [20 GAMES] [WON 12 OF 20]
┌ YOUR TEST NOTES ──────────────────────────────────────────────┐
│ Watch if Kaalia gets removed before attacking, or if colors…   │
└────────────────────────────────────────────────────────────────┘
```

- `Built {relative}` with the absolute time in `title` (same JS as Review).
- Freshness chip: `Up to date` (ok, dot) or `Stale` (signal, dot, `title` = reason).
- Mode chip (data tone): `Standard pod` · `Random each game` · `Specific`. Specific: the deck names are in the chip's `title`.
- Games chip (neutral): `20 games`. Result chip: `Won 12 of 20` (ok when wins ≥ ¼ of games, else neutral). Draws are counted in the log, not hidden.
- Notes callout (`hud-callout`, one column): label `YOUR TEST NOTES`, the text as typed, wrapped. **Omitted entirely** if the run had no notes.
- **Stale** adds a `hud-panel--signal` `role="status"` note: "**This report is out of date.** The decklist changed after this run: {n cards swapped (+A, +B, −C)}. The numbers describe the old list. Open *Run new simulation* above to refresh." Report content stays full-contrast (not dimmed). Cards in 03 that left the deck get a neutral `cut since` chip.
- Tray in the stale state: unchanged, closed. (Mockup `stale&tray=1` shows it open with notes.)

### 6.2 Stat strip (4 tiles, `hud-stat-strip`)

| Tile | Value | Foot |
|---|---|---|
| Win rate (`--signal`) | 60% | `CI 39–78% · even odds 25%` (Wilson 95%; 25% is a fair 4-seat share) |
| Avg game | 18.4 turns | `table turns · fastest 9` |
| Commander cast | T5.4 avg | `85% cast it · static: T4` |
| Mana used T2–T6 | 61% | `static math: 74%` |

When `curve_tempo.metrics_available` is false the last two tiles show `n/a` (muted) with foot "not recorded in random mode". Phone: 2×2.

### 6.3 Section 01 · One-off Oracle simulation review

- `<p class="sm-headline">`: Oracle's one-sentence verdict, at `--fs-lg`, max 46ch.
- Then `hud-rv-prose` with `hud-rv-h` subheads, in this order: **On your notes** (answers each point in the notes, in the user's order; if no notes were given, this block is omitted and nothing else moves), **Against the static review** (sim vs the saved Review: where they agree, where the games contradict it), **Worth trying** (`hud-doc-list`, 2–4 concrete tweaks).
- Card names inside prose use `hud-cp-name` + `data-card-preview` (hover preview).
- Oracle writes prose only. Numbers in the prose are checked by the app against the report (same rule as Review).
- **Oracle offline / invalid:** sections 02–04 still save. Section 01 shows `hud-empty` "Oracle couldn't write the review for this run. The numbers below are saved." No retry button unless SM-3 exposes one; otherwise *Run new simulation* is the retry.

### 6.4 Section 02 · Curve & tempo: simulation vs static math

Two panels.

**A. Land drop made on turn** (`hud-comp` rows, 6 of them, T1–T6). Row = name · track · `95% vs 97%` · read chip.
- Cyan fill = simulated rate. White tick = static math (hypergeometric for this deck's land count). Fill turns **amber** (`is-under`) when the read is `worse`.
- Panel meta: `20 games · math: 35 lands in 99`. Legend above rows: ▬ Simulated | Static math.
- Each track has `role="img"` and an `aria-label` with both numbers.

**B. Tempo & commander** (`hud-table`, not sortable: it's a fixed list): `Metric | Sim | Math | Read`.

| Metric (hint under it) | Sim | Math |
|---|---|---|
| T5 mana starvation (lower is better) | rate | Review's value |
| Commander first cast (mean turn, when cast) | T5.4 | from commander-on-curve |
| Commander on curve (cast on turn *cmc*) | 35% | `commander_on_curve.p_with_ramp` |
| Commander casts / game (tax +2 on each recast) | 1.3 | `n/a` |
| Commander never cast (n of N games) | 15% | `n/a` |
| Mana used, T2–T6 (spent ÷ available) | 61% | `1 − unspent_avg_t2_t6 ÷ mana` |
| First spell cast (mean turn) | T2.6 | `n/a` |

- **Read** chip: land-drop rows always carry one (`In line` neutral when within tolerance, so the all-clear is explicit). Table B shows a chip only when `worse` or `better`. `worse` = signal `▼ Below math` / `▼ Worse than math` / `▼ Later than math`; `better` = ok `▲ Above math`. Wording per metric comes from SM-3 (`read_label`), so "lower is better" metrics don't read backwards.
- `n/a` in Math = the static Review has no matching number; muted, no chip.
- Foot note: "A chip appears only when sim and math differ by more than {N} games can explain. Smaller gaps read as In line." (Threshold lives in SM-3: it scales with game count, so 60 games flags smaller gaps than 20.)
- **Not recorded** (`metrics_available: false`): both panels replaced by one `sm-unrec` panel (amber left bar): "**Not recorded for random pods.** Random mode logs each game quietly to stay fast, so land drops, starvation, commander timing and mana use were not captured. Run a *Standard pod* or *Specific 3* simulation to compare against the math. Sections 01 and 04 still describe these N games." (Edit the cause sentence if decision 1 changes it.) Never render zeros.

### 6.5 Section 03 · Card performance vs static review

Intro line: "U is how much a card did against how often it should have been drawn. **1.0** is as expected."

Three panels, always in this order (most surprising first):

| Panel | Rule (computed by SM-3) | Treatment |
|---|---|---|
| **Surprise dead draws** | Stranded in hand / low U in the sim **and not** a static straggler | `hud-panel--signal`-style amber left bar; chip `Not in static` (signal) |
| **Confirmed stragglers** | Low U **and** in the static Review's stragglers | chip `Both agree` (ok) |
| **Confirmed workhorses** | High U | chip `Both agree` (ok) if also in static "Carrying the deck", `Sim only` (data) if not |

- Row = card name (`hud-cp-name`, hover preview via `HUD.cardRefAttrs` / `cardRefAttrString`, keyboard focusable) · track (cyan fill = U/2, white tick at 50% = 1.0) · `U 0.08` · chip. Under it one note line (`hud-tcard-note`): how often drawn / cast / stranded, plus the static score where relevant. e.g. "Stuck in hand in 5 of 6 draws, cleanup-discarded twice. Static review rates it fine (0.31)."
- Show top **3** per panel, `Show all N` ghost button (`aria-expanded`) reveals the rest in place. Panel meta: `3 cards · static review missed these`.
- Empty panel: `hud-empty`: "No confirmed stragglers. Every card the Review flagged did its job here." / "No workhorses stood out." / "No surprise dead draws." The panel stays so the absence is visible.
- Footnote: "62 of 99 cards were drawn too rarely in 20 games to judge and are left out. 60 games judges more of them." (`unjudged_n`, `deck_size`).
- Not recorded: same `sm-unrec` panel as 02 ("Per-card casts and stranded draws need the full game log. …").
- Card refs: pass the deck's printing map when the page embeds it (`window.HUD_CARD_REFS`); otherwise name-only ref, as in Review.

### 6.6 Section 04 · Per-game opponent & outcome log

Panel (`hud-panel--flush`) with a toolbar and a `hud-table` (`data-hud-sortable`).

Toolbar: left meta `12 W · 7 L · 1 timeout · same pod all 20 games` (random: `20 different pods`; use `summary.distinct_pods`); right `hud-seg` filter **All 20 | Wins | Losses**.

| Col | Content | Sort |
|---|---|---|
| `#` | `#1…#N`, mono, right | num (default, ascending) |
| `Seat 2` / `Seat 3` / `Seat 4` | Deck name, `SET YEAR` below in mono muted. Header carries the AI under it: Reckless / Default / Cautious (fixed per seat) | text |
| `Winner` | Win: chip `■ You won`. Loss: the winning opponent's deck name + `Seat n` below. Draw/timeout: chip `No winner` (signal) | text (`You` < decks < no winner) |
| `Turns` | total table turns, right | num (first click: high to low, per `hud.js`) |
| `How it ended` | `win_reason`, sentence case, `--fs-xs` | text |

- **Random mode:** every row shows its own three opponents; the seat columns are the proof of per-game randomization. Standard/specific: the same three on every row (cheap to scan; the toolbar says so).
- Two-line rows (name + set / seat) keep the row height constant, so a loss row isn't taller than a win row.
- Deck names are plain text (they're decks, not cards: no hover preview).
- `aria-sort` kept in sync by `hud.js`; header buttons are real `<button class="hud-sort">`.
- Filter hides rows (`is-hidden`), doesn't re-sort, announces "Showing 7 of 20 games" via the meta's `aria-live`.
- Draws: if `games_log` has `user_won=false` and no winner, treat as `No winner`.
- Long run (60 games): table is as long as it is; no pagination (60 two-line rows ≈ 2,000px, acceptable at the end of the page). Sticky header is *not* added (Review tables don't).

---

## 7. States, in one table

| State | Builder | Job | Report | Shot |
|---|---|---|---|---|
| Empty | open panel, "No simulation saved yet" | none | none | `01-empty`, `02-empty-random`, `03-empty-specific` |
| Saved, fresh | closed tray | none | `Up to date` | `06-fresh`, `06b-fresh-builder-open` |
| Saved, stale | closed tray | none | `Stale` chip + note | `08-stale` |
| Running (simulating) | closed, disabled | `Simulating game X of N` | **old report stays** | `04-running` |
| Running (Oracle) | closed, disabled | `Writing Oracle review` (indeterminate) | old report stays | `05-oracle` |
| Random-mode report | closed tray | none | 02 + 03 `Not recorded` | `07-random` |
| Failed | tray re-opened, error inline | failed card + Dismiss | old report stays | n/a: copy in §5 |
| Simulator offline | banner, no form | none | old report stays | `09-offline` |
| Couldn't load | `hud-empty` "Couldn't load the simulation. Try again later." | n/a | n/a | n/a |

**Offline banner** (`hud-panel--signal`, `role="status"`): "**The Forge simulator isn't reachable.** New runs are paused. The saved report below is unaffected. [Check again]". Amber, not red: nothing of Andrew's is broken. In the empty state it is the only content besides the "what you get" footer.

---

## 8. Responsive

Verified with the mockup at **1440** (shell with sidebar) and **390** (shell with the 56px icon rail, so the content column is ~334px). `documentElement.scrollWidth == clientWidth` in every state at both widths.

| Piece | ≥1181 | 901–1180 | ≤900 | ≤640 |
|---|---|---|---|---|
| Mode tiles | 3 across | 3 across | stacked | stacked |
| Specific pickers | 3 across | stacked | stacked | stacked |
| Seat list | 3 cols | same | same | name under seat label |
| Tray summary | one line | one line | one line | two lines, "Last run" under |
| Run button | auto | auto | auto | full width |
| Stat strip | 4 | 4 | 4 | 2×2 |
| Land-drop rows | name · track · value · chip | narrower name | same | name + chip on row 1, track row 2, value row 3 |
| Tempo table | 4 col table | same | same | **one card per metric** (metric left; Sim/Math side by side; chip right) |
| Card rows | name · track · U · chip | same | same | name + U row 1, track row 2, chip + note below |
| Game log | 7-col table | same | same | **one card per game**: `#n [result] 24 turns` / `SEAT 2 name set` ×3 / reason. Header row hides visually; a **Sort** `<select>` (Game #, Turns longest first, Result) replaces it |
| Section index | one row | one row | one row | wraps |

Rules held: no fixed pixel widths on the page column, `minmax(0, …)` on every grid track, chips wrap with `overflow-wrap: anywhere`, panel meta text wraps (`white-space: normal`) in the log toolbar.

---

## 9. Motion

Vocabulary is the kit's: `--dur-fast` 100ms, `--dur-base` 160ms, `--dur-slow` 320ms, `--ease`, `--stagger` 40ms.

| What | Motion | Why |
|---|---|---|
| Selected mode tile | border + fill transition, `--dur-fast` | confirms the choice |
| Tray chevron | rotates 90°, `--dur-fast` (existing) | |
| Job bar | `transform: scaleX`, `--dur-slow` per finished game | each game *lands* |
| Queued / Oracle phases | existing `job-slide` | indeterminate |
| Land-drop and U fills | existing `comp-grow`, staggered by `--i` | report entrance |
| Combobox list | none (instant) | typing speed |

All animate `transform` / `opacity` only. **`prefers-reduced-motion`:** the job bar steps without transition, tile transition off, `comp-grow` and `job-slide` already have static fallbacks in `hud.css`.

---

## 10. Accessibility and keyboard

| | |
|---|---|
| Tab `8` | works anywhere not in a text field (existing guard `inTextField`) |
| Tiles / seg | real radios, visually hidden inputs, `:focus-visible` outline `--focus-ring` on the label; arrow keys move within the group (native) |
| Combobox | §4.2; selected option `aria-selected`, disabled rows `aria-disabled` |
| Textarea | labelled; ⌘/Ctrl+Enter submits; counter `aria-live="off"` (no chatter) |
| Job | `aria-live="polite"`; takes focus on start; title text is the announcement (“Simulating game 7 of 20”), announced at most once per game |
| Report | `<article>` with `aria-labelledby` per section; tracks `role="img"` with value in label |
| Chips | words + glyph (`▼ Below math`, `■ Up to date`); the glyph is decoration |
| Contrast | kit pairs only: amber on `--signal-dim`, cyan on `--data-dim`, `--text-3` on surface-1 (5.8:1) |
| Table | `<th scope="col">`, sort buttons, `aria-sort`; phone card layout keeps DOM order and `data-value` so screen readers get the same row |

---

## 11. Data the UI needs (proposal to SM-3; shape, not values)

`GET /api/decks/{id}/sim` → `{ success, exists, engine: {ok}, pool: {total, precons: [{name, set, year}], synced: [{name, status}]}, sim: Report | null }`

```jsonc
Report = {
  "created_at": "...", "stale": false, "stale_reason": "3 cards swapped (+A, +B, −C)",
  "params": { "opponent_mode": "standard|random|specific", "games": 20, "opponents": ["…"], "notes": "…" },
  "summary": { "games": 20, "user_wins": 12, "draws": 1, "win_rate": 0.6, "win_rate_ci95": [0.39, 0.78],
               "avg_total_turns": 18.4, "fastest": 9, "distinct_pods": 20 },
  "curve_tempo": { "metrics_available": true,
    "land_drop": [{ "turn": 1, "sim": 0.95, "static": 0.97, "read": "in_line|worse|better" }, …],
    "rows": [{ "key": "t5_starvation", "label": "…", "hint": "…", "sim": 0.45, "static": 0.38, "unit": "pct|turn|n",
               "read": "worse", "read_label": "Worse than math" }, …] },
  "cards": { "metrics_available": true, "unjudged_n": 62, "deck_size": 99,
    "dead_draws": [{ "name": "…", "uid": "…", "u": 0.08, "note": "…", "static_score": 0.31, "in_deck": true }],
    "stragglers": [ … ], "workhorses": [{ …, "also_static_strong": true }] },
  "games_log": [{ "game": 1, "opponents": [{ "seat": 2, "deck": "Cavalry Charge", "set": "MOC", "year": 2023, "ai": "Reckless" }, …],
                  "winner": "…", "winner_seat": 1, "user_won": true, "total_turns": 25, "win_reason": "…" }],
  "oracle": { "status": "ok|offline|invalid", "headline": "…", "notes_answer": "…", "static_comparison": "…", "tweaks": ["…"] }
}
```

`POST .../sim/run {opponent_mode, games, opponents?, notes?}` → `202 {job_id}` · `409 {error_kind:"busy", job_id?, deck?}`.
`GET .../sim/jobs/{id}` → `{status: queued|running|oracle|done|failed, games_completed, games_total, created_at, updated_at, error?}`.

Notes for SM-3: strip `[SET] [year]` from daemon deck names into `set`/`year`; `read`/bucket logic and the "can't be explained by N games" threshold live server-side; `metrics_available` must be false whenever parser metrics are the quiet-mode zeros.

---

## 12. Deliberately not doing

- **Cancel run.** No daemon endpoint.
- **Live per-game results while running.** The log appears with the report. A streaming table is a good v2 (the daemon already has `games_completed`).
- **Run history / diff between two runs.** One saved report, like Review. A "vs last run" strip is the natural next card.
- **Pod preview in random mode.** It would contradict "fresh every game".
- **Win rate by opponent / by pod.** Needs more games than 20 to mean anything.
- **Header action button.** See §3.
- **Modal confirm.** See §4.3.
- **Stale = dimmed report.** Stays readable; the chip and note are enough.

---

## 13. Checklist for SM-4 and the reviewer

1. Tab `8` navigates from every other deck tab; `/deck/{name}/sim` has `aria-current` on Simulate; keys 1–7 still work.
2. Empty: panel open, standard selected, `Run simulation`. Saved: tray closed, label `Run new simulation`, opens prefilled, notes blank.
3. Switching mode swaps the detail block; specific: Run with an empty seat → inline error + focus, no request.
4. Combobox: type to filter, ↑↓/Enter/Esc, disabled `in Seat 2` row, grouped headers.
5. During a run the old report is still on screen, unchanged; job shows `Simulating game X of N`, bar width = completed/total; reload mid-run resumes the job.
6. Fresh and stale chips; stale shows the note and `cut since` chips.
7. Random-mode report: section 04 rows show different pods per game; 02 and 03 show *Not recorded* (not zeros) while `metrics_available` is false.
8. Section 04 sorts on `#`, seats, winner, turns, reason; filter seg works; `aria-sort` correct.
9. Hover on any card name in 01 and 03 shows the preview; focus does too.
10. 1440 and 390: `scrollWidth == clientWidth` in empty / running / fresh / stale / random. Phone game log renders as cards with a Sort select.
11. `prefers-reduced-motion`: no bar transition, no fill animation.
12. Render tests (SM-4): empty, fresh, stale, random per-game pods, not-recorded 02/03, offline banner.
