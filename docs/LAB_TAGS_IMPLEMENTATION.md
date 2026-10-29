# Lab-Managed Tags - Implementation Complete

## Overview

The tag system was redesigned so that **Cmdr Lab owns the tags**. Lab tags are the source of truth and are managed entirely within the app; there is no longer an outward push to Archidekt.

## Key Changes

### 1. Lab Tags vs Archidekt Tags

**Lab Tags** (stored in Redis `lab_tags`):
- Your source of truth
- Stored per card name (oracle level)
- Independent of Archidekt
- Can be edited in the UI
- Apply across all decks

**Archidekt Tags** (stored in Redis `tag_vocabulary`):
- Reference only
- Shown in UI for comparison
- Display cardinality (how many decks use each tag)
- Not editable (read from Archidekt during sync)

### 2. Sync Model (pull-only)

**FROM Archidekt (python sync.py)**:
- Pulls all decks and cards
- Learns tag vocabulary from Archidekt (for reference)
- Initializes Lab tags for new cards using high-confidence tags (50%+)
- Does NOT overwrite existing Lab tags

> **REMOVED:** Lab tags no longer push back to Archidekt. The former
> "Sync button in UI" push path (`POST /api/sync-tags`,
> `scripts/sync_to_archidekt.py`) has been deleted — see "Removed:
> Archidekt Push Path" below.

### 3. New UI Features

**Tag Management Page** (`/tags`):
- Displays Lab tags (bright blue) vs Archidekt reference tags (dimmed)
- Edit Lab tags per card

**Tag Editor**:
- Add/remove Lab tags
- See Lab tags (editable, bright blue)
- See Archidekt tags below (reference only, dimmed)
- No deck selection needed - Lab tags apply to the card globally

## API Changes

### GET /api/tags
Returns Lab-managed tags only (not Archidekt tags).

### GET /api/card/{name}/tags
Returns:
```json
{
  "card_name": "Sol Ring",
  "lab_tags": ["Ramp"],
  "archidekt_tags": [
    {"tag": "Ramp", "count": 36, "total_decks": 53, "confidence": 0.68},
    {"tag": "Artifact", "count": 13, "total_decks": 53, "confidence": 0.25}
  ],
  "total_decks": 53
}
```

### POST /api/card/{name}/tags
Add or remove Lab tags:
```json
{
  "action": "add" | "remove",
  "tag": "Ramp"
}
```

## Removed: Archidekt Push Path

The following were removed (Lab tags are no longer pushed to Archidekt):
- `POST /api/sync-tags` endpoint (`app.py`)
- `scripts/sync_to_archidekt.py`
- "Sync All Decks to Archidekt" / "Sync Selected Decks" buttons and their JS in `templates/tags.html`
- Reading of `ARCHIDEKT_SESSION` / `ARCHIDEKT_CSRF` env vars

GET-only sync (`sync.py`, pulling from Archidekt) is unaffected.

## Workflow

### Initial Setup
```bash
cd /mnt/data/tank/workspace/personal/commander-lab
source venv/bin/activate

# Sync from Archidekt (initializes Lab tags with high-confidence tags)
python sync.py

# Start app
uvicorn app:app --reload
```

### Daily Usage

1. **Edit Lab Tags**:
   - Visit http://localhost:8000/tags
   - Search for a card
   - Click "Edit"
   - Add or remove tags
   - Lab tags are saved immediately

2. **Re-sync from Archidekt** (optional):
   - Run `python sync.py` to pull latest Archidekt data
   - Existing Lab tags are preserved
   - Only new cards get initialized with high-confidence tags

## Visual Indicators

**In the tag list**:
- Lab tags: **Bright blue** (`#1e40af` background)
- Archidekt tags: **Dimmed** (50% opacity) with count/total

**In the editor**:
- Lab tags section: Editable, bright blue badges
- Archidekt tags section: Below a divider, read-only, dimmed

## Example: Sol Ring

After sync, Sol Ring has:
- **Lab tag**: `Ramp` (initialized from 68% confidence in Archidekt)
- **Archidekt reference tags**:
  - Ramp: 36/53 (68%)
  - Artifact: 13/53 (25%)

If you add "Fast Mana" to Sol Ring's Lab tags:
- Sol Ring now has Lab tags: `["Ramp", "Fast Mana"]`
- This is reflected in the Lab UI only (no longer pushed to Archidekt)

## Data Storage

**Redis keys**:
- `lab_tags`: `{card_name: [tag1, tag2, ...]}`
- `tag_vocabulary`: `{card_name: {tag: count}}` (Archidekt reference)
- `card:{name}`: Card inventory (unchanged)
- `decks`: Deck list with IDs (unchanged)

## Notes

- Lab tags are **card-level** (oracle name), not deck-specific
- Structural categories (Commander, Sideboard, Maybeboard) are always preserved
- Initial tag assignment uses 50% confidence threshold from Archidekt data

## Files Modified

1. `sync.py` - Initialize Lab tags, don't auto-apply anymore
2. `app.py` - API endpoints for Lab tags (push-to-Archidekt endpoint removed, see above)
3. `templates/tags.html` - Show Lab vs Archidekt tags (sync buttons removed, see above)
4. `scripts/sync_to_archidekt.py` - Deleted; Lab tags are no longer pushed to Archidekt

## Next Steps

- Adjust the 50% confidence threshold if needed (in sync.py, line ~255)
