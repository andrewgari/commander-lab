# TH-3R: Theory UX review (Ideas tab + doc view)

Reviewer: designer · Live build reviewed: 107f457 (v0.4.24) on :8020 · Deck: Holy Hellfire
Docs used: `ef5c6036ce5e` (angels, kept) and `a49befd418ad` (dragons, kept), plus two docs generated for
this review and deleted afterwards: demons and merfolk, both "open to swap". **Oracle kept Kaalia both
times, even for off-colour merfolk**, so no live doc has a commander_change section yet.

**Shipped:** PR #84 (merge a2f6c51), live as v0.4.25. Every fix below was checked in the live DOM at 1440 and 390.

## Verdict

**PASS with fixes.** The doc already reads as a document: numbered sections, a thesis lead, pillars,
role-grouped 100 in two columns, cuts, and buy total. It's the same HUD kit as Journal and Tuner (section
heads, panels, stat strip, chips), and nothing overflows at 390. The problems were scannability and one
real bug. All 11 small fixes ship in this PR (CSS section 12 in `hud.css` plus three partials). The larger
items are a fix list for a coder card (bottom of this doc).

## Fixed in this PR

| # | Where | Problem (measured live) | Fix |
|---|---|---|---|
| F1 | Ideas · job card | **Bug.** The **Dismiss** button showed *while writing*. JS sets `hidden`, but `.hud-btn{display:inline-flex}` beats the UA `[hidden]` rule (`hidden=true`, computed `display:flex`). Clicking it hid the running job with no way back until reload. | `.hud-btn[hidden]{display:none}` kit-wide (also guards any future hidden button). Test added. |
| F2 | Doc head | The red **Delete** sat 8px from the buy total, the loudest thing in the header. | Delete moves to the back row (`← All ideas … Delete`), away from the facts. |
| F3 | Ideas · ≤1180px | In the single-column layout the running job card rendered at y≈1232 on a 1493px page, below every theory. You'd submit and see nothing happen. | The aside (form + job) moves above the list while a job is visible. |
| F4 | Ideas rows | Open and Delete were 4px apart, and on phone each took a 50/50 share: a mis-tap deletes. | 12px gap. On phone, Open stretches and Delete stays compact. |
| F5 | Decklist | 75 identical bordered `KEPT` pills (88 cards) made a stripe that buried the 12 buys. Kept prices ($60–72 tutors) carried the same weight as spend. | Kept is the default state, so its chip is plain text with no border or fill and its price is `--text-3`. Buy rows get a 2px signal bar in the gutter plus a signal qty. The buys now scan as the exceptions. |
| F6 | Decklist | Salt notes were `--signal` amber, the same as BUY, so 19 salty *kept* cards read as purchases. Every note also ended with `; salt: unverified [scryfall oracle]` and wrapped to 2–3 lines at 390. | Salt has its own voice: a `SALT · HIGH · UNVERIFIED` mono label in `--danger` plus a muted reason. The verification status stays visible; only the source moves to `title`. A non-string `reason` is stringified (it is free-form extra data). "Over the soft cap" stays amber (it is a money signal). |
| F7 | Doc · thesis | The pull quote rendered at 36px against a 26px doc title, so the thesis out-shouted the page. | In a doc it's capped at 26px (`clamp(fs-lg, 2vw, 1.625rem)`), 48ch. |
| F8 | Decklist counts | `0 owned` rendered as a **green** chip. A zero isn't good news. | A zero count gets the neutral tone. |
| F9 | Buy total | `$40.25 BUY TOTAL` sat next to `$40.00 CAPS / CARD` at the same size, which reads as $0.25 over budget. | Relabelled **Per-card cap** as a quiet tile (`$40.00 hard`, foot "soft $15.00 · applies to each buy, not the total"). |
| F10 | Decklist · salt | `.hud-tcard-note.is-salt` was shared by salt and soft-cap notes. | Split into `.is-salt` / `.is-cap` so each keeps its meaning. |
| F11 | Doc head · 390 | The buy total stayed right-aligned after wrapping under the facts, leaving an orphan right-aligned label. | Left-aligned on phone. |

Screenshots (before/after, 1440 and 390) are attached to the kanban card (t_37f19221).

## Checked and fine (no change)

- **Hierarchy.** Breadcrumb → deck head → tabs → `← All ideas` → doc eyebrow / title / facts → numbered
  sections. It's the same section-head language as Stats, Journal and Tune.
- **100-card list.** Role groups never split across columns (`break-inside: avoid`), the counts sum to
  100, and no card name wraps at 1440 or 390 (narrowest name column is 150px at 390).
- **Commander-change callout (template only, not seen live).** A signal-tinted panel with the old commander
  struck → new commander, and a big reuse count. It uses the kit's signal-dim/indicator language and stacks
  to one column at 390 (CSS). Unit tests cover it, but no real doc has exercised it. That gap is tracked in
  TH-3F (t_10b438ba).
- **Request form.** An empty submit focuses Theme and says "Enter a theme." The segmented Keep / Open to
  swap control fills the row on phone. The submit becomes `Writing…` and is disabled.
- **Long wait.** Indeterminate bar (transform only, with a reduced-motion fallback), a live clock, and
  copy that says you can leave. The job resumed after navigating away and back (verified).
- **390.** `scrollWidth` is 390 on both pages, and the stat strip stacks to one column.

## Fix list (follow-up coder card TH-3F, t_10b438ba)

1. **Lock or echo the form while a job runs.** After a reload mid-job the form shows `Keep` and an empty
   theme while the card says "merfolk · open to swap". Make the inputs read-only and echo the in-flight
   values, or clear them on purpose and label it "next theory".
2. **The header "New theory" button duplicates the form.** On desktop it focuses an input that is already
   on screen. On phone it should scroll the form into view (it only calls `focus()`, so the scroll
   depends on the browser). Make it `scrollIntoView` + focus, or drop it on the Ideas tab.
3. **Cuts ↔ adds pairing.** Many cut reasons name their replacement ("swapped for Arena of Glory"). A
   swap view (cut → add pairs) would make the plan readable at a glance. This needs the oracle schema to
   carry the pairing, which is a backend/skill change.
4. **Sticky section index on long docs.** At 390 the doc is about 8100px. A compact `01 Thesis · 02 Decklist
   …` jump row under the doc head would help. It's a template + JS change and should be reused across docs.
5. **Pre-existing shell issues (from TH-3).** The phone icon rail doesn't span the page height, and the deck
   tab strip has no scroll affordance (the first tab is clipped to "…Y 4"). These are shell-wide, not Theory.
6. **Relative time on the Ideas list** ("2 h ago", absolute time in a tooltip). Minor; matches Journal.
