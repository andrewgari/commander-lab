#!/usr/bin/env python3
"""
Quick test script for the tag management API.

Usage:
    python test_tags.py
"""

import requests
import json

BASE_URL = "http://localhost:8000/api"

def test_get_all_tags():
    print("\n=== Testing GET /api/tags ===")
    r = requests.get(f"{BASE_URL}/tags")
    r.raise_for_status()
    data = r.json()
    tags = data["tags"]
    print(f"Found {len(tags)} unique tags")
    print(f"First 10 tags: {tags[:10]}")
    return tags

def test_get_card_tags(card_name):
    print(f"\n=== Testing GET /api/card/{card_name}/tags ===")
    r = requests.get(f"{BASE_URL}/card/{card_name}/tags")
    r.raise_for_status()
    data = r.json()
    print(f"Card: {data['card_name']}")
    print(f"Appears in {data['total_decks']} decks")
    print("Tags:")
    for tag_info in data["tags"]:
        confidence_pct = int(tag_info["confidence"] * 100)
        print(f"  - {tag_info['tag']}: {tag_info['count']}/{tag_info['total_decks']} ({confidence_pct}%)")
    return data

def test_add_tag(card_name, tag, decks=None):
    print(f"\n=== Testing POST /api/card/{card_name}/tags (add '{tag}') ===")
    payload = {
        "action": "add",
        "tag": tag,
        "decks": decks or []
    }
    r = requests.post(f"{BASE_URL}/card/{card_name}/tags", json=payload)
    r.raise_for_status()
    data = r.json()
    print(f"Result: {data}")
    return data

def test_remove_tag(card_name, tag):
    print(f"\n=== Testing POST /api/card/{card_name}/tags (remove '{tag}') ===")
    payload = {
        "action": "remove",
        "tag": tag,
        "decks": []
    }
    r = requests.post(f"{BASE_URL}/card/{card_name}/tags", json=payload)
    r.raise_for_status()
    data = r.json()
    print(f"Result: {data}")
    return data

if __name__ == "__main__":
    print("Testing Tag Management API")
    print("=" * 60)
    
    # Test 1: Get all tags
    all_tags = test_get_all_tags()
    
    # Test 2: Get tags for Sol Ring
    sol_ring_tags = test_get_card_tags("Sol Ring")
    
    # Test 3: Get tags for Cultivate
    cultivate_tags = test_get_card_tags("Cultivate")
    
    # Test 4: Add a test tag
    print("\n" + "=" * 60)
    print("Adding test tag 'TEST_TAG' to Sol Ring...")
    test_add_tag("Sol Ring", "TEST_TAG")
    
    # Verify it was added
    sol_ring_tags = test_get_card_tags("Sol Ring")
    
    # Test 5: Remove the test tag
    print("\n" + "=" * 60)
    print("Removing test tag from Sol Ring...")
    test_remove_tag("Sol Ring", "TEST_TAG")
    
    # Verify it was removed
    sol_ring_tags = test_get_card_tags("Sol Ring")
    
    print("\n" + "=" * 60)
    print("All tests passed! ✓")
