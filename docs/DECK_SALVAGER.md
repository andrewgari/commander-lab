# Deck Salvager

## Goal
Paste a decklist (Archidekt/Moxfield URL, or raw pasted text) into a new
standalone view. For each card, show whether it's available to build the
deck right now from existing inventory -- without touching inventory or
creating a deck until the user explicitly opts to save it.

This is a **read-only inventory check**, not an import. `POST /api/import`
(existing) already handles "I want this deck in my registry now." The
salvager answers "could I build this deck today from what I own" first,
with save as an optional, separate, explicit second step.

## Input
Both accepted, matching the earlier clarification:
- **URL/id** -- reuses `providers.fetch_deck(provider, identifier)` exactly
  like `/api/import` does. Provider inferred from URL host
  (`archidekt.com` / `moxfield.com`), or passed explicitly.
- **Raw paste** -- plain decklist text, one card per line, formats:
  `1 Sol Ring`, `1x Sol Ring`, or bare `Sol Ring` (quantity defaults to 1).
  Blank lines and lines starting with `#` or `//` ignored (comment/section
  headers like "// Commander" or "Sideboard" pasted from an export). No
  provider metadata (no color identity, no commander flag) is available
  from raw paste alone -- salvager still works, just skips those fields.

New module `providers/raw_paste.py`:
```python
def parse_decklist(text: str) -> list[dict]:
    """Returns [{"name": str, "quantity": int}, ...]. Same shape as
    NORMALIZED_DECK_SHAPE["cards"], so downstream code (matching, save)
    doesn't care which input path was used."""
```
Line regex: `^(\d+)\s*[xX]?\s+(.+)$` falling back to `^(.+)$` (qty=1) if no
leading number. Strips categories in brackets, e.g. `1 Sol Ring [Ramp]` ->
name `Sol Ring` (bracket suffix common in Moxfield/Archidekt text exports).

## Matching against inventory
For each `{name, quantity}` line, look up instances by `card_name` (same
lookup `instances.list_instances(r, card_name=...)` uses today -- name-keyed,
not oracle_id-keyed, matching the rest of the instance layer as it stands).
Bucket every instance for that card by `ownership_status`:

- **free** = `in_collection` count -- genuinely available, not bound to
  any deck, this is what "you have it" means.
- **committed** = `in_deck` count -- owned, but currently bound to another
  deck's build. Distinct from free per the user's explicit ask: this card
  physically exists in the collection but "stealing" it means unbinding it
  from wherever it lives now.
- **in_mail** count shown too (`in_mail` = ordered/incoming, not yet in hand)
  -- bucketed as its own third state, not merged into free or committed,
  since it's neither immediately available nor bound to a deck.
- assumed **missing** = no instance at all, or all instances are
  `not_owned` (not_owned means "positively known to not exist right now" per
  the existing model, not "state unknown" -- there's no separate
  "unknown/never tracked" state in the model, so missing covers both cases
  identically for this feature's purposes).

Per-card status is the single richest bucket, ranked in this priority
order for the primary badge (a card can be in multiple buckets across
several instances; the UI shows all non-zero counts, but this order picks
which one drives the badge color):
1. `free >= quantity` -> **green "have it"** (`free` count shown)
2. `free > 0` but `< quantity` -> **yellow "you have it, but you'd have to
   steal it"** wording per the user's own phrasing -- shown when the
   partial free count plus committed count could cover quantity, i.e.
   there's more of this card in the collection overall but not enough
   fully free. (Also covers the "own 1, deck wants 2" case naturally: this
   falls under "not enough free copies", which is a steal-some scenario,
   not a clean have-it.)
3. `free == 0` and `committed > 0` -> **orange "committed elsewhere"** --
   zero free copies, but the card exists in the collection, currently
   built into another deck. This is the "steal it" case in its purest
   form: 0 free means every existing copy needs to be pulled from another
   deck to use it here.
4. `free == 0` and `committed == 0` and `in_mail > 0` -> **blue "incoming"**
   -- ordered but not arrived.
5. none of the above -> **gray "you don't have it"** (assumed missing).

This gives 5 visually distinct states (green/yellow/orange/blue/gray),
satisfying "VERY distinct" between free / committed-elsewhere / assumed
absent, with the two extra states (partial-free, incoming) as natural
sub-cases the user's phrasing implies rather than contradicts.

Quantity display: never a bare "have 1 need 2" progress bar (user
explicitly rejected fractional/partial framing) -- text is literally
"You have it, but you'd have to steal it from another deck" for case 2,
listing which deck(s) the committed copies are bound to so the user knows
what they'd be dismantling.

## API
- `POST /api/salvage` `{"provider": "archidekt"|"moxfield"|"raw",
  "url": str}` or `{"provider": "raw", "text": str}` -- fetches/parses the
  decklist (no DB writes at all), runs the matching above, returns:
  ```json
  {
    "success": true,
    "deck": { "name": ..., "color": ..., "commanders": [...], "cards": [...] },
    "matches": [
      {"name": "Sol Ring", "quantity": 1, "status": "have",
       "free": 1, "committed": 0, "in_mail": 0,
       "committed_decks": []},
      {"name": "Mana Crypt", "quantity": 1, "status": "steal",
       "free": 0, "committed": 1, "in_mail": 0,
       "committed_decks": ["Najeela Hatebears"]},
      {"name": "Rhystic Study", "quantity": 1, "status": "missing",
       "free": 0, "committed": 0, "in_mail": 0, "committed_decks": []}
    ],
    "summary": {"have": 41, "steal": 6, "incoming": 1, "missing": 52}
  }
  ```
  Purely computed on the fly from `instances.list_instances` calls per
  card -- no new Redis keys written by this endpoint. Safe to call
  repeatedly, e.g. while iterating on a raw paste.

- `POST /api/salvage/save` `{"deck": {...same normalized shape as
  returned above...}}` -- this is the **only** point that touches the
  registry, and it's explicitly separate from `/api/salvage` per "doesn't
  automatically" save. Internally this is just `registry.upsert_deck(r,
  deck, default_status="testing")` -- a normal non-physical import,
  exactly like `/api/import` today, with the deck payload already in hand
  from the salvage step instead of re-fetching. Zero inventory footprint,
  per the existing non-physical-deck rule (PR #21) -- salvaging and saving
  a decklist never creates or binds instances.

## UI
New page `templates/salvage.html` (linked from nav alongside Decks /
Inventory / Tags):
- Textarea for raw paste, OR a URL input -- one form, salvager infers which
  based on whether the input parses as a URL.
- "Check inventory" button -> calls `/api/salvage`, renders the decklist
  as a card list with the 5-color badge per line, sorted by status
  (missing first, so the "what do I need to buy" view is immediate).
- Summary strip at the top: "41 have · 6 steal · 1 incoming · 52 missing"
- "Save as deck" button, disabled until a successful `/api/salvage`
  response exists, calls `/api/salvage/save` with the last-fetched deck
  payload. Confirms with a toast, does not navigate away (so the user can
  keep salvaging other lists in the same session).

## Summary of touched files
- NEW `providers/raw_paste.py` -- raw decklist text parser
- NEW `salvage.py` -- matching logic (`match_decklist_to_inventory`)
  kept out of `app.py`/`instances.py` since it's read-only/derived, not a
  core inventory or registry concern
- `app.py` -- two new routes: `POST /api/salvage`, `POST /api/salvage/save`,
  plus `GET /salvage` page route
- NEW `templates/salvage.html`
- `templates/base.html` -- nav link
- NEW `tests/test_salvage.py`, `tests/test_raw_paste_parser.py`
