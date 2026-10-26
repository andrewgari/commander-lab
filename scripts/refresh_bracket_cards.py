#!/usr/bin/env python
"""Refresh the checked-in bracket card lists (analytics/data/bracket_cards.json):
mass land denial, extra turns and tutors, from Scryfall Tagger otags.

These are the same queries Commander Spellbook uses for its `massLandDenial`,
`extraTurn` and `tutor` card flags (backend/spellbook/tasks/scryfall.py):

  mass_land_denial  otag:mass-land-denial
  extra_turns       otag:extra-turn
  tutors            otag:tutor -otag:tutor-land -otag:tutor-seek mv<=3

Curated overrides live in analytics/bracket_check.py (*_INCLUDE / *_EXCLUDE),
not in this file, so a refresh never drops them.

Usage: python scripts/refresh_bracket_cards.py [--check]
  --check  exit 1 if the snapshot differs from Scryfall, without writing.
"""
import asyncio
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import bracket_check as bc  # noqa: E402
from src.server.infra.providers.scryfall import ScryfallClient  # noqa: E402


async def fetch(client: ScryfallClient, query: str):
    names = {c.name async for c in client.search_all_cards(query, unique="cards", order="name")}
    if not names:
        raise SystemExit(f"Scryfall returned nothing for {query!r}; refusing to write an empty list")
    return sorted(names)


async def main(check: bool) -> int:
    lists = {}
    async with ScryfallClient() as client:
        for key, query in bc.SCRYFALL_QUERIES.items():
            lists[key] = await fetch(client, query)
    try:
        with open(bc.DATA_PATH, encoding="utf-8") as f:
            old = json.load(f)
    except FileNotFoundError:
        old = {}
    changed = False
    for key, names in lists.items():
        prev = old.get(key, [])
        added, removed = sorted(set(names) - set(prev)), sorted(set(prev) - set(names))
        changed = changed or bool(added or removed)
        print(f"{key}: Scryfall {len(names)}; snapshot {len(prev)}; +{added} -{removed}")
    if check:
        return 1 if changed else 0
    payload = {
        "as_of": date.today().isoformat(),
        "queries": dict(bc.SCRYFALL_QUERIES),
        "source": "https://api.scryfall.com/cards/search (Scryfall Tagger otags; same queries as "
                  "Commander Spellbook's card flags)",
        "note": "Refresh with: python scripts/refresh_bracket_cards.py. Curated overrides are in "
                "analytics/bracket_check.py.",
        "counts": {k: len(v) for k, v in lists.items()},
        **lists,
    }
    with open(bc.DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {bc.DATA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--check" in sys.argv[1:])))
