#!/usr/bin/env python
"""Refresh the checked-in Game Changers snapshot (analytics/data/game_changers.json)
from Scryfall's `is:gamechanger` search. The snapshot is only an offline/test
fallback; the app itself reads the live list (see analytics/brackets.py).

Usage: python scripts/refresh_game_changers.py [--check]
  --check  exit 1 if the snapshot differs from Scryfall, without writing.
"""
import asyncio
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import brackets as br  # noqa: E402
from src.integrations.scryfall import ScryfallClient  # noqa: E402

OFFICIAL_UPDATE = br.SOURCES["update_2026_02"]


async def main(check: bool) -> int:
    async with ScryfallClient() as client:
        names = await br.fetch_game_changers(client)
    try:
        with open(br.FALLBACK_PATH, encoding="utf-8") as f:
            old = json.load(f).get("cards", [])
    except FileNotFoundError:
        old = []
    added, removed = sorted(set(names) - set(old)), sorted(set(old) - set(names))
    print(f"Scryfall: {len(names)} Game Changers; snapshot: {len(old)}; +{added} -{removed}")
    if check:
        return 1 if (added or removed) else 0
    payload = {
        "query": br.SCRYFALL_QUERY,
        "source": "https://api.scryfall.com/cards/search?q=is%3Agamechanger&unique=cards&order=name",
        "as_of": date.today().isoformat(),
        "official_update": OFFICIAL_UPDATE,
        "note": "Offline/test fallback only; the app reads Scryfall (cached in Redis). "
                "Refresh with: python scripts/refresh_game_changers.py",
        "count": len(names),
        "cards": names,
    }
    with open(br.FALLBACK_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {br.FALLBACK_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main("--check" in sys.argv[1:])))
