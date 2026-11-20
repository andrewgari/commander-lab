# Tag Management System - Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          ARCHIDEKT (Source)                         │
│  56 Decks with categories on individual cards                       │
└────────────────────────────┬────────────────────────────────────────┘
                             │
                             │ python sync.py
                             │ • Fetches all decks
                             │ • Learns tag vocabulary
                             │ • Applies high-confidence tags (50%+)
                             │ • Rate-limited (0.5s/deck)
                             ▼
┌─────────────────────────────────────────────────────────────────────┐
│                       REDIS (Source of Truth)                       │
│                                                                     │
│  card:{name}                                                        │
│  ├─ type, color, oracle_text                                       │
│  └─ copies[]                                                        │
│     └─ deck, categories[], uid, price, owned                       │
│                                                                     │
│  tag_vocabulary                                                     │
│  └─ {card_name: {tag: count}}                                      │
│                                                                     │
│  decks                                                              │
│  └─ [{id, name, color, commanders, status}]                        │
└─────────────────┬───────────────────────────┬───────────────────────┘
                  │                           │
                  │ FastAPI                   │ python sync_to_archidekt.py
                  │ • GET /api/tags           │ • Reads Redis inventory
                  │ • GET /api/card/X/tags    │ • PUTs updated decks to Archidekt
                  │ • POST /api/card/X/tags   │ • Preserves structural categories
                  │                           │ • Rate-limited (1s/deck)
                  ▼                           ▼
┌───────────────────────────────┐   ┌──────────────────────────────────┐
│  WEB UI (http://localhost:8000/tags)   │   ARCHIDEKT (Updated)        │
│                               │   │  Categories synchronized          │
│  • Tag cloud                  │   │  Lab is source of truth           │
│  • Search/filter by tag       │   │                                   │
│  • Edit tags per card         │   └──────────────────────────────────┘
│  • Confidence scores          │
│                               │
│  Tag: Ramp                    │
│  ├─ Sol Ring (36/53 = 68%)    │
│  ├─ Cultivate (18/24 = 75%)   │
│  └─ Arcane Signet (35/51=69%) │
└───────────────────────────────┘

KEY CONCEPTS:

1. HIGH-CONFIDENCE AUTO-APPLICATION
   If a card appears in N decks and is tagged the same way in ≥50% of them,
   that tag is automatically applied to all copies.

2. CARDINALITY TRACKING
   Every tag is tracked with "count/total" - how many decks have this tag
   out of how many total appearances of the card.

3. STRUCTURAL vs THEMATIC CATEGORIES
   Structural (preserved, never learned):
     • Commander, Sideboard, Maybeboard, Considering
   
   Thematic (learned and managed):
     • Ramp, Draw, Removal, Board Wipe, etc.

4. ORACLE-LEVEL TAGGING
   Tags apply to the card name (oracle), not specific printings.
   All printings of "Sol Ring" share the same tags.

WORKFLOW:

Initial Setup:
  python sync.py → Redis populated with learned tags

Daily Use:
  1. Browse http://localhost:8000/tags
  2. Edit tags through UI
  3. python sync_to_archidekt.py (when ready to push)

Resync from Archidekt:
  python sync.py (re-learns tags, applies high-confidence)
