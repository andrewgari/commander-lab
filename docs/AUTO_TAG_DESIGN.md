# Auto Tag — Design & Data Model

## Problem

Tag cards within a deck by role or theme (Ramp, Removal, Tokens, etc.) so
that deck builders can reason about their deck's composition. Tags must also
be trackable at the deck level (e.g. the deck itself is tagged "Fast",
"Aggro").

The existing `lab_tags` key stores tags globally per card name — the same
card always gets the same tags everywhere. For Auto Tag we need **per-deck**
semantics: the same card (e.g. `Cultivate`) might be tagged "Ramp" in one
deck but "Draw" in another.

## Data Model

### Deck-level tags

```
Key:  deck_tags:{deck_id}
Type: JSON array
Item: { "tag": str, "source": "auto"|"user", "created_at": ISO-8601, "version": int }
```

Each tag carries a **source** so that re-running the auto-tag engine never
overwrites manual edits. Source `"user"` tags are preserved across re-runs;
source `"auto"` tags are replaced on each fresh run.

### Per-deck-card tags

```
Key:  deck_card_tags:{deck_id}
Type: JSON object
Value: { card_name: [ { "tag", "source", "created_at", "version" }, ... ] }
```

This is the core requirement: the same card can carry different tags in
different decks because the data is keyed by `(deck_id, card_name)`.

### Version counters

Each deck (and each deck+card pair) gets a monotonically-increasing version
number stored in a Redis SET keyed by a UUID (auto-capped at 100 members).
Version allows sorting tags newest-first and gives a lightweight audit trail
without needing a full history table.

### Key summary

| Key pattern                  | Purpose                           |
|------------------------------|-----------------------------------|
| `deck_tags:{deck_id}`        | Tags for the deck itself          |
| `deck_card_tags:{deck_id}`   | Per-card tags within a deck       |
| `deck_tags_version:{id}`     | Version counter (deck-level)      |
| `deck_card_tags_version:id:card` | Version counter (card-level) |

### Existing keys (unchanged)

| Key               | Purpose                                    |
|-------------------|--------------------------------------------|
| `lab_tags`        | Global per-card tags (oracle level)        |
| `tag_vocabulary`  | Archidekt reference tags (read-only)       |
| `core_tags`       | Set of core archetype tags                 |
| `deck_categories:{deck_id}` | Per-deck per-card category overrides |

## Where the tagging engine should live

A future `tags.py` sub-module `tags.auto_tag` (or a dedicated `auto_tag.py`)
should contain the ML / heuristic tagging engine. The engine will receive:

- The full deck cardlist (card names)
- Per-card data from Redis: Scryfall oracle text, type line, CMC, color
  identity, keywords, sub-types, super-types
- The existing `lab_tags` and `core_tags` for context
- The deck's color identity (from the deck record)

The engine should be callable as:

```python
async def auto_tag_deck(r, deck_id: str) -> dict:
    """Run the tagging engine for a deck. Writes to deck_card_tags and
    deck_tags. Returns a summary of tags applied/updated."""
```

## API Endpoints

| Method | Path                              | Description                    |
|--------|-----------------------------------|--------------------------------|
| POST   | `/api/decks/{id}/auto-tag`        | Run engine (501 — stub)        |
| GET    | `/api/decks/{id}/tags`            | Deck-level tags                |
| POST   | `/api/decks/{id}/tags`            | Add deck-level tag             |
| DELETE | `/api/decks/{id}/tags`            | Remove deck-level tag          |
| GET    | `/api/decks/{id}/card-tags`       | Per-card tags (all or filter)  |
| POST   | `/api/decks/{id}/card-tags`       | Add card tag                   |
| DELETE | `/api/decks/{id}/card-tags`       | Remove card tag                |

## Sync.py interaction

`sync.py` does **not** touch `deck_tags` or `deck_card_tags`. These keys are
written exclusively by the auto-tag engine and the manual API endpoints.
Re-running sync will never clobber auto-tag or user-edited tags.
