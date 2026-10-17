# Scryfall hover preview: PR notes

Branch: `feat/scryfall-hover-preview`. Audit: `docs/CARD_RENDER_AUDIT.md` (site numbers below refer to its section 3).

## CardRef shape

Server: `card_refs.py` (`CardRef` TypedDict, every key always present). Client: `HUD.cardRef(src, refs)` in `static/js/hud.js`.

```
{ name, scryfallId, oracleId, set, collectorNumber, layout, imageUris, cardFaces,
  printingSpecific, source }   # source: instance | deck | owned | commander | name
```

- `printingSpecific` is true when `scryfallId`, or `set` + `collectorNumber`, is known. When it is false the ref is a name-only fallback and is NOT printing-specific.
- `imageUris` / `cardFaces` are always null for now, because nothing in Redis stores Scryfall image JSON. The preview builds the image from `scryfallId` (`HUD.scryfall` / `ScryfallImage`). A server resolver (audit G2/G5) can fill these fields later without changing the shape.
- Markup convention: `data-card-name` + `data-card-ref` (compact JSON, null keys dropped).
  - Jinja: `{{ card_ref_attrs(name, head.card_refs) }}`, a global registered in `app.py`.
  - JS: `HUD.cardRefAttrs(HUD.cardRef(row, CARD_REFS))`.
- Deck printing map: `head.card_refs` = `{card_key: CardRef}`, built in `_deck_head_context`, so every deck sub-page has it.
  - It holds this deck's `in_deck` instances plus its commanders. It is deliberately deck-scoped, not the whole collection, because it is embedded in each page.
  - Keys use `card_key(name)` (front face, lowercase), matching `cardKey()` in deck.html.

## Payload changes

- `/api/inventory` rows (`inventory_view.py`) now also carry `collector_number` and `oracle_id` (audit G3, additive). The `uid`, `set` and `set_name` fields were already present.

## Checklist: site → fields available

Done in this change (deck overview page, `templates/deck.html`):

- [x] #1 visual grid `cardArt()`: scryfallId, set, collectorNumber, oracleId (from the inventory row)
- [x] #2 card modal `#modalCardName`: scryfallId, set, collectorNumber, oracleId (`data-card-ref` set on open)
- [x] #3 commander portraits: inventory-backed portraits use the row (as #1). A commander without a row uses the `commander_roles` uid → scryfallId. Neither → name only.
- [x] #4 list/table view `<tr>`: same as #1
- [x] #5 bracket flag captions: rendered inside the #1/#4 elements, so they inherit those refs.

Rolled out (CardHoverPreview applied; every site below shows the preview):

Mechanism: `base.html` loads `card_hover_preview.css`, `scryfall_image.js` and `card_hover_preview.js` on every page. The controller is one delegated listener plus one shared popover, so sweeping a list re-targets the same popover (60 ms warm swap) instead of flickering. It treats any element with `data-card-ref` (JSON CardRef, from `card_ref_attrs` / `HUD.cardRefAttrs`) or `data-card-preview` + `data-cp-*` as a trigger. Helpers: Jinja `card_preview_attrs(name)` (reads `head.card_refs`, name-only without it), JS `HUD.cardName(name)`, `HUD.cardRefAttrString(ref)`, `rankList` item `card: true`. `window.HUD_CARD_REFS` carries the deck map to client-rendered pages.

- [x] #6 deck_stats graph nodes (`data-card-ref` on each node `<g>`), inspector art, edge list, isolated tray and members
- [x] #7 deck_stats rank lists (low fit, EDHREC rarest, played-with most/least, most connected), combo pieces
- [x] #8 review strong_cards / stragglers / protected pets
- [x] #9 review curve durdle cards, combos (in deck, one away, missing), engines, focus win lines, composition unknown cards. Joined strings are now looped so each name is its own trigger (G7).
- [x] #10 theory decklist / cuts / buy. Buy cards stay name only (no printing exists).
- [x] #11 deck_tune cut/own/buy options, pet, weak performers, change list. Adds and buys stay name only.
- [x] #12 deck_journal pet chips (server-rendered via `hud.picker_chip(p, refs)` and the JS `chipHtml`)
- [x] #13 deck_manage rows. `/api/decks/{id}/manage` rows now carry `uid`, `set` and `collector_number` (G3, additive).
- [x] #14 inventory table name and detail image (the hand-rolled `api.scryfall.com ?format=image` URL is now `HUD.scryfall`, CDN with fallback)
- [x] #15 instances table (`archidekt_uid`, set, collector number)
- [x] #16 tags table (first copy with a uid)
- [x] #17 decks grid art, #18 decks modal images (hand-rolled API URL replaced with `HUD.scryfall`)
- [x] #19 `hud.commanders` Jinja macro, #20 personality thumb, #21 `HUD.commanders` JS. Commander with no uid falls back to name only (G9).
- n/a #22 styleguide, #23 design mockups (not user-facing)

No legacy hover preview existed (the audit found none; the graph `mouseenter` only highlights edges), so nothing was removed.

Verified locally with the real app (fake Redis) in Chromium: two Sol Ring printings (c21 263 and cmr 472) in the inventory list open different art (`.../4/c/4cbc6901-....jpg` vs `.../5/8/58b26011-....jpg`).

Name-only by nature (no printing exists): user-entered or pasted decklist entries with no uid, suggested adds, buys, EDHREC and combo suggestions. These render with `printingSpecific: false`.

## Verification

Verified 2026-10-16 by the tester profile (task t_6f69c9f7) against `main` at `bb22e64` (PR #149). The branch `feat/scryfall-hover-preview` is merged, so the checks ran on `main`.

Setup: a local app (`uvicorn app:app`, `REDIS_URL` pointing at a fresh Redis) seeded with one deck, "Hover Test Deck": Sol Ring c21/263 (in deck), Sol Ring cmr/472 (in collection), Delver of Secrets // Insectile Aberration isd/51 (in deck, DFC commander), Arcane Signet m3c/283 and Counterspell (name only). Browser: Playwright Chromium. Scripts and raw output are in the tester scratch directory (`t6f69/`) and were attached to the task.

Caveat: `docs/design/hover-preview/SPEC.md` is referenced from `static/js/card_hover_preview.js` but is not in the repo, so the in-file behaviour notes were used as the spec.

### Test suite, lint, typecheck

| Check | Result |
|---|---|
| `REDIS_URL=… INTERACTIONS_DISABLED=1 python -m unittest discover -s tests` | PASS: 2062 tests, OK (skipped=1) |
| CI lint step (`python -m py_compile app.py sync.py scripts/*.py tests/*.py`) | PASS (also compiles the new e2e file) |
| `ruff check .` (default rules, not configured in repo, not run in CI) | Not a gate: 2737 findings already on `main`. Not fixed. |
| Typecheck | N/A: no typecheck is configured for this repo. |

### Criteria

| # | Criterion | Result | Evidence / notes |
|---|---|---|---|
| 1a | Hover a name/thumbnail at inventory (#14) shows a preview | PASS | 5/5 visible triggers open (`results.json` A) |
| 1b | … instances (#15) | PASS | 5/5 open |
| 1c | … tags (#16) | PASS | 4/4 open |
| 1d | … deck manage (#13) | PASS | 4/4 open |
| 1e | … deck overview grid (#1–#5, #19) | PASS | 6/6 open with a real pointer at the element centre (`extras.json` overview) |
| 1f | … stats graph (#6) | PASS | graph nodes and inspector open |
| 1g | … stats rank list (#7) | FAIL | `.hud-rank-track` bar is a sibling of the name span and sits on top of it. Hover over the bar part of the name shows nothing. Repro: `/deck/Hover%20Test%20Deck/stats`, hover the centre of the "Delver of Secrets" row in the first rank list (x≈1246, y≈556 at 1400 px wide). |
| 1h | … decks grid art (#17) | FAIL | A transparent `button.deck-open` covers the art and takes the pointer, so `pointerover` never reaches the `img[data-card-preview]`. Preview never opens. Repro: `/decks`, hover the centre of any deck thumbnail; `elementFromPoint` returns `BUTTON.deck-open`. |
| 1i | #18 deck modal, #7/#8/#9/#10/#11/#12 (review, theory, tune, journal rank/pet lists), #20 personality, #21 JS commanders | NOT VERIFIED | Seed data does not populate these pages: review, ideas, tune and journal rendered 0 triggers. Needs a richer seed (`tests/fixtures/holy_hellfire_cards.json`). |
| 2 | Two printings of one card show different URL and art | PASS | Sol Ring c21/263 → `cards.scryfall.io/normal/front/4/c/4cbc6901-…`; cmr/472 → `…/5/8/58b26011-…`. Screenshots `B-solring-0.png`, `B-solring-1.png`. |
| 3a | DFC shows both faces (desktop) | PASS | Front and back both loaded at natural width 488 px (manage, inventory). `C-dfc-*.png` |
| 3b | DFC narrow (520 px viewport) shows one face and a Flip button, Flip changes face | PASS | Shows "FRONT" + Flip, then the back `…/back/1/1/…`. `C-dfc-narrow-*.png` |
| 4 | Fast sweep across a list: no flicker, no repeated open/close, no blank frame | PASS | MutationObserver over 5 adjacent rows at 35 ms per step: 0 close or hide events, one open (`results.json` D). |
| 5a | Loading state appears | PASS | Image request held open: skeleton with "Loading" label (`E-loading.png`). |
| 5b | Error state appears after a failed image | PASS | Aborted cards.scryfall.io requests show "Failed to load image" (`E-error.png`). |
| 6a | Touch: tap on inert text opens the sheet; tap on the scrim dismisses | PASS | `results.json` I; `I-touch-tap.png` |
| 6b | Touch: long-press on inert text | PASS | opens the sheet (`extras.json` touch_inert) |
| 6c | Touch: long-press on an actionable tile (inside a button) opens the sheet | PASS (isolated) | Isolated run: long-press 700 ms opens sheet on the deck tile (`dbg_touch` run). In the combined sequence the preceding tap was not isolated, so this is not conclusive there. |
| 6d | Touch: quick tap on an actionable tile does not open the preview | PASS | `quick_tap_open: false` (`extras.json` touch_actionable) |
| 7a | Keyboard focus on a trigger opens the preview (400 ms) | FAIL (intermittent) | Isolated runs open in 0.4 s. In the full run and in the e2e test, the focused trigger is removed by an inventory table re-render and focus falls to `<body>` before the open timer fires. Repro: load `/inventory`, press Tab until a `.hud-cp-name` span is focused, and wait 4 s. Focus leaves the trigger after the table re-render. |
| 7b | Esc closes the preview | PASS | `closed_after_esc: true` in every run where the preview opened |
| 7c | `aria-describedby` is set while open, focus stays on trigger | NOT VERIFIED | Only observed when the preview opened, which was not reproduced in a stable run. |
| 8a | Browser requests to api.scryfall.com stay at or under ~10/s | PASS | Peak 5–6 requests in any 1 s window over 40+ hovers across inventory, manage and stats (`results.json` G) |
| 8b | Server-side requests send User-Agent and Accept | PASS | Against a local mock server: `ScryfallClient` GET `/cards/named`, GET `/cards/{id}` and `/cards/search` send `User-Agent: commander-lab/1.0 (+https://github.com/andrewgari/commander-lab)` and `Accept: application/json`. `analytics/prices.py` POST `/cards/collection` sends `commander-lab/1.0 (prices)` and `application/json`. `analytics/interactions/tagger.py` and `analytics/theory.py` define the same headers but were not exercised on the wire. |
| 9 | Name-only fallbacks are marked | PASS (marking) / FAIL (warn once) | Counterspell shows the "Printing unknown" chip and `is-name-only` (`H-nameonly.png`). The spec says the console warns once per name, but the same warning was logged 5+ times for one card in one page load (`results.json` J). |
| 10 | No console errors on the audited pages | FAIL (environmental, see notes) | Browser lookups `GET api.scryfall.com/cards/named?exact=Counterspell` failed with a CORS error. Scryfall answered **429** at the time, and 429 responses carry no `Access-Control-Allow-Origin`, so the browser reports a CORS failure. The name-only card then shows "Failed to load image". Scryfall was rate-limiting the shared IP during this session; the code path itself is the throttled `ScryfallImage` lookup. |

### Repro for the failures

1. **Deck grid thumbnail (1h):** `/decks`, move the pointer to the centre of a deck art tile. The `.deck-open` button sits on top, so no preview opens. Fix idea: forward `pointerover` from the button to its art, or make the art the trigger and keep the button for clicks only.
2. **Stats rank bar (1g):** `/deck/<name>/stats`, pointer over the `hud-rank-track` bar on a rank-list row. The bar is outside the trigger span, so nothing opens. Fix idea: make the track `pointer-events: none` or move it inside the trigger.
3. **Keyboard focus lost on re-render (7a):** `/inventory`, Tab onto a card name, then wait. The table re-renders (`tbody.innerHTML = ''` on the async load path in `templates/inventory.html`) and focus falls to `<body>`. This is a page-level issue, not the controller. The e2e test `test_keyboard_focus_opens_and_esc_closes` is marked xfail with this reason.
4. **Scryfall 429 (10):** when Scryfall returns 429 to browser lookups, the response lacks CORS headers, so the browser logs a CORS error and the card shows "Failed to load image". The controller could treat a failed lookup as a retryable state and show the name-only chip instead of an error.

### Test added

`tests/e2e/test_hover_preview_e2e.py` (pytest-playwright, uses `--base-url`). Five tests: name hover opens a printing-specific image; two Sol Ring printings differ; DFC shows both faces; keyboard focus opens and Esc closes (xfail, see 7a); fast sweep does not close. Skips when the seed is missing. Run: `pytest tests/e2e/test_hover_preview_e2e.py --base-url=http://localhost:8000`. Against the seeded app: 4 passed, 1 xfailed.
