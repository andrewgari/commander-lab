# Tag Management System

Cmdr Lab now includes an intelligent tag management system that learns from your category assignments across all decks and automatically applies high-confidence tags.

## How It Works

### 1. Tag Learning (during sync)

When you run `python sync.py`, the system:
- Pulls all your decks and cards from Archidekt
- Learns which tags you've assigned to each card across all decks
- Tracks **cardinality**: how many decks have each card tagged a certain way
- Applies **high-confidence tags** automatically (50%+ threshold)

Example:
- You have "Cultivate" in 10 decks
- You've tagged it as "Ramp" in 8 of those decks
- Confidence: 80% (8/10)
- The system automatically adds "Ramp" to the 2 remaining decks

### 2. Tag Management UI

Visit `/tags` to:
- View all learned tags in a clickable cloud
- Search and filter cards by tag
- See tag confidence for each card (e.g., "Ramp 8/10")
- Add or remove tags from cards
- Choose which decks to apply tags to

## Setup

### Environment Variables

Add to your `.env`:

```bash
# Required for syncing FROM Archidekt
ARCHIDEKT_USERNAME=your_username
```

> **Note:** Cmdr Lab no longer pushes tag changes back to Archidekt. The
> outward sync (`scripts/sync_to_archidekt.py`, `POST /api/sync-tags`, and
> the "Sync to Archidekt" UI buttons) has been removed. `ARCHIDEKT_SESSION`
> and `ARCHIDEKT_CSRF` are no longer read by the app; if you previously set
> them in your `.env`, they can be removed manually (not touched by this
> change).

## Workflow

### Initial Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Start Redis
redis-server

# Sync from Archidekt (learns tags automatically)
python sync.py

# Start the app
uvicorn app:app --reload
```

### Daily Usage

1. **View/Edit Tags**: Visit http://localhost:8000/tags
2. **Tag a Card**: 
   - Click "Edit" on any card
   - Add or remove tags
   - Choose which decks to apply to

### Re-sync from Archidekt

If you make changes directly on Archidekt, re-run:

```bash
python sync.py
```

This will re-learn tags and apply high-confidence ones again.

## Tag Confidence

Tags are automatically applied when they meet the 50% threshold:
- **High confidence (80%+)**: Tagged consistently across most decks
- **Medium confidence (50-79%)**: Tagged in half or more decks (auto-applied)
- **Low confidence (<50%)**: Tagged in less than half (not auto-applied, but visible in UI)

## Structural vs Thematic Categories

The system distinguishes between:

**Structural** (never auto-applied, always preserved):
- Commander
- Sideboard
- Maybeboard
- Considering

**Thematic** (learned and managed):
- Ramp
- Draw
- Removal
- Counterspell
- Board Wipe
- Recursion
- etc.

## Notes

- Tags are tracked at the **oracle card level** (all printings share tags)
- The Lab is the source of truth for categories
- Archidekt push sync has been removed; tags are managed in the Lab only
