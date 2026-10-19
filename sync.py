import os
import requests
import redis
from dotenv import load_dotenv
import json
from collections import defaultdict
import time

import cards as card_store
import registry
import lab_tags as lab_tag_store

# Key set by scripts/migrate_to_instances.py --apply --delete-old to signal
# that the legacy card:{name} inventory keys have been purged. sync.py checks
# this before rewriting those keys so a subsequent sync doesn't silently
# recreate what the migration just deleted.
MIGRATION_LEGACY_CARD_KEYS_PURGED = "migration:legacy_card_keys_purged"


def should_write_legacy_card_keys(r):
    """Return False when the instance-model migration has purged legacy
    card:{name} keys (marker is set). Callers use this to decide whether to
    write the legacy inventory blob for each card in their inventory dict.
    """
    return not r.get(MIGRATION_LEGACY_CARD_KEYS_PURGED)

load_dotenv(override=True)

ARCHIDEKT_USERNAME = os.getenv("ARCHIDEKT_USERNAME")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

if not ARCHIDEKT_USERNAME:
    print("Error: ARCHIDEKT_USERNAME environment variable not set.")
    exit(1)

r = redis.from_url(REDIS_URL, decode_responses=True)

# Categories that are structural, not thematic (preserve, don't learn from)
STRUCTURAL_CATEGORIES = {"Commander", "Sideboard", "Maybeboard", "Considering"}

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
    
    # Lab-managed tags: card_name -> [tag1, tag2, ...]
    # These are independent of what's in Archidekt
    lab_tags = {}
    existing_lab_tags_json = r.get("lab_tags")
    if existing_lab_tags_json:
        lab_tags = json.loads(existing_lab_tags_json)
    
    # Card tag vocabulary: card_name -> {tag_name: count}
    # This is just for reference/statistics from Archidekt
    tag_vocabulary = defaultdict(lambda: defaultdict(int))
    
    # Track which cards appear in which decks for cardinality calculation
    card_deck_appearances = defaultdict(set)
    
    # Display names for the account's folders. Display only: which decks are
    # physical comes from the folder id (registry.physical_folder_id()).
    FOLDER_MAP = {
        588380: "Commander Decks",
        656395: "Test Decks",
        45665: "Loose Decks"
    }
    
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
            
        parent_id = registry.folder_id_of(deck.get("parentFolderId", deck.get("parentFolder")))
        folder_name = deck.get("parentFolderName") or FOLDER_MAP.get(
            parent_id, f"Folder {parent_id}" if parent_id else "Loose Decks")
        
        # Lifecycle status (physical | digital | retired | testing) follows the
        # physical-folder rule, applied after the merge below through
        # registry.apply_folder_rules -> set_status (auto-bind on entering the
        # folder, pending_removal on leaving it). A deck new to the registry
        # starts with only its saved non-physical status, if any, so entering
        # the folder is a real transition.
        saved_status = r.get(f"deck_status:{deck_id}")
        status = saved_status if saved_status in registry.EDITABLE_STATUSES else None
        
        deck_obj = {
            "id": deck_id,
            "name": deck_name, 
            "color": color_code, 
            "commanders": [], 
            "commander_uids": [],
            "folder": folder_name,
            "status": status,
            "description": "",
            "parent_folder_id": parent_id,
            "theorycrafted": bool(deck.get("theorycrafted")),
        }
        deck_names.append(deck_obj)
        print(f"Processing deck: {deck_name} (ID: {deck_id})")
        
        deck_res = requests.get(f"https://archidekt.com/api/decks/{deck_id}/")
        deck_res.raise_for_status()
        deck_data = deck_res.json()
        deck_obj["description"] = deck_data.get("description", "")
        
        # Rate limit to avoid 429s
        time.sleep(0.5)
        
        cards = deck_data.get("cards", [])

        # Normalized mainboard cardlist (same shape as providers.archidekt
        # fetch_deck): the folder rule's auto-bind needs it when a deck
        # enters the physical folder during this sync.
        mainboard = {}
        for card in cards:
            cats = card.get("categories") or []
            if "Maybeboard" in cats or "Sideboard" in cats:
                continue
            nm = card.get("card", {}).get("oracleCard", {}).get("name")
            if nm:
                mainboard[nm] = mainboard.get(nm, 0) + (card.get("quantity", 1) or 1)
        deck_obj["cards"] = [{"name": n, "quantity": q} for n, q in mainboard.items()]
        for card in cards:
            categories = card.get("categories") or []
            if "Commander" in categories:
                cname = card.get("card", {}).get("oracleCard", {}).get("name")
                cuid = card.get("card", {}).get("uid")
                if cname:
                    deck_obj["commanders"].append(cname)
                if cuid:
                    deck_obj["commander_uids"].append(cuid)
        
        # Learn tag vocabulary from existing categories
        for card in cards:
            categories = card.get("categories") or []
            
            # Skip structural categories and sideboards
            if "Maybeboard" in categories or "Sideboard" in categories:
                continue
            
            oracle_card = card.get("card", {}).get("oracleCard", {})
            card_name = oracle_card.get("name")
            if not card_name:
                continue
            
            # Track deck appearances for cardinality
            card_deck_appearances[card_name].add(deck_name)
            
            # Learn from thematic categories
            for cat in categories:
                if cat not in STRUCTURAL_CATEGORIES:
                    tag_vocabulary[card_name][cat] += 1
                    
        for card in cards:
            # Check if mainboard
            lbl = card.get("label", "") or ""
            text = lbl.split(',')[0].strip().lower()
            categories = card.get("categories") or []
            
            # Determine card copy physical status:
            # - "virtual": if deck is virtual (or folder indicates virtual, e.g. Test Decks)
            # - "pending": if label/tag/category indicates pending/ordered
            # - "possible": if label/tag/category indicates possible/maybe
            # - "have": default for accounted for / have tag
            
            status = "have"
            if "virtual" in deck_name.lower() or folder_name == "Test Decks":
                status = "virtual"  # card-copy ownership status vocabulary, unrelated to deck lifecycle status
            elif text in ["pending", "ordered", "in flight"] or "Pending" in categories:
                status = "pending"
            elif text in ["possible", "maybe"] or "Possible" in categories:
                status = "possible"
            elif text in ["i have", "have"]:
                status = "have"
            else:
                status = "have"
            
            # We ignore printing, so we use the card's oracle name
            oracle_card = card.get("card", {}).get("oracleCard", {})
            card_name = oracle_card.get("name")
            if not card_name:
                continue
                
            color_identity = oracle_card.get("colorIdentity", [])
            card_types = oracle_card.get("types", [])
            
            categories = card.get("categories") or []
            # Primary category is the first non-structural category, or the first category if none
            non_struct_cats = [c for c in categories if c not in STRUCTURAL_CATEGORIES]
            primary_tag = non_struct_cats[0] if non_struct_cats else (categories[0] if categories else "")
            
            # The user requested to only show the mainboard (i.e. exclude Sideboard/Maybeboard)
            if "Maybeboard" in categories or "Sideboard" in categories:
                continue
                
            display_type = card_types[0] if card_types else "Unknown"
            is_commander = "Commander" in categories
            cmc = oracle_card.get("cmc", 0)
            
            super_types = oracle_card.get("superTypes", [])
            oracle_text = oracle_card.get("text", "")
            
            # Use Scryfall's color identity
            if not color_identity:
                color_code = "C"
            else:
                # Map colors to letters W,U,B,R,G
                color_map = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}
                color_code = "".join([color_map.get(c, "") for c in color_identity])
                
            quantity = card.get("quantity", 1)

            # Scryfall's oracle_id, surfaced by Archidekt as oracleCard.uid.
            # Feeds the new card:{oracle_id} ORM keyspace (cards.py) below,
            # additive alongside the legacy name-keyed inventory dict.
            oracle_id = oracle_card.get("uid")

            if card_name not in inventory:
                inventory[card_name] = {
                    "type": display_type,
                    "color": color_code,
                    "super_types": super_types,
                    "sub_types": oracle_card.get("subTypes", []),
                    "keywords": oracle_card.get("keywords", []),
                    "oracle_text": oracle_text,
                    "copies": []
                }

            if oracle_id:
                card_store.upsert_card(r, {
                    "oracle_id": oracle_id,
                    "name": card_name,
                    "type": display_type,
                    "color": color_code,
                    "super_types": super_types,
                    "sub_types": oracle_card.get("subTypes", []),
                    "keywords": oracle_card.get("keywords", []),
                    "oracle_text": oracle_text,
                    "cmc": cmc,
                    # Consumed by deck_validator.CardInfo.from_record.
                    "types": card_types,
                    "commander_legality": (oracle_card.get("legalities") or {}).get("commander"),
                })
            
            for _ in range(quantity):
                inventory[card_name]["copies"].append({
                    "deck": deck_name,
                    "set": card.get("card", {}).get("edition", {}).get("editioncode", "").upper(),
                    "set_name": card.get("card", {}).get("edition", {}).get("editionname", ""),
                    "modifier": card.get("modifier", "Normal"),
                    "categories": categories,
                    "primary_tag": primary_tag,
                    "uid": card.get("card", {}).get("uid", ""),
                    "alt_name": card.get("card", {}).get("displayName") or "",
                    "price": card.get("prices", {}).get("tcgfoil", 0.0) if card.get("modifier") == "Foil" and card.get("prices", {}).get("tcgfoil") else card.get("prices", {}).get("tcg", 0.0),
                    "is_commander": is_commander,
                    "cmc": cmc,
                    "status": status
                })

    # Don't auto-apply tags anymore - Lab tags are managed separately
    # Just store the vocabulary for reference
    print("\n=== Learning Tag Vocabulary from Archidekt ===")
    print(f"Learned tags from {len(tag_vocabulary)} cards")
    
    # Store tag vocabulary in Redis for reference
    tag_vocab_data = {}
    for card_name, tags in tag_vocabulary.items():
        tag_vocab_data[card_name] = dict(tags)
    r.set("tag_vocabulary", json.dumps(tag_vocab_data))
    
    # Initialize lab_tags for new cards
    for card_name in inventory.keys():
        if card_name not in lab_tags:
            # Default: use high-confidence tags as initial Lab tags
            total_appearances = len(card_deck_appearances[card_name])
            if total_appearances > 0 and card_name in tag_vocabulary:
                high_conf_tags = []
                for tag, count in tag_vocabulary[card_name].items():
                    confidence = count / total_appearances
                    if confidence >= 0.5 and tag not in STRUCTURAL_CATEGORIES:
                        high_conf_tags.append(tag)
                lab_tags[card_name] = high_conf_tags
                for tag in high_conf_tags:
                    lab_tag_store.set_source(r, card_name, tag, lab_tag_store.LEARNED)
            else:
                lab_tags[card_name] = []
    
    # Save lab tags
    r.set("lab_tags", json.dumps(lab_tags))
    print(f"Stored Lab tags for {len(lab_tags)} cards")

    # Clear old inventory and set new
    print("\nUpdating Redis inventory...")
    # Delete existing keys. NOTE: `card:*` is shared with the new
    # card:{oracle_id} ORM keyspace (cards.py) written above in this same
    # sync pass — only clear the legacy name-keyed entries here, or a
    # full resync would wipe the oracle_id records right after writing them.
    old_keys = [k for k in r.keys("card:*") if not card_store.is_oracle_id(k[len("card:"):])]
    if old_keys:
        r.delete(*old_keys)

    # Guard: skip rewriting legacy card:{name} keys if the instance-model
    # migration has already run and purged them (scripts/migrate_to_instances.py
    # --delete-old sets this marker). Without this guard, every sync run would
    # silently recreate the legacy keys that the migration just deleted.
    if should_write_legacy_card_keys(r):
        for name, data in inventory.items():
            r.set(f"card:{name}", json.dumps(data))
    else:
        print("Migration marker found -- skipping legacy card:{name} write.")
        
    # Merge into the registry instead of replacing the whole key. A plain
    # r.set("decks", deck_names) used to drop every field this pull doesn't
    # produce (links, source/registry_id, cards, moxfield_id) and every deck
    # it didn't fetch (Moxfield copies). See registry.merge_full_sync.
    existing_decks = registry.list_decks(r)
    r.set(registry.DECKS_KEY, json.dumps(registry.merge_full_sync(existing_decks, deck_names)))
    changed = registry.apply_folder_rules(r)
    print(f"Physical-folder rule: {len(changed)} deck status change(s).")
    # BR-5: nudge the bracket worker (separate process) to re-check decklists
    # now. Best effort: the worker finds changed decks by input hash anyway.
    try:
        from analytics import bracket_worker
        bracket_worker.wake(r)
    except Exception as exc:  # noqa: BLE001 - must never fail the sync
        print(f"Bracket worker wake skipped: {exc}")
        
    print(f"Sync complete! {len(inventory)} unique cards saved to inventory.")

if __name__ == "__main__":
    sync_inventory()
