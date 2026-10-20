## Reference only — DO NOT MERGE

DV-1 (`t_2a92eea6`). This draft is deliberately left open as a design reference for Andrew to choose suggestions, not an implementation waiting to be merged. No app, template, backend, infrastructure or registry files changed. No sub-cards created.

## Start here

- **Audit:** `docs/DESIGN_DECK_VIEW.md` — top-to-bottom inventory and ranked, individually pickable suggestions (problem/change/effort/value) for all 12 deck surfaces.
- **Main-screen directions:** `docs/design/deck-view/overview.html?direction=a|b|c` — conservative reorder, recommended **Deck dossier**, radical decision desk.
- **Page proposals:** `docs/design/deck-view/workspaces.html` — nine focused workspace proposals.
- **Real screenshots:** `docs/design/deck-view/evidence.html`, `current/manifest.json`, and 24 full-page PNGs: 12 live surfaces × 1440/390.
- **Rendered previews:** `preview-b-1440.png`, `preview-b-390.png`, and all 24 mockup renders under `proposed/`.

HTML runs offline: serve the repository with `python3 -m http.server 18932 --bind localhost`, then open `/docs/design/deck-view/overview.html`. GitHub does not render HTML as a live site.

## Top five picks

1. **DV-01:** Lead Overview with commander, saved Game Plan, ordered pillars and protected pet cards.
2. **DV-02:** Move Update/Push, external links, lifecycle/folder and destructive actions into deck Settings/bottom utilities.
3. **DV-03:** Put shape against the selected preset before three source-labelled, freshness-qualified concerns.
4. **DV-04:** Give the existing decklist a direct Cards destination rather than a 4,904px-deep phone entry.
5. **DV-05:** Separate authored preferences from inferred/stale analysis; never apply computed labels as settings.

**Recommendation:** B, delivered in phases; A is an inexpensive first phase, C stays optional. Preserve Andrew's chosen roster Option C art tiles from #90. All vocabulary remains editable user data; the observed deck examples are not seeded options.

## Evidence / verification

- Fetched and read `origin/main` at `bcb07af`; inspected the real v0.4.124 app, not code alone.
- Reused #62 and SM-4R / #225; explicitly avoided duplicating the unfinished Simulate state/interaction review.
- Live pages: Overview + embedded Cards, Stats, Identity, Journal, Tune, Ideas, saved Theory, Review, Simulate, Manage, Composition, Primer Builder. All 12 returned 200, captured at both widths.
- Production browser blocked non-GET/HEAD/OPTIONS. Tune explanation and Primer auto-suggestion load writes were blocked; guard-induced errors are labelled, not reported as service failures. No jobs/saves/sync/deletion were submitted.
- `verify.py`: 24 responsive mockup renders pass; no page overflow, broken images/local navigation or JS page errors. Example search/view switch, staging/clear, C focus, mobile navigation and keyboard disclosure/focus exercised.
- `package.py`: 24 real + 24 proposed captures counted; Markdown local links pass; measured text contrast 7.59–14.79:1, control border 3.30:1.
- `node --check mockup.js` and `git diff --cached --check` pass.

This is a bounded UX/hierarchy audit, not a production functional or WCAG certification. Later Primer steps and active simulation/error states are explicitly not claimed as tested.

**Reviewer:** validate evidence completeness and proposal clarity only. Leave the PR OPEN and DRAFT; do not merge or implement any suggestion without Andrew's selection.
