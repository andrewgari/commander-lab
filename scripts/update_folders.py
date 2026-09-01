import redis
import json
import os

r = redis.from_url("redis://redis:6379/0", decode_responses=True)
decks_json = r.get("decks")
if decks_json:
    decks = json.loads(decks_json)
    for d in decks:
        if d.get("folder") == "Theorycrafting / WIP":
            d["folder"] = "Test Decks"
        elif d.get("folder") == "Other Decks":
            d["folder"] = "Loose Decks"
    r.set("decks", json.dumps(decks))
    print("Folders updated in Redis.")
else:
    print("No decks found in Redis.")
