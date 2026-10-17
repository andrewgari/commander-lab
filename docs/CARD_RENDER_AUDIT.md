# Card name / thumbnail render-site audit (for hover-preview)

Audit only. No feature code changed. Audited at `origin/main` = `b4ca96e` (RV-4, #117). The open Single Card Review PR chain (#118 → #119 → #121) is not merged and is not covered.

## 1. Repo and stack

- Repo: `/mnt/data/tank/workspace/personal/commander-lab` (this worktree: `.worktrees/t_637bb00b`). It is the only MTG web project in the workspace; it uses Archidekt, Scryfall, EDHREC and Redis.
- Backend: Python 3.11, FastAPI + Uvicorn (`app.py`, 2611 lines), Redis, Jinja2 server templates, and a pydantic `analytics/` package.
- Frontend: server-rendered Jinja2 plus vanilla JS. There is no bundler, no npm and no framework. The design system is the in-house "HUD": `static/css/hud.css`, `static/js/hud.js` and the Jinja macros in `templates/partials/hud.html`. Most pages are built client-side with template strings. Templates `deck.html`, `deck_stats.html`, `deck_tune.html` and `deck_journal.html` use JS. `decks.html` and `inventory.html` still contain some legacy inline CSS.
- Scryfall client: `src/integrations/scryfall.py` (async, in-memory `TTLCache` with a 300 s default TTL, and a rate limiter at 10 requests/s). It has `get_card_by_name`, `get_card_by_id`, `search_cards` and `ScryfallCard.get_image_uri()`, which falls back to `card_faces[0].image_uris`. It is **server-side only**; the browser never calls it.
- Tooltip, popover or floating-UI library: **none**. There is no Tippy, Popper or Floating UI. Existing patterns:
  - Native `title=` attributes.
  - A CSS-only `[data-tooltip]::after` bubble on repo badges (`decks.html` ~L352–380).
  - `.hud-modal` open/close (`HUD.openModal/closeModal` in `hud.js`).
  - A `.hud-menu` macro (`hud.menu`).

  A hover preview will be new code, either hand-rolled or a vendored lib. `static/vendor/` already holds d3-force as a precedent for vendoring.

## 2. How images are built today

| Helper | Where | URL produced | Notes |
|---|---|---|---|
| `HUD.scryfall(uid, version)` | `static/js/hud.js` ~L222 | `https://cards.scryfall.io/{version}/front/{id[0]}/{id[1]}/{id}.jpg` with `fallback = https://api.scryfall.com/cards/{id}?format=image&version={v}` | **The best existing helper.** It uses the CDN, which is not rate-limited, because the API `?format=image` redirect returns 429 when about 100 cards load at once. A global capture-phase `error` listener retries once from `data-fallback`. It is **front face only** and has no png handling beyond `.png`. It returns `{src:"", fallback:""}` when there is no uid. |
| `HUD.art(name, img, opts)` / `hud.art` macro | `hud.js` ~L156, `hud.html` L137 | Wraps an `<img>` in a `.hud-art` frame with a caption. | JS and Jinja twins. They render a placeholder, not an image, when `img` is empty. |
| `HUD.commanders` / `hud.commanders` | `hud.js`, `hud.html` L158 | CDN art_crop by uid. **Falls back to `/cards/named?exact=NAME&format=image`** when there is no uid. | The macro hand-inlines the CDN URL, so the URL logic exists twice more. |
| Hand-rolled copies | `static/js/personality.js` ~L424, `templates/partials/hud.html` L169, `templates/deck.html` L401, `decks.html` L526 and L826–830, `hud.js` L202 | Mixed CDN, `/cards/{id}?format=image` and `/cards/named?exact=`. | See the URL-builder duplication gap below. |
| `ScryfallCard.get_image_uri()` | `src/integrations/scryfall.py` L221, `models.py` L41 | The real `image_uris[version]`. It handles DFC by taking the first face that has `image_uris`. | Server-side. Nothing in the app currently exposes its output to templates. |

No client-side cache layer exists beyond browser HTTP caching. There is no `image_uris` usage in any template or JS, so the app never reads Scryfall's real image JSON in the browser; it **constructs** URLs from an id.

## 3. Render sites

Legend for the identifiers column: **SID** = Scryfall printing UUID (the field is called `uid`, and it is the Archidekt card uid, which equals the Scryfall `id`). **S+CN** = set code plus collector number. **OID** = oracle_id. **name** = name only.

| # | File / component | Shows | Ids in scope | How the image is fetched | DFC / split / flip? | Printing known? |
|---|---|---|---|---|---|---|
| 1 | `templates/deck.html` `cardArt()` + `renderCards()` (visual decklist grid, `.hud-art--card`) | both | **SID** (`card.uid`), `set` code (no CN), `set_name`; no OID | `HUD.scryfall(uid, version)` → CDN, with fallback | Yes (MDFC, transform, split, adventure). The CDN helper returns the front face only. | Yes if `uid` is non-empty, otherwise **no** (renders "No Image") |
| 2 | `deck.html` card modal `openCardModal()` (`#modalCardArt`, `#modalCardName`) | both (large) | SID, `set`, `set_name`, `modifier` (foil) | `HUD.scryfall(uid,'large')` | Yes, front only | Same as #1 |
| 3 | `deck.html` commander portraits (~L385–410) | both | SID from inventory, otherwise `commander_roles[].uid` | `cardArt()`, or the named fallback `/cards/named?exact=…&version=large` when there is no uid | Yes. Many commanders are DFC or MDFC. | Mostly yes. The named fallback is **not printing-exact**. |
| 4 | `deck.html` decklist table/text view (`decklistView`) | name | Same inventory row (SID available) | none | Yes (name only) | Id is in scope but not used |
| 5 | `deck.html` bracket flag captions (`flagWhy`, `bracketFlags` keyed by `cardKey(name)`) | name text | **name only** (key is lowercased and split on ` // `) | none | Yes: split/DFC names use `A // B` | **No**. Can be joined back to inventory by name. |
| 6 | `templates/deck_stats.html` interaction graph + inspector (`.inspector` card at ~L231) | both (graph nodes are names; inspector shows art_crop) | **name only** for nodes and edges (`VIEW.meta[id]`). The inspector joins `D.inventory` by `c.name === id` to get `uid`. | `H.scryfall(inv.uid,'art_crop')` | Yes. Graph nodes use the `shortName` of a split card. | Inspector: yes if the join hits. Graph nodes and the "Fed by / Feeds" `<button data-goto>` name lists: **no** |
| 7 | `deck_stats.html` rank lists (`H.rankList` items: EDHREC inclusion, weak performers, salt, similarity, combo `card_names`) | name | **name only** (`label: x.name`) | none | Yes | **No** |
| 8 | `templates/partials/review/strong_cards.html`, `stragglers.html` (Deep Review: "Carrying the deck", "Cut candidates", protected pets) | name | **name only** (`c.name`, `data-card`). Review doc rows come from `analytics/review_*`. | none | Yes | **No** |
| 9 | `partials/review/curve_tempo.html` (durdle cards), `combos_engines.html` (`c.cards | join`), `focus.html` (`l.cards | join`), `composition.html` (`unknown_cards`) | name (joined text) | **name only** | none | Yes | **No**. Names are pre-joined into strings, so they can't carry ids. |
| 10 | `partials/theory/decklist.html`, `cuts.html`, `buy_total.html` (Ideas/theory doc: kept, owned, buy; price rows) | name (+qty, price, salt) | **name only** (`card.name`; `card.price` comes from Scryfall USD, so a lookup already happens upstream in `analytics/theory.py`/`prices.py` and discards the id) | none | Yes | **No**. These are the cards with no printing at all: "buy" cards by definition are not yet owned. |
| 11 | `templates/deck_tune.html` JS (`hud-swap`, `.hud-opt` options: cut / add / buy-instead / pet / weak performers / moves) | name | **name only** (`o.name`, `s.cut.name`, `s.buy.name`, `w.name`) | none | Yes | **No**. Adds/buys are unowned, so they have no printing. |
| 12 | `templates/deck_journal.html` (pet-card chips; `CARDS = deck_cards | tojson`) | name | **name only** (`deck_cards` is a sorted list of name strings) | none | Yes | **No** |
| 13 | `templates/deck_manage.html` (`.manage-card-row`, `card.card_name`) | name | name, plus `instance_id`. The `/api/decks/{id}/manage` response drops `set`, `collector_number` and `archidekt_uid`, though the underlying instance record has them. | none | Yes | **No in the payload**, but the instance is one lookup away |
| 14 | `templates/inventory.html` table row (`displayName`) + expanded detail image (`.details-image`, ~L323) | name in the row; both in the detail view | SID (`card.uid`), `set`, `set_name`, `alt_name` | The detail view uses a **hand-rolled** `https://api.scryfall.com/cards/{uid}?format=image` (rate-limit prone). The row has no image. | Yes (front only) | Yes if `uid` is present |
| 15 | `templates/instances.html` table (`inst.card_name`) + detail (`set_name`, `collector_number`) | name | name, `set`, `set_name`, **`collector_number`**, `archidekt_uid`, instance `id`. This is the **only** place that has S+CN. | none (no thumbnail) | Yes | **Yes** (S+CN, usually SID) but not displayed as an image |
| 16 | `templates/tags.html` card table (`card.name`) + editor | name | name only (`cardMap` keyed by name; copies carry `uid` and `set` as a side effect) | none | Yes | Partly. `copies[].uid` is in the `/api/inventory` rows. |
| 17 | `templates/decks.html` deck grid (`deckArt`/`deckArtImg`, ~L515–535) | thumbnail only (art_crop) | commander SID from `commander_roles[].uid`; names | `HUD.scryfall(uid,'art_crop')`; **named fallback** when there is no uid | Yes (commanders are often DFC) | Mostly. Named fallback is not exact. |
| 18 | `decks.html` deck modal (~L826–830) | thumbnail (large) | `commander_uids`, otherwise names | **Hand-rolled** `/cards/{uid}?format=image`, otherwise `/cards/named?exact=` | Yes | Mostly |
| 19 | `templates/partials/deck_head.html` → `hud.commanders` macro (deck-page header, all deck sub-pages) | both | `head.commander_roles[].uid` (SID); names | CDN, hand-inlined in the macro; named fallback | Yes | Mostly |
| 20 | `static/js/personality.js` `thumb()` (profile page deck rows) | thumbnail (art_crop, first commander only) | `commander_uids[0]` | Hand-rolled CDN URL with fallback | Yes | Mostly |
| 21 | `static/js/hud.js` `commanders()` (JS mirror) | both | `commander_roles` uid or name | Uses `scryfall()`; named fallback | Yes | Mostly |
| 22 | `templates/styleguide.html` | demo only (`/cards/named?exact=`) | name | named | n/a | n/a (not user-facing) |
| 23 | `docs/design/*.html` | static mockups | hard-coded SIDs | api `?format=image` | n/a | n/a (not part of the app) |

Not render sites: `genres.html`, `deck_identity.html`, `settings.html`, `home.html` and `changelog.html` show genre, pillar and deck names only, not card names. Pillar descriptions may name cards in free text; see gap G6.

## 4. Where printing data lives (the data model)

- **Instance** (`instances.py`, Redis): `card_name`, `set`, `set_name`, `collector_number`, `finish`, `archidekt_uid` (= Scryfall printing id), `deck_id`. This is the owned, exact printing.
- `inventory_view.py` → `/api/inventory?deck=` returns one row per instance. It exposes `uid` (= `archidekt_uid`), `set`, `set_name` and `modifier`. It **omits `collector_number`** and `oracle_id`. Frontend joins happen through this payload.
- **Card record** (`cards.py`): `card:{oracle_id}` plus the `idx:card_name_to_oracle:{name}` index. It stores oracle-level data only (type, cmc, oracle_text, colors). It has **no printing, no image data and no `card_faces`**.
- **Deck record** (`registry`/providers): `commanders`, `commander_uids` and `commander_roles` (uid). Commander printing is generally known, and the Archidekt and Moxfield providers both fill it. Mainboard entries in imported decklists are `{name, quantity}` only, so only commander rows carry ids.
- `deck_intake.py` models (`analytics/models.py`) have `oracle_id` and `scryfall_id` fields, but they are populated only when the source decklist provided them, or via `_resolve_card_meta`.
- Analytics outputs (review/theory/tune/bracket/interactions/EDHREC/combos): **name strings only**. No ids.

Because the stored id is an Archidekt uid, the "specific printing" currently means the printing that the user's Archidekt deck points at. For a card that was never bound to an instance (suggested adds, buys, combo pieces, EDHREC suggestions) no printing exists, and "exact printing" is unattainable. A default printing has to be chosen.

## 5. Printing-data gaps and proposed fixes

| Id | Gap | Sites | Proposed fix |
|---|---|---|---|
| G1 | Name-only mentions of **cards that are in the deck** (review, bracket, graph, rank lists, journal, manage, tags) | #5–#9, #11–#13, #16 | Do not thread ids through each analytics payload; there are too many producers. Add one resolver endpoint, `GET /api/decks/{deck}/printings` (or embed `D.printings = {name_lower: {uid, set, cn}}` in the existing deck payload), built from the deck's instances. In the preview JS, look up by **normalized name** (lowercase, split on ` // `, matching the existing `cardKey()` in `deck.html`). Mark rendered names with `data-card-name` (already used in several places) plus an optional `data-sid`. If the deck has several copies with different printings, use the first. |
| G2 | Name-only mentions of cards **not owned or not in the deck** (theory "buy"/"owned", tune adds and swaps, combo one-away, EDHREC lists, weak-performer replacements) | #7, #9–#11 | No exact printing exists. Fall back by priority: owned instance by name, then oracle default printing. Server-side `GET /api/card-image?name=…` calls `ScryfallClient.get_card_by_name(exact=)` and returns `{id, set, collector_number, image_uris/card_faces}`, with a Redis cache keyed by `oracle_id` or lowercased name. Do not link the browser directly to `/cards/named` for hover, because that is rate-limited and returns only one image. Later, producers (`analytics/theory.py`, which already hits `cards/collection`) can attach `scryfall_id` when they resolve prices. |
| G3 | `/api/inventory` drops `collector_number`, `oracle_id` and `finish` fidelity; `/manage` drops everything printing-related | #1, #13, #14 | Add `collector_number` and `oracle_id` (via `idx:card_name_to_oracle`) to `inventory_view` rows, and `uid`, `set` and `collector_number` to the manage rows. Both are additive changes. |
| G4 | `uid` is the **Archidekt** uid, and the code assumes it equals the Scryfall id. It is empty on instances created manually or by import without a uid. | #1–#3, #14 | Treat empty `uid` as "printing unknown". Resolve S+CN via Scryfall `/cards/{set}/{cn}` (it supports `?format=json`) when the instance has `set` and `collector_number` but no uid, then cache the id back onto the instance. |
| G5 | **Double-faced cards**: the CDN helper builds a front-face URL only, and the app stores no `card_faces`. A hover cannot show the back, and a split or flip card shows one image (which is correct for split and flip, since they use one image). | all | Preview should call a server endpoint that returns `faces: [{name, image_normal}]` from `ScryfallCard`. For `image_uris`-less layouts (transform, modal_dfc, reversible_card, double_faced_token) read `card_faces[i].image_uris`. The CDN pattern supports `/back/` if the layout is already known. Show a flip control or both faces. Split, flip and adventure cards use a single top-level `image_uris`, so the front-only URL is already right. |
| G6 | Card names inside **free text** (pillar descriptions, oracle prose, review "why" strings, combo `produces`) | #8–#10, deck_identity | Out of scope for v1. If wanted later, linkify using the deck's known names (longest match first), then follow G1. |
| G7 | Joined strings (`(c.cards | join(", "))`, `d.unknown_cards | join`) cannot hold per-name markup | #9 | Change those templates to loop and wrap each name in a `<span class="hud-card-name" data-card-name=…>`. A trivial template change; do it in the hover feature, not here. |
| G8 | **Image URL logic is copy-pasted** into 8 places with 3 different URL schemes. Some use the rate-limited API redirect. | #14, #17–#20, hud.html L169, `hud.js` L202 | See section 6. |
| G9 | Commander image fallback to `/cards/named` is not printing-exact | #3, #17–#19, #21 | Fill `commander_uids` at intake. The Archidekt and Moxfield providers already do. Raw-paste decks do not. |

## 6. Recommended single source of truth for the Scryfall image URL

Make **`HUD.scryfall(id, version, face)`** in `static/js/hud.js` the only client-side URL builder, and extend it:

1. Keep the CDN pattern `https://cards.scryfall.io/{version}/{front|back}/{id[0]}/{id[1]}/{id}.jpg`, with the API fallback in `fallback`. Add a `face` argument (`"front"` default, `"back"`) to cover DFCs (G5), and add a `.jpg` extension for every version except `png`.
2. Replace the hand-rolled copies (rows #14, #18, #20 and the three inlined copies in `hud.html` and `hud.js`) with calls to it. The Jinja macro `hud.commanders` should use a single small `scryfall_img(uid, version)` Jinja global that mirrors the JS.
3. For anything without a printing id (G2, G9), do **not** build `/cards/named` URLs in the browser. Resolve it server-side through the existing `ScryfallClient` (`src/integrations/scryfall.py`, which already handles `image_uris`/`card_faces` fallback and has the TTL cache and rate limiter). Expose it as one small endpoint and cache the result in Redis by `oracle_id` or lowercased name. The browser then receives a Scryfall id, or explicit `image_uris`, and goes through `HUD.scryfall`.
4. Identifier precedence for the hover preview, from most to least exact:
   1. Scryfall printing id (`uid` / `scryfall_id`);
   2. set + collector number (`/cards/{set}/{cn}`);
   3. `oracle_id` (default printing);
   4. normalized name (`/cards/named?exact=`, server-side only).

The image id is never derived from the name alone when a better key is in scope.

## 7. Suggested implementation order for the hover feature (informational)

1. G8/G5: extend `HUD.scryfall`, dedupe the copies.
2. G3: add `collector_number`/`oracle_id` to the inventory rows.
3. G1: add the deck-level printing map and the `data-card-name` convention; build the hover from `HUD.openModal`-style positioning code (no library is present).
4. G2/G5: add the server-side resolver endpoint for non-owned cards and the multi-face payload.
