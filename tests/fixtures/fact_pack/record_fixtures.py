"""Re-record the golden-deck fixtures from the live sources (NOT run in CI).

    python tests/fixtures/fact_pack/record_fixtures.py

Writes scryfall_collection.json, spellbook_find_my_combos.json, edhrec_commander.json
and scryfall_search_*.json next to this file. Respects Scryfall's 2 req/s limit
and sends a descriptive User-Agent (docs/reviewer/EXTERNAL_APIS.md).
"""
import json
import time
from pathlib import Path

import requests

HERE = Path(__file__).parent
UA = {"User-Agent": "commander-lab/1.0 (fixture-recorder)", "Accept": "application/json"}
COMMANDER = "Atraxa, Praetors' Voice"
GOLDEN = json.loads((HERE / "golden_deck.json").read_text())
SEARCHES = {
    "ramp": "otag:ramp", "draw": "otag:draw", "removal": "otag:removal",
    "wipe": "otag:sweeper", "protection": "otag:protects-creature", "recursion": "otag:recursion",
}


def main():
    names = [COMMANDER] + [c["name"] for c in GOLDEN["cards"]]
    coll = {"data": [], "not_found": []}
    for i in range(0, len(names), 75):
        r = requests.post("https://api.scryfall.com/cards/collection", headers=UA, timeout=30,
                          json={"identifiers": [{"name": n} for n in names[i:i + 75]]})
        r.raise_for_status()
        j = r.json()
        coll["data"] += j["data"]
        coll["not_found"] += j.get("not_found", [])
        time.sleep(0.6)
    (HERE / "scryfall_collection.json").write_text(json.dumps(coll, indent=1, sort_keys=True))
    for role, otag in SEARCHES.items():
        q = f"f:commander id<=wubg usd<=5 {otag}"
        r = requests.get("https://api.scryfall.com/cards/search", headers=UA, timeout=30,
                         params={"q": q, "order": "edhrec"})
        r.raise_for_status()
        j = r.json()
        j["data"] = j["data"][:12]
        (HERE / f"scryfall_search_{role}.json").write_text(json.dumps({"query": q, **j}, indent=1, sort_keys=True))
        time.sleep(0.6)
    body = {"commanders": [{"card": COMMANDER}],
            "main": [{"card": c["name"]} for c in GOLDEN["cards"]]}
    r = requests.post("https://backend.commanderspellbook.com/find-my-combos", json=body,
                      headers={"User-Agent": "commander-lab/1.0 (fixture-recorder)"}, timeout=60)
    r.raise_for_status()
    (HERE / "spellbook_find_my_combos.json").write_text(json.dumps(r.json(), indent=1, sort_keys=True))
    r = requests.get("https://json.edhrec.com/pages/commanders/atraxa-praetors-voice.json", headers=UA, timeout=30)
    r.raise_for_status()
    page = r.json()
    j = page["container"]["json_dict"]
    for cl in j["cardlists"]:
        cl["cardviews"] = cl["cardviews"][:25]
    slim = {"container": {"json_dict": {"card": j["card"], "cardlists": j["cardlists"]}}}
    (HERE / "edhrec_commander.json").write_text(json.dumps(slim, indent=1, sort_keys=True))
    print("recorded", len(coll["data"]), "cards; not_found", coll["not_found"])


if __name__ == "__main__":
    main()
