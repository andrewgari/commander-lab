from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import redis
import os
import json
import time
from typing import List, Optional

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
async def update_deck_status(deck_id: int, request: Request):
    data = await request.json()
    status = data.get("status")
    if status in ["physical", "virtual", "retired", "test"]:
        r.set(f"deck_status:{deck_id}", status)
        decks_json = r.get("decks")
        if decks_json:
            decks = json.loads(decks_json)
            for d in decks:
                if d.get("id") == deck_id:
                    d["status"] = status
            r.set("decks", json.dumps(decks))
        return {"success": True, "status": status}
    return {"success": False, "error": "Invalid status"}

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
