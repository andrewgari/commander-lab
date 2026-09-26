#!/usr/bin/env python3
"""Archive existing Moxfield decks: rename + re-file. Never deletes anything.

    python scripts/archive_moxfield_decks.py --dry-run
    python scripts/archive_moxfield_decks.py
    python scripts/archive_moxfield_decks.py --folder "Old Decks" --keep-visibility

Every deck's original name and visibility are saved to Redis
(`moxfield_archive:{deck_id}`) BEFORE anything is changed, so the rename is
reversible with --restore. Decks this app transcribed itself are skipped.
See docs/MOXFIELD_ARCHIVE.md.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from dotenv import load_dotenv

import src.server.domain.decks.archive as archive
from src.server.infra.providers.moxfield_writer import MoxfieldAuthError, MoxfieldWriteError

load_dotenv(override=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--folder", default=archive.DEFAULT_ARCHIVE_FOLDER,
                        help=f"archive folder name (default: {archive.DEFAULT_ARCHIVE_FOLDER})")
    parser.add_argument("--prefix", default=archive.DEFAULT_PREFIX,
                        help=f"name prefix (default: {archive.DEFAULT_PREFIX!r})")
    parser.add_argument("--keep-visibility", action="store_true",
                        help="don't change deck visibility (default: set private)")
    parser.add_argument("--include-transcribed", action="store_true",
                        help="also archive decks this app created via transcribe")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would change without modifying anything")
    parser.add_argument("--restore", metavar="DECK_ID",
                        help="undo one deck's archive (original name + visibility)")
    args = parser.parse_args()

    r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)

    if args.restore:
        try:
            result = archive.restore(r, args.restore)
        except (MoxfieldAuthError, MoxfieldWriteError, archive.ArchiveError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        print(f"Restored {result['deck_id']} -> {result['restored_name']!r} "
              f"(visibility {result['restored_visibility']})")
        return 0

    try:
        report = archive.archive_all(
            r,
            folder_name=args.folder,
            prefix=args.prefix,
            visibility=None if args.keep_visibility else archive.DEFAULT_VISIBILITY,
            skip_transcribed=not args.include_transcribed,
            dry_run=args.dry_run,
        )
    except (MoxfieldAuthError, MoxfieldWriteError, archive.ArchiveError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"Decks on account: {report['total']}")
    print(f"Folder: {report['folder']}  Prefix: {report['prefix']!r}  "
          f"Visibility: {report['visibility'] or 'unchanged'}")
    if args.dry_run:
        print("(dry run -- nothing was modified)\n")
    print("Nothing is ever deleted by this script.\n")

    for result in report["results"]:
        action = result["action"]
        name = result["name"]
        if action == "archived":
            print(f"  + {name!r} -> {result['new_name']!r}")
            if result.get("folder_error"):
                print(f"      warning: renamed but not filed ({result['folder_error']})")
        elif action == "would_archive":
            print(f"  ~ {name!r} -> {result['new_name']!r}")
        elif action == "skipped_already":
            print(f"  = {name!r} already archived")
        elif action == "skipped_transcribed":
            print(f"  - {name!r} skipped (created by transcribe)")
        elif action == "failed":
            print(f"  ! {name!r} FAILED: {result.get('error')}")

    verb = "would archive" if args.dry_run else "archived"
    print(f"\n{verb}: {report['archived']}  already: {report['skipped_already']}  "
          f"transcribed-skipped: {report['skipped_transcribed']}  failed: {report['failed']}")
    if not args.dry_run and report["archived"]:
        print("Undo one with: python scripts/archive_moxfield_decks.py --restore <DECK_ID>")
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
