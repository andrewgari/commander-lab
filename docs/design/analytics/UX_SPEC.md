# AN UX spec: Stats, Identity (pillars), Journal + check-in, Tune

Card: `t_d032a937` (AN-D). Owner: designer. Coders build against this file.
Mockups: `docs/design/analytics/*.html`. Open them straight from disk; they
load the real `static/css/hud.css` and `static/js/hud.js`. Screenshots are in
`docs/design/analytics/shots/` (1440px desktop + 390px phone).
Inputs: `docs/ANALYTICS_SPEC.md`, `docs/DESIGN_REVIEW.md` (Direction C,
"Identity Lab"), `docs/DESIGN_TOKENS.md`, live prod at
`http://192.168.50.3:8020/deck/Holy%20Hellfire` (v0.4.6, 2026-10-12).

What's real in the mockups: the Holy Hellfire card list, CMC, colours,
Scryfall ids, and the curve (it matches prod). What's illustrative: edges,
engines, pillars, scores, EDHREC %, prices, and where owned cards live.

If something isn't covered here, comment on your card and do the simplest
thing that fits this doc. Don't invent new UX. I review every UI card after
it ships.

---

## 0. Decisions (the short version)

| # | Decision | Why |
|---|---|---|
| D1 | **Chart library: none for charts, `d3-force` only for the graph.** Bars, curve, composition, meters and histograms are the existing `HUD.barChart` / `HUD.rankList` / CSS. The graph uses `d3-force@3`, vendored as one file (17.9 KB min, 6.3 KB gz), plus ~200 lines of our own SVG code. | No build step and no new visual language. Chart.js/ECharts would bring their own look and 60–300 KB. Cytoscape is 137 KB gz. We only need the layout maths from d3, not d3's DOM. |
| D2 | **The graph is laid out once, synchronously (300 ticks), then frozen.** No live physics, no dragging in v1. | Stable picture means spatial memory. Reduced-motion users get the same image. There's no jitter on a 60-node graph. |
| D3 | **Four deck tabs, one header.** `Overview · Stats · Identity · Journal · Tune` with keys `1`–`5`. The URLs are `/deck/{name}` (existing), `/deck/{name}/stats`, `/deck/{name}/identity`, `/deck/{name}/journal` and `/deck/{name}/tune`. | Every AN surface is about one deck. Tabs keep the deck in context, and `1`–`5` is muscle memory. Manage stays where it is (Phase 1 moves it to a tab). |
| D4 | **Review = Journal check-in, then Tune.** The existing **Review** button on the deck page goes to `/deck/{name}/journal?review=1`. Finishing the check-in lands on Tune. | One linear flow ("is this still true?", then "here's what to change"). Spec §Layer 2. |
| D5 | **Owned-first is the default, and a buy has to earn its slot.** Show a buy only when `buy.score − best_owned.score ≥ 0.10`. Otherwise show "owned is good enough". | Matches "good enough" in the spec. 0.10 is a constant (`GAP_WORTH`), easy to tune later. |
| D6 | **Hard cap = hidden but counted. Soft cap = shown with a written justification.** | You should know a better card exists ("1 better buy hidden · over hard cap $40") without being sold it. |
| D7 | **Pets are never cut suggestions.** If a pet is the deck's weakest card, show one quiet row ("♥ Platinum Angel · pet · weakest card … never cut") so the tuner doesn't look broken. | Trust: the tuner obviously saw it and obeyed you. |
| D8 | **No red for "bad".** Short on the template = `--signal` (amber). Over = neutral. ±1 = ok. Danger red only means destructive or a hard limit (hard cap, Reject). | Direction C rule: scores are advice, not homework. |
| D9 | **Three token fixes ship with AN-4:** `--text-3 → #8a96a2`, `--danger → #ee6a61`, and `.hud-rank-fill/.hud-rank-track { display:block }`. | The first two fail AA (DESIGN_REVIEW §1). The third is a **live bug**: every ranked bar in the app renders as an empty track today, because the fill is an inline `<span>`. |

---

## 1. Information architecture

```
/deck/{name}              Overview   (existing HUD-4 page; gets the shared deck header + tabs)
/deck/{name}/stats        Stats      §3.1  graph · shape · identity · combos
/deck/{name}/identity     Identity   §3.2  thesis + pillars editor
/deck/{name}/journal      Journal    §3.3  form; ?review=1 opens the check-in above it
/deck/{name}/tune         Tune       §3.4  fixes · swaps · weak performers · change list
```

The tab counts are badges, not totals. Identity shows the number of
pillars (4). Tune shows the number of open suggestions (9: 3 fixes, 5 swaps
and 1 pet notice). When Tune has no open suggestions, hide the badge. Don't
show "0".

---

## 2. System

### 2.1 Tokens (new or changed, moving into the `hud.css` token block)

| Token | Value | Use |
|---|---|---|
| `--text-3` | `#8a96a2` (was `#76828e`) | **AA fix.** 5.8:1 on surface-1 |
| `--danger` | `#ee6a61` (was `#e5584f`) | **AA fix** on danger-dim |
| `--fs-display` | 4.5rem / 72px | reserved: thesis hero on the Lab (Phase 3). Identity uses `clamp(--fs-lg, 3.2vw, 36px)` |
| `--dur-slow` | 320ms | entrances |
| `--stagger` | 40ms | per-item entrance delay |
| `--graph-h` | 36rem | graph plot height, desktop |
| `--graph-node-min` / `-max` | 5 / 18 (px, unitless, read by JS) | node radius by √degree |
| `--graph-edge` | `var(--line-strong)` | resting edge |
| `--graph-dim` | 0.14 | opacity of non-neighbours while focused |

The existing tokens are unchanged. No new colours: the graph uses
`--signal` (hot/commander), `--data`/`--data-dim` (engine members) and
`--surface-3`/`--line-strong` (rest).

### 2.2 Colour meaning on these pages (fixed, don't improvise)

| Colour | Means | Never means |
|---|---|---|
| `--signal` amber | commander · the current focus · "short of target" · Pillar 1 hero fill · primary action | "bad" |
| `--data` cyan | engine membership · pillar tags · chart bars | status |
| `--ok` green | owned / free · accepted · "still true" · within ±1 of target | "best" |
| `--danger` red | hard cap · Reject · destructive | low score |
| `--text-4` grey | generic (fits no pillar) | |
| mana `--mana-*` | colour identity bar only | decoration |

### 2.3 Libraries

- Vendor `docs/design/analytics/vendor/d3-force-3.0.0.bundle.min.js` to
  `static/vendor/d3-force-3.0.0.min.js`. The file is the jsDelivr combine of
  d3-dispatch@3, d3-quadtree@3, d3-timer@3 and d3-force@3, and it exposes
  `window.d3`. **Load it only on `/stats`** (`{% block extra_js %}`), never
  in `base.html`.
- Port `docs/design/analytics/graph.js` to `HUD.graph` in `static/js/hud.js`,
  keeping the API (§3.1.4). It's ~200 lines with no other dependencies.
- No chart library. Everything else is an existing kit builder plus the
  classes in `analytics.css`.

### 2.4 CSS

`docs/design/analytics/analytics.css` becomes **`hud.css` section 7
"Analytics II"**. The class names are final. Port it top to bottom, but:
- drop `.mock-note`;
- move the `:root` block into the token block (§2.1);
- section 7 at the bottom ("Kit fixes") patches existing kit rules, so apply
  those in place;
- `docs/DESIGN_TOKENS.md` gets the new tokens and a "Component kit" row per
  new root class: `hud-graph`, `hud-engine`, `hud-inspector`, `hud-comp`,
  `hud-meter`, `hud-pillars`/`hud-pillar`, `hud-thesis`, `hud-tray`,
  `hud-form`/`hud-field`/`hud-seg`/`hud-picker`/`hud-money`, `hud-savebar`,
  `hud-checkin-head`/`hud-checklist`/`hud-check`, `hud-swap`/`hud-opts`/`hud-opt`,
  `hud-why`, `hud-fix`, `hud-changes`, `hud-combos`, `hud-kbd`, `hud-freshness`.
- Add the matching Jinja macros to `partials/hud.html` **only** where a
  surface is server-rendered (the Journal form). Stats, Identity and Tune
  render from JSON, like the deck view does now. (Identity moved to JSON in
  AN-4, because every edit needs the rescored payload; approved in AN-4R.)

### 2.5 Motion vocabulary (that's all of it)

| Motion | Where | Spec |
|---|---|---|
| Rise | pillar tiles, page sections | `opacity 0→1, translateY 8px→0`, `--dur-slow`, stagger `--i × --stagger` |
| Grow | composition bars | `scaleX 0→1` from the left, `--dur-slow`, staggered |
| Pop | graph nodes on first paint | `scale .4→1`, 12ms stagger by degree (hubs first) |
| Fade-focus | graph engine/hover focus | non-members fade to `--graph-dim`, `--dur-base` |
| Press | buttons | the existing `.hud-btn` behaviour |

`prefers-reduced-motion: reduce` turns all five off (the rules are at the
bottom of `analytics.css`). Only `transform` and `opacity` animate. The graph
viewBox **jumps** on engine focus. Don't tween it, because that would
re-layout every text node each frame.

---

## 3. Surfaces

### 3.0 Shared deck header + tabs (all five tabs)

The markup is in `mock.js → deckhead()`. Make it a partial,
`partials/deck_head.html`, and include it from `deck.html` too.

- Breadcrumb `Decks / {deck} / {Tab}` in `.app-topbar`. The right side shows
  freshness: `Archidekt {age}` and `Graph {age}` (the graph's age only once
  AN-1 exists). At ≤ 640px, freshness hides.
- Art (art_crop, 88px), an eyebrow `Commander deck · {commander}`, an H1 with
  the deck name, pips, a status chip, and `{n} cards · {lands} lands`.
- A per-tab action slot on the right. At most one primary button.
- `.hud-tabs.hud-deck-tabs` with `data-key` 1–5. The global keydown handler
  ignores keys while focus is in an input, textarea, select or
  contenteditable element.

### 3.1 Stats (`stats.html`)

Order, top to bottom. It answers "what is this deck" before "what's wrong
with it".

1. **Stat strip** (`HUD.stat` ×6). The first tile is `hero signal`:
   On-plan %, Engines (`{loops} loops · {chains} chains`), Edges
   (`{per card}`), EDHREC overlap % ("lower = more yours"), Combos (in deck,
   `+N one card away`), and Avg CMC.
2. **01 Interactions:** the graph block (§3.1.1–3.1.5) plus the Isolated tray.
3. **02 Shape:** Composition vs template (left, 1.25fr) and Mana curve
   (right). Panels align to the top; they don't stretch.
4. **03 Identity:** Pillar fit, Least on-plan and EDHREC inclusion, in a
   3-up row (2-up ≤ 1380px, 1-up ≤ 900px).
5. **04 Combos.**

#### 3.1.1 Graph layout

It's a three-column grid: Engines (14rem) | Stage (fluid) | Inspector (17rem).
At ≤ 1380px the Inspector drops below as a full-width row in three columns.
At ≤ 640px, see §3.1.5.

**Data in:** `nodes[{id: card name, degree}]` and
`edges[{source, target, reasons[]}]`, where *source's output feeds target's
input* (directed). `engines[{id, name, flow, kind: chain|loop, pillar|null, cards[]}]`
(AN-2 shape; the pillar is the majority AN-3 fit pillar among members at ≥ 40%).

**Default filter:** cards whose **only** edges are mana/land reasons
(`mana`, `land`, `ramp`) are hidden. The "Show mana" checkbox brings them
back, and its state goes in the URL as `?mana=1`. Without this filter, every
signet connects to everything and the graph turns into a hairball.

**Encoding:**

| Thing | Encoding |
|---|---|
| Node radius | `min + (max − min) · √(degree / maxDegree)`. Commander = max + 4 |
| Node fill | commander `--signal`; engine member (while an engine is focused) `--data-dim`, stroke `--data`; the rest `--surface-3`, stroke `--line-strong` |
| Node label | EB Garamond 11px, the card name **before the first comma** ("Razaketh", "Isshin"), with a 4px `--surface-1` halo (`paint-order: stroke`). Shown only when degree ≥ 3 at rest; while something is focused, shown for all focused nodes and hidden for the rest |
| Edge width | `0.75 + 0.75 × reasons.length` px |
| Edge colour | rest `--graph-edge`; focused `--signal`, with an arrowhead at the target |
| Edge label | mono 10px uppercase in `--signal-strong`, with the reasons joined by ` · `. **Only on focused edges.** See the label rules below |

**Edge-label rules** (the hard part, so follow them exactly):
1. Group the focused edges by `(hub, reasons)`, where the hub is the endpoint
   with the higher degree. Label **one** edge per group (the middle one) and
   append `×N` when N > 1. Kaalia's 30 "cheat into play" spokes become one
   label, `CHEAT INTO PLAY · ETB VALUE ×9`.
2. Greedy declutter: sort the candidates by `reasons.length` (descending),
   then drop any label whose box intersects a visible node or a label
   that's already placed. The inspector lists every reason, so nothing is
   lost.

**Layout:**
`forceLink(distance 70 + 18/reasons, strength .35)`,
`manyBody(−340)`, `collide(r + 26, 2 iterations)`, `x/y(center, .06/.09)`,
the commander pinned at the centre, and 300 sync ticks. Then set the
`viewBox` to the node bounds plus label room
(`preserveAspectRatio xMidYMid meet`). The numbers are in `graph.js`. They
hold up for 40–70 nodes. Beyond 90 nodes, raise `collide` before changing
anything else.

**DOM rule** (a bug I hit, so don't repeat it): each node is
`<g class="g-node" transform="translate()">` holding
`<g class="g-node-in">`, and the CSS animation goes on the **inner** g. A
CSS transform on the outer g *replaces* the `translate()` attribute and
stacks every node at 0,0.

#### 3.1.2 Engines column

`button.hud-engine[aria-pressed]` per engine shows the name, the flow (mono
11px), the kind (`chain`/`loop`), the pillar chip (`data`) or `no pillar`
(ghost), and the card count on the right.

- Click, Enter or Space toggles focus. Only one engine is focused at a time.
- Focusing an engine:
  - fades non-members;
  - lights the internal edges in `--signal` with labels;
  - zooms the viewBox to fit the members (minimum 45% of the full bounds);
  - sets the inspector title to **Engine** and shows the engine card.
- `[` and `]` cycle engines, and `Esc` clears.
- **On load, the first engine is focused.** That's the "aha" view, and it
  avoids an empty inspector.

The panel foot defines the two kinds: "Chain = resource made → spent → paid
off. Loop = cards that feed each other."

#### 3.1.3 Inspector

It has three states, and the panel title changes with them:
- **Overview:** nothing is selected and no engine is focused. Shows the
  `Tab` / `[` `]` hint and "Most connected" (top 8,
  `.hud-rank-compact`).
- **Engine:** shows the kind, the count, the name, the flow, the pillar chip
  (or the amber "none: candidate pillar?" chip), member buttons
  (`.hud-cardlink`, which select the node), the internal-edge count, the
  weakest link, and a `Find more for this engine →` link to
  Tune?engine={id}.
- **Card:** a node was clicked, or Enter was pressed on a focused node. It
  shows:
  - the art;
  - **Makes / Uses** tag chips from AN-1's produces/consumes (`data` /
    `neutral`);
  - **Feeds · N** and **Fed by · N** lists. Each row is
    `→`/`←`, a card button, and the reasons, capped at 6 with "+N more";
  - `Open card` (the existing card modal).

The body is `aria-live="polite"`.

#### 3.1.4 `HUD.graph` API (keep it)

```js
var g = HUD.graph(svgEl, { nodes, edges, engines, commander, onSelect(id, conns) });
g.focusEngine(id | null); g.select(id | null); g.search(q) /* → id|null */; g.nodeEl(id);
```

Keyboard:
- Nodes are `role=button tabindex=0`, with
  `aria-label="{name}, {degree} connections"`. Tab walks them in degree
  order.
- Enter selects.
- `/` focuses search. Typing highlights the first match; Enter selects it.
- `Esc` clears everything.

The SVG is `role=group` with a label that points to the inspector, which is
the text equivalent.

#### 3.1.5 Graph at ≤ 640px

The force graph **does not render** on phones (a 390px force graph is soup).
The engines list stays as the primary control. The stage swaps to
`[data-view=list]`, which lists the focused engine's members with their
edge to the hub, and the foot says "Full graph on a wider screen." The
inspector follows below. Keep building the SVG in JS anyway; it's hidden
with CSS, so rotating the phone to landscape at > 640px works.

#### 3.1.6 Isolated tray

Under the graph: cards with 0–1 edges, as `.hud-cardlink` buttons (they
select the node), plus `See in Tune →` (`tuner#weak`). This is how a
low-connection card becomes visible without hunting for a tiny dot.

#### 3.1.7 Composition vs template

- **Rows:** `Lands · Ramp · Card advantage · Targeted disruption · Mass disruption · Plan`.
- **Defaults:** Command Zone ep. 658 (Feb 2025): **38 / 10 / 12 / 12 / 6 /
  22**. That totals 100. ANALYTICS_SPEC said "plan ~25", but the source says
  22. Coder: use 22, and AN-3 should confirm it.
- **Row layout:** name, track, value (`35/38`, with the target in
  `--text-3`), and a delta chip. The fill is `--data`, or `--signal` when
  short by ≥ 2. The target is a 2px `--text-1` tick, with no label on the
  tick because labels there collide (the value column already has it).
- **Delta chip:** within ±1 is `ok` ("on target", "+1 ok"); short by ≥ 2 is
  `signal` ("−3 short"); over by ≥ 2 is `neutral` ("+16 over").
- **Override:** a button toggles an inline form (`aria-expanded`) with 6
  number inputs, the hint "Saved for this deck only.", a live total,
  `Reset to CZ 2025` and `Save`. The total doesn't have to be 100 (cards can
  count twice), but show it.
- **Panel foot:** "A card can count in two rows. Plan = everything else."
- Clicking a row name filters the decklist on Overview to that category. That
  link is a later card; in v1 the names are plain text.

#### 3.1.8 Curve

`HUD.barChart` (the existing one), with the commander CMC bar `hl`, plus a
dashed **template reference** step line. The reference is a fixed *shape*,
`[0,4,18,12,10,7,4,3]` (sums to 58), normalised to the deck's non-land
count (`v × nonLand / 58`), so the line always totals the same cards as the
bars. Put the template in the legend. The panel foot is a one-line,
commander-aware note when the commander cheats or ramps (LLM prose, cached,
optional). The footnote is optional; the chart isn't.

#### 3.1.9 Pillar fit, Least on-plan, EDHREC

- **Pillar fit:**
  - a `hud-meter` (pillar `--signal` / glue `--data-2` / generic `--text-4`)
    with a legend of counts;
  - a `.hud-rank-compact` list of the pillars by card count, where Pillar 1
    is `hl` and each sub-line is `pinned` / `draft`;
  - `Edit pillars` → Identity.
- **Least on-plan:** the bottom 6 by fit score (2 decimals). The sub-line is
  always `{edges} edge(s) · {short reason}`: always both, in that order.
  Pets show ♥ before the name.
- **EDHREC inclusion:**
  - a histogram of the deck's non-land cards bucketed by inclusion %:
    `<5, 5–14, 15–29, 30–49, 50–74, 75+` (non-overlapping labels);
  - the `<5` bucket is `hl`;
  - "Your rarest picks" = the 4 lowest;
  - the foot reads: "Low % ≠ bad. Low % + few edges + off-pillar = weak
    performer."

#### 3.1.10 Combos

- Data comes from Commander Spellbook, in two groups: **In deck · N** and
  **One card away · N**.
- **Row layout:**
  - piece chips joined by `+`; the missing piece is a dashed
    `--signal-line` chip;
  - an ownership chip for the missing piece:
    - `owned · free` (ok)
    - `owned · in {deck}` (signal)
    - `not owned · ${price}` (neutral)
  - an `Add` button → Tune with that card pre-filled as an add;
  - the result line in `--text-3`.
- **Filter:** a `hud-seg` with All / Owned (owned = the missing piece is
  owned).
- **Ordering:** owned-free first, then owned-elsewhere, then by price.
- Hide combos whose missing piece breaks colour identity, or that are
  `Excluded` win cons in the Journal (combo excluded → the whole section
  collapses to a one-line "Combo is excluded in your Journal · show anyway").

### 3.2 Identity: thesis + pillars (`pillars.html`)

The left column holds the thesis, and the right column (20rem) holds the
On-plan card.

**Thesis**
- **Layout:** a colour-identity bar (hard stops, mana tokens, 6px), then the
  label `Thesis · {pinned|draft} · {who/when}`, then the blockquote
  (`clamp(--fs-lg, 3.2vw, 36px)`). One phrase may be `<em>`, which renders
  in `--signal`; the generator returns it.
- **Actions:** `Edit` (`e`) turns the blockquote into a textarea in place,
  and Esc cancels. `Show generated draft` opens a diff-free side-by-side view
  in the same space. Freshness reads "Generated draft from sync {date} ·
  {provider}".

**Pillars grid**
- **Grid:** `.hud-pillars` is 3 columns (2 at ≤ 1380px, 1 at ≤ 640px), with
  1px seams. Pillar 1 is `.is-hero`: it spans 2 columns, has a solid
  `--signal` fill and `--text-on-signal` ink. That's the one loud object on
  the page.
- **Max 5 pillars.** The 5th cell is the `+ Add pillar` tile (it's hidden
  when there are 5). The add tile offers "write it yourself" or "draft one
  from the generic cards".

**Tile content**
- The label (`Pillar N`, and `· the engine` for Pillar 1) and a state chip.
- The name (h3), and a description, which is the **text cards are embedded
  against**. Say so in edit mode.
- The count and a delta (`thin: 8, aim 10`, `new`). Then
  `Top fit: <3 card names>`.
- The action row.

**Tile states**

| State | Visual | Actions |
|---|---|---|
| pinned | 2px `--signal` left indicator, chip `pinned` | Unpin · Edit · Reject |
| unpinned (accepted) | plain | Pin · Edit · Reject |
| draft / proposed | `--surface-0` fill, inset 1px `--data` ring, chip `proposed` | **Keep** (primary) · Edit · Reject (danger) |
| editing | `--surface-2`; inputs for the name (max 40) and the description (textarea) | Done · Cancel · ↑↓ Move |
| rejected | moves to the Rejected tray | Restore |

**Pin semantics** (this is the contract with AN-3):
- **Pinned** = re-sync must not change it. Edited and Kept pillars are pinned
  automatically.
- **Unpinned** = re-sync may rename or merge it.
- **Rejected** = never proposed again (it's matched by embedding similarity
  ≥ 0.85, not by name).
- A re-sync only ever **adds drafts**, and it shows the blue
  `id-proposal` banner: "Re-sync proposed N new pillar(s). Your pinned
  pillars and the thesis were not touched."

**Saving.** Edits are local until **Save & rescore** (`⌘/Ctrl+Enter`) in the
sticky `hud-savebar`. The bar shows `N unsaved changes · {summary}` and gets
the `--signal` top border while dirty. A rescore spinner isn't needed:
scores are local embeddings, so the response should come back in < 1 s.
Swap the On-plan number when it arrives.

**Keys** (shown in the section head): `n` new · `e` edit (the focused tile)
· `p` pin/keep · `x` reject · `⇧↑/⇧↓` reorder. Tiles are `tabindex=0`, and
arrow keys move between them.

**Rejected tray:** `<details class="hud-tray">`, collapsed by default. Each
row is the struck name, the date, an optional reason, and `Restore`.

### 3.3 Journal + review check-in (`journal.html`)

The fields map 1:1 to `schemas/user_intent.schema.json`. **New fields**
(AN-5 adds them to the schema): `pet_cards[]`, `novelties`, and
`budget_constraints.hard_cap` / `soft_cap` (per card; `max_card_price` is
kept as an alias of `hard_cap`).

#### 3.3.1 Form (Journal tab, or "Change" from the check-in)

- It's `.hud-form`: a 13rem section header column next to the fields, which
  stacks at ≤ 900px. A sticky anchor nav at the top (`Vision · Win cons ·
  Target power · Budget · Pets & novelties`) highlights the section in view.
- **Vision:**
  - `intent` as a `hud-seg` (new build / tune-up / overhaul);
  - a `description` textarea, hinted "Feeds the thesis generator";
  - `target_turn_win` as a number field (optional).
- **Win cons:**
  - Primary is a `hud-seg` radio over all 8 enum values, short labels.
  - Secondary is a `hud-seg--multi` of checkboxes.
  - Excluded is `hud-seg--multi hud-seg--exclude`, where checked = struck
    through on `--danger-dim`.
  - Show only the 3–4 most relevant options per group, plus a `More…`
    button that expands the rest. The full enum list is in the schema.
- **Target power:** a 1–10 `hud-seg--scale` with the tier rail under it
  (Casual 1–4 · Focused 5–6 · Optimized 7–8 · cEDH 9–10, with the active
  tier in `--signal`). The tier is derived and never picked separately. The
  hint shows the computed estimate.
- **Budget:**
  - a `No budget` toggle, which disables the rest;
  - three `hud-money` inputs: deck total, **hard cap / card**, **soft cap /
    card**;
  - each cap hint says "Global default $X · overridden" or "Using global
    default";
  - the hint line: "Owned cards cost $0 against the budget."
- **Pets & novelties:**
  - pets is a `hud-picker` combobox (`role=combobox`, listbox popup) that
    autocompletes from **this deck's** list only. Chips have a
    `.hud-chip-remove`.
  - novelties is a textarea, and so are notes.
- **Save:** a sticky `hud-savebar` with `Discard` and `Save journal`. It
  warns on navigate-away while dirty (`beforeunload`).

#### 3.3.2 Check-in (`?review=1`, or the Review button)

- **Header card** (`hud-checkin-head`, with a 2px `--signal` left border):
  - "Review · since {last date} · {N} days" and the H2 **"Is this still true?"**;
  - a **since** row: cards `+a / −r`, power estimate, price, and On-plan %.
    Ups are `--ok`, downs `--signal` (never red).
  - progress on the right: segments, done = `--ok`, changed = `--signal`.
- **Checklist rows:** Vision, Win cons, Target power, Budget, Pet cards,
  Novelties, Pillars, and Card changes (context only, "✓ Seen").
- **Each row shows:**
  - the field label;
  - the current value;
  - an optional **evidence** line in `--signal` mono. It's computed: e.g.
    "Deck is $468, 94% of budget. 2 cards over soft cap." or "Platinum Angel
    has 0 edges and is your lowest fit. Still a pet?". Evidence is the
    reason a row exists; with no evidence, the row is a quick `y`.
  - actions: `✓ Still true` (`aria-pressed`) and `Change`. Change expands
    the matching form section inline under the row, not a navigation.
    After a change the row shows `Changed` with `Undo` and a `was …`
    sub-line.
- **Keys:** `j`/`k` move (`.is-current`, with a 2px indicator), `y` = still
  true and advance, `c` = change.
- Answers save as you go (PATCH per row). Nothing is lost on close.
- **Footer:** `Skip check-in` (ghost) and **`Finish → Tune`** (primary). It
  stays enabled even when rows are unanswered: unanswered = "still true".
  Andrew can skip, because this isn't homework.

### 3.4 Tune (`tuner.html`)

**Filter bar**
- The source `hud-seg`: **Owned first** (default) / Owned only / Best
  regardless.
- `Include sleeved-elsewhere` (default on).
- Freshness: caps and their source (deck override or global), plus the
  price age.

**Main column** (fluid), with a sticky **Change list** on the right (20rem;
it stacks at ≤ 1180px).

**01 Shape fixes** (`.hud-fix`). Each one comes from a composition row more
than 1 short. It shows the label, the gap (`−3`), the count (`7 / 10`), one
line of advice naming **owned, free** cards, and one action (`See 3 owned`
filters the swaps; `Add 2 lands` adds basics to the change list).

**02 Swaps** (`.hud-swaps > .hud-swap`)
- **Layout:** `Cut` | `→` | `options` | `actions`.
- **Cut:** the label, the struck name, and the **why** mini-bars
  (`.hud-why`): Edges / Fit / EDHREC, with the low value in `--signal`. These
  are the three weak-performer inputs, so the cut is self-explaining.
- **Options:** `.hud-opts` is a 2-up grid.
  - **Left = best owned.** Its source line is `Owned · box · free` or
    `Owned · in {deck}`.
  - **Right = best buy**, shown only if it passes D5. Otherwise the cell
    says "No buy beats the owned pick by ≥ 0.10. Good enough: keep it free."
  - Each option shows the score (2 dp), the pillar it serves, and any cap
    chip.
  - The picked option gets a 2px top indicator: `--ok` for owned,
    `--signal` for buy.
- **Gap line** (when both exist): `Gap +0.27 buy vs owned` and the verdict
  chip `worth buying` (signal) or `owned is good enough` (ok).
- **Soft cap:** the buy shows the chip `over soft cap $15`, plus a
  `.hud-justify` line under the options. That line is LLM prose, but the
  numbers in it are computed. The pick stays **owned** unless the gap ≥ 0.25.
  Treat that 0.25 as the "really worth breaking your soft cap" threshold,
  constant `GAP_SOFT_OVERRIDE`.
- **Hard cap:** never shown as an option. If one existed, add the line
  `1 better buy hidden · over hard cap $40`.
- **Actions:** `Accept` (primary, takes the picked option) · `Buy instead`
  (only when a buy exists and the pick is owned) · `Dismiss`.
  - An accepted row turns `--ok-dim` with `Accepted` and `Undo`.
  - A dismissed row fades to 0.45, then collapses, and won't return this
    review.
- **Keys:** `j/k` move, `a` accept, `b` buy instead, `x` dismiss, `h` mark
  the cut card as a pet (removes the row and adds the card to the
  Journal's pets).
- **Pet notice row** (D7): at most one per review.
- **Ordering:** by weak score of the cut (descending), then by gap.

**03 Weak performers.** A sortable `hud-table` with columns Card / Edges /
Pillar fit / EDHREC / Weak score / action. Pets get ♥ and a `pet` chip, with
no "Find swap". At ≤ 640px only Card, Weak score and action remain.
Foot: "Weak = few edges + off-pillar + low EDHREC, equally weighted. No game
logs."

**Change list** (sticky aside)
- An ordered list of `− cut` / `+ add` pairs, each with its source.
- A two-up `hud-caps`: **Spend** (sum of buy prices; owned = $0) and
  **On-plan** (`71% → 73%` projected).
- `Apply to Archidekt list` (primary) and `Copy as text`.
- The hint: "Physical moves appear in Manage after apply."
- Below it, a Keys panel.
- Apply is out of scope for AN-6 if Archidekt write isn't ready. If so,
  ship `Copy as text` only and hide Apply. Don't show it disabled.

---

## 4. Keyboard map (global on deck pages)

| Key | Where | Action |
|---|---|---|
| `1`–`5` | any deck tab | switch tab |
| `/` | Stats | focus graph search |
| `[` `]` | Stats | previous/next engine |
| `Esc` | Stats | clear focus/selection; blur input |
| `Tab` / `Enter` | Stats graph | walk nodes by degree / select |
| `n` `e` `p` `x` `⇧↑↓` | Identity | new · edit · pin/keep · reject · reorder |
| `⌘/Ctrl ↵` | Identity, Journal | save |
| `j` `k` `y` `c` | Check-in | move · still true · change |
| `j` `k` `a` `b` `x` `h` | Tune | move · accept · buy instead · dismiss · pet |

Single-letter keys never fire while focus is in a text field. Show the keys
where they're used (`.hud-kbd`), not in a help modal.

## 5. Accessibility checklist (the reviewer will run this)

- [ ] Contrast fixes D9 are in `hud.css`. Run an axe or Lighthouse pass on
      all five tabs: 0 contrast failures.
- [ ] Every interactive thing is a `button`, `a`, or native input. Graph
      nodes are `role=button tabindex=0` with labels. Segmented controls are
      native radios/checkboxes (keyboard behaviour for free).
- [ ] Focus is visible everywhere (`--focus-ring`). The graph node focus
      ring is the `--signal` stroke.
- [ ] The inspector and the check-in progress are `aria-live="polite"`.
- [ ] Charts carry `role=img` and an `aria-label` with the numbers (the kit
      builders already do this).
- [ ] Reduced motion: emulate `prefers-reduced-motion: reduce` in DevTools.
      No animation fires; the graph looks identical.
- [ ] 390px: no horizontal page scroll on any tab. The deck tab strip
      scrolls inside itself, as the existing `.hud-tabs` does.

## 6. Data contract the UI needs (for AN-1/2/3/5/6 to confirm)

The UI reads these. The field names are suggestions; the shapes aren't.

```
GET /api/decks/{id}/interactions          ← SHIPPED by AN-1 (PR #66). Use as-is:
  { graph: { built_at, vocab_version, counts:{nodes,edges,isolated,untagged}, labels:{key:"Label"},
    nodes:[{name,is_commander,produces[],consumes[],degree,in_degree,out_degree,reason_counts}],
    edges:[{source,target,reasons:[key],weight,label,explanations[]}], top_connected:[...] } }
  UI mapping: node.id = name; edge label text = reasons.map(k => labels[k]).join(" · ");
  inspector "why" rows may show explanations[0] as a tooltip. Default filter drops
  edges whose reasons ⊆ {mana, lands} (client-side; add ?mana=1 server-side later if needed).
  Verified: shots/stats_real-an1-data_1440.png renders the real Holy Hellfire payload
  (87 nodes/241 edges → 50/190 after the filter) through graph.js. Open stats.html?data=real.
  MISSING for the UI (ask AN-1/AN-2 owner): engines[{id,name,flow,kind,pillar_id,cards[]}].
  Until it exists, AN-4 ships the Engines column empty-state: "Engines appear after the
  next rebuild" and the inspector defaults to Overview.
```

```
GET /api/decks/{id}/composition
  { template:{name,"lands":38,...,"plan":22, overridden:bool}, actual:{...}, by_card:{name:[rows]} }
GET /api/decks/{id}/identity
  { thesis:{text,emphasis,state:"pinned"|"draft",updated_by,updated_at,draft_text},
    pillars:[{id,name,description,state:"pinned"|"accepted"|"draft"|"rejected",order,count,top_cards[3],delta_note}],
    fit:{pillar,glue,generic, per_card:[{name,score,pillar_id|null}]} , proposal:{new:int}|null }
GET /api/decks/{id}/edhrec   { decks_sampled, per_card:[{name,inclusion}] }
GET /api/decks/{id}/combos   { in_deck:[...], one_away:[{pieces[],missing,missing_owned:"free"|"deck:{name}"|null,price,result}] }
GET /api/decks/{id}/journal  user_intent + {pet_cards[],novelties,budget_constraints:{hard_cap,soft_cap,...}}
GET /api/decks/{id}/review   { since, deltas:{added[],removed[],power:[a,b],price:[a,b],on_plan:[a,b]},
                               rows:[{field,value,evidence|null,answer:null|"true"|"changed"}] }
GET /api/decks/{id}/tune     { fixes:[...], swaps:[{cut,why:{edges,fit,edhrec},own:{...}|null,buy:{...}|null,
                               gap,pick:"own"|"buy",soft_note|null,hidden_hard:int}], weak:[...], pet_notice|null }
```

## 7. What I deliberately chose against

- **A live, draggable physics graph.** It's pretty for 3 seconds, then
  unreadable, and it costs 60fps of layout. It's frozen in v1. Drag-to-pin
  could come in Phase 2 if Andrew asks.
- **A chord diagram or an adjacency heatmap** instead of the graph. They're
  denser, but they don't show *engines*, and the card asks for labelled
  edges.
- **Radar charts for composition.** They make 6 numbers unreadable. Bars
  against a tick work better.
- **A chat-style journal.** The spec says form, and a form is skimmable in
  a check-in.
- **Gamified scores** (grades, streaks, red). See D8.
- **A light theme.** Same call as the design review.

## 8. Build order and who reads which section

| Card | Reads | Builds |
|---|---|---|
| AN-4 (t_729a57d9) | §0–§3.2, §4, §5 | deck header partial + tabs, `/stats`, `/identity`, `HUD.graph`, CSS section 7, D9 fixes |
| AN-5 (Journal/Review) | §3.3, §4, §5 | `/journal`, check-in, schema fields |
| AN-6 (Tuner) | §3.4, §4, §5 | `/tune` |

Screenshots of each mockup are in `shots/` (`*_1440.png`, `*_390.png`). My
review card after each one checks the build against these screenshots and
§5.
