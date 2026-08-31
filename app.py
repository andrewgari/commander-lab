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
                total = card_data.get("total", 0)
                card_decks = card_data.get("decks", {})
            except:
                # Fallback for old data format
                total = int(val)
                card_decks = {}
                
            if not card_decks and total > 0:
                # Fallback if no deck data but total exists
                for _ in range(total):
                    inventory.append({"name": card_name, "deck": "Unknown Deck"})
            else:
                for deck_name, qty in card_decks.items():
                    if not target_decks or deck_name in target_decks:
                        for _ in range(qty):
                            inventory.append({"name": card_name, "deck": deck_name})
            
    # Sort alphabetically by card name, then deck name
    inventory.sort(key=lambda x: (x["name"], x["deck"]))
    return {"inventory": inventory}
