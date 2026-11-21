"""
Backfill missing `commander_legality` on `card:{oracle_id}` records (and the
legacy `card_meta:{name}` records) from Scryfall `/cards/collection`.

Only records whose commander_legality is empty are touched; known values are
never overwritten. Dry run by default.

Usage:
    python scripts/backfill_commander_legality.py                 # report only
    python scripts/backfill_commander_legality.py --apply         # write
    python scripts/backfill_commander_legality.py --apply --name "Kaalia of the Vast"
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
import requests

import src.server.domain.inventory.cards as card_store

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
COLLECTION_URL = "https://api.scryfall.com/cards/collection"


def _s(k):
    return k.decode() if isinstance(k, bytes) else k


def find_missing(r, only_name=None):
    """[(redis_key, record)] for card:/card_meta: records lacking legality."""
    out = []
    for pat in ("card:*", "card_meta:*"):
        for key in r.scan_iter(match=pat):
            key = _s(key)
            suffix = key.split(":", 1)[1]
            if pat == "card:*" and not card_store.is_oracle_id(suffix):
                continue
            raw = r.get(key)
            try:
                rec = json.loads(raw) if raw else None
            except (ValueError, TypeError):
                continue
            if not isinstance(rec, dict) or rec.get("commander_legality"):
                continue
            if only_name and (rec.get("name") or suffix) != only_name:
                continue
            out.append((key, rec))
    return out


def scryfall_legality(identifiers):
    """Map identifier-json -> commander legality via /cards/collection (<=75/call)."""
    res = {}
    for i in range(0, len(identifiers), 75):
        batch = identifiers[i:i + 75]
        resp = requests.post(COLLECTION_URL, json={"identifiers": batch}, timeout=30)
        resp.raise_for_status()
        for card in resp.json().get("data", []):
            res[card.get("oracle_id")] = res[(card.get("name") or "").casefold()] = \
                card_store.commander_legality_of(card)
        time.sleep(0.1)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--name")
    a = ap.parse_args()
    r = redis.from_url(REDIS_URL, decode_responses=True)
    missing = find_missing(r, a.name)
    print(f"{len(missing)} record(s) missing commander_legality")
    idents = []
    for key, rec in missing:
        oid = rec.get("oracle_id") or (key.split(":", 1)[1] if card_store.is_oracle_id(key.split(":", 1)[1]) else None)
        idents.append({"oracle_id": oid} if oid else {"name": rec.get("name") or key.split(":", 1)[1]})
    legal = scryfall_legality(idents) if idents else {}
    fixed = 0
    for key, rec in missing:
        leg = legal.get(rec.get("oracle_id")) or legal.get((rec.get("name") or key.split(":", 1)[1]).casefold())
        print(f"  {key}: {leg}")
        if leg and a.apply:
            rec["commander_legality"] = leg
            r.set(key, json.dumps(rec))
            fixed += 1
    print(f"backfilled {fixed}" if a.apply else "dry run; pass --apply to write")


if __name__ == "__main__":
    main()
