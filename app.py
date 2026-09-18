from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import redis
import os
import json
import time
from typing import List, Optional

import instances as instance_store
from instances import InstanceError
import registry
from providers import fetch_deck, ProviderError
import linked_accounts
from linked_accounts import LinkedAccountError

app = FastAPI()

templates = Jinja2Templates(directory="templates")

# Connect to Redis
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url, decode_responses=True)

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    providers = [p.strip() for p in os.getenv("ENABLED_PROVIDERS", "archidekt,moxfield,commandersalt").split(",") if p.strip()]
    return templates.TemplateResponse(request=request, name="decks.html", context={"providers": providers})

@app.get("/decks", response_class=HTMLResponse)
async def decks(request: Request):
    providers = [p.strip() for p in os.getenv("ENABLED_PROVIDERS", "archidekt,moxfield,commandersalt").split(",") if p.strip()]
    return templates.TemplateResponse(request=request, name="decks.html", context={"providers": providers})

@app.get("/inventory", response_class=HTMLResponse)
async def inventory(request: Request):
    return templates.TemplateResponse(request=request, name="inventory.html")

@app.get("/tags", response_class=HTMLResponse)
async def tags(request: Request):
    return templates.TemplateResponse(request=request, name="tags.html")


@app.get("/instances", response_class=HTMLResponse)
async def instances_view(request: Request):
    """Instance list view: ownership source of truth for physical card copies."""
    return templates.TemplateResponse(request=request, name="instances.html")

@app.get("/deck/{deck_name}", response_class=HTMLResponse)
async def deck_view(request: Request, deck_name: str):
    providers = [p.strip() for p in os.getenv("ENABLED_PROVIDERS", "archidekt,moxfield,commandersalt").split(",") if p.strip()]
    return templates.TemplateResponse(request=request, name="deck.html", context={"providers": providers, "deck_name": deck_name})

@app.get("/api/decks")
async def get_decks():
    decks_json = r.get("decks")
    decks = json.loads(decks_json) if decks_json else []
    return {"decks": decks}

@app.post("/api/decks/{deck_id}/status")
async def update_deck_status(deck_id: str, request: Request):
    """Update a deck's lifecycle status: physical | digital | retired | testing.
    Flipping to "physical" auto-binds one Instance per decklist card (see
    instances.auto_bind_physical); the report is returned so the UI can show
    what got created vs. reused.
    """
    data = await request.json()
    status = data.get("status")
    if status not in registry.VALID_STATUSES:
        return {"success": False, "error": f"invalid status, must be one of {sorted(registry.VALID_STATUSES)}"}
    try:
        result = registry.set_status(r, deck_id, status)
    except ValueError as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "status": status, "auto_bind": result["auto_bind"]}


@app.post("/api/import")
async def import_deck(request: Request):
    """Import a single deck from Archidekt or Moxfield by URL/id and add it
    to the deck registry (default lifecycle status: "testing"). Re-importing
    an already-registered deck refreshes its cardlist/metadata in place and
    preserves its current status.
    """
    data = await request.json()
    provider = data.get("provider", "")
    identifier = data.get("url") or data.get("id") or ""
    if not identifier:
        return {"success": False, "error": "url or id is required"}
    try:
        normalized = fetch_deck(provider, identifier)
    except ProviderError as e:
        return {"success": False, "error": str(e)}
    except Exception as e:
        return {"success": False, "error": f"fetch failed: {e}"}

    deck = registry.upsert_deck(r, normalized, default_status="testing")
    return {"success": True, "deck": deck}

@app.get("/api/inventory")
async def get_inventory(query: str = "", deck: Optional[List[str]] = Query(None)):
    # Fetch all keys (card names)
    keys = r.keys("card:*")
    inventory = []
    
    # Check if specific decks were filtered
    target_decks = deck if deck else []
    
    # Also support the old comma-separated format just in case it's passed as a single string
    if target_decks and len(target_decks) == 1 and "," in target_decks[0]:
        # Only split if it's a known multi-deck comma string, but actually it's better to just trust the exact string if it's a valid deck name
        pass
        
    core_tags_json = r.get("core_tags")
    core_tags = set(json.loads(core_tags_json)) if core_tags_json else set()
    
    for key in keys:
        card_name = key.replace("card:", "")
        if query.lower() in card_name.lower():
            val = r.get(key)
            if not val: continue
            
            try:
                card_data = json.loads(val)
                card_type = card_data.get("type", "Unknown")
                card_color = card_data.get("color", "C")
                super_types = card_data.get("super_types", [])
                sub_types = card_data.get("sub_types", [])
                keywords = card_data.get("keywords", [])
                oracle_text = card_data.get("oracle_text", "")
                card_copies = card_data.get("copies", [])
            except:
                continue
                
            for copy in card_copies:
                if not target_decks or copy.get("deck") in target_decks:
                    copy_status = copy.get("status", "have")
                    # Global inventory filters out virtual cards
                    if not target_decks and copy_status == "virtual":
                        continue
                        
                    inventory.append({
                        "name": card_name,
                        "deck": copy.get("deck", "Unknown Deck"),
                        "type": card_type,
                        "super_types": super_types,
                        "sub_types": sub_types,
                        "keywords": keywords,
                        "oracle_text": oracle_text,
                        "color": card_color,
                        "set": copy.get("set", ""),
                        "set_name": copy.get("set_name", ""),
                        "modifier": copy.get("modifier", "Normal"),
                        "categories": copy.get("categories", []),
                        "primary_tag": copy.get("primary_tag", ""),
                        "uid": copy.get("uid", ""),
                        "alt_name": copy.get("alt_name", ""),
                        "price": copy.get("price", 0.0),
                        "is_commander": copy.get("is_commander", False),
                        "cmc": copy.get("cmc", 0),
                        "status": copy_status,
                        "is_core": any(cat in core_tags for cat in copy.get("categories", []))
                    })
            
    # Sort alphabetically by card name, then deck name
    inventory.sort(key=lambda x: (x["name"], x["deck"]))
    return {"inventory": inventory}

@app.get("/api/tags")
async def get_tags():
    """Get all Lab-managed tags"""
    lab_tags_json = r.get("lab_tags")
    lab_tags = json.loads(lab_tags_json) if lab_tags_json else {}
    
    # Aggregate all unique tags from Lab
    all_tags = set()
    for card_tags in lab_tags.values():
        all_tags.update(card_tags)
    
    return {"tags": sorted(all_tags)}

@app.get("/api/card/{card_name}/tags")
async def get_card_tags(card_name: str):
    """Get Lab tags and Archidekt reference tags for a specific card"""
    # Lab tags (our source of truth)
    lab_tags_json = r.get("lab_tags")
    lab_tags_all = json.loads(lab_tags_json) if lab_tags_json else {}
    card_lab_tags = lab_tags_all.get(card_name, [])
    
    # Archidekt reference tags (for comparison)
    vocab_json = r.get("tag_vocabulary")
    vocabulary = json.loads(vocab_json) if vocab_json else {}
    card_archidekt_tags = vocabulary.get(card_name, {})
    
    # Core tags defined globally
    core_tags_json = r.get("core_tags")
    core_tags = json.loads(core_tags_json) if core_tags_json else []
    
    # Get deck appearances for cardinality
    card_data_json = r.get(f"card:{card_name}")
    if not card_data_json:
        return {"error": "Card not found"}, 404
    
    card_data = json.loads(card_data_json)
    copies = card_data.get("copies", [])
    total_decks = len(set(copy.get("deck") for copy in copies))
    
    # Calculate confidence for Archidekt tags (reference only)
    archidekt_stats = []
    for tag, count in card_archidekt_tags.items():
        confidence = count / total_decks if total_decks > 0 else 0
        archidekt_stats.append({
            "tag": tag,
            "count": count,
            "total_decks": total_decks,
            "confidence": confidence
        })
    
    archidekt_stats.sort(key=lambda x: -x["confidence"])
    
    return {
        "card_name": card_name,
        "lab_tags": card_lab_tags,
        "archidekt_tags": archidekt_stats,
        "total_decks": total_decks,
        "core_tags": core_tags
    }

@app.get("/api/tags/core")
async def get_core_tags():
    core_tags_json = r.get("core_tags")
    return {"core_tags": json.loads(core_tags_json) if core_tags_json else []}

@app.post("/api/tags/core")
async def toggle_core_tag(request: Request):
    """Mark or unmark a tag as a core archetype tag"""
    data = await request.json()
    tag = data.get("tag")
    action = data.get("action")
    
    if not tag or action not in ["add", "remove"]:
        return {"error": "Invalid request"}, 400
        
    core_tags_json = r.get("core_tags")
    core_tags = set(json.loads(core_tags_json)) if core_tags_json else set()
    
    if action == "add":
        core_tags.add(tag)
    else:
        core_tags.discard(tag)
        
    r.set("core_tags", json.dumps(list(core_tags)))
    return {"success": True, "core_tags": list(core_tags)}

@app.post("/api/card/{card_name}/tags")
async def update_card_tags(card_name: str, request: Request):
    """Add or remove Lab tags from a card"""
    data = await request.json()
    tag = data.get("tag")
    action = data.get("action")  # "add" or "remove"
    
    if not tag or action not in ["add", "remove"]:
        return {"error": "Invalid request"}, 400
    
    # Get Lab tags
    lab_tags_json = r.get("lab_tags")
    lab_tags = json.loads(lab_tags_json) if lab_tags_json else {}
    
    # Initialize if card doesn't exist
    if card_name not in lab_tags:
        lab_tags[card_name] = []
    
    # Update Lab tags
    if action == "add" and tag not in lab_tags[card_name]:
        lab_tags[card_name].append(tag)
    elif action == "remove" and tag in lab_tags[card_name]:
        lab_tags[card_name].remove(tag)
    
    # Save Lab tags
    r.set("lab_tags", json.dumps(lab_tags))
    
    return {
        "success": True,
        "lab_tags": lab_tags[card_name],
        "action": action,
        "tag": tag
    }

@app.get("/api/decks/{deck_id}/categories")
async def get_deck_categories(deck_id: str):
    """Get per-deck category overrides: {card_name: [tags]}.

    This is the Deck Categories override layer (see docs/DECK_MANAGEMENT_VIEW.md) —
    distinct from and secondary to the global Lab Tags (/api/card/{name}/tags).
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    deck_categories_json = r.get(f"deck_categories:{deck_id}")
    deck_categories = json.loads(deck_categories_json) if deck_categories_json else {}

    return {"deck_id": deck_id, "categories": deck_categories}


@app.post("/api/decks/{deck_id}/categories")
async def update_deck_categories(deck_id: str, request: Request):
    """Add or remove a per-deck category override tag for a card.

    NOTE: this endpoint is for the Deck Categories override layer only. Editing
    a card's global Lab Tags from the deck-manage UI must go through the
    existing /api/card/{name}/tags endpoint instead, per the DECISION comment
    on this task — Deck Categories is a secondary/override layer, not primary.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    data = await request.json()
    card_name = data.get("card_name")
    tag = data.get("tag")
    action = data.get("action")  # "add" or "remove"

    if not card_name or not tag or action not in ["add", "remove"]:
        return JSONResponse({"error": "Invalid request"}, status_code=400)

    deck_categories_json = r.get(f"deck_categories:{deck_id}")
    deck_categories = json.loads(deck_categories_json) if deck_categories_json else {}

    if card_name not in deck_categories:
        deck_categories[card_name] = []

    if action == "add" and tag not in deck_categories[card_name]:
        deck_categories[card_name].append(tag)
    elif action == "remove" and tag in deck_categories[card_name]:
        deck_categories[card_name].remove(tag)

    # Drop empty entries so the map only ever holds cards with an actual override
    if not deck_categories[card_name]:
        del deck_categories[card_name]

    r.set(f"deck_categories:{deck_id}", json.dumps(deck_categories))

    return {
        "success": True,
        "deck_id": deck_id,
        "card_name": card_name,
        "categories": deck_categories.get(card_name, []),
        "action": action,
        "tag": tag
    }


@app.post("/api/decks/{deck_id}/cards")
async def add_card_to_deck(deck_id: str, request: Request):
    """Manually add a card to a deck (the sanctioned manual add path — see
    docs/DECK_MANAGEMENT_VIEW.md "Add / remove flow").

    - Reuses an existing `in_collection` instance of `card_name` if one
      exists, else creates a new one via instances.create_instance, then
      transitions it to `in_deck` bound to this deck.
    - Appends card_name to the deck's local decklist if not already present.
    - Explicitly bypasses the is_deck_physical lock (this endpoint is the
      intended exception to that lock, unlike direct instance PATCH calls).
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": "Deck not found"})

    data = await request.json()
    card_name = data.get("card_name")
    if not card_name:
        return JSONResponse(status_code=400, content={"success": False, "error": "card_name is required"})

    registry_id = registry.registry_id_of(deck)
    deck_name = deck.get("name", "")

    try:
        existing = [
            inst for inst in instance_store.list_instances(r, card_name=card_name, ownership_status="in_collection")
        ]
        if existing:
            instance = existing[0]
        else:
            instance = instance_store.create_instance(
                r, card_name=card_name, ownership_status="in_collection"
            )

        instance = instance_store.transition_status(
            r,
            instance["id"],
            new_status="in_deck",
            deck_id=registry_id,
            deck_name=deck_name,
            _allow_physical_lock_bypass=True,
        )
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

    registry.add_card_to_decklist(r, registry_id, card_name)

    return {"success": True, "instance": instance, "deck_id": registry_id, "card_name": card_name}


@app.delete("/api/decks/{deck_id}/cards/{instance_id}")
async def remove_card_from_deck(deck_id: str, instance_id: str):
    """Manually remove a card instance from a deck (the sanctioned manual
    remove path — see docs/DECK_MANAGEMENT_VIEW.md "Add / remove flow").

    - Validates the instance is currently bound `in_deck` to this deck_id
      (mismatched deck_id or unknown instance_id is a 404, not a silent
      no-op).
    - Transitions the instance to `in_collection` via
      instance_store.transition_status, which clears deck_id per the
      existing state machine — the instance becomes plain inventory
      immediately.
    - Explicitly bypasses the is_deck_physical lock (this endpoint is the
      intended exception to that lock, unlike direct instance PATCH calls),
      mirroring add_card_to_deck.
    - If multiple instances of the same card are bound to the deck, the
      caller (UI) picks which instance_id to unbind; this endpoint only
      ever acts on the single instance_id given.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse(status_code=404, content={"success": False, "error": "Deck not found"})

    registry_id = registry.registry_id_of(deck)

    instance = instance_store.get_instance(r, instance_id)
    if not instance:
        return JSONResponse(status_code=404, content={"success": False, "error": "Instance not found"})

    if instance.get("ownership_status") != "in_deck" or str(instance.get("deck_id")) != str(registry_id):
        return JSONResponse(
            status_code=404,
            content={"success": False, "error": "Instance is not bound to this deck"},
        )

    try:
        instance = instance_store.transition_status(
            r,
            instance_id,
            new_status="in_collection",
            _allow_physical_lock_bypass=True,
        )
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

    return {"success": True, "instance": instance, "deck_id": registry_id, "instance_id": instance_id}


@app.get("/api/decks/{deck_id}/search")
async def search_deck_cards(deck_id: str, q: str = ""):
    """Local card-name autocomplete for the add-card dropdown in the deck
    management view. Case-insensitive substring search over `card_meta:*`
    records (same filtering pattern as /api/tags-adjacent card lookups).

    v1 scope: local Redis card_meta matching only — no live Scryfall lookups.
    """
    deck = registry.find_deck(r, deck_id)
    if not deck:
        return JSONResponse({"error": "Deck not found"}, status_code=404)

    query = q.strip()
    if not query:
        return {"deck_id": deck_id, "query": q, "results": []}

    query_lower = query.lower()
    results = []
    for key in r.keys("card_meta:*"):
        card_name = key[len("card_meta:"):]
        if query_lower not in card_name.lower():
            continue

        raw = r.get(key)
        if not raw:
            continue
        try:
            meta = json.loads(raw)
        except (TypeError, ValueError):
            continue

        results.append({
            "name": card_name,
            "type": meta.get("type", "Unknown"),
            "cmc": meta.get("cmc", 0),
        })

    results.sort(key=lambda c: c["name"])
    results = results[:50]

    return {"deck_id": deck_id, "query": q, "results": results}


@app.post("/api/sync-tags")
async def sync_tags_to_archidekt(request: Request):
    """Force sync Lab tags to Archidekt for specific decks or all decks"""
    data = await request.json()
    deck_names = data.get("decks", [])  # Empty = all decks
    
    session_id = os.getenv("ARCHIDEKT_SESSION")
    csrf_token = os.getenv("ARCHIDEKT_CSRF")
    
    if not session_id or not csrf_token:
        return {
            "error": "ARCHIDEKT_SESSION and ARCHIDEKT_CSRF must be set in .env"
        }, 400
    
    # Get Lab tags
    lab_tags_json = r.get("lab_tags")
    lab_tags = json.loads(lab_tags_json) if lab_tags_json else {}
    
    # Get decks to sync
    decks_json = r.get("decks")
    all_decks = json.loads(decks_json) if decks_json else []
    
    if deck_names:
        decks_to_sync = [d for d in all_decks if d["name"] in deck_names]
    else:
        decks_to_sync = all_decks
    
    results = []
    for deck in decks_to_sync:
        try:
            result = sync_deck_tags_to_archidekt(
                deck["id"], 
                deck["name"], 
                lab_tags, 
                session_id, 
                csrf_token
            )
            results.append(result)
            time.sleep(1)  # Rate limit
        except Exception as e:
            results.append({
                "deck_name": deck["name"],
                "success": False,
                "error": str(e)
            })
    
    return {
        "success": True,
        "synced_decks": len([r for r in results if r.get("success")]),
        "total_decks": len(results),
        "results": results
    }

def sync_deck_tags_to_archidekt(deck_id, deck_name, lab_tags, session_id, csrf_token):
    """Sync Lab tags to a specific Archidekt deck"""
    import requests
    import time
    
    # Fetch current deck
    deck_url = f"https://archidekt.com/api/decks/{deck_id}/"
    headers = {
        "Cookie": f"sessionid={session_id}; csrftoken={csrf_token}",
        "X-CSRFToken": csrf_token,
        "Referer": f"https://archidekt.com/decks/{deck_id}/",
        "Content-Type": "application/json"
    }
    
    response = requests.get(deck_url, headers=headers)
    response.raise_for_status()
    deck_data = response.json()
    
    # Update categories on deck cards
    cards = deck_data.get("cards", [])
    updated_count = 0
    
    structural_categories = {"Commander", "Sideboard", "Maybeboard", "Considering"}
    
    for card in cards:
        oracle_name = card.get("card", {}).get("oracleCard", {}).get("name")
        if not oracle_name or oracle_name not in lab_tags:
            continue
        
        current_cats = card.get("categories") or []
        
        # Preserve structural categories
        structural = [c for c in current_cats if c in structural_categories]
        
        # Get Lab tags for this card
        thematic = lab_tags[oracle_name]
        
        # Combine: structural + Lab tags
        new_cats = structural + thematic
        
        if set(current_cats) != set(new_cats):
            card["categories"] = new_cats
            updated_count += 1
    
    if updated_count == 0:
        return {
            "deck_name": deck_name,
            "success": True,
            "updated_count": 0,
            "message": "No changes needed"
        }
    
    # PUT the updated deck back
    response = requests.put(deck_url, headers=headers, json=deck_data)
    response.raise_for_status()
    
    return {
        "deck_name": deck_name,
        "success": True,
        "updated_count": updated_count
    }


# ---------------------------------------------------------------------------
# Card Instance Registry — ownership source of truth (see docs/CARD_DATABASE.md)
# ---------------------------------------------------------------------------

@app.post("/api/instances")
async def create_instance(request: Request):
    """Create a new unique card instance (in_mail, in_collection, in_deck, or not_owned)."""
    data = await request.json()
    try:
        record = instance_store.create_instance(
            r,
            card_name=data.get("card_name"),
            ownership_status=data.get("ownership_status", "not_owned"),
            set=data.get("set", ""),
            set_name=data.get("set_name", ""),
            collector_number=data.get("collector_number", ""),
            finish=data.get("finish", "nonfoil"),
            condition=data.get("condition", "NM"),
            language=data.get("language", "EN"),
            deck_id=data.get("deck_id"),
            deck_name=data.get("deck_name", ""),
            considered_for_deck=data.get("considered_for_deck"),
            price_paid=data.get("price_paid", 0.0),
            source=data.get("source", ""),
            date_ordered=data.get("date_ordered", ""),
            date_acquired=data.get("date_acquired", ""),
            archidekt_uid=data.get("archidekt_uid", ""),
            notes=data.get("notes", ""),
        )
        return {"success": True, "instance": record}
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.get("/api/instances")
async def list_instances(
    card: Optional[str] = None,
    status: Optional[str] = None,
    deck_id: Optional[str] = None,
    set: Optional[str] = None,
):
    """List/filter instances by card name, ownership status, deck, or set."""
    records = instance_store.list_instances(
        r, card_name=card, ownership_status=status, deck_id=deck_id, set=set
    )
    return {"instances": records, "count": len(records)}


@app.get("/api/instances/{instance_id}")
async def get_instance(instance_id: str):
    record = instance_store.get_instance(r, instance_id)
    if not record:
        return JSONResponse(status_code=404, content={"error": "Instance not found"})
    return {"instance": record}


@app.patch("/api/instances/{instance_id}")
async def update_instance(instance_id: str, request: Request):
    """Update instance fields, or transition ownership_status/deck assignment.

    Body may include `ownership_status` (+ optional `deck_id`/`deck_name` when
    moving to in_deck) to transition state, and/or any editable detail fields
    (set, condition, finish, price_paid, source, notes, etc).
    """
    data = await request.json()
    try:
        record = None
        if "ownership_status" in data:
            record = instance_store.transition_status(
                r,
                instance_id,
                new_status=data["ownership_status"],
                deck_id=data.get("deck_id"),
                deck_name=data.get("deck_name", ""),
            )

        detail_fields = {
            k: v for k, v in data.items()
            if k not in {"ownership_status", "deck_id", "deck_name"}
        }
        if detail_fields:
            record = instance_store.update_instance_fields(r, instance_id, **detail_fields)

        if record is None:
            record = instance_store.get_instance(r, instance_id)
            if not record:
                return JSONResponse(status_code=404, content={"error": "Instance not found"})

        return {"success": True, "instance": record}
    except InstanceError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})


@app.delete("/api/instances/{instance_id}")
async def delete_instance(instance_id: str):
    deleted = instance_store.delete_instance(r, instance_id)
    if not deleted:
        return JSONResponse(status_code=404, content={"error": "Instance not found"})
    return {"success": True}


@app.get("/api/cards/{card_name}/instances")
async def get_card_instances(card_name: str):
    """Ownership rollup for a card: counts by status and per-deck breakdown."""
    return instance_store.card_rollup(r, card_name)


# ---------------------------------------------------------------------------
# Linked Accounts — provider-account registry + sync (see docs/LINKED_ACCOUNTS.md)
# Thin wrappers over linked_accounts.py; no business logic lives here.
# ---------------------------------------------------------------------------

@app.get("/api/linked-accounts")
async def list_linked_accounts():
    """List every linked account with its sync status."""
    accounts = linked_accounts.list_accounts(r)
    return {"accounts": accounts, "count": len(accounts)}


@app.post("/api/linked-accounts")
async def add_linked_account(request: Request):
    """Link a new provider account and trigger its immediate first sync.

    Body: {"provider": "archidekt"|"moxfield", "username": str}
    """
    data = await request.json()
    provider = data.get("provider", "")
    username = data.get("username", "")
    try:
        account = linked_accounts.add_account(r, provider, username)
    except LinkedAccountError as e:
        return JSONResponse(status_code=400, content={"success": False, "error": str(e)})

    account = linked_accounts.sync_account(r, account["id"])
    return {"success": True, "account": account}


@app.delete("/api/linked-accounts/{account_id}")
async def remove_linked_account(account_id: str):
    """Unlink an account. Decks already pulled from it stay as-is (pull-only,
    non-destructive design — see docs/LINKED_ACCOUNTS.md)."""
    try:
        linked_accounts.remove_account(r, account_id)
    except LinkedAccountError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    return {"success": True}


@app.post("/api/linked-accounts/{account_id}/sync")
async def sync_linked_account(account_id: str):
    """Manually resync one linked account."""
    try:
        account = linked_accounts.sync_account(r, account_id)
    except LinkedAccountError as e:
        return JSONResponse(status_code=404, content={"success": False, "error": str(e)})
    return {"success": True, "account": account}


@app.post("/api/linked-accounts/sync-all")
async def sync_all_linked_accounts():
    """Manually resync every enabled linked account."""
    accounts = linked_accounts.sync_all(r)
    return {"success": True, "accounts": accounts, "count": len(accounts)}

