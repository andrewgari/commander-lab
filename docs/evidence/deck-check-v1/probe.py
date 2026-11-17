"""Read-only Deck Check probe: Archidekt JSON -> repo's pure checks. Writes nothing to any service.
usage: python probe.py <archidekt_json> <repo_root> <out_json>
"""
import json, sys
src, repo, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
sys.path.insert(0, repo)
from src.server.domain.draft import deck_validator as dv
from src.server.domain.analytics import bracket_check as bc, bracket_eval as be, brackets as br
from src.server.domain.analytics import composition as comp
from src.server.domain.analytics.interactions import combos as cm

raw = json.load(open(src))
EXCLUDE = {"Maybeboard", "Sideboard", "Considering"}
excluded_cats = {c["name"] for c in raw["categories"] if not c.get("includedInDeck")} | EXCLUDE
commanders, cards, slots, recs = [], [], {}, {}
for e in raw["cards"]:
    oc = e["card"]["oracleCard"]
    name = oc["name"]
    cats = e.get("categories") or []
    tl = " ".join(oc.get("superTypes") or []) + " " + " ".join(oc.get("types") or [])
    if oc.get("subTypes"):
        tl += " \u2014 " + " ".join(oc["subTypes"])
    recs[name] = {"name": name, "color_identity": "".join(oc.get("colorIdentity") or []) or "",
                  "type_line": tl.strip(), "oracle_text": oc.get("text") or "",
                  "legalities": oc.get("legalities") or {}, "keywords": oc.get("keywords") or [],
                  "power": oc.get("power") or None, "toughness": oc.get("toughness") or None,
                  "layout": oc.get("layout"), "game_changer": oc.get("gameChanger"),
                  "salt": oc.get("salt")}
    # Archidekt colorIdentity is a list of colour names
    ci = oc.get("colorIdentity") or []
    recs[name]["color_identity"] = "".join({"White": "W", "Blue": "U", "Black": "B", "Red": "R", "Green": "G"}.get(c, c) for c in ci)
    if cats and cats[0] in excluded_cats:
        slots.setdefault(cats[0], []).append({"name": name, "quantity": e["quantity"]})
        continue
    if "Commander" in cats:
        commanders.append(name)
    cards.append({"name": name, "quantity": e["quantity"]})

deck = {"name": raw["name"], "registry_id": f"archidekt:{raw['id']}", "commanders": commanders, "cards": cards}
catalog = dv.CardCatalog.from_records(recs.values())
dd = dv.DraftDeck.from_builder_draft(deck)
val = dv.validate_deck(dd, catalog)


class MemR(dict):
    def get(self, k): return dict.get(self, k)
    def set(self, k, v): self[k] = v


combo_err = None
try:
    fmc = cm.SpellbookClient(MemR()).find_my_combos(commanders, cm._deck_names(deck))
except cm.SpellbookError as exc:
    fmc, combo_err = None, str(exc)
gc = br.load_fallback()
oracle = {n: {"oracle_text": r["oracle_text"]} for n, r in recs.items()}
findings = bc.check_deck_brackets(deck, game_changers=gc.cards, combos=fmc, oracle=oracle)
ev = be.evaluate_bracket(deck, None, {"findings": findings})
texts = {n: {"type_line": r["type_line"], "oracle_text": r["oracle_text"]} for n, r in recs.items()}
composition = comp.compute_composition(deck, texts)

out = {
    "deck": {"name": deck["name"], "registry_id": deck["registry_id"], "commanders": commanders,
             "mainboard_qty": sum(c["quantity"] for c in cards),
             "unique": len(cards), "excluded_slots": {k: [c["name"] for c in v] for k, v in slots.items()},
             "archidekt_updated_at": raw.get("updatedAt"), "archidekt_edhBracket": raw.get("edhBracket")},
    "legality": {"valid": val.valid, "errors": [e.to_dict() for e in val.errors]},
    "bracket_findings": findings.model_dump(),
    "bracket_eval": ev.model_dump(exclude={"findings"}),
    "combos": None if fmc is None else {
        "identity": fmc.get("identity"), "fetched_at": fmc.get("fetched_at"),
        "in_deck": [{k: v.get(k) for k in ("id", "card_names", "produces", "bracket_tag", "requires", "popularity")}
                    for v in fmc.get("included") or []],
        "counts": {k: len(fmc.get(k) or []) for k in ("included", "almost_included", "almost_included_off_identity")},
        "one_away_top": [{k: v.get(k) for k in ("id", "card_names", "produces", "bracket_tag")}
                         for v in sorted(fmc.get("almost_included") or [], key=lambda c: -c.get("popularity", 0))[:8]]},
    "combo_error": combo_err,
    "composition": {k: composition[k] for k in ("rows", "total_cards", "unknown_cards")},
    "game_changers_snapshot": {"count": len(gc.cards), "as_of": getattr(gc, "fetched_at", None) or getattr(gc, "as_of", None)},
    "high_salt": sorted([(n, r["salt"]) for n, r in recs.items() if (r.get("salt") or 0) >= 1.5], key=lambda x: -x[1]),
}
json.dump(out, open(out_path, "w"), indent=1, default=str)
print("written", out_path)
