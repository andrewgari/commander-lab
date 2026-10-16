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

Remaining (downstream t_c842f790). Each site joins by name through `head.card_refs` / `card_ref_attrs`:

- [ ] #6 deck_stats graph + inspector: scryfallId/set/CN via map for deck cards. Graph nodes are names.
- [ ] #7 deck_stats rank lists: deck cards via map; EDHREC/combo names not in the deck → name only
- [ ] #8 review strong_cards / stragglers: deck cards via map
- [ ] #9 review curve/combos/focus/composition: via map once the joined strings are split (G7)
- [ ] #10 theory decklist/cuts/buy: kept/owned-in-deck via map. **buy → name only** (unowned, so there is no printing).
- [ ] #11 deck_tune: cuts via map. **adds/buys → name only.**
- [ ] #12 deck_journal pet chips: via map
- [ ] #13 deck_manage rows: the instance has uid/set/CN, but the `/manage` payload still drops them (G3 manage half)
- [ ] #14 inventory table/detail: row has scryfallId, set, collectorNumber, oracleId (payload done here). Template not yet wired.
- [ ] #15 instances table: scryfallId (`archidekt_uid`), set, collectorNumber already in the payload
- [ ] #16 tags table: `copies[].uid`/set from /api/inventory
- [ ] #17–#21 deck grid/modal, deck_head `hud.commanders`, personality thumb, HUD.commanders: commander uid → scryfallId. With no uid → name only (G9).
- n/a #22 styleguide, #23 design mockups (not user-facing)

Name-only by nature (no printing exists): user-entered or pasted decklist entries with no uid, suggested adds, buys, EDHREC and combo suggestions. These render with `printingSpecific: false`.
