# Cmdr Lab - Tag Management System

## Summary

I've built a complete tag management system for your Archidekt collection that:

1. **Learns from your existing tags** - Automatically scans all your decks and builds a vocabulary of which cards you've tagged with which categories
2. **Auto-applies high-confidence tags** - If you've tagged a card the same way in 50%+ of your decks, it automatically applies that tag to the remaining decks
3. **Provides a management UI** - View, search, add, and remove tags through a web interface at `/tags`
4. **Syncs back to Archidekt** - Your local tags become the source of truth and can be pushed back to Archidekt

## What Was Built

### Files Created/Modified

1. **sync.py** (enhanced)
   - Added tag vocabulary learning during sync
   - Tracks cardinality (how many decks have each tag)
   - Auto-applies tags with 50%+ confidence
   - Rate-limited API calls (0.5s between deck fetches)

2. **app.py** (new endpoints)
   - `GET /api/tags` - List all learned tags
   - `GET /api/card/{name}/tags` - Get tag stats for a specific card
   - `POST /api/card/{name}/tags` - Add/remove tags from a card
   - `GET /tags` - Tag management UI page

3. **templates/tags.html** (new)
   - Tag cloud showing all learned tags
   - Search and filter cards by tag
   - Edit tags per card with deck selection
   - Shows confidence percentages (e.g., "Ramp 36/53")

4. **sync_to_archidekt.py** (new)
   - Syncs categories from Cmdr Lab back to Archidekt
   - Preserves structural categories (Commander, Sideboard)
   - Only syncs thematic tags (Ramp, Draw, Removal, etc.)
   - Rate-limited to avoid 429 errors

5. **TAG_MANAGEMENT.md** (documentation)

6. **templates/base.html** (updated)
   - Added "Tag Management" navigation link

## Quick Start

```bash
# 1. Activate venv (already created)
cd /mnt/data/tank/workspace/personal/commander-lab
source venv/bin/activate

# 2. Sync from Archidekt (learns tags automatically)
python sync.py

# 3. Start the app
uvicorn app:app --reload

# 4. Visit the tag management UI
# http://localhost:8000/tags
```

## Example Results

From your sync run:

**Sol Ring** (appears in 53 decks):
- Tagged as "Ramp" in 36 decks (68% confidence) ✓ Auto-applied
- Tagged as "Artifact" in 13 decks (25% confidence)

**Command Tower** (appears in 49 decks):
- Tagged as "Land" in 46 decks (94% confidence) ✓ Auto-applied

**Cultivate** (appears in 24 decks):
- Tagged as "Ramp" in 18 decks (75% confidence) ✓ Auto-applied

The system learned tags from **2,375 unique cards** across your **56 decks**.

## How to Use

### View Tags
1. Go to http://localhost:8000/tags
2. Browse the tag cloud to see all learned tags
3. Click a tag to filter cards by that tag

### Edit Tags
1. Search for a card
2. Click "Edit" on that card
3. Type a new tag (autocomplete from existing tags)
4. Select which decks to apply it to (default: all decks with that card)
5. Click "Add Tag"

### Remove Tags
1. Open the editor for a card
2. In the "Current Tags" section, click any tag to remove it

### Sync to Archidekt
After editing tags locally:

```bash
# Add these to your .env first:
ARCHIDEKT_SESSION=your_sessionid_cookie
ARCHIDEKT_CSRF=your_csrftoken_cookie

# Then sync
python sync_to_archidekt.py
```

Get session/CSRF cookies from your browser DevTools → Application → Cookies while logged into Archidekt.

## Architecture

**Data Flow:**

1. **Archidekt → Cmdr Lab** (sync.py)
   - Fetches all decks and cards
   - Learns tag vocabulary with cardinality
   - Applies high-confidence tags (50%+)
   - Stores in Redis

2. **Cmdr Lab UI** (/tags)
   - View tag vocabulary
   - Edit tags per card
   - Updates Redis directly

3. **Cmdr Lab → Archidekt** (sync_to_archidekt.py)
   - Reads Redis inventory
   - PUTs updated decks back to Archidekt
   - Preserves structural categories

**Redis Storage:**

- `card:{name}` - Full card data including copies with categories
- `tag_vocabulary` - Card → {tag: count} mapping
- `decks` - List of all decks with metadata

## Next Steps

1. **Test the UI**: Open http://localhost:8000/tags and try editing tags
2. **Adjust thresholds**: The 50% confidence threshold is configurable in sync.py (line 190)
3. **Add more tags**: Use the UI to create new thematic categories
4. **Sync to Archidekt**: Once you're happy with tags, run `sync_to_archidekt.py`

## Notes

- Tags are tracked at the **oracle card level** (all printings share tags)
- Cmdr Lab is the source of truth for categories
- Structural categories (Commander, Sideboard, Maybeboard) are preserved
- The system handles rate limiting automatically (429 errors)
- Your sync took ~30 seconds for 56 decks with rate limiting

Let me know if you want to adjust the confidence threshold, add more features, or need help with the Archidekt write-back!
