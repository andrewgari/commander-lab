# CR UX spec: Single Card Review (deck view)

Card: `t_54629bee` (CR-D). Owner: designer. Coders build against this file.
Mockup: `docs/design/card-review/review.html`. Open it straight from disk; it loads the
real `static/css/hud.css` and `static/js/hud.js`. Pick any state from the **State** menu,
flip **Digital / Physical**, or hit **Play live** to watch a scripted review stream in.
Screenshots: `docs/design/card-review/shots/` (`d-*` 1440px, `m-*` 390px).
Adversarial pass: `docs/design/card-review/reviews/CR-D-adversarial.md`.

What is real in the mockup: the Holy Hellfire deck, card names, CMC, colours and Scryfall
ids. What is illustrative: every EDHREC %, strength score, price, ownership, argument and
ruling. They are the **shape** the API should return (section 8), not values it will.

Design system: the shipped HUD kit (`docs/DESIGN_TOKENS.md`, `static/css/hud.css`). Dark
slate, amber `--signal` as the only warm accent, cyan `--data` as the only cool one, mono
for numbers, 1px hairlines, 0 to 3px corners, no glow, no blur, no gradients. **No new
colours and no new fonts.** Everything new is a `hud-cr-*` class built from existing tokens.

If something isn't covered here, comment on the card and do the simplest thing that fits
this doc. Don't invent new UX.

---

## 0. Decisions (the short version)

| # | Decision | Why |
|---|---|---|
| D1 | **Three ways in, one flow.** Card detail modal gets a primary **Review card** button. Cards get a right-click / `⋯` menu with **Review card**. `R` on a focused card. | The modal button is the only path that works on touch and for screen readers; the menu and key are the power-user paths. All three call the same `openReview(card)`. |
| D2 | **Review is a dialog, not a page.** It reuses `.hud-modal` / `.hud-modal-dialog` at 64rem. The card detail modal is *replaced*, not stacked (a `← Card detail` button goes back). | HUD's Esc handler closes every open modal, so stacking would drop both. The deck stays visible behind the scrim. |
| D3 | **The card stays on the left; the stage on the right morphs** (deliberation, then verdict, then replacements). One dialog, three acts. | The user's eye never leaves the card they asked about. |
| D4 | **Remove is amber, Keep is green. Never red for a ruling.** Red (`--danger`) is only for failures. | "Remove" deletes nothing and a low score is advice, not an error (AN-D D8). |
| D5 | **Every colour is paired with a word or shape.** `KEEP` / `REMOVE`, `= Like-for-like` / `↻ Function change`, `● Owned` / `○ Not owned`. | WCAG 1.4.1 and colour-blind safe. |
| D6 | **Closing the dialog never cancels the review.** `Cancel review` is a separate, explicit button. | A 25 second job shouldn't die because someone pressed Esc. Reopening the card resumes it. |
| D7 | **Replacements load as a second step** after the verdict, with their own loading and error state. A replacement failure never discards the ruling. | The ruling is the valuable part. |
| D8 | **Digital: "Swap into deck". Physical: "Add to want/buy list".** If a physical replacement is owned and free, the action reads "Pull from collection". Both write to the existing Tune decisions (section 8.4). | The brief asks for the want/buy label on physical decks. The `own`/`buy` split is already how Tune thinks about it. |
| D9 | **One pick per review.** After one replacement is chosen, the others' primary action becomes "Use this instead". | A cut has exactly one add. |
| D10 | **Streamed arguments appear whole, not per character.** A token caret on the active argument is the only per-token motion, and the screen-reader live region announces phases and the verdict, never tokens. | Per-character typing is a gimmick for sighted users and noise for everyone else. |

---

## 1. Flow and state machine

```
 card ──[Review]──▶ RUNNING ──▶ VERDICT ──┬─ remove ─▶ REPLACEMENTS ──┬─ ready ──▶ pick / skip
                      │  │                │                            ├─ empty
                      │  └─ error ◀───────┤                            └─ error (verdict kept)
                      │                   └─ keep ───▶ "No replacement needed" (+ Show alternatives)
                      └─ blocked (commander, basic land, not in the 99)
```

`RUNNING` has four phases, in order: **1 Evidence · 2 Opening · 3 Rebuttal · 4 Ruling**.
Typical runtime is 10 to 25 s. At 30 s the message turns into "taking longer than usual",
at 90 s the job fails with `timeout`.

State names used by the mockup (`mock.js`, the **State** menu) are the same names to use in
tests: `run-1-evidence … run-4-ruling`, `run-slow`, `verdict-loading`, `verdict-remove`,
`remove-chosen`, `remove-closecall`, `repl-empty`, `repl-error`, `verdict-keep`, `degraded`,
`stale`, `err-offline`, `err-timeout`, `err-badruling`, `err-gone`, `err-network`,
`blocked-cmdr`.

---

## 2. Entry points

### 2.1 Card detail modal (all devices): the canonical path

`#cardModal` gains an action row under the Lab tags:

```
[ Review card  R ]  [ Edit tags ]
Two sides argue keep versus remove. If it's a remove, you get replacements for your want/buy list.
```

- `Review card` is the only `hud-btn--primary` in the modal.
- The helper line changes with deck type: physical says "…for your want/buy list."
- Unreviewable card (section 7.3): button gets `aria-disabled="true"`, and the helper
  line says why ("Commanders can't be reviewed."). Don't hide it, so the reason is findable.
- Phone: the row is `position: sticky; bottom: 0` with 44px buttons (`--cr-touch`). Add
  `hud-cr-modal` to `#cardModal` so it becomes a full-height sheet at ≤ 640px too.

### 2.2 Card context menu (desktop and mobile)

Stock `.hud-menu` (no new component). Items:

| Item | Key | Notes |
|---|---|---|
| Card detail | `↵` | existing behaviour |
| **Review card** | `R` | amber text (`.hud-menu-item--primary`) |
| — separator — | | |
| Edit tags… | | opens detail focused on the tag field |
| View on EDHREC ↗ | | existing link |

Triggers, all open the same menu:

- **Right-click** on a frame or table row. Don't `preventDefault` when `Shift` is held, so
  the browser menu stays reachable.
- **`⋯` button** at the card's top-right: `.hud-cr-more`. It is a *sibling* of the
  `role="button"` frame, never inside it (a button inside a button is invalid).
  Fine pointer: appears on hover and `focus-within`. Coarse pointer
  (`@media (hover: none), (pointer: coarse)`): always visible, 44×44px.
- **`ContextMenu` key / `Shift+F10`** on a focused card.
- **List view**: add a trailing 44px column with the same `⋯` button
  (`aria-label="Actions for {name}"`). Don't rely on row hover.

Menu actions are `<button class="hud-menu-item">`. The stock class was written for `<a>`, so
`card-review.css` adds a `button.hud-menu-item` reset. Keep it.

**Why not long-press on mobile:** browsers own long-press on images (save/drag menu) and
it is undiscoverable. The visible `⋯` is the mobile path. Tap on the frame still opens
detail.

### 2.3 Keyboard

`R` on a focused card (not while a text field has focus, and not with ctrl/meta/alt) calls
`openReview`. Inside the dialog (replacements list): `j`/`k` move, `a` acts, `x` skips,
`Esc` closes. These are the same keys as Tune (`j k a x`), on purpose.

### 2.4 Result marker on the grid (optional, P2)

Once a card has a saved review, show a small chip over its frame (`.hud-cr-mark`): amber
`Remove?` or green `Kept`. A review in flight shows `Reviewing…` (neutral). This is what
makes D6 (close without cancelling) discoverable. If it slips, the dialog still resumes
when the card is reopened.

---

## 3. The dialog

### 3.1 Desktop (≥ 901px): `grid-template-columns: 15rem 1fr`, gap `--sp-6`

```
┌ CARD REVIEW ───────────────────────────── ← Card detail   Close ✕ ┐
│ [card art]      ▪ VIRTUAL  Digital deck. Suggestions queue onto…   │
│  Steel Hellkite  ┌ VERDICT ──────────────────────────────────────┐ │
│  CMC 6           │ REMOVE high confidence       CONFIDENCE  78%  │ │
│ TYPE   …         │ judge line (1 sentence)      STRENGTH  34/100 │ │
│ ROLE   [chip]    │ [role chip] 9 in deck…       ▮▮▮▯│ median 62  │ │
│ IN DECK …        │ EDHREC ▬▬   FIT ▬▬   OVERLAP ▬▬               │ │
│  (sticky)        └───────────────────────────────────────────────┘ │
│                  ┌ CASE TO KEEP  overruled ┬ CASE TO REMOVE ruled ┐ │
│                  └─────────────────────────┴──────────────────────┘ │
│                  REPLACE STEEL HELLKITE · 4      2 like-for-like…  │
│                  ┌ thumb │ name · kind · rationale │ [Swap]  Skip ┐ │
└────────────────────────────────────────────────────────────────────┘
```

- The left column is `position: sticky; top: --sp-4`, so the card stays beside a long list.
- `--cr-dialog-w: 64rem`. The card detail modal stays at `--modal-w` (56rem).
- The scope line (chip + hint) sits first in the stage and states what the actions will do.

### 3.2 Tablet (≤ 900px)

Single column. The subject becomes a row: 8rem art on the left, key/value on the right.
Replacement rows keep thumb + body, and the action column drops under them as a full row.

### 3.3 Phone (≤ 640px): full-height sheet

- `.hud-cr-modal` loses its padding; the dialog is `min-height: 100dvh`, no border, no
  radius, and slides up (`cr-sheet`, 320ms, transform + opacity).
- Panel head is sticky, 52px, with 44px buttons.
- The subject collapses into a **sticky identity bar**: 40px thumb, card name, type, and the
  verdict chip once there is one. It's the context you keep while scrolling replacements.
- Debate becomes **one chronological feed**, each argument prefixed `KEEP · OPENING`.
- Verdict cases stack. The side that won is first and open; the other is a collapsed
  `<details>` ("Case to keep" `+`).
- Replacement rows: 4.5rem thumb, body, then a full-width action row with 44px buttons.
- The `j k a x` hint row is hidden (no keyboard).

---

## 4. Deliberation (running)

Four things, top to bottom. Build them so each resolves in place (no layout jump).

**4.1 Job panel** (`.hud-job`, existing). Title "Reviewing {card}", a mono `mm:ss` clock,
the 4-segment stepper (`.hud-cr-steps`), the indeterminate track, one sentence of status,
and the actions row:

| Step | Message |
|---|---|
| 1 | Pulling EDHREC inclusion and similarity to the rest of the deck. |
| 2 | Each side makes its opening case. |
| 3 | Each side answers the other. |
| 4 | The judge weighs both cases. |
| ≥ 30 s | Taking longer than usual. The model is queued behind another request. You can keep waiting, or close this and come back: the review finishes in the background. |

Footer line: "Closing this window does not cancel the review." Button: `Cancel review` (ghost).
Stepper segments: done = `--ok` with `✓`, active = `--signal`, pending = `--surface-3`. The
step label is always shown (never colour alone).

**4.2 Evidence strip** (`.hud-cr-evidence`, 3 cells). EDHREC inclusion, Fit to deck (HF
embedding similarity, plus the nearest card), Role. Each cell is dashed with `…` until its
value arrives, then it rises in (`cr-rise`). A missing value (no EDHREC data) shows
`no data` in `--text-3`, not an error.

**4.3 The debate** (`.hud-cr-debate`). Two sides on a 1px gutter: **Keep** (green top
bar) left, **Remove** (amber top bar) right. Each side lists its arguments as they arrive:

- Each argument: a mono tag (`OPENING` / `REBUTTAL`), then ≤ 280 characters of plain text.
- The side currently speaking shows `ARGUING ●●●` (pulsing dots); a finished side shows
  `RESTED`; a side yet to speak shows `WAITING`.
- While tokens stream, the active argument has a `▍` caret.
- A dashed empty box marks "not said yet" (the kit bans gradient skeletons).
- A one-line **judge strip** under the debate: `JUDGE · Listening → Deliberating`.

**4.4 Transport modes, same DOM.** *Stream* (SSE): arguments grow token by token. *Poll*:
the stepper advances and arguments appear whole when each phase completes. If the backend
only returns at the end, show the stepper and evidence only, and render both sides at
once when done. Don't fake a stream.

**4.5 Reduced motion.** No rise, no pulse, no caret blink. The indeterminate track is a
static half-filled bar. The stepper and text still change.

---

## 5. Verdict card (`.hud-cr-verdict`)

Left block: the ruling, then the **one-sentence judge line**, then the role.
Right block: Confidence, Strength, Deck median. Under both: three signal rows.

| Element | Spec |
|---|---|
| Ruling | `REMOVE` or `KEEP`. `--fs-hero` (44px), mono, semibold. Colour = amber / green. 26px on phone. Left bar of the card takes the same colour. |
| Confidence | `78%` plus a bar, and the band word beside the ruling: **High ≥ 75, Medium 55 to 74, Low < 55**. Bar colour = ruling colour. |
| Strength | `34 / 100 · Weak` plus a bar with a **tick at the deck median** and "DECK MEDIAN 62 · BELOW". Bands: Strong ≥ 80, Solid 60 to 79, Fair 40 to 59, Weak < 40. Bar is cyan (`--data`): it is a measurement, not a verdict. |
| Role | `.hud-chip--data` ("Top-end threat") plus a hint: "9 in deck · target 7 to 9". |
| Signals | The stock `.hud-why` micro-bars: EDHREC %, Fit to deck, Role overlap. A low value turns the bar amber (`.is-low`). |
| Cases | Two columns, "Case to keep" and "Case to remove", 2 to 3 reasons each. Each reason is one sentence plus a ghost chip citing its evidence (`EDHREC 12%`). The side that won gets `--surface-2` and a `Ruled for` chip; the other gets `Overruled`. |

Notes that appear **above** the verdict card, inline, never as toasts (`.hud-cr-note`):

| Condition | Note |
|---|---|
| No EDHREC data | "No EDHREC data for {card} in this commander. The ruling used deck fit and oracle text only, so confidence is capped at Medium." (info) |
| Saved review, deck changed since | "The deck changed since this review (N cards swapped). The ruling may no longer hold." + `Re-run review` (amber) |
| Confidence < 60 and ruling is Remove | In the replacements section: "Close call. Keep is defensible. Treat these as options, not a recommendation." (amber) |

`Reviewed {when}` sits at the right of the scope line.

### Keep verdict

Replaces the replacements list with an info card: "Keep {card}. No replacement needed."
and one button, `Show alternatives anyway`, which loads the same list as a Remove would.

---

## 6. Replacements (`.hud-cr-list`)

Header: `Replace {card} · 4`, and on the right `2 like-for-like · 2 function change`.

Each row (`.hud-cr-item`), desktop columns `6rem | 1fr | 13rem`:

| Part | Spec |
|---|---|
| Thumb | Scryfall `normal` image, `--card-ratio`, `--radius-2`, `alt` = card name. 6rem wide (4.5rem on phone). |
| Name line | `.hud-card-name` (EB Garamond, `--fs-md`), then `CMC n` and pips. |
| **Kind chip** | `= Like-for-like` (`--data`) or `↻ Function change` (`--signal`). Always both a glyph and a word. |
| **Role** | Like-for-like: the shared role ("Top-end threat"). Function change: `~~Top-end threat~~ → Extra combat` (old role struck through, new role plain). |
| Rationale | 1 to 2 sentences, ≤ 160 characters, `max-width: 60ch`. |
| Facts line | Mono micro: `EDHREC 24% · $4.10`, plus, on **physical decks only**: `● Owned · box, free` / `◐ In Dawnbringer` / `○ Not owned`. A cap flag if one applies (`OVER SOFT CAP $15`). |
| Oracle text | `<details>` disclosure, closed by default. |
| Actions | Pick button + ghost `Skip`. Labels below. **Only the current row's pick button is amber** (the first row by default, then wherever `j`/`k` put the cursor); the rest are neutral. Four amber buttons in a stack mean none of them is the primary. |

**Primary action label**

| Deck | Replacement | Label | Tune decision |
|---|---|---|---|
| Digital | any | **Swap into deck** | `own` |
| Physical | not owned, or owned elsewhere | **Add to want/buy list** | `buy` |
| Physical | owned and free | **Pull from collection** | `own` |
| any | after one is chosen | **Use this instead** (secondary style) | replaces the pick |

**Row states** (all via classes, not new components)

| State | Look | Behaviour |
|---|---|---|
| default | surface-1 | |
| current (keyboard) | `--surface-2` + amber left bar | `j`/`k` move it; first row starts current |
| chosen | `--ok-dim` + green left bar, chip `Queued` / `On list` / `Pulled`, `Undo` | adds a confirmation strip above the list ("Steel Hellkite → X added to your want/buy list" + link to the list / change list) |
| skipped | thumb + body at 45% opacity, `Restore` | **never dim the buttons** (AA) |

Footer under the list: `None of these? [Show more] [Widen search]`, and the key hints
`j k move · a swap/add · x skip · esc close` (desktop only).

The list is sorted by model rank. Like-for-like and function-change are interleaved by
rank, not grouped: the user cares about "is this good", and the chip says what kind.

**Loading** (`aria-busy`): three dashed ghost rows with the same geometry as real rows,
fading 0.9s (`cr-fade`), and a status line "Finding replacements that keep its job, or
change it if the deck needs something else…".

---

## 7. Empty, error and blocked states

All are inline in the stage (never toasts), built from `.hud-job.is-failed` (errors that
interrupt a run) or `.hud-cr-state` (everything else). The card on the left never
disappears, so the user always knows what failed.

### 7.1 Empty

| State | Heading | Body | Actions |
|---|---|---|---|
| Replacements empty | No better options found | Why (from the API): "Every candidate in W/B/R under your $15 soft cap was either already in the deck or scored below {card}'s own 34. Widening the search drops the cap and allows off-role cards." | `Widen search` · `Remove without replacing` (physical: `Mark as cut, no replacement`) · `Re-run` |
| Keep verdict | Keep {card}. No replacement needed. | | `Show alternatives anyway` |
| No EDHREC data | (a note, not a state) see section 5 | | |

### 7.2 Errors

| Code | Where | Heading | What it keeps | Actions |
|---|---|---|---|---|
| `offline` (503) | start | The review engine is offline | Shows the **numbers-only** card: EDHREC, strength, role (deterministic, no model) | `Try again` |
| `timeout` (504, 90 s) | running | The review timed out after 90 seconds | Numbers-only card; evidence is cached so retry is faster | `Try again` |
| `failed` (502) | ruling | The judge couldn't reach a ruling | **Evidence and both arguments stay on screen**; judge strip says `No ruling` | `Re-run the ruling only` · `Start over` |
| `gone` (404) | any | This card is no longer in the deck | | `Back to the deck` |
| `network` | streaming | Connection lost | whatever had arrived | `Reconnect now` (the job continues server-side) |
| replacements 502 | after verdict | Couldn't load replacements | **The verdict stays.** Only the list is replaced by the error | `Try again` · `Mark as cut, no replacement` |

Rules: the heading says what happened in plain words, the body says what it means for the
user's deck ("Nothing was changed"), the technical code is in a `<code>` at the end of
the body, and the primary action is always a retry. `role="alert"` on the failed job.

### 7.3 Blocked (not reviewable)

| Card | Heading | Body |
|---|---|---|
| Commander / partner / companion | Commanders aren't reviewed | The commander defines what the rest of the deck is judged against, so there is nothing to argue for removing. Review any other card instead. |
| Basic land | Basic lands aren't reviewed | (same pattern) |
| Sideboard / Maybeboard / Considering | Not in the 99 | (structural categories, same rule as tag learning) |
| Pet (♥) | *allowed*, with a note | "♥ You marked this as a pet. The ruling is advisory and replacements are hidden until you ask." (pets are never suggested as cuts: AN-D D7) |

Blocked states are reached from the dialog only if someone gets there by key (`R`); the
menu item and the modal button should already be `aria-disabled` with the reason.

---

## 8. Data contract and transport (frontend's view)

The review API is a backend card. This is what the UI needs; the coder and the backend
agree on the final names.

### 8.1 Start / resume / poll

```
POST /api/decks/{deck_id}/card-review   {card: "Steel Hellkite", force?: bool}
  200 {review}                  cached, matches the deck fingerprint
  202 {job_id}                  started or already running for this card (resume)
  409 {error:"not_reviewable", reason:"commander"|"basic_land"|"not_in_main"}
  404 {error:"card_not_in_deck"}
  503 {error:"oracle_unavailable"}

GET  /api/decks/{deck_id}/card-review/{job_id}          poll:  {status, phase, evidence, arguments[], verdict?, replacements?, error?}
GET  /api/decks/{deck_id}/card-review/{job_id}/events   SSE (optional)
```

SSE events: `evidence {key,main,sub}` · `phase {name: evidence|opening|rebuttal|ruling}` ·
`argument {id, side, round, text, done}` (text may arrive as deltas) ·
`verdict {…}` · `replacements {state, items[]|reason}` · `error {code}`.
The UI must work from poll alone. SSE only makes it feel live.

### 8.2 Verdict

```
{ ruling: "keep"|"remove", confidence: 0..1, strength: 0..100, deck_median: 0..100,
  judge_line: str ≤ 160,
  role: {label, detail?},
  signals: [{label, v: 0..1, text, low?}],            // EDHREC, Fit to deck, Role overlap
  reasons: {keep:[{text, cite?}], remove:[{text, cite?}]},   // 1 to 3 each
  notes: [{tone:"info"|"signal", text, action?}],
  reviewed_at, deck_fingerprint, stale: bool }
```

### 8.3 Replacement

```
{ name, uid, cmc, colors, mana_cost?, oracle?,
  kind: "like_for_like"|"function_change",
  role, from_role,                                   // from_role is the reviewed card's role
  rationale: str ≤ 160, edhrec: 0..100, price: number|null,
  ownership: "owned_free"|"owned_elsewhere"|"not_owned",   // only meaningful on physical decks
  where?: "box, free"|"Dawnbringer", flag?: "Over soft cap $15" }
```

### 8.4 Where the action writes

There is no write path from this app to Archidekt. **v1 queues onto the Tune change list**
through the existing endpoint:

```
PUT /api/decks/{deck_id}/tune/decisions
    {cut: "Steel Hellkite", decision: "own"|"buy"|"dismissed"|null, add: "Scourge of the Throne"}
```

`Undo` sends `decision: null`. `Skip` sends `dismissed` for that add. The confirmation strip
links to the Tune change list. **This is the one open question for Andrew** (section 11).

### 8.5 Trust rules for the model text (backend, but it shapes the UI)

- Arguments and reasons are **plain text**, escaped. No markdown.
- Any number inside an argument (`12%`, `strength 34`) must come from the evidence the
  backend gave the model. A mismatch between an argument and the evidence strip is the
  fastest way to lose trust. Validate before streaming.
- `confidence` is the judge's own number, capped at 0.65 when EDHREC data is missing.

---

## 9. Implementation notes

### 9.1 Files

| Do | Where |
|---|---|
| Port `card-review.css` into `static/css/hud.css` as a new section "Card review". Class names are final. Move the `:root` block into the token block. Delete the `.mock-*` rules. | `static/css/hud.css` |
| Lift `card-review.js` into `static/js/card_review.js`. It is a view layer: `CardReview.render(state)` returns HTML, `CardReview.show(modal, state)` paints, `CardReview.onAction(fn)` receives every `data-cr` action. Wire it to the transport in 8.1. | `static/js/` |
| Add the entry points (2.1, 2.2), the `#reviewModal` shell (copy from `review.html`), and `hud-cr-modal` on `#cardModal`. | `templates/deck.html` |
| Add `docs/DESIGN_TOKENS.md` rows for the new tokens. | `docs/DESIGN_TOKENS.md` |

New tokens: `--cr-dialog-w` 64rem · `--cr-subject-w` 15rem · `--cr-thumb-w` 6rem · `--cr-thumb-w-sm` 4.5rem ·
`--cr-act-w` 13rem · `--cr-touch` 2.75rem · `--cr-keep` / `--cr-remove` (aliases of `--ok` / `--signal`).

The mockup uses `docs/design/analytics/analytics.css` for the shell tokens only. The app
already has `hud.css`, so don't carry that import over.

### 9.2 Four things the adversarial pass found that bite in real code

1. **Repaint rule.** The mockup repaints the whole dialog body on every event. That replays
   every entrance animation (a streamed argument flashes per token) and drops focus and
   scroll. `CardReview.show` fixes it with `data-k` keys: an element already on screen gets
   `animation: none`, and focus and scroll are restored. In the real app, prefer appending to
   the DOM per event. If you do repaint, keep the keys.
2. **`HUD.openModal` doesn't manage focus.** It doesn't move focus into the dialog, trap
   Tab, or return focus on close (verified: after opening, `document.activeElement` is
   `<body>` and Tab walks the page behind the scrim). `CardReview.enter(modal)` is a reference
   fix (focus the title, trap Tab, restore focus on close). Put it **inside** `HUD.openModal`
   so card detail and every other modal gets it.
3. **The live region must outlive the repaint.** It lives in the dialog shell (`data-cr-live`),
   outside the body that gets rewritten. Announce: "Opening arguments started", "Rebuttals
   started", "The judge is deliberating", "Verdict: remove, 78 percent confidence, strength
   34 of 100", "4 replacement options". Never announce tokens.

4. **One `.hud-menu` shared by many triggers breaks the arrow keys.** `HUD.initMenus` attaches
   the panel's key handler once per trigger, so 100 cards on one panel make `ArrowDown` fire
   100 times. Guard it in `hud.js` (`if (panel.__hudKeys) return;` before the panel listeners),
   or render one menu per card as the mockup does. Right-click needs a pointer-anchored
   variant of `placeMenu` (a small `HUD.openMenuAt(panel, x, y, returnFocusEl)`).

### 9.3 Keep out

- No spinner overlay. The job panel *is* the progress.
- No confetti, no sound, no auto-focus change when the verdict lands.
- No new colours. If you reach for one, use `--signal`, `--data`, `--ok` or a neutral.

---

## 10. Accessibility, motion, performance

- **Contrast.** All text uses `--text-1/2/3` on `--surface-*` (AA). The only `--text-4` uses
  are the disabled menu item and decorative `…`. `.is-skipped` dims the card, not the buttons.
- **Roles.** Dialog `role="dialog" aria-modal="true" aria-labelledby`. Failed job
  `role="alert"`. Stepper is an `<ol>` with `aria-current="step"`. Meters are
  `role="img"` with a full-sentence `aria-label` ("Strength 34 of 100, deck median 62").
  Replacement list is a `<ul>`; thumb `alt` is the card name. `Case to …` blocks are
  `<details>`/`<summary>`, so they work without JS.
- **Focus.** Dialog title is focused on open (9.2). Focus ring is the kit's solid 1px amber.
  The menu returns focus to its trigger on Esc (stock behaviour).
- **Reading order on phone.** The debate feed is visually chronological but the DOM is
  grouped by side (`display: contents` + `order: var(--seq)`). That's deliberate: a screen
  reader reads "Keep" then "Remove", which is easier to follow than interleaving.
- **Reduced motion.** `prefers-reduced-motion: reduce` removes: sheet slide, `cr-rise`,
  `cr-grow`, `cr-pulse`, `cr-fade`, the caret blink and the indeterminate slide. Verified:
  `document.getAnimations()` is empty in the running state.
- **Motion vocabulary.** Entrances `cr-rise` (6px, 320ms, `--ease`) staggered by `--stagger`
  (40ms) per item. Meters `cr-grow` (scaleX). Sheet `cr-sheet`. Only `transform` and
  `opacity` animate.
- **Touch.** Every control in the phone sheet is ≥ 44px (`--cr-touch`). Verified at 390px.
- **Performance.** Thumbs are `loading="lazy"` Scryfall `normal` images. No libraries.

---

## 11. Open question (one)

**What should "Swap into deck" do on a digital deck?** The app can't write to Archidekt.
*Recommendation (and what this spec assumes):* v1 queues the cut + add onto the **Tune
change list** (section 8.4), with `Undo`, and the strip says "queued on the change list".
That's honest, reversible, and already built. The alternative (a write-back to Archidekt)
is a backend and auth feature; if you want it, it's a separate card and only the label's
hint line changes.

Smaller things I decided and you can overrule: `Pull from collection` for owned
physical replacements (the brief only asked for the want/buy label), and pets being
reviewable but with replacements hidden.
