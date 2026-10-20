# Deck view: put the deck first

**DV-1 · proposal only · do not merge this reference PR.** No app code changed.

## Recommendation in 30 seconds

Ship **B: Deck dossier**, incrementally. Make the main screen answer **what is this deck, what does it want to do, what is its shape, and what needs attention?** Everything else is a destination, not a competing hero.

1. **DV-01 — Identity first:** commander + saved Game Plan + pillars + pet cards at the top, read from the existing Journal/Identity sources.
2. **DV-02 — Move the wiring:** external links, Update/Push, folder/lifecycle and destructive operations into deck Settings; a small bottom utility link is enough on Overview.
3. **DV-03 — Shape → problems → evidence:** show composition against the selected preset, then the top three source-labelled concerns with links to their evidence.
4. **DV-04 — Give Cards its own home:** promote the existing visual/list decklist out of the Overview basement; preserve art-led browsing and printing-aware previews.
5. **DV-05 — Separate intent from inference:** saved Game Plan/pets/pillars are authoritative; stale reviews, inferred archetypes and model scores are clearly secondary, never settings silently applied for the user.

These five are independently selectable. The ranked tables below contain the smaller choices, including effort and value. **Nothing here authorizes an implementation, migration, registry seed, production write or merge.**

### Open the previews

- [Overview directions A / B / C](design/deck-view/overview.html) — conservative reorder, recommended dossier, radical decision desk. Buttons switch directions.
- [Sub-page workspaces](design/deck-view/workspaces.html) — Cards, Stats, Identity, Journal, Tune, Ideas, Review, Simulate and Settings. Links switch views; native disclosures show evidence without running anything.
- [Current-state screenshot gallery](design/deck-view/evidence.html) — all 12 real surfaces at **1440 × 900** and **390 × 844**; click a thumbnail for the entire page.
- [Desktop contact sheet](design/deck-view/current-contact-1440.jpg) · [phone contact sheet](design/deck-view/current-contact-390.jpg).

Serve the repository root with `python3 -m http.server 18932 --bind 127.0.0.1`, then open `/docs/design/deck-view/overview.html`. GitHub shows the HTML source, not a hosted preview. Mockups have no production connection, no build step and no external font dependency. Example labels/card names come from the observed Holy Hellfire deck, **not proposed option lists**. “Proposed” marks new layout or editorial grouping, not new analytics output.

## 1. Evidence and boundaries

### What was actually inspected

Read-only Chromium inspection of **Holy Hellfire / Kaalia of the Vast**, served by the real app at `http://tower:8020`, version **v0.4.124** during capture (2026-10-19 EDT / 2026-10-20 UTC). Every route in the evidence manifest returned **200**. Both widths were rendered; screenshots include the whole page, not just the initial viewport. Text transcripts accompany them for searching. This is a hierarchy audit of a populated physical deck, not a universal functional certification.

`git fetch origin` and fast-forward to **origin/main `bcb07af`** preceded the source review. `git show origin/main:templates/...` covered the shared header and all deck templates, including linked Composition, Theory and Primer Builder. Live observations take precedence over source expectations; this doc does not claim the running container equals that commit.

**There is no separate Cards tab today.** The eight shared tabs are Overview, Stats, Identity, Journal, Tune, Ideas, Review, Simulate. Manage is a header action. Composition, a saved Theory, and Primer Builder are additional deck surfaces. All twelve are included below. Cards is a *suggested* destination, not a missing audited route. Export/card-detail dialogs are contextual tools, not additional deck pages.

Production was not edited. The browser guard aborted non-GET/HEAD/OPTIONS requests. Two page-load writes were attempted and blocked: Tune `/tune/why` and Primer `/primer/suggest`. Thus the builder’s suggestion failure is **audit-guard induced, not evidence of a production outage**, and on-demand Tune prose was not fully exercised. No simulation, Oracle job, save, sync, deletion, physical move or preset change was submitted. GET routes may have their normal server-side cache effects; “read-only” here means no mutation requests or intentional data changes.

### Prior work reused, not re-litigated

- [PR #62](https://github.com/andrewgari/commander-lab/pull/62), [full-app review](DESIGN_REVIEW.md): reuse identity-led hierarchy and the distinction between deck work and plumbing. Its historical empty inventory/review-stub findings are **not current**: this deck now has 100 card entries and a real saved Review. Do not carry forward its draft-seeding advice or its proposed genericity leaderboard. The current user-owned vocabulary rule supersedes it.
- [PR #90](https://github.com/andrewgari/commander-lab/pull/90): Andrew chose **Option C art tiles** for the deck roster. Keep that decision. This audit does not redesign the roster or revive the older list-first recommendation. For in-deck Cards, retain the current art-first option and optional dense list; do not confuse the two grids.
- **SM-4R, `t_1cee23ec` / [draft PR #225](https://github.com/andrewgari/commander-lab/pull/225):** reuse [Simulate spec](design/sim/UX_SPEC.md) and that card’s existing handoff. The review was deployment-blocked, then unblocked when `/sim` returned 200. It is not a finished UX sign-off. Its saved/empty/running/stale controls, uncertainty and per-opponent correctness remain owned there. DV-1 covers Simulate’s placement and reading hierarchy only, with new current-state screenshots. No duplicate cards were created.

### Measured hierarchy, not just taste

Positions are CSS document Y coordinates (including shell/header), from a separate loaded Overview inspection; viewport-dependent content and fonts can shift them slightly. [Raw measurements](design/deck-view/current/positions.json).

| Overview element | 1440px | 390px | Meaning |
|---|---:|---:|---|
| Ability scores | 684 | 2,336 | A personality read outranks the deck’s written plan |
| Update / Push area | 1,191 | 1,685 | Wiring is encountered before a short description |
| Description | 1,658 | 4,116 | Even the existing pitch is several screens down |
| Decklist start | 2,152 | 4,904 | Card browsing has no first-class navigation entry |

The actual **saved Game Plan and five pillars are absent from Overview entirely**. They are on Identity; the pets are in Journal. Moving Description alone will not meet the brief.

At 390 the measured document width is **429px on Identity** and **411px on Composition**, versus a 390px viewport. Other captured pages stayed at 390 document width, but that does not certify every inner table or state. Overview is approximately **52,774px tall**, Manage **21,913px**, Tune **11,107px**. Long data is not intrinsically bad; forcing a task through unrelated preceding material is.

## 2. Reading order and source ownership

### Proposed Overview order (B)

1. **Identity:** one commander/partner composition, deck title/colours; Game Plan; compact pillars in the user’s order; pets with their protected status. No destructive action in the hero.
2. **Shape:** card/land counts, curve and a small composition-versus-*this deck’s resolved preset* summary. Link to the full Stats and Composition pages.
3. **Needs attention:** at most three source-labelled concerns, with timestamps/staleness and an evidence destination. Separate a target deficit from a judgement about card quality.
4. **Continue working:** Cards, Tune, Simulate; latest saved Review and optional primer excerpt/link. No full report or 100-card gallery by default.
5. **Utilities:** deck Settings / source links at the bottom. A non-blocking link-status problem stays here. Only a real task-blocking issue earns a contextual notice near the affected action.

### One concept, one source

| Visible item | Existing owner / proposed read model | Rule |
|---|---|---|
| Game Plan (the brief’s “thesis”) | Identity’s saved Game Plan | Preserve pinned/edited provenance; do not synthesize an identity from role counts |
| Vision, target win turn, preferences | Journal | Do not silently overwrite Game Plan or pitch when Vision changes |
| Pillars | Identity; Journal currently links to them | One editor, read-only projections elsewhere; no duplicate copies to keep in sync |
| Pet cards / novelties | Journal | Pets remain protected even when a model dislikes them |
| Short description / primer | Existing description + primer records | Supporting presentation, not replacement truth; generated suggestions need explicit Use/Save |
| Style, Deck Tags, themes, kindred | User-editable registries and the deck’s saved selections | Render actual selections; search/manage existing registry values; never hardcode suggestions as settings |
| Archetype / role fit | Chosen value versus derived analysis | Display separately; “computed” is not “you selected” |
| Preset / composition categories | Deck’s resolved preset and editable category registry | Show selection mode/source; never imply one universal 38-land/10-ramp rule |
| Needs-attention summary | Existing Stats/Tune/Review/Sim evidence | A presentation aggregation with deep links; no invented unified health score |
| Folder, lifecycle, sync | Existing integrations and lifecycle model | Still discoverable in deck Settings; global accounts remain global Settings |

**Empty/old/error contract:** no Game Plan → “No saved Game Plan” + Edit Identity; no pets → a quiet “No pet cards selected”; no preset → “Choose a preset to compare shape”; no fresh report → “No current diagnosis” + open last saved report/rebuild action. Missing is not 0%, and 100% on-plan is not 100% healthy. Keep the identity visible when an analytics request fails. Stale material can be opened for reference but cannot silently feed a fresh concern tile. Conflicting sources are labelled, not averaged.

## 3. Three directions, conservative → radical

| Direction | Main-screen composition | Gain | Cost / risk | Effort |
|---|---|---|---|---|
| **A — Reorder the HUD (safe)** | Add saved Game Plan/pillars/pets before the existing compact charts; put admin in a bottom disclosure; retain decklist below | Fastest way to fix the priority inversion without a new route | Still a very long Overview; Cards remains buried unless DV-04 is also selected | M |
| **B — Deck dossier (recommended)** | Identity → shape → top concerns; dedicated Cards and deck Settings destinations; existing specialist pages stay intact | Clearly about the deck; stops each feature competing for first viewport; works on phones without a three-pane trick | Read-only projection of several existing sources plus route/navigation work | M–L |
| **C — Decision desk (bold)** | Same identity-first foundation, then a focus selector (Inspect shape / Refine plan / Test a claim) and one selected evidence/action panel | Turns browsing into a deliberate deck question; denser desktop workflow | Selected panel, URL state, cross-page focus and responsive navigation are real complexity; easy to turn into a cockpit | L |

**I would ship B.** A is a useful first phase, not the endpoint. C is an optional workspace mode only after B’s structure proves itself. Do not start by merging tabs or building a new client-side application. The mockups deliberately share tokens/content: these are alternative hierarchies, not unrelated skins.

### System, not a rebrand

Keep the dark HUD, warm amber for the active action, cyan for data/evidence, serif accents for deck voice/card names and mono for measurements. Mockup tokens: ink `#edf1f4`, muted `#b0bac5`, page `#0d1117`, panel `#161e28`, amber `#ffbf47`, cyan `#79d9ef`; 4/8/12/16/24/32/48 spacing; 12/14/16/24/36/52 type scale. The bold option is stronger type and a large identity block, not more badge colours. No gradient, fake radar chart or animated background.

Keyboard-visible focus; native links for navigation and `<details>` for evidence; minimum 44px action targets; colour + words for state. At 1440 use a 12-column mental grid, identity 8/4 and shape/concerns below. At 390 preserve the **same DOM reading order**, stack to one column, wrap labels, two-column tiny stats only, and avoid a fixed right dock. Use a labelled section chooser instead of nine inaccessible off-screen tabs. Existing numeric shortcuts must not be silently reassigned when Cards is inserted: preserve old keys during transition or explicitly publish a new mapping. No global letter shortcut while typing.

Motion proposal: 160ms opacity/transform only for panel change, no entrance delays on evidence, none under `prefers-reduced-motion`. Mockups stay still by default. Keep card images lazy-loaded below the fold, printing-aware and accessible without hover. Add no framework or font download to realize this design.

## 4. Page-by-page audit and independently pickable changes

Effort: **S** = localized copy/layout, **M** = several components or view-state changes, **L** = new aggregation/workflow/navigation state; not calendar estimates. Value: **H** = resolves the priority/task-trust problem, **M** = noticeable efficiency/readability, **L** = polish. Rows are ranked **within each page**, with global first picks in §5. No row requires accepting a whole direction unless stated.

### 4.1 Overview + embedded Cards (`/deck/{name}`)

**Now, top → bottom:** shared title/commander/status and Manage, Review, Decommission, Reconstruct actions → eight tabs → large bracket/no-target explanation → Auto Tag strip/empty deck tags → two-column hero: commander art, External links/Update/Push, Lifecycle on the left; card count/CMC/lands/value, five Ability scores, curve/types, colour distribution and Profile on the right → Briefing (description/draft suggestion, primer/editor entry) → Decklist with Visual/List and tag filters → all type-grouped card art.

**Misplaced:** admin is not worthless, but it is worthless *here*. The personality panel answers “what kind of player/deck is this?” before the actual authored plan. “Value — no price data” spends hero space saying nothing. Profile repeats commander/folder and derives an “Archetype: Interaction 10” from categories; it is not the saved Game Plan. No-target bracket copy is louder than the identity. Auto Tag is an editing operation, not an introduction to the deck. The repeated commander and full decklist make a mobile landing page enormous.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · DV-01 | Identity exists but is not on the landing page | Project saved Game Plan, ordered pillars and pets beside commander/title; one Edit identity/Journal path per owner | M | H — answers the main brief |
| 2 · DV-02 | Wiring/destructive controls own the hero | Deck Settings for external/Update/Push, lifecycle/folder and danger zone; keep directional sync semantics and confirmations | M | H — removes distraction and accidental-action adjacency |
| 3 · DV-03 | Raw charts say little about what matters | Shape first, then top three source-labelled concerns; show resolved preset and “target”, not “rule” | M–L | H — a useful diagnosis, not another dashboard |
| 4 · DV-04 | Cards starts ~4,904px down on phone | Make Cards a direct destination; retain Visual/List and printing-aware preview; optional Overview “Browse 100 cards” link | M | H — browsing starts immediately |
| 5 · DV-06 | Bracket, Auto Tag and ability panel dominate | Bracket compact in shape unless violation; Auto Tag on Cards; Ability scores as a small secondary read/link with evidence | S–M | H — preserves features without priority theft |
| 6 · DV-07 | Description, Game Plan, primer can disagree | Make Game Plan authoritative at top; description optional subtitle, primer excerpt/detail; provenance and explicit accept for generated drafts | M | H — avoids three competing pitches |
| 7 · DV-08 | Empty value and repeated Profile facts | Remove empty price hero metric; show price where relevant to Tune/budget; replace inferred Profile labels with saved selections, or omit if none | S | M — less noise and false identity |

### 4.2 Stats (`/stats`)

**Now:** shared header/Refresh analytics → On-plan, Engines, Edges, EDHREC overlap, Combos, Avg CMC → Interactions (engine rail, graph/search/mana toggle, inspector) → large isolated-card tray → Shape (composition/preset override, curve) → Identity (pillar fit, least on-plan, EDHREC inclusion) → most/least played-with → combos in deck, then one-card-away suggestions. Phone replaces graph with a connection list, but the engine list and members still precede shape.

**Misplaced:** a 190-edge graph is a great investigation tool, a poor answer to “what is wrong?”. Shape is below it; 100% on-plan overstates assurance when 47 of 100 are glue. “Lower = more yours” for EDHREC overlap makes novelty sound automatically desirable. Isolated lands and utility cards should not look like automatic cuts.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · ST-01 | Diagnostics hidden below relationship exploration | Order Shape → plan fit → interactions → comparison/combos; Overview concern links land on the exact section | S–M | H |
| 2 · ST-02 | A single 100% looks like a quality grade | Lead with the 53 pillar / 47 glue / 0 generic split; label “classification coverage, not quality” | S | H |
| 3 · ST-03 | Graph consumes attention before a question | Collapsible/lazy graph, compact engine summaries and text evidence; reveal graph for selected engine, preserve list alternative | M | M |
| 4 · ST-04 | Model novelty / isolation can read as objective weakness | Replace “lower = more yours” with neutral comparison; show coverage/missing model data and pet/utility caveats next to the finding | S | H |
| 5 · ST-05 | Current preset editing is distant/duplicated | Add direct Composition link beside resolved preset; keep targets read-only here, one canonical deck preset editor | S–M | M |

### 4.3 Identity (`/identity`)

**Now:** shared header/See fit on Stats → pinned edited Game Plan with generator provenance → On-plan split/explanation → five pillar tiles (first bright amber; count, top-fit examples, unpin/edit/reject controls) → rejected section. The core content is good and already exists.

**Misplaced:** the Overview makes this feel optional while it contains the product’s thesis. Reject/unpin are peers of reading content. Top-fit examples are estimates; “Commander Protection” showing Lord of the Void is a reason to expose evidence, not rubber-stamp the label. Mobile overflow is measurable (429px document).

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · ID-01 | Main deck never reflects this work | Reuse the saved read model in DV-01; keep this page the focused editor, not a second independent identity store | M | H |
| 2 · ID-02 | 390px page exceeds viewport | Wrap provenance/action rows; `min-width:0` on tile/text tracks; stack score block; verify 390 document width | S | H |
| 3 · ID-03 | Editing/rejection competes with reading | Read view first; Edit reveals scoped controls; rejection remains reversible and separate from keeping/pinning | M | M |
| 4 · ID-04 | Fit/count looks more authoritative than evidence | Clickable “why these cards” list with method/provenance; distinguish authored pillar wording from computed membership | M | H |

### 4.4 Journal (`/journal`, check-in `?review=1`)

**Now:** shared header/Review → section anchor strip → Vision (intent, text, draft, target win turn) → Deck Tags (Style, Themes, Kindred; registry choices and derived-review hint) → Archetype selector/derived label → win-cons primary/secondary/excluded → power & bracket → budget → pets/novelties/notes → pillar chips linking Identity → sticky Save/Discard. Review mode inserts “Is this still true?” check-in above the form, finishing into Tune. Check-in mutation was not exercised.

**Misplaced:** pets and pillar context come after constraints. Two things called Review (this check-in action and the report tab) do different jobs. A stale “Group hug 100%” derived hint near saved tags may be mistaken for Andrew’s chosen identity. It is not evidence the deck should be relabelled.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · JR-01 | Defining preferences are after budget/power | Organize Identity inputs (Vision, tags, pets/novelties, pillar link) → win approach → constraints (power/bracket/budget); retain deep anchors | S–M | H |
| 2 · JR-02 | “Review” has two meanings | Rename Journal/header action **Check in**; report tab remains **Review** / **Deep Review** consistently | S | H |
| 3 · DV-05 | Computed and selected labels blur | Saved selections first; derived analysis in a labelled evidence disclosure with freshness; explicit use/choose required, editable registries only | M | H |
| 4 · JR-03 | Sticky save feedback is generic | Say which section has unsaved changes; preserve error text and focus; no autosave semantics implied by mockup | M | M |

### 4.5 Tune (`/tune`)

**Now:** generated time/Recompute → ownership scope controls and hard/soft cap/source line → four Shape fixes → twelve Cut → owned alternative → buy alternative swaps with many scores and ownership explanations → protected pet note → full computed Weak performers table → Change list (desktop aside, after the whole list on phone), projected/spend/copy controls → keyboard guide.

**Misplaced:** the decision is buried under metrics and competing options. “Accept” depends on a selected option that must be very clear. Scope/budget are relevant, not removable admin. The phone change list appears after ~65 weak-performer rows; accepting a suggestion should not require a pilgrimage to review it. The weak table repeats the swap queue and admits “no game logs,” unlike Simulate.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · TU-01 | Selected changes vanish below long mobile content | Compact sticky “Change list · N · spend” link/button, opening the existing list; safe-area/keyboard-aware, never covering row actions | M | H |
| 2 · TU-02 | Score wall hides why/which replacement | Each row: Cut → explicitly selected Add, one plain-language reason, source/cost, **Stage this swap**; other option + metrics under Compare evidence | M | H |
| 3 · TU-03 | Staging can be mistaken for applying | Persistent “staged only; deck unchanged”; physical flow and source edits remain explicit. No invented Apply endpoint | S | H |
| 4 · TU-04 | Weak table duplicates queue | Shape / Swaps / Weak-performer local filters; show a short ranked weak list then “all”; protected pets always visible | M | M |
| 5 · TU-05 | Target deficit sounds like universal failure | Show active preset and constraints next to fixes; “below your preset target”, with evidence/override link | S | H |

### 4.6 Ideas (`/ideas`)

**Now:** shared header/New theory → two saved overhaul rows (theme, age, commander kept, kept/owned/buy counts, buy total, Open/Delete) → non-mutating explanation → new theory form (free-text theme, keep/open commander, Write theory), desktop aside; background job area when active.

**Misplaced:** this is one of the better-organized pages. Delete competes with Open. “Theme” could be confused with a saved Deck Tag, but here it is a free-text experiment prompt. No need to invent a fixed theme menu.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · IE-01 | Consequences are not the first scan | Keep saved work first; each idea reads **proposal only · changes · purchase total · age**; strengthen “does not change your deck” | S | M |
| 2 · IE-02 | Delete is a peer of Open | Secondary overflow/disclosure + named confirmation; retain keyboard access and focus return | S | M |
| 3 · IE-03 | Prompt may masquerade as taxonomy | Label “Explore an idea” free text; link to existing deck context, never turn examples into seeded settings | S | M |

### 4.7 Saved Theory (`/ideas/{doc_id}`)

**Now:** shared tabs → All ideas/Delete → theme/date/commander/card-source counts/buy total → local section navigation → thesis + proposed pillars → full 100-card decklist with kept/owned/buy and price notes → cuts → buy total/export actions → notes. This is a proposal, not the saved identity.

**Misplaced:** a full retained decklist separates the thesis from the actual delta. On a phone the useful “what would I change and spend?” detail is far below unchanged basics. The theory’s “Thesis” vocabulary also drifts from the current Game Plan label.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · TH-01 | Unchanged cards bury the proposal | Proposed Game Plan → **changes first** (adds/cuts/commander delta) → cost/ownership → expandable full list → notes | M | H |
| 2 · TH-02 | Proposed identity could be confused with saved truth | Explicit “experiment, not your saved Game Plan”; no automatic Journal/pillar replacement; keep export side effects explicit | S | H |
| 3 · TH-03 | Cost/source snapshot can go stale | Show price/ownership checked time and recheck affordance before export, without pretending the snapshot is current inventory | M | M |

### 4.8 Review (`/review`)

**Now:** shared header/Rebuild → second deck title, saved age, Stale and Explanation unavailable → explicit registry-change notice → local index → Genre coverage → Composition → Curve/Tempo → Focus/win lines → Strong cards → Stragglers → Combos/Engines → Oracle summary. Current review is stale because registry version changed, not necessarily because the deck changed.

**Misplaced:** coverage labels (“Group hug 100%”) lead; the actionable narrative is last. Large raw role chips and card dumps crowd the useful mismatch findings. However, moving a stale Oracle paragraph to the top without warning would make this worse.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · RV-01 | Summary last, stale output looks current | Freshness gate first → short source-labelled takeaways if current; otherwise “last saved report” with stale cause → shape/tempo → evidence sections | M | H |
| 2 · RV-02 | Computed genre looks like chosen identity | “Model role coverage”, never the user’s Style; chosen versus derived side-by-side when useful, without changing settings | S–M | H |
| 3 · RV-03 | Huge lists compete with diagnosis | Collapsible method/card lists; keep top concerns and strong examples readable; section anchors survive | M | M |
| 4 · RV-04 | Report and check-in share Review label | Pair with JR-02: **Deep Review** for this report, **Check in** for Journal flow, **Review card** remains scoped | S | H |

### 4.9 Simulate (`/sim`)

**Now:** shared header → collapsed Run new simulation / last settings → saved report (Up to date, Specific 3, 20 games, 5 wins) → saved tester notes → Win rate / average game / commander first cast / mana used tiles → section links → One-off Oracle simulation review (currently fallback explaining Oracle was unavailable) → Curve & Tempo comparison → Card performance sections → per-game opponent/outcome log with filter/sort and twenty rows. This is a populated saved run, not a new test submitted for this audit.

**Misplaced:** this page is already better than Overview: configuration is collapsed and the saved report leads. Tester notes are relevant context, but must not become an outsized banner on every visit. A bare 25% over twenty games is easy to overread. “Up to date” describes the deck/run relationship, not Oracle availability or confidence.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · SM-01 | Numbers precede the question/limits | Compact test question + setup/sample + freshness/provenance → major sim-vs-static differences → detailed evidence/log. Retain collapsed run builder | S–M | H |
| 2 · SM-02 | Deck freshness, Oracle source and uncertainty blur | Separate labels; uncertainty near win rate; “fallback, Oracle unavailable” beside the interpretation, never only in prose | S–M | H |
| 3 · SM-03 | Overview might duplicate the entire report | Only a small latest-run link/one qualified concern in Overview; `/sim` remains the canonical report | S | M |

**Ownership boundary:** SM-02 overlaps the existing SM-4R uncertainty/fallback review. Select it there rather than opening another implementation. Do not infer correctness of standard/random/specific modes, active progress, stale cases or opponent-seat identity from these screenshots. DV-1 does not rerun those tests.

### 4.10 Manage (`/manage`)

**Now:** separate legacy Manage title + explanatory category precedence + Back to Deck → add-card search → effective-category groups with each physical card/copy’s tags, overrides and Remove. A resync-pending panel is conditional, **not present in this captured state**. No shared deck tabs/header. Card CMC repeatedly reads 0 here even when Overview has the proper nonzero value.

**Misplaced:** “Manage” sounds like sync/settings, but is really category and physical-instance work. Repeated Remove/override controls dominate a 21,913px phone page. The misleading zeroes are a data-display issue, not something to fix with a prettier chart.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · MG-01 | Manage breaks navigation and mixes jobs | Shared deck header; label work **Physical cards & categories**; entry from Cards and deck Settings, not an isolated back button | M | H |
| 2 · MG-02 | Every copy is a full editing form | Search/filter + compact rows; disclose copy/tag overrides on selection; show category source (override / Lab / none) | M | H |
| 3 · MG-03 | Remove is permanently adjacent to browsing | Scope removal to selected copy with named confirmation; distinguish remove override, unbind instance and delete data | M | H |
| 4 · MG-04 | CMC 0 looks like real card data | Verify binding against card metadata before showing; unknown displays —, never fabricated zero. Flag for implementation owner, not fixed in docs | S–M | H |
| 5 · MG-05 | Pending drift is a special case | When present, show concise physical reconciliation summary first; it stays in this task-specific view, not a full Overview banner | M | M |

### 4.11 Composition (`/composition`)

**Now:** shared deck header/Stats → resolved preset, selection mode, target lands → preset description/source and category target table → deck preset mode Auto/Pin, preset choice, Save/Reset. This page exists outside the eight tabs; live example resolves Command Zone 2025. At 390 its document extends to 411px.

**Misplaced:** the action that chooses the comparison baseline comes after a large explanation of that baseline. Stats has another override editor. This must not become yet another source of category truth. The current “builtin” source is an observation, **not a recommendation to seed presets**.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · CP-01 | Baseline selection scattered | Single deck-scoped preset selection editor, reached from Shape/Stats and Settings; global Manage presets remains separate | M | H |
| 2 · CP-02 | Resolved versus selected is hard to follow | Compact “Using X · Auto/Pin · why resolved” summary, chooser, then targets/source detail; values from editable registry only | S–M | H |
| 3 · CP-03 | Mobile width exceeds viewport | Stack preset/mode facts; wrap label/source strings; contain only genuinely wide tables, not page-level overflow | S | H |

### 4.12 Primer Builder (`/primer/build`)

**Now:** distinct header, Draft not saved yet, Write by hand, Close → five-step navigator (The deck, Game plan, Key cards, Mulligan & piloting, Table talk) → step-one known metadata, pitch, table tone/bracket/win turn and Next → outline/progress/fill-blanks aside. At 390 it stacks into a long form. Only step one was captured; later step names/structure were read from source, not asserted as fully exercised. The auto-draft request was deliberately blocked.

**Misplaced:** another pitch/identity writing surface can conflict with Journal and Identity. Preview completeness is useful but “inferred” sections should never suggest they are already authored. The nested `<main>` structure found by the browser also needs semantic cleanup when this screen is changed.

| Rank / ID | Problem | Change | Effort | Value |
|---|---|---|---|---|
| 1 · PB-01 | Copying identity can create competing facts | Read saved Game Plan/pets as inputs; distinguish primer prose from Journal settings; final Save states exactly which record changes | M | H |
| 2 · PB-02 | Auto-request can run merely by visiting | Make suggestion generation explicit per field or a named “Draft blanks” action, with source/accept/reject; never write guesses into settings | M | H |
| 3 · PB-03 | Three-pane wizard is heavy on mobile | One current step + compact progress/outline disclosure + Next; preserve draft on close/back and clearly mark unsaved state | M | M |
| 4 · PB-04 | Multiple main landmarks | One page main landmark; step body is a labelled section | S | M |

## 5. Phased path and pick list

Choose by ID; rejecting a later phase does not invalidate the earlier improvements.

| Phase | Pickable scope | Explicit exit test |
|---|---|---|
| **1 — Correct the landing page** | DV-01, DV-02, DV-06, DV-08; JR-02/RV-04 naming; ID-02/CP-03 overflow | At 1440 and 390, commander + saved Game Plan + at least first pillar/pet context appear before wiring/scores. No Decommission in hero. No document overflow |
| **2 — Make the dossier useful** | DV-03, DV-04, DV-05, DV-07; ST-01/ST-02; CP-01/02 | Cards reachable without scrolling. Shape uses resolved preset. Concern links land on exact evidence; stale/missing states do not masquerade as current findings |
| **3 — Shorten decisions** | TU-01…03, TH-01/02, RV-01…03, MG-01…03, JR-01 | Phone user can inspect/stage a swap and review changes without traversing the full weak table; theory delta precedes retained list; managed copy identity is unambiguous |
| **4 — Optional depth** | C workspace experiment, ST-03/04, remaining lower-ranked page polish; coordinate SM items with SM-4R | No new health score, forced onboarding or vocabulary seeds. URL/back/focus work; layout still passes at 390 |

**Do not bundle every table row into a mega-PR.** These are suggestions for Andrew to accept/drop, not sub-cards created by this audit. Identity projection and concerns aggregation may need read-model/API work; assign that to the implementation owner later rather than fabricating it inside this design task.

### Verification checklist for any eventual implementation

- **Content:** saved Game Plan is verbatim; pillars preserve user order; pets are never auto-cut. Empty Game Plan is not replaced with the largest role count. Description/primer and generated drafts have distinct provenance.
- **Vocabulary:** search and edit actual registry data; custom values round-trip; no array of sample styles/themes/archetypes/presets/folders shipped as settings. Computed reads remain optional analysis.
- **Freshness:** current, stale, absent, failed, generating, Oracle unavailable and unknown price all have distinct text. Never turn an unavailable metric into zero or a generated label into a saved preference.
- **Responsive:** 1440 and 390; long deck/pillar/registry names; partners and companions retain role hierarchy; no page overflow. Mobile has one reading order and reachable section chooser; fixed/sticky tools respect keyboard and safe area.
- **Interaction:** keyboard focus visible, disclosure labels meaningful, back/forward restores selected screen, no silent shortcut remapping; card details/preview work without hover. Buttons name the consequence; staging is not applying; sync keeps Update vs Push confirmation.
- **Performance / access:** lazy imagery, no duplicated full card grid in hidden tabs, charts do not block identity, AA contrast, 200% text and reduced motion. Do not add a chart merely because a metric exists.

## 6. Rendered verification

The offline prototypes were served locally and exercised with Chromium via [verify.py](design/deck-view/verify.py). [Raw verification results](design/deck-view/verification.json): **24 successful renders** (three Overview directions + nine workspace proposals, each at 1440 and 390), no document overflow, broken images/local links or JS page errors. Exercised Cards search and visual/list navigation, Tune demo stage/clear, C focus selection, mobile section chooser, native keyboard disclosure and visible focus; an additional 720px reduced-motion reflow check passed. This does not claim production behavior changed or full accessibility certification.

[Recommended desktop preview](design/deck-view/preview-b-1440.png) · [phone preview](design/deck-view/preview-b-390.png) · [all proposed desktop tops](design/deck-view/proposed-contact-1440.jpg) · [all proposed phone tops](design/deck-view/proposed-contact-390.jpg). Full proposed renders are in `design/deck-view/proposed/`. Text contrast checks: ink/panel **14.79:1**, muted/raised panel **7.59:1**, cyan/panel **10.38:1**, dark ink/amber **11.53:1**; input/control border/panel **3.30:1**. Card-art credits and source URLs: [art-sources.json](design/deck-view/art-sources.json).

## 7. Evidence index / audit limitations

[Manifest](design/deck-view/current/manifest.json) lists **24 captures: 12 surfaces × 2 widths**, HTTP status, page height and scroll width. File pairs are `overview`, `stats`, `identity`, `journal`, `tune`, `ideas`, `theory`, `review`, `simulate`, `manage`, `composition`, `primer-builder` with `-1440.png` / `-390.png`. The gallery links every one. These are screenshots of the real application, not mockups or fixture-rendered substitutes.

Not covered: saves/confirmation outcomes, simulation execution/LLM correctness, every deck kind, later wizard-step interactions, all modal states, dynamic error/running states, 200% text on production, or a full WCAG certification. No findings were inferred as “passed” from unexercised code. This audit’s deliverable is the ranked redesign proposal plus rendered responsive mockups, not production fixes.
