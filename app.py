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

@app.get("/api/inventory")
async def get_inventory(query: str = ""):
    # Fetch all keys (card names)
    keys = r.keys("card:*")
    inventory = []
    
    for key in keys:
        card_name = key.replace("card:", "")
        if query.lower() in card_name.lower():
            quantity = r.get(key)
            inventory.append({"name": card_name, "quantity": int(quantity)})
            
    # Sort alphabetically
    inventory.sort(key=lambda x: x["name"])
    return {"inventory": inventory}
