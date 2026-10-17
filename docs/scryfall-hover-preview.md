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
