"""Regenerate recorded gate fixtures: ``python -m tests.review_evals.build_fixtures``.

Writes ``fixtures/good/case.json`` and one ``fixtures/bad/<gate>.json`` per
gate (the good case with exactly one defect injected). Everything is recorded
data: no network, no LLM.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from tests.review_evals.gates import fact_pack_hash, fresh_fact_pack

ROOT = Path(__file__).parent / "fixtures"


def _card(ci, legal="legal"):
    return {"name": "", "cmc": 2, "type_line": "Artifact", "colors": [], "color_identity": ci,
            "legalities": {"commander": legal}, "prices": {"usd": "1.00"}}


def base_case() -> dict:
    sc = {
        "Meren of Clan Nel Toth": _card(["B", "G"]),
        "Sol Ring": _card([]), "Command Tower": _card(["B", "G"]),
        "Eternal Witness": _card(["G"]), "Sakura-Tribe Elder": _card(["G"]),
        "Skullclamp": _card([]), "Viscera Seer": _card(["B"]),
        "Lightning Bolt": _card(["R"]), "Mana Crypt": _card([], "banned"),
        "Reanimate": _card(["B"]), "Demonic Tutor": _card(["B"]),
    }
    for n, c in sc.items():
        c["name"] = n
    deck = {"commanders": ["Meren of Clan Nel Toth"],
            "cards": ["Meren of Clan Nel Toth", "Sol Ring", "Command Tower", "Eternal Witness", "Sakura-Tribe Elder"],
            "pet_cards": ["Eternal Witness"], "budget_hard_cap": 50.0}
    ref = lambda n: {"name": n}
    doc = {
        "schema_version": "review_document.v1",
        "meta": {"deck_id": "fixture-meren", "deck_name": "Meren fixture", "commanders": [ref("Meren of Clan Nel Toth")],
                 "fact_pack_hash": ""},
        "strengths": [{"id": "s-recursion", "axis": "Synergy", "claim": "Meren recurs Viscera Seer-style sac fodder every end step.",
                       "evidence": {"cards": [ref("Meren of Clan Nel Toth")], "fact_keys": []}, "severity": "high", "confidence": 0.9}],
        "problems": [{"id": "p-engine", "axis": "Consistency", "claim": "No sacrifice outlet to turn Meren's experience into value.",
                      "evidence": {"cards": [ref("Sakura-Tribe Elder")], "fact_keys": ["roles.sac_outlet.count"]}, "severity": "med", "confidence": 0.7}],
        "swaps": [{"id": "sw-1", "cut": ref("Sakura-Tribe Elder"), "add": ref("Viscera Seer"), "resolves_finding_id": "p-engine",
                   "tradeoff": "Loses ramp, gains an outlet.", "bracket_effect": "none", "budget_effect": "~$1", "alternative": None,
                   "owned": False, "price": 1.0},
                  {"id": "sw-2", "cut": ref("Command Tower"), "add": ref("Skullclamp"), "resolves_finding_id": "p-engine",
                   "tradeoff": "Loses a land, gains draw.", "bracket_effect": "none", "budget_effect": "~$1", "alternative": None,
                   "owned": False, "price": 1.0}],
        "cuts_to_consider": [{"card": ref("Sakura-Tribe Elder"), "reason": "Low impact here.", "evidence": ["roles.ramp.count"]}],
        "bracket": {"estimated": 2, "target": 3, "reasons": ["No tutors"]},
        "charts": {},
    }
    case = {"deck": deck, "scryfall": sc, "review": doc}
    pack = fresh_fact_pack(deck, sc)
    doc["meta"]["fact_pack_hash"] = fact_pack_hash(pack)
    doc["charts"] = json.loads(json.dumps(pack["charts"]))
    return case


def _mut(gate, fn):
    c = copy.deepcopy(base_case())
    fn(c)
    c["expect_gate"] = gate
    return c


def bad_cases() -> dict[str, dict]:
    ref = lambda n: {"name": n}
    sw = lambda c: c["review"]["swaps"]
    return {
        "hallucinated_card_names": _mut("hallucinated_card_names", lambda c: sw(c)[0].update(add=ref("Meren's Totally Real Spellbook"))),
        "add_outside_color_identity": _mut("add_outside_color_identity", lambda c: sw(c)[0].update(add=ref("Lightning Bolt"))),
        "add_not_commander_legal": _mut("add_not_commander_legal", lambda c: sw(c)[0].update(add=ref("Mana Crypt"))),
        # Two swaps adding the same new card -> duplicate after applying all swaps.
        "singleton_violations": _mut("singleton_violations", lambda c: sw(c)[1].update(add=ref("Viscera Seer"))),
        "add_already_in_deck": _mut("add_already_in_deck", lambda c: sw(c)[0].update(add=ref("Sol Ring"))),
        "cut_is_pet_card": _mut("cut_is_pet_card", lambda c: sw(c)[0].update(cut=ref("Eternal Witness"))),
        "add_above_budget_cap": _mut("add_above_budget_cap", lambda c: sw(c)[0].update(price=500.0)),
        "swaps_exceed_target_bracket": _mut("swaps_exceed_target_bracket", lambda c: (c["review"]["bracket"].update(estimated=3, target=3), sw(c)[0].update(bracket_effect="raises"))),
        "unresolved_finding_id": _mut("unresolved_finding_id", lambda c: sw(c)[0].update(resolves_finding_id="p-does-not-exist")),
        "empty_evidence": _mut("empty_evidence", lambda c: c["review"]["problems"][0].update(evidence={"cards": [], "fact_keys": []})),
        "charts_mismatch_fact_pack": _mut("charts_mismatch_fact_pack", lambda c: (c["review"]["meta"].update(fact_pack_hash="0" * 64), c["review"]["charts"].update(curve={"fabricated": True}))),
    }


def main() -> None:
    (ROOT / "good").mkdir(parents=True, exist_ok=True)
    (ROOT / "bad").mkdir(parents=True, exist_ok=True)
    (ROOT / "good" / "case.json").write_text(json.dumps(base_case(), indent=2, sort_keys=True) + "\n")
    for gate, case in bad_cases().items():
        (ROOT / "bad" / f"{gate}.json").write_text(json.dumps(case, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
