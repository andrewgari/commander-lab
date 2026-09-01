"""
Sync categories from Cmdr Lab back to Archidekt decks.

This script reads the Redis inventory (which is the source of truth for categories)
and updates each deck on Archidekt using the bulk import endpoint.

Requires ARCHIDEKT_SESSION and ARCHIDEKT_CSRF environment variables.
"""

import os
import requests
import redis
from dotenv import load_dotenv
import json
from collections import defaultdict
import time

load_dotenv(override=True)

ARCHIDEKT_USERNAME = os.getenv("ARCHIDEKT_USERNAME")
ARCHIDEKT_SESSION = os.getenv("ARCHIDEKT_SESSION")
ARCHIDEKT_CSRF = os.getenv("ARCHIDEKT_CSRF")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

if not ARCHIDEKT_USERNAME:
    print("Error: ARCHIDEKT_USERNAME environment variable not set.")
    exit(1)

if not ARCHIDEKT_SESSION or not ARCHIDEKT_CSRF:
    print("Error: ARCHIDEKT_SESSION and ARCHIDEKT_CSRF must be set for write access.")
    print("Get these from your browser's cookies when logged into Archidekt.")
    exit(1)

r = redis.from_url(REDIS_URL, decode_responses=True)

def get_deck_id_by_name(deck_name):
    """Fetch deck ID from stored deck list"""
    decks_json = r.get("decks")
    if not decks_json:
        return None
    
    decks = json.loads(decks_json)
    for deck in decks:
        if deck["name"] == deck_name:
            return deck["id"]
    return None

def build_deck_exports():
    """Build export data for each deck from Redis inventory"""
    # Get all cards
    keys = r.keys("card:*")
    
    # Group cards by deck
    deck_cards = defaultdict(list)
    
    for key in keys:
        card_name = key.replace("card:", "")
        val = r.get(key)
        if not val:
            continue
        
        card_data = json.loads(val)
        copies = card_data.get("copies", [])
        
        for copy in copies:
            deck_name = copy.get("deck")
            if not deck_name:
                continue
            
            deck_cards[deck_name].append({
                "name": card_name,
                "set": copy.get("set", ""),
                "categories": copy.get("categories", []),
                "modifier": copy.get("modifier", "Normal"),
                "uid": copy.get("uid", "")
            })
    
    return deck_cards

def export_deck_to_text(cards):
    """
    Build Archidekt-compatible decklist text format.
    Format: 1 Card Name (SET) *FOIL
    
    Categories are NOT part of the text import format - they must be preserved
    from the existing deck structure. We'll need to use the full deck update API instead.
    """
    lines = []
    for card in cards:
        line = f"1 {card['name']}"
        if card['set']:
            line += f" ({card['set']})"
        if card['modifier'] == "Foil":
            line += " *FOIL"
        lines.append(line)
    return "\n".join(lines)

def sync_deck_categories(deck_id, deck_name, lab_cards):
    """
    Sync categories from Lab to Archidekt for a specific deck.
    
    Strategy:
    1. Fetch current deck state from Archidekt
    2. Update categories on matching cards (match by oracle name)
    3. PUT the updated deck back
    """
    print(f"\n--- Syncing {deck_name} (ID: {deck_id}) ---")
    
    # Fetch current deck
    deck_url = f"https://archidekt.com/api/decks/{deck_id}/"
    headers = {
        "Cookie": f"sessionid={ARCHIDEKT_SESSION}; csrftoken={ARCHIDEKT_CSRF}",
        "X-CSRFToken": ARCHIDEKT_CSRF,
        "Referer": f"https://archidekt.com/decks/{deck_id}/",
        "Content-Type": "application/json"
    }
    
    response = requests.get(deck_url, headers=headers)
    response.raise_for_status()
    deck_data = response.json()
    
    # Build lookup from Lab data
    lab_categories = {}
    for card in lab_cards:
        lab_categories[card["name"]] = card["categories"]
    
    # Update categories on deck cards
    cards = deck_data.get("cards", [])
    updated_count = 0
    
    structural_categories = {"Commander", "Sideboard", "Maybeboard", "Considering"}
    
    for card in cards:
        oracle_name = card.get("card", {}).get("oracleCard", {}).get("name")
        if not oracle_name:
            continue
        
        if oracle_name in lab_categories:
            current_cats = card.get("categories") or []
            lab_cats = lab_categories[oracle_name]
            
            # Preserve structural categories from Archidekt
            structural = [c for c in current_cats if c in structural_categories]
            
            # Get thematic tags from Lab
            thematic = [c for c in lab_cats if c not in structural_categories]
            
            # Combine: structural + thematic
            new_cats = structural + thematic
            
            if set(current_cats) != set(new_cats):
                card["categories"] = new_cats
                updated_count += 1
    
    if updated_count == 0:
        print(f"  No changes needed for {deck_name}")
        return
    
    print(f"  Updating {updated_count} cards...")
    
    # PUT the updated deck back
    # Note: The full deck PUT is complex and may require all fields
    # For safety, we'll only update the cards array
    update_payload = {
        "cards": cards
    }
    
    # Archidekt's API may require the full deck object, so let's send everything back
    response = requests.put(deck_url, headers=headers, json=deck_data)
    
    if response.status_code == 200:
        print(f"  ✓ Successfully updated {deck_name}")
    else:
        print(f"  ✗ Failed to update {deck_name}: {response.status_code}")
        print(f"    {response.text[:200]}")

def sync_all_decks():
    """Sync all decks from Lab to Archidekt"""
    print("Building deck exports from Lab inventory...")
    deck_cards = build_deck_exports()
    
    print(f"Found {len(deck_cards)} decks to sync")
    
    for deck_name, cards in deck_cards.items():
        deck_id = get_deck_id_by_name(deck_name)
        if not deck_id:
            print(f"⚠ Skipping {deck_name}: deck ID not found")
            continue
        
        try:
            sync_deck_categories(deck_id, deck_name, cards)
            # Rate limit to avoid 429s
            time.sleep(1)
        except Exception as e:
            print(f"✗ Error syncing {deck_name}: {e}")
            continue
    
    print("\n=== Sync Complete ===")

if __name__ == "__main__":
    sync_all_decks()
