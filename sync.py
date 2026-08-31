import os
import requests
import redis
from dotenv import load_dotenv

load_dotenv(override=True)

ARCHIDEKT_USERNAME = os.getenv("ARCHIDEKT_USERNAME")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

if not ARCHIDEKT_USERNAME:
    print("Error: ARCHIDEKT_USERNAME environment variable not set.")
    exit(1)

r = redis.from_url(REDIS_URL, decode_responses=True)

def sync_inventory():
    print(f"Fetching user ID for username: {ARCHIDEKT_USERNAME}...")
    # 1. Get user ID
    user_url = f"https://archidekt.com/api/users/?username={ARCHIDEKT_USERNAME}"
    user_res = requests.get(user_url)
    user_res.raise_for_status()
    user_data = user_res.json()
    
    if not user_data.get("results"):
        print(f"Error: Could not find user {ARCHIDEKT_USERNAME}")
        return
        
    user_id = user_data["results"][0]["id"]
    print(f"Found user ID: {user_id}")
    
    # 2. Get all decks for the user
    print(f"Fetching decks...")
    url = f"https://archidekt.com/api/decks/v3/?ownerId={user_id}&pageSize=100"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    
    decks = data.get("results", [])
    print(f"Found {len(decks)} total decks.")
    
    inventory = {}
    deck_names = []
    
    for deck in decks:
        deck_id = deck["id"]
        deck_name = deck["name"]
        deck_colors = deck.get("colors", {})
        
        # Calculate deck's color identity code
        color_code = ""
        for c in ["W", "U", "B", "R", "G"]:
            if deck_colors.get(c, 0) > 0:
                color_code += c
        if not color_code:
            color_code = "C"
            
        deck_names.append({"name": deck_name, "color": color_code})
        print(f"Processing deck: {deck_name} (ID: {deck_id})")
        
        deck_res = requests.get(f"https://archidekt.com/api/decks/{deck_id}/")
        deck_res.raise_for_status()
        deck_data = deck_res.json()
        
        cards = deck_data.get("cards", [])
        for card in cards:
            # Check if mainboard
            # Archidekt uses categories. Usually 'Commander' or 'Mainboard' or custom categories.
            # We want cards in the main deck (not maybeboard etc.) 
            # We look for "I have" in the card's categories.
            lbl = card.get("label", "") or ""
            text = lbl.split(',')[0].strip().lower()
            
            # The user called it the "I have" color tag, but it might be named "Have"
            has_tag = (text in ["i have", "have"])
            
            if has_tag:
                # We ignore printing, so we use the card's oracle name
                oracle_card = card.get("card", {}).get("oracleCard", {})
                card_name = oracle_card.get("name")
                if not card_name:
                    continue
                    
                color_identity = oracle_card.get("colorIdentity", [])
                card_types = oracle_card.get("types", [])
                
                # Format a display type (e.g. "Creature", "Artifact", "Land")
                categories = card.get("categories") or []
                display_type = card_types[0] if card_types else "Unknown"
                is_commander = "Commander" in categories
                cmc = oracle_card.get("cmc", 0)
                
                # Use Scryfall's color identity
                if not color_identity:
                    color_code = "C"
                else:
                    # Map colors to letters W,U,B,R,G
                    color_map = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}
                    color_code = "".join([color_map.get(c, "") for c in color_identity])
                    
                quantity = card.get("quantity", 1)
                
                if card_name not in inventory:
                    inventory[card_name] = {
                        "type": display_type,
                        "color": color_code,
                        "copies": []
                    }
                
                for _ in range(quantity):
                    inventory[card_name]["copies"].append({
                        "deck": deck_name,
                        "set": card.get("card", {}).get("edition", {}).get("editioncode", "").upper(),
                        "set_name": card.get("card", {}).get("edition", {}).get("editionname", ""),
                        "modifier": card.get("modifier", "Normal"),
                        "categories": categories,
                        "uid": card.get("card", {}).get("uid", ""),
                        "alt_name": card.get("card", {}).get("displayName") or "",
                        "price": card.get("prices", {}).get("tcgfoil", 0.0) if card.get("modifier") == "Foil" and card.get("prices", {}).get("tcgfoil") else card.get("prices", {}).get("tcg", 0.0),
                        "is_commander": is_commander,
                        "cmc": cmc
                    })

    # Clear old inventory and set new
    print("Updating Redis inventory...")
    # Delete existing keys
    old_keys = r.keys("card:*")
    if old_keys:
        r.delete(*old_keys)
        
    for name, data in inventory.items():
        import json
        r.set(f"card:{name}", json.dumps(data))
        
    import json
    r.set("decks", json.dumps(sorted(deck_names, key=lambda d: d["name"])))
        
    print(f"Sync complete! {len(inventory)} unique cards saved to inventory.")

if __name__ == "__main__":
    sync_inventory()
