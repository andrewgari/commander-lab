#!/usr/bin/env python3
"""Import the Archidekt card collection into Commander Lab instances.

    python scripts/import_collection.py --dry-run
    python scripts/import_collection.py

Requires ARCHIDEKT_SESSION + ARCHIDEKT_CSRF (Archidekt has no public
collection API) and ARCHIDEKT_USERNAME, or pass --username.
See docs/COLLECTION_IMPORT.md.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from dotenv import load_dotenv

import src.server.domain.inventory.collection as collection
from src.server.infra.providers.archidekt_collection import CollectionAuthError, CollectionFetchError

load_dotenv(override=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--username", default=None, help="Archidekt username (default: $ARCHIDEKT_USERNAME)")
    parser.add_argument("--dry-run", action="store_true", help="report the diff without writing instances")
    args = parser.parse_args()

    r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

    try:
        result = collection.import_collection(r, username=args.username, dry_run=args.dry_run)
    except (CollectionAuthError, CollectionFetchError, collection.CollectionImportError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    report = result["report"]
    verb = "would create" if args.dry_run else "created"
    print(f"Archidekt collection for {result['username']}:")
    print(f"  remote rows:      {report['rows']}")
    print(f"  remote copies:    {report['remote_copies']}")
    print(f"  {verb + ':':<17} {report['created']}")
    print(f"  wishlist promoted: {report['promoted']}")
    print(f"  already present:  {report['already_present']}")

    if report["surplus"]:
        total = sum(s["count"] for s in report["surplus"])
        print(f"\n  {total} local copies not in Archidekt (untouched, review manually):")
        for entry in report["surplus"][:40]:
            print(f"    {entry['count']}x {entry['card_name']} [{entry['set']}] {entry['finish']}")
        if len(report["surplus"]) > 40:
            print(f"    ... and {len(report['surplus']) - 40} more")

    if args.dry_run:
        print("\n(dry run — nothing written)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
