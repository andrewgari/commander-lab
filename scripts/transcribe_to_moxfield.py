#!/usr/bin/env python3
"""Transcribe physical Archidekt decks to Moxfield.

    python scripts/transcribe_to_moxfield.py --dry-run
    python scripts/transcribe_to_moxfield.py
    python scripts/transcribe_to_moxfield.py --folder "Paper Decks" --force

Only decks with registry status "physical" are transcribed. They are created
private, in one folder, with a generated description. Requires a saved
Moxfield session: run `python scripts/setup_auth.py` first.
See docs/MOXFIELD_TRANSCRIBE.md.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from dotenv import load_dotenv

import src.server.domain.decks.transcribe as transcribe
from src.server.infra.providers.moxfield_writer import MoxfieldAuthError, MoxfieldWriteError

load_dotenv(override=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", default=transcribe.DEFAULT_FOLDER_NAME,
                        help=f"Moxfield folder name (default: {transcribe.DEFAULT_FOLDER_NAME})")
    parser.add_argument("--visibility", default=transcribe.DEFAULT_VISIBILITY,
                        choices=sorted(["public", "unlisted", "private"]),
                        help="deck visibility on Moxfield (default: private)")
    parser.add_argument("--force", action="store_true",
                        help="re-transcribe decks already pushed to Moxfield")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be created without calling Moxfield")
    args = parser.parse_args()

    r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

    try:
        report = transcribe.transcribe_all(
            r,
            folder_name=args.folder,
            visibility=args.visibility,
            force=args.force,
            dry_run=args.dry_run,
        )
    except (MoxfieldAuthError, MoxfieldWriteError, transcribe.TranscribeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Physical decks eligible: {report['eligible']}")
    print(f"Folder: {report['folder']}  Visibility: {report['visibility']}")
    if args.dry_run:
        print("(dry run -- Moxfield was not contacted)\n")

    for result in report["results"]:
        action = result["action"]
        name = result["name"]
        if action == "created":
            print(f"  + {name} -> {result.get('moxfield_url', '')}")
            if result.get("description_error"):
                print(f"      warning: description not set ({result['description_error']})")
        elif action == "would_create":
            print(f"  ~ {name} ({result.get('cards')} cards) would be created")
        elif action == "skipped_already":
            print(f"  = {name} already on Moxfield: {result.get('moxfield_url', '')}")
        elif action == "skipped_not_physical":
            print(f"  - {name} skipped (status={result.get('status')})")
        elif action == "failed":
            print(f"  ! {name} FAILED: {result.get('error')}")

    verb = "would create" if args.dry_run else "created"
    print(f"\n{verb}: {report['created']}  already done: {report['skipped_already']}  failed: {report['failed']}")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
