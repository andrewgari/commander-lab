from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
import redis
import os
import json

app = FastAPI()

# Connect to Redis
redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(redis_url, decode_responses=True)

@app.get("/", response_class=FileResponse)
async def index():
    return FileResponse("templates/index.html")

@app.get("/api/decks")
async def get_decks():
    decks_json = r.get("decks")
    decks = json.loads(decks_json) if decks_json else []
    return {"decks": decks}

@app.get("/api/inventory")
async def get_inventory(query: str = "", deck: str = ""):
    # Fetch all keys (card names)
    keys = r.keys("card:*")
    inventory = []
    
    # Check if a specific deck was filtered
    target_decks = [d.strip() for d in deck.split(",")] if deck else []
    
    for key in keys:
        card_name = key.replace("card:", "")
        if query.lower() in card_name.lower():
            val = r.get(key)
            if not val: continue
            
            try:
                card_data = json.loads(val)
                card_type = card_data.get("type", "Unknown")
                card_color = card_data.get("color", "C")
                card_copies = card_data.get("copies", [])
            except:
                continue
                
            for copy in card_copies:
                if not target_decks or copy.get("deck") in target_decks:
                    inventory.append({
                        "name": card_name,
                        "deck": copy.get("deck", "Unknown Deck"),
                        "type": card_type,
                        "color": card_color,
                        "set": copy.get("set", ""),
                        "set_name": copy.get("set_name", ""),
                        "modifier": copy.get("modifier", "Normal"),
                        "categories": copy.get("categories", []),
                        "uid": copy.get("uid", ""),
                        "alt_name": copy.get("alt_name", ""),
                        "price": copy.get("price", 0.0),
                        "is_commander": copy.get("is_commander", False),
                        "cmc": copy.get("cmc", 0)
                    })
            
    # Sort alphabetically by card name, then deck name
    inventory.sort(key=lambda x: (x["name"], x["deck"]))
    return {"inventory": inventory}
