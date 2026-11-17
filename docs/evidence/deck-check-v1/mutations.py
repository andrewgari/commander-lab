"""Read-only: apply known-bad mutations to the control deck, run the repo's pure checks.
usage: python mutations.py <archidekt_json> <repo_root> <out_json>
Network: Scryfall GET /cards/named (card facts), Spellbook POST find-my-combos (read-only query).
"""
import copy, json, sys, time
import requests
src, repo, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo)
from src.server.domain.draft import deck_validator as dv
from src.server.domain.analytics import bracket_check as bc, brackets as br
from src.server.domain.analytics import composition as comp
from src.server.domain.analytics.interactions import combos as cm

COL = {"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}
raw = json.load(open(src))
recs, commanders, cards, planes = {}, [], [], []
for e in raw["cards"]:
    oc = e["card"]["oracleCard"]
    n = oc["name"]
    tl = " ".join(oc.get("superTypes") or []) + " " + " ".join(oc.get("types") or [])
    if oc.get("subTypes"):
        tl += " \u2014 " + " ".join(oc["subTypes"])
    recs[n] = {"name": n, "color_identity": "".join(COL.get(c, c) for c in oc.get("colorIdentity") or []),
               "type_line": tl.strip(), "oracle_text": oc.get("text") or "", "legalities": oc.get("legalities") or {},
               "keywords": oc.get("keywords") or [], "power": oc.get("power") or None,
               "toughness": oc.get("toughness") or None, "layout": oc.get("layout")}
    cats = e.get("categories") or []
    if cats and cats[0] in ("Sideboard", "Maybeboard", "Considering"):
        planes.append(n)
        continue
    if "Commander" in cats:
        commanders.append(n)
    cards.append({"name": n, "quantity": e["quantity"]})
BASE = {"name": raw["name"], "commanders": commanders, "cards": cards}


def scry(name):
    time.sleep(0.12)
    j = requests.get("https://api.scryfall.com/cards/named", params={"exact": name},
                     headers={"User-Agent": "commander-lab-oracle-probe", "Accept": "application/json"}, timeout=30).json()
    if j.get("object") == "error":
        return None
    return {"name": j["name"], "color_identity": "".join(j.get("color_identity") or []), "type_line": j.get("type_line", ""),
            "oracle_text": j.get("oracle_text") or " // ".join(f.get("oracle_text", "") for f in j.get("card_faces") or []),
            "legalities": j.get("legalities") or {}, "keywords": j.get("keywords") or [], "power": j.get("power"),
            "toughness": j.get("toughness"), "layout": j.get("layout"), "game_changer": j.get("game_changer")}


EXTRA = ["Mana Crypt", "Birds of Paradise", "Rhystic Study", "Smothering Tithe", "Cyclonic Rift",
         "Kiki-Jiki, Mirror Breaker", "Felidar Guardian", "Time Warp", "Temporal Manipulation", "Persistent Petitioners"]
facts_checked = {}
for n in EXTRA:
    r = scry(n)
    if r:
        recs[r["name"]] = r
        facts_checked[r["name"]] = {"ci": r["color_identity"], "commander": r["legalities"].get("commander"),
                                    "game_changer": r.get("game_changer")}


def swap_basic(d, add, n=1, basic=None):
    """Replace n basic copies with `add` cards (keeps 100)."""
    for _ in range(n):
        for c in d["cards"]:
            if c["name"] in ("Island", "Plains", "Mountain") and (basic is None or c["name"] == basic) and c["quantity"] > 0:
                c["quantity"] -= 1
                break
    d["cards"] = [c for c in d["cards"] if c["quantity"] > 0]
    for a in add:
        hit = next((c for c in d["cards"] if c["name"] == a), None)
        if hit:
            hit["quantity"] += 1
        else:
            d["cards"].append({"name": a, "quantity": 1})
    return d


def mk(fn):
    d = copy.deepcopy(BASE)
    return fn(d) or d


def drop_one_basic(d):
    swap_basic(d, [], 1)


def dupe_sol(d):
    swap_basic(d, ["Sol Ring"], 1)


def bad_pair(d):
    d["commanders"] = ["The Tenth Doctor", "Jhoira of the Ghitu"]


def fake(d):
    swap_basic(d, ["Totally Fake Card"], 1)


def land_starved(d):
    for _ in range(8):
        swap_basic(d, ["Persistent Petitioners"], 1)


SCEN = [
    ("K1_banned", "Island -> Mana Crypt", lambda: mk(lambda d: swap_basic(d, ["Mana Crypt"]))),
    ("K2_off_identity", "Island -> Birds of Paradise", lambda: mk(lambda d: swap_basic(d, ["Birds of Paradise"]))),
    ("K3_99_cards", "remove one basic", lambda: mk(drop_one_basic)),
    ("K4_duplicate", "basic -> 2nd Sol Ring", lambda: mk(dupe_sol)),
    ("K5_plane_in_main", "basic -> Bad Wolf Bay (plane) in mainboard", lambda: mk(lambda d: swap_basic(d, ["Bad Wolf Bay"]))),
    ("K6_bad_partner", "Rose Tyler -> Jhoira of the Ghitu as 2nd commander", lambda: mk(bad_pair)),
    ("K7_unknown_card", "basic -> 'Totally Fake Card'", lambda: mk(fake)),
    ("K8_game_changers", "3 basics -> Rhystic Study, Smothering Tithe, Cyclonic Rift",
     lambda: mk(lambda d: swap_basic(d, ["Rhystic Study", "Smothering Tithe", "Cyclonic Rift"], 3))),
    ("K9_two_card_combo", "2 basics -> Kiki-Jiki + Felidar Guardian",
     lambda: mk(lambda d: swap_basic(d, ["Kiki-Jiki, Mirror Breaker", "Felidar Guardian"], 2))),
    ("K10_extra_turns", "2 basics -> Time Warp, Temporal Manipulation",
     lambda: mk(lambda d: swap_basic(d, ["Time Warp", "Temporal Manipulation"], 2))),
    ("K11_land_starved", "8 basics -> 8x Persistent Petitioners", lambda: mk(land_starved)),
    ("K12_spellbook_down", "control deck, combo data unavailable", lambda: mk(lambda d: None)),
]


class MemR(dict):
    def get(self, k): return dict.get(self, k)
    def set(self, k, v): self[k] = v


gc = br.load_fallback().cards
results = {"facts_checked_scryfall": facts_checked}
for key, desc, build in SCEN:
    deck = build()
    catalog = dv.CardCatalog.from_records(r for r in recs.values())
    val = dv.validate_deck(dv.DraftDeck.from_builder_draft(deck), catalog)
    if key == "K12_spellbook_down":
        fmc = None
    else:
        try:
            fmc = cm.SpellbookClient(MemR()).find_my_combos(deck["commanders"], cm._deck_names(deck))
        except cm.SpellbookError as exc:
            fmc = None
    oracle = {n: {"oracle_text": r["oracle_text"]} for n, r in recs.items()}
    f = bc.check_deck_brackets(deck, game_changers=gc, combos=fmc, oracle=oracle)
    texts = {n: {"type_line": r["type_line"], "oracle_text": r["oracle_text"]} for n, r in recs.items()}
    co = comp.compute_composition(deck, texts)
    results[key] = {
        "mutation": desc,
        "total_with_commanders": sum(c["quantity"] for c in deck["cards"]),
        "basics_left": {c["name"]: c["quantity"] for c in deck["cards"] if c["name"] in ("Island", "Plains", "Mountain")},
        "legality": [{"code": e.to_dict()["code"], "cards": e.to_dict()["cards"], "message": e.to_dict()["message"]}
                     for e in val.errors],
        "minimum_bracket": f.minimum_bracket,
        "floors": [fl.model_dump() for fl in f.floors],
        "two_card_combos": [{"cards": c.cards, "tag": c.bracket_tag, "early": c.early, "produces": c.produces}
                            for c in f.two_card_combos.combos],
        "combos_checked": f.two_card_combos.checked,
        "in_deck_combo_count": None if fmc is None else len(fmc.get("included") or []),
        "notes": f.notes,
        "composition": {r["key"]: [r["actual"], r["target"], r["status"]] for r in co["rows"]},
        "unknown_cards": co["unknown_cards"],
    }
    time.sleep(1.0)
json.dump(results, open(out_path, "w"), indent=1, default=str)
print("written", out_path)
