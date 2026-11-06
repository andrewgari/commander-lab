"""Archidekt one-way import job (ARCHITECTURE.md §2 workers/importer.py).

Imports ONE Archidekt deck (id or URL) into Postgres: cards/printings,
decks + deck_cards, and a first profile_snapshots row (when the deck is new
or its content changed). Idempotent.

Usage:
    DATABASE_URL=postgresql://... python -m src.server.workers.importer 5054882
    DATABASE_URL=... python -m src.server.workers.importer https://archidekt.com/decks/5054882
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from src.server.domain.decks.deck_import import DeckImportError
from src.server.infra.services.import_service import import_archidekt_deck

log = logging.getLogger("commander_lab.importer")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("identifier", help="Archidekt deck id or archidekt.com/decks/{id} URL")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = import_archidekt_deck(args.identifier)
    except DeckImportError as e:
        log.error("import rejected: %s", e)
        return 2
    except Exception as e:  # upstream HTTP / DB errors: fail loudly, no partial state
        log.error("import failed: %s", e)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
