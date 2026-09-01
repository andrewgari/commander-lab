# Lab-Managed Tags - Implementation Complete

## Overview

The tag system has been completely redesigned so that **Cmdr Lab owns the tags** and can force-sync them to Archidekt on demand.

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

### 2. Sync Model

**FROM Archidekt (python sync.py)**:
- Pulls all decks and cards
- Learns tag vocabulary from Archidekt (for reference)
- Initializes Lab tags for new cards using high-confidence tags (50%+)
- Does NOT overwrite existing Lab tags

**TO Archidekt (Sync button in UI)**:
- Pushes Lab tags to Archidekt
- Preserves structural categories (Commander, Sideboard, Maybeboard)
- Replaces thematic tags with Lab tags
- Can sync all decks or selected decks

### 3. New UI Features

**Tag Management Page** (`/tags`):
- Displays Lab tags (bright blue) vs Archidekt reference tags (dimmed)
- Edit Lab tags per card
- Two sync buttons:
  - **Sync All Decks to Archidekt**: Overwrites all Archidekt tags
  - **Sync Selected Decks**: Prompt for comma-separated deck names

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

### POST /api/sync-tags (NEW)
Force sync Lab tags to Archidekt:
```json
{
  "decks": []  // Empty = all decks, or ["Deck Name 1", "Deck Name 2"]
}
```

Returns:
```json
{
  "success": true,
  "synced_decks": 45,
  "total_decks": 56,
  "results": [
    {"deck_name": "Spider-Man", "success": true, "updated_count": 15},
    ...
  ]
}
```

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

2. **Sync to Archidekt**:
   - Click "Sync All Decks" to push all Lab tags to Archidekt
   - Or click "Sync Selected Decks" and enter specific deck names
   - Requires `ARCHIDEKT_SESSION` and `ARCHIDEKT_CSRF` in `.env`

3. **Re-sync from Archidekt** (optional):
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
- When you sync to Archidekt, all 53 decks will get both tags

## Data Storage

**Redis keys**:
- `lab_tags`: `{card_name: [tag1, tag2, ...]}`
- `tag_vocabulary`: `{card_name: {tag: count}}` (Archidekt reference)
- `card:{name}`: Card inventory (unchanged)
- `decks`: Deck list with IDs (unchanged)

## Notes

- Lab tags are **card-level** (oracle name), not deck-specific
- Structural categories (Commander, Sideboard, Maybeboard) are always preserved during sync
- Syncing to Archidekt requires cookies (session + CSRF token) from your browser
- Rate limiting: 1 second between deck updates to avoid 429 errors
- Initial tag assignment uses 50% confidence threshold from Archidekt data

## Files Modified

1. `sync.py` - Initialize Lab tags, don't auto-apply anymore
2. `app.py` - New API endpoints for Lab tags and sync
3. `templates/tags.html` - Show Lab vs Archidekt tags, sync buttons
4. `sync_to_archidekt.py` - No longer needed (functionality moved to app.py)

## Next Steps

- Test the sync by editing a card's Lab tags and clicking "Sync All Decks"
- Check Archidekt to verify tags were pushed correctly
- Adjust the 50% confidence threshold if needed (in sync.py, line ~255)
