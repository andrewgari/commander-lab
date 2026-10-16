# Reconstruct API & Session Specification

## Overview

**Reconstruct** is a decommission variant in Commander Lab. It allows a player to decommission one or two physical Commander decks, snapshot their decklists, and guide an AI agent to build up to two new target decks primarily using the retired cards and existing collection.

### Core Lifecycle

```
[Physical Deck(s)]
       │
       ▼ (POST /api/reconstruct/start)
 [Retired Decks] + [Session Created] (status: "pending_preferences")
       │
       ▼ (POST /api/reconstruct/{session_id}/preferences)
 [Preferences Stored] (status: "pending_preferences")
       │
       ▼ (POST /api/reconstruct/{session_id}/build)
 [Building] (status: "building") ──► [Drafts Ready] (status: "drafts_ready")
                                             │
                       ┌─────────────────────┴─────────────────────┐
                       ▼                                           ▼
         (POST .../accept)                                (POST .../abandon)
   [Accepted] (status: "accepted")                 [Abandoned] (status: "abandoned")
   - New physical deck(s) created
   - Tier 1 & 2 cards auto-bound (in_deck)
```

---

## Card Sourcing Tiers & Cost Model

When draft decks are generated:
1. **Tier 1 (`previous_deck`)**: Cards sourced from the retired source deck snapshot(s).
   - **Cost**: `0.00` (free, already owned from retired decks).
2. **Tier 2 (`owned`)**: Cards sourced from the user's unassigned collection (`ownership_status == "in_collection"`).
   - **Cost**: `0.00` (free, owned and not dedicated to another active deck).
3. **Tier 3 (`new`)**: Cards not currently owned.
   - **Cost**: Market purchase price (e.g. Scryfall / TCGPlayer).

---

## Pluggable Builder Interface

The Reconstruct backend decouples the REST API / session management from the deck-building engine via `BaseReconstructBuilder` in `reconstruct.py`:

```python
class BaseReconstructBuilder:
    def build(self, session: dict, r: Any) -> list[dict]:
        """Given a session record and Redis client, return up to 2 draft deck dicts."""
        raise NotImplementedError
```

The active builder can be retrieved or replaced at runtime via:
- `reconstruct.get_builder() -> BaseReconstructBuilder`
- `reconstruct.set_builder(builder: BaseReconstructBuilder) -> None`

The default implementation is `StubReconstructBuilder`, which generates typed mock drafts until the dedicated agent builder task (`t_c5954272`) is merged.

---

## API Endpoints

### 1. Start Reconstruct
**Endpoint**: `POST /api/reconstruct/start` (alias: `POST /api/reconstruct`)

Snapshots source decklists, executes physical decommission (retires decks, removes decommission tag, unbinds cards to `in_collection`), and creates a Reconstruct session.

**Request Body**:
```json
{
  "deck_ids": ["archidekt:42", "archidekt:43"],
  "tag": "removed"
}
```
*Notes*:
- `deck_ids`: list of 1 or 2 deck identifiers (`registry_id` or numeric string id). Alternatively, `{"deck_id": "archidekt:42"}`.
- `tag`: optional deck tag to drop upon retirement (defaults to `"removed"`).

**Responses**:
- `201 Created`:
  ```json
  {
    "success": true,
    "session_id": "recon_49df99e5251a",
    "session": {
      "session_id": "recon_49df99e5251a",
      "status": "pending_preferences",
      "source_deck_ids": ["archidekt:42", "archidekt:43"],
      "source_decks": [
        {
          "id": 42,
          "registry_id": "archidekt:42",
          "name": "Atraxa Superfriends",
          "commanders": ["Atraxa, Praetors' Voice"],
          "cards": [{"name": "Sol Ring", "quantity": 1}, ...],
          "color": "WUBG",
          "status": "physical"
        }
      ],
      "decommission_results": [...],
      "preferences": [],
      "drafts": [],
      "created_at": "2026-10-16T21:40:00Z",
      "updated_at": "2026-10-16T21:40:00Z"
    }
  }
  ```
- `400 Bad Request`: Invalid count (> 2 decks), deck not physical, or already decommissioned.
- `404 Not Found`: One of the source decks was not found.

---

### 2. Get Reconstruct Session
**Endpoint**: `GET /api/reconstruct/{session_id}`

Retrieves the session state, including source deck snapshots, user preferences, current status, and draft decks.

**Responses**:
- `200 OK`:
  ```json
  {
    "success": true,
    "session": { ... }
  }
  ```
- `404 Not Found`: Session does not exist.

---

### 3. List Sessions
**Endpoint**: `GET /api/reconstruct/sessions?limit=50`

Returns recent Reconstruct sessions sorted newest first.

**Responses**:
- `200 OK`:
  ```json
  {
    "success": true,
    "sessions": [ ... ]
  }
  ```

---

### 4. Submit Preferences
**Endpoint**: `POST /api/reconstruct/{session_id}/preferences`

Submits target deck preferences for 1 or 2 new decks.

**Request Body**:
```json
{
  "preferences": [
    {
      "name": "Kraum & Tymna Spellslinger",
      "commanders": ["Kraum, Ludevic's Opus", "Tymna the Weaver"],
      "themes": ["Spellslinger", "Card Advantage"],
      "strategies": ["tempo", "aggro-control"],
      "art_style": ["retro frame", "dark fantasy"],
      "flavor": "guild skirmishers",
      "notes": "Prefer low-mana curve; no extra-turn spells."
    },
    {
      "name": "Thrasios Landfall",
      "commanders": ["Thrasios, Triton Hero"],
      "themes": ["Landfall", "Ramp"],
      "strategies": ["midrange", "combo-light"],
      "art_style": "borderless",
      "flavor": "simic evolution",
      "notes": "Maximize land triggers."
    }
  ]
}
```

**Preferences Schema Fields**:
| Field | Type | Description |
|---|---|---|
| `commanders` | `list[str]` | 0, 1, or 2 commanders (supports partner, background, friends forever). |
| `themes` | `list[str]` | Thematic archetype keywords (e.g. `["Aristocrats", "Tokens"]`). |
| `strategies` | `list[str]` | Strategy tags (e.g. `["aggro", "control", "combo", "stax-lite"]`). |
| `art_style` | `str` or `list[str]` | Desired aesthetics (e.g. `"retro frame"` or `["full-art", "showcase"]`). |
| `flavor` | `str` | Flavor or story concept (free text). |
| `notes` | `str` | Free-form instructions/constraints (free text). |

**Responses**:
- `200 OK`: Returns updated `session`.
- `400 Bad Request`: More than 2 decks, invalid schema, or session already accepted/abandoned.
- `404 Not Found`: Session does not exist.

---

### 5. Trigger Build
**Endpoint**: `POST /api/reconstruct/{session_id}/build?sync=false`

Invokes the pluggable builder to generate draft decks.

**Query / Body Options**:
- `sync`: boolean (`?sync=true` or `{"sync": true}`). Defaults to `false`.
  - When `false`: transitions session to `"building"` and runs builder in background (`BackgroundTasks`). Returns `202 Accepted`.
  - When `true`: runs builder inline and returns `200 OK` with session in `"drafts_ready"`.

**Responses**:
- `200 OK` (sync) / `202 Accepted` (async):
  ```json
  {
    "success": true,
    "status": "drafts_ready",
    "session": { ... }
  }
  ```
- `400 Bad Request`: Preferences not submitted, or session already accepted/abandoned.
- `404 Not Found`: Session does not exist.

---

### 6. Fetch Drafts
**Endpoint**: `GET /api/reconstruct/{session_id}/drafts`

Fetches generated draft decks.

**Responses**:
- `200 OK`:
  ```json
  {
    "success": true,
    "status": "drafts_ready",
    "drafts": [
      {
        "id": "draft_0",
        "name": "Kraum & Tymna Spellslinger",
        "commanders": ["Kraum, Ludevic's Opus", "Tymna the Weaver"],
        "themes": ["Spellslinger"],
        "strategies": ["tempo"],
        "total_new_cost": 3.25,
        "explanation": "Draft constructed from retired source deck cards...",
        "cards": [
          {
            "name": "Sol Ring",
            "quantity": 1,
            "tier": 1,
            "tier_label": "previous_deck",
            "cost": 0.0,
            "rationale": "Sourced from retired source deck snapshot"
          },
          {
            "name": "Demonic Tutor",
            "quantity": 1,
            "tier": 2,
            "tier_label": "owned",
            "cost": 0.0,
            "rationale": "Sourced from undedicated collection"
          },
          {
            "name": "Arcane Signet",
            "quantity": 1,
            "tier": 3,
            "tier_label": "new",
            "cost": 1.25,
            "rationale": "Recommended staple for color identity"
          }
        ]
      }
    ]
  }
  ```
- `404 Not Found`: Session does not exist.

---

### 7. Accept Draft
**Endpoint**: `POST /api/reconstruct/{session_id}/accept`

Accepts one or all draft decks, transitions session status to `"accepted"`, registers the new deck(s) in `registry.py`, and auto-binds cards as dedicated (`in_deck`).

**Request Body** (optional):
```json
{
  "draft_index": 0,
  "status": "physical"
}
```
*Notes*:
- `draft_index`: integer index of draft to accept. Can also pass `draft_indices: [0, 1]`. If omitted, accepts all generated drafts.
- `status`: lifecycle status of created deck(s) (defaults to `"physical"`).

**Responses**:
- `200 OK`:
  ```json
  {
    "success": true,
    "session": {
      "session_id": "recon_49df99e5251a",
      "status": "accepted",
      "accepted_draft_indices": [0],
      "created_deck_ids": ["reconstruct:recon_49df99e5251a_0"],
      ...
    },
    "created_decks": [ ... ]
  }
  ```
- `400 Bad Request`: Session not in `drafts_ready` state, or invalid index.
- `404 Not Found`: Session does not exist.

---

### 8. Abandon Reconstruct
**Endpoint**: `POST /api/reconstruct/{session_id}/abandon`

Abandons the Reconstruct flow.

**Request Body** (optional):
```json
{
  "reason": "User decided not to rebuild"
}
```

**Responses**:
- `200 OK`:
  ```json
  {
    "success": true,
    "session": {
      "session_id": "recon_49df99e5251a",
      "status": "abandoned",
      "abandon_reason": "User decided not to rebuild",
      ...
    }
  }
  ```
- `400 Bad Request`: Session is already accepted.
- `404 Not Found`: Session does not exist.
