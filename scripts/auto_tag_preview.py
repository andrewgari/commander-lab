#!/usr/bin/env python3
"""Dev preview for the Auto Tag UI (docs/design/auto-tag/UX_SPEC.md).

Boots the real app against a throwaway Redis, seeds one Commander deck, and
swaps the 501 `POST /api/decks/{id}/auto-tag` stub for a keyword heuristic that
writes through the REAL tag storage (tags.py). Everything else (GET/POST/DELETE
tags and card-tags) is the production code path.

THE HEURISTIC IS NOT THE ENGINE. It exists so the UI can be exercised until the
tagging engine lands; it lives here, not in app.py.

    docker run -d --rm -p 6431:6379 --name at-redis redis:alpine
    REDIS_URL=redis://localhost:6431/0 python scripts/auto_tag_preview.py [--port 8931] [--engine 501]

--engine 501  keep the real stub (shows the "not available yet" state)
--engine fail make the engine return 500 (shows the error state)
--no-run      seed the deck but do not pre-run Auto Tag (empty state)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DECK_ID = 9501
DECK_NAME = "Trostani Tokens"
COMMANDER = "Trostani, Selesnya's Voice"
CARDS = """Sol Ring|Arcane Signet|Cultivate|Kodama's Reach|Rampant Growth|Farseek|Birds of Paradise|Llanowar Elves|Elvish Mystic|Smothering Tithe
Skullclamp|Mentor of the Meek|Guardian Project|Beast Whisperer|Soul of the Harvest|Garruk's Uprising|Harmonize|Return of the Wildspeaker
Swords to Plowshares|Path to Exile|Beast Within|Generous Gift|Council's Judgment|Oblivion Ring|Reclamation Sage
Wrath of God|Austere Command|Settle the Wreckage|Heroic Intervention|Teferi's Protection|Swiftfoot Boots|Lightning Greaves
Parallel Lives|Doubling Season|Anointed Procession|Secure the Wastes|Verdant Force|Avenger of Zendikar|Elspeth, Sun's Champion|Hornet Queen
Rampaging Baloths|Divine Visitation|Cathars' Crusade|Intangible Virtue|Mirror Entity|Craterhoof Behemoth|Increasing Devotion
Eternal Witness|Sun Titan|Loyal Apprentice|Rhys the Redeemed|Emmara, Soul of the Accord|Selesnya Guildmage|Soul Warden|Essence Warden
Command Tower|Temple Garden|Sunpetal Grove|Brushland|Canopy Vista|Fortified Village|Sacred Foundry|Windswept Heath|Plains|Forest""".replace("\n", "|").split("|")

TAG_RULES = [  # (tag, regex on oracle text, optional type regex) -- preview heuristic only
    ("Ramp", r"search your library for (a|up to \w+) (basic )?(plains|forest|land)|add \{|adds? \w+ mana|untap target land", r"^(?!.*Land)"),
    ("Draw", r"draw (a|\w+) cards?|whenever .* you draw|you may draw", None),
    ("Removal", r"(destroy|exile) (target|another target|up to one target)|return target .* to its owner", r"^(?!.*Land)"),
    ("Board Wipe", r"destroy all|exile all|each creature|all creatures", r"^(?!.*Land)"),
    ("Protection", r"hexproof|indestructible|protection from|phase out|shroud", None),
    ("Recursion", r"return .* from your graveyard to (the battlefield|your hand)", None),
    ("Tokens", r"create (a|an|one|two|three|x|that many|\d+|.{0,20}) .{0,40}token|populate|tokens? (you control|would)", None),
    ("Lifegain", r"gain \w+ life|whenever you gain life", None),
    ("Finisher", r"creatures you control get \+|gain trample|extra turn|you win the game", None),
    ("Fixing", r"add \{[wubrg]\}|any color|fetch|search your library for a .* land card", r"Land"),
]


def _scry(names: list[str]) -> dict[str, dict]:
    cache = Path(os.environ.get("TMPDIR", "/tmp")) / "auto_tag_preview_scryfall.json"
    if cache.exists():
        return json.loads(cache.read_text())
    out: dict[str, dict] = {}
    for i in range(0, len(names), 70):
        body = json.dumps({"identifiers": [{"name": n} for n in names[i:i + 70]]}).encode()
        req = urllib.request.Request("https://api.scryfall.com/cards/collection", data=body,
                                     headers={"Content-Type": "application/json", "User-Agent": "commander-lab-preview/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            for c in json.loads(r.read())["data"]:
                out[c["name"]] = c
    cache.write_text(json.dumps(out))
    return out


def seed(r) -> None:
    import src.server.domain.inventory.instances as instances  # noqa: F401  (record shape)
    names = [COMMANDER] + CARDS
    try:
        data = _scry(names)
    except Exception as e:  # offline: tiles show "No art", chips still work
        print("scryfall unavailable:", e, file=sys.stderr)
        data = {}
    r.flushdb()
    deck = {"id": DECK_ID, "name": DECK_NAME, "source": "archidekt", "source_id": str(DECK_ID),
            "registry_id": f"archidekt:{DECK_ID}", "status": "digital", "cards": [{"name": n, "quantity": 1} for n in names],
            "folder": "Digital", "color": "GW", "commanders": [COMMANDER], "tags": []}
    r.set("decks", json.dumps([deck]))
    for i, n in enumerate(names):
        c = data.get(n) or {}
        face = (c.get("card_faces") or [{}])[0]
        r.set(f"card_meta:{n}", json.dumps({
            "type": c.get("type_line", "Unknown"), "cmc": c.get("cmc", 0), "color": "".join(c.get("color_identity", [])) or "C",
            "oracle_text": c.get("oracle_text") or face.get("oracle_text", ""), "sub_types": [], "super_types": [], "keywords": c.get("keywords", []),
        }))
        iid = f"at-{i}"
        r.set(f"instance:{iid}", json.dumps({
            "id": iid, "card_name": n, "ownership_status": "in_deck", "deck_id": f"archidekt:{DECK_ID}", "deck_name": DECK_NAME,
            "history": [], "set": c.get("set", ""), "set_name": c.get("set_name", ""), "collector_number": c.get("collector_number", ""),
            "finish": "nonfoil", "condition": "NM", "language": "EN", "considered_for_deck": None, "price_paid": float((c.get("prices") or {}).get("usd") or 0),
            "source": "preview", "date_ordered": "", "date_acquired": "", "archidekt_uid": c.get("id", ""), "notes": "", "updated_at": "",
        }))


def heuristic_tags(meta: dict) -> list[str]:
    text, typ = meta.get("oracle_text", ""), meta.get("type", "")
    tags = []
    for tag, rx, type_rx in TAG_RULES:
        if type_rx and not re.search(type_rx, typ):
            continue
        if re.search(rx, text, re.I):
            tags.append(tag)
    if "Land" in typ and "Fixing" not in tags and "any color" in text.lower():
        tags.append("Fixing")
    return tags[:4]


def install_engine(app_mod, mode: str) -> None:
    from fastapi.responses import JSONResponse
    import src.server.domain.tags.deck_tags as tag_store

    if mode == "501":
        return
    app_mod.app.router.routes[:] = [
        rt for rt in app_mod.app.router.routes
        if not (getattr(rt, "path", "") == "/api/decks/{deck_id}/auto-tag" and "POST" in getattr(rt, "methods", set()))
    ]

    @app_mod.app.post("/api/decks/{deck_id}/auto-tag")
    async def preview_engine(deck_id: str):
        import time
        time.sleep(0.9)  # long enough to see the running state
        if mode == "fail":
            return JSONResponse({"success": False, "error": "Tagging engine timed out after 30s"}, status_code=500)
        r = app_mod.r
        deck = app_mod.registry.find_deck(r, deck_id)
        if not deck:
            return JSONResponse({"error": "Deck not found"}, status_code=404)
        # Replace auto entries, keep user entries (the contract in AUTO_TAG_DESIGN.md).
        cur = tag_store.get_deck_card_tags(r, deck_id)
        for name, entries in cur.items():
            user_tags = {e["tag"] for e in entries if e["source"] == "user"}
            for e in entries:
                if e["source"] == "auto" and e["tag"] not in user_tags:
                    tag_store.remove_card_tag_from_deck(r, deck_id, name, e["tag"])
        counts: dict[str, int] = {}
        for c in deck["cards"]:
            raw = r.get(f"card_meta:{c['name']}")
            for t in heuristic_tags(json.loads(raw) if raw else {}):
                if not any(x["tag"] == t and x["source"] == "user" for x in tag_store.get_card_tags_for_deck(r, deck_id, c["name"])):
                    tag_store.add_card_tag_to_deck(r, deck_id, c["name"], t, source="auto")
                counts[t] = counts.get(t, 0) + 1
        for t, n in counts.items():
            if t in ("Tokens", "Lifegain", "Ramp") and n >= 5:
                tag_store.add_deck_tag(r, deck_id, t, source="auto")
        return {"success": True, "summary": counts}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8931)
    ap.add_argument("--engine", choices=["preview", "501", "fail"], default="preview")
    ap.add_argument("--no-run", action="store_true")
    a = ap.parse_args()
    os.environ.setdefault("REDIS_URL", "redis://localhost:6431/0")
    import uvicorn
    import src.server.app.main as app_mod
    from fastapi.testclient import TestClient

    seed(app_mod.r)
    install_engine(app_mod, a.engine)
    if a.engine == "preview" and not a.no_run:
        TestClient(app_mod.app).post(f"/api/decks/archidekt:{DECK_ID}/auto-tag")
    print(f"preview: http://localhost:{a.port}/deck/{DECK_NAME.replace(' ', '%20')}  (engine={a.engine})", flush=True)
    uvicorn.run(app_mod.app, port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
