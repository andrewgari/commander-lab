import redis
import json

r = redis.from_url("redis://redis:6379/0", decode_responses=True)
decks_json = r.get("decks")
if decks_json:
    decks = json.loads(decks_json)
    for d in decks:
        if d.get("folder") == "Commander Decks":
            d["status"] = "physical"
            r.set(f"deck_status:{d['id']}", "physical")
        else:
            # Set default test status if no status is saved
            saved = r.get(f"deck_status:{d['id']}")
            if not saved:
                d["status"] = "test"
                r.set(f"deck_status:{d['id']}", "test")
            else:
                d["status"] = saved
    r.set("decks", json.dumps(decks))
    print("Tagged Commander Decks as physical.")
else:
    print("No decks found.")
