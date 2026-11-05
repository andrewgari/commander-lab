"""TH-2 Theory: doc schema, verification, storage, generation and API (docs/THEORY.md).

Oracle is always mocked (httpx.MockTransport behind the real OracleClient, or a fake
client). Scryfall facts and prices are injected, so nothing touches the network.
Fixture deck only: no real deck's Journal/decklist is ever written.
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import theory  # noqa: E402
from src.server.domain.analytics.oracle_client import OracleClient, OracleUnavailable  # noqa: E402
from tests.test_interactions import FakeRedis as _BaseFakeRedis  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


class FakeRedis(_BaseFakeRedis):
    def __init__(self):
        super().__init__()
        self.zsets = {}
        self.ttl = {}

    def set(self, key, value, nx=False, ex=None):
        if ex:
            self.ttl[key] = ex
        return super().set(key, value, nx=nx, ex=ex)

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    def zrem(self, key, *members):
        z = self.zsets.get(key, {})
        return sum(1 for m in members if z.pop(m, None) is not None)

    def zrevrange(self, key, start, end):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: -kv[1])
        names = [k for k, _ in items]
        return names[start:] if end == -1 else names[start:end + 1]


# --- fixture deck (WBR Kaalia, 100 cards: Kaalia + 2 pets + 58 spells + 39 lands) -------------

DECK_ID = "test:theory-fixture"
SPELLS = [f"Spell {i:02d}" for i in range(60)]
DECK = {"id": "theory-fixture", "name": "Theory Fixture", "color": "WBR", "commanders": ["Kaalia of the Vast"],
        "registry_id": DECK_ID, "source": "test", "source_id": "theory-fixture",
        "cards": [{"name": "Kaalia of the Vast", "quantity": 1}, {"name": "Lyra Dawnbringer", "quantity": 1},
                  {"name": "Dragon Tempest", "quantity": 1}]
        + [{"name": n, "quantity": 1} for n in SPELLS[:58]]
        + [{"name": "Plains", "quantity": 13}, {"name": "Swamp", "quantity": 13}, {"name": "Mountain", "quantity": 13}]}

# Scryfall facts for every name the tests use.
FACTS = {"Kaalia of the Vast": "WBR", "Lyra Dawnbringer": "W", "Dragon Tempest": "R",
         "Plains": "", "Swamp": "", "Mountain": "", "Island": "", "Forest": "",
         "Sol Ring": "", "Free Agent Angel": "W", "Sleeved Demon": "B", "Pricey Dragon": "R",
         "Soft Cap Dragon": "R", "Cheap Buy": "R", "Unpriced Thing": "W", "Blue Card": "U",
         "Banned Card": "B", "Kenrith, the Returned King": "WUBRG", "Sram, Senior Edificer": "W",
         "Isshin, Two Heavens as One": "WBR"}
FACTS.update({n: "W" for n in SPELLS})
ILLEGAL = {"Banned Card"}
CANT_COMMAND = {"Sol Ring", "Blue Card"}
PRICES = {"Sol Ring": 1.5, "Free Agent Angel": 3.0, "Sleeved Demon": 12.0, "Pricey Dragon": 55.0,
          "Soft Cap Dragon": 20.0, "Cheap Buy": 2.25, "Unpriced Thing": None, "Lyra Dawnbringer": 4.0,
          "Dragon Tempest": 6.0, "Kaalia of the Vast": 3.0, "Kenrith, the Returned King": 1.0,
          "Isshin, Two Heavens as One": 2.0, "Sram, Senior Edificer": 1.25}
PRICES.update({n: 0.5 for n in SPELLS})
for b in ("Plains", "Swamp", "Mountain", "Island", "Forest"):
    PRICES[b] = 0.1

OWNED = {
    "free agent angel": {"name": "Free Agent Angel", "copies": 1, "available": 1, "decks": []},
    "sol ring": {"name": "Sol Ring", "copies": 2, "available": 1, "decks": ["Other Deck"]},
    "sleeved demon": {"name": "Sleeved Demon", "copies": 1, "available": 0, "decks": ["Other Deck"]},
}
CAPS = {"no_budget": False, "hard_cap": 40.0, "soft_cap": 15.0}
PETS = ["Dragon Tempest", "Lyra Dawnbringer"]


def lookup(names):
    return {n: {"name": n, "color_identity": FACTS[n], "legal": n not in ILLEGAL, "commander_ok": n not in CANT_COMMAND}
            for n in names if n in FACTS}


def prices(names):
    return {n: PRICES.get(n) for n in names}


def card(name, source=None, price=None, qty=1, **extra):
    c = {"name": name, "qty": qty, **extra}
    if source is not None:
        c["source"] = source
    if price is not None:
        c["price"] = price
    return c


def base_groups(extra_cards=(), drop=()):
    """A 100-card list: the fixture deck minus `drop` (oldest spells first) plus extras."""
    spells = [s for s in SPELLS[:58] if s not in drop]
    return [
        {"role": "Commander", "cards": [card("Kaalia of the Vast", "kept")]},
        {"role": "Threats", "cards": [card("Lyra Dawnbringer", "kept"), card("Dragon Tempest", "kept")]
         + [card(s, "kept") for s in spells]},
        {"role": "New", "cards": list(extra_cards)},
        {"role": "Lands", "cards": [card("Plains", "kept", qty=13), card("Swamp", "kept", qty=13),
                                    card("Mountain", "kept", qty=13)]},
    ]


def raw_doc(groups, sections_extra=(), commander_mode="keep", **kw):
    secs = [{"type": "thesis_pillars", "title": "Thesis", "data": {"thesis": "Angels go brr", "pillars": ["Air"]}},
            {"type": "decklist", "title": "Decklist", "data": {"groups": groups}},
            {"type": "cuts", "title": "Cuts", "data": {"cards": kw.pop("cuts", [])}},
            {"type": "buy_total", "title": "Buy total", "data": {"total_usd": kw.pop("buy_total", 0)}}]
    secs.extend(sections_extra)
    return {"schema_version": "1", "deck_id": DECK_ID, "theme": "angels", "commander_mode": commander_mode,
            "created_at": "2026-10-12T12:00:00+00:00", "sections": secs, **kw}


def run_verify(raw, caps=CAPS, pets=PETS, owned=OWNED):
    doc = theory.validate_doc(raw)
    return theory.verify(doc, DECK, owned=owned, caps=caps, pets=pets, lookup=lookup, prices=prices,
                         now="2026-10-12T12:00:01+00:00")


def sec(doc, type_):
    return next(s for s in doc["sections"] if s["type"] == type_)


def all_cards(doc):
    return {c["name"]: c for g in sec(doc, "decklist")["data"]["groups"] for c in g["cards"]}


def kinds(doc):
    return [c["kind"] for c in doc["verification"]["corrections"]]


# ---------------------------------------------------------------------------


class SchemaTests(unittest.TestCase):
    def test_unknown_section_type_is_preserved_not_rejected(self):
        future = {"type": "matchup_matrix", "title": "Matchups", "data": {"vs": {"stax": "bad"}, "notes": ["x"]},
                  "render_hint": "table"}
        doc = theory.validate_doc(raw_doc(base_groups(), sections_extra=[future]))
        self.assertEqual(doc.section("matchup_matrix").data, future["data"])
        out = run_verify(raw_doc(base_groups(), sections_extra=[future]))
        kept = sec(out, "matchup_matrix")
        self.assertEqual(kept["data"], future["data"])
        self.assertEqual(kept["title"], "Matchups")
        self.assertEqual(kept["render_hint"], "table")  # extra keys survive too
        # its slot in the order survives (after buy_total, as sent)
        self.assertEqual([s["type"] for s in out["sections"]],
                         ["thesis_pillars", "decklist", "cuts", "buy_total", "matchup_matrix"])

    def test_unknown_top_level_keys_survive_round_trip(self):
        r = FakeRedis()
        out = run_verify(raw_doc(base_groups(), future_field={"a": 1}, doc_id="d1"))
        theory.store(r, out)
        self.assertEqual(theory.load(r, DECK_ID, "d1")["future_field"], {"a": 1})

    def test_required_fields(self):
        with self.assertRaises(theory.TheoryParseError):
            theory.validate_doc({"sections": []})
        with self.assertRaises(theory.TheoryParseError):
            theory.validate_doc({"deck_id": "x", "created_at": "t", "commander_mode": "maybe"})
        with self.assertRaises(theory.TheoryParseError):
            theory.validate_doc({"deck_id": "x", "created_at": "t", "sections": [{"title": "no type"}]})

    def test_unsupported_schema_version_is_rejected(self):
        with self.assertRaises(theory.TheoryParseError):
            theory.validate_doc({"schema_version": 2, "deck_id": "x", "created_at": "t"})
        self.assertEqual(theory.validate_doc({"schema_version": 1, "deck_id": "x", "created_at": "t"}).schema_version,
                         "1")

    def test_can_command(self):
        self.assertTrue(theory.can_command("Legendary Creature — Human", ""))
        self.assertTrue(theory.can_command("Legendary Enchantment — Background", ""))
        self.assertTrue(theory.can_command("Legendary Planeswalker — Teferi", "Teferi can be your commander."))
        self.assertFalse(theory.can_command("Artifact", "{T}: Add {C}{C}."))
        self.assertFalse(theory.can_command("Legendary Artifact", ""))

    def test_parse_reply_takes_last_json_block(self):
        text = "Here you go.\n```json\n{\"a\": 1}\n```\nprose\n```json\n{\"b\": 2}\n```\n"
        self.assertEqual(theory.parse_reply(text), {"b": 2})
        self.assertEqual(theory.parse_reply('{"c": 3}'), {"c": 3})
        with self.assertRaises(theory.TheoryParseError):
            theory.parse_reply("no json here")
        with self.assertRaises(theory.TheoryParseError):
            theory.parse_reply("")


class VerifyTests(unittest.TestCase):
    def test_clean_doc_counts_and_no_corrections(self):
        out = run_verify(raw_doc(base_groups()))
        self.assertEqual(out["verification"]["counts"], {"kept": 100, "owned": 0, "buy": 0, "total": 100})
        self.assertEqual(out["verification"]["corrections"], [])
        self.assertEqual(sec(out, "cuts")["data"]["cards"], [])
        self.assertEqual(sec(out, "buy_total")["data"]["total_usd"], 0.0)
        self.assertFalse(out["verification"]["commander_changed"])

    def test_false_ownership_claims_are_corrected(self):
        extras = [card("Sleeved Demon", "owned", price=0),   # owned but sleeved in another deck -> buy
                  card("Sol Ring", "kept"),                   # not in this deck; has a free copy -> owned
                  card("Free Agent Angel", "buy", price=9.99),  # free owned copy -> owned, $0 claim irrelevant
                  card("Cheap Buy", "owned")]                 # not owned at all -> buy
        out = run_verify(raw_doc(base_groups(extras, drop=SPELLS[:4]),
                                 cuts=[{"name": s, "reason": "off-theme"} for s in SPELLS[:4]]))
        cards = all_cards(out)
        self.assertEqual(cards["Sleeved Demon"]["source"], "buy")
        self.assertEqual(cards["Sol Ring"]["source"], "owned")
        self.assertEqual(cards["Free Agent Angel"]["source"], "owned")
        self.assertEqual(cards["Cheap Buy"]["source"], "buy")
        src_fixes = {c["card"]: c for c in out["verification"]["corrections"] if c["kind"] == "source"}
        self.assertEqual(set(src_fixes), {"Sleeved Demon", "Sol Ring", "Free Agent Angel", "Cheap Buy"})
        self.assertEqual((src_fixes["Sleeved Demon"]["claimed"], src_fixes["Sleeved Demon"]["actual"]), ("owned", "buy"))
        self.assertIn("Other Deck", src_fixes["Sleeved Demon"]["message"])  # never pulled from other decks
        self.assertEqual(out["verification"]["counts"], {"kept": 96, "owned": 2, "buy": 2, "total": 100})
        # prices recomputed from Scryfall, buy_total = sum of buy prices only
        self.assertEqual(cards["Sleeved Demon"]["price"], 12.0)
        self.assertEqual(sec(out, "buy_total")["data"]["total_usd"], 14.25)
        self.assertEqual(out["verification"]["buy_total_usd"], 14.25)
        self.assertEqual([c["name"] for c in sec(out, "buy_total")["data"]["cards"]], ["Sleeved Demon", "Cheap Buy"])
        # cuts keep oracle's reasons
        self.assertEqual([c["name"] for c in sec(out, "cuts")["data"]["cards"]], sorted(SPELLS[:4]))
        self.assertEqual(sec(out, "cuts")["data"]["cards"][0]["reason"], "off-theme")

    def test_prices_and_buy_total_are_never_trusted(self):
        out = run_verify(raw_doc(base_groups([card("Cheap Buy", "buy", price=0.01)], drop=SPELLS[:1]),
                                 cuts=[SPELLS[0]], buy_total=999))
        self.assertEqual(all_cards(out)["Cheap Buy"]["price"], 2.25)
        self.assertEqual(sec(out, "buy_total")["data"]["total_usd"], 2.25)
        self.assertIn("price", kinds(out))
        self.assertIn("buy_total", kinds(out))

    def test_hard_cap_drops_buy_soft_cap_flags_unpriced_dropped(self):
        extras = [card("Pricey Dragon", "buy"), card("Soft Cap Dragon", "buy"), card("Unpriced Thing", "buy")]
        out = run_verify(raw_doc(base_groups(extras, drop=SPELLS[:3]), cuts=SPELLS[:3]))
        cards = all_cards(out)
        self.assertNotIn("Pricey Dragon", cards)
        self.assertNotIn("Unpriced Thing", cards)
        self.assertTrue(cards["Soft Cap Dragon"]["over_soft_cap"])
        self.assertIn("hard_cap", kinds(out))
        self.assertIn("unpriced_buy", kinds(out))
        self.assertIn("card_count", kinds(out))  # 98 after drops: recorded, not hidden
        self.assertEqual(out["verification"]["counts"]["total"], 98)
        # no budget: the hard cap does not apply
        out2 = run_verify(raw_doc(base_groups(extras, drop=SPELLS[:3]), cuts=SPELLS[:3]),
                          caps={"no_budget": True, "hard_cap": None, "soft_cap": None})
        self.assertIn("Pricey Dragon", all_cards(out2))

    def test_color_identity_legality_and_unknown_cards_dropped(self):
        extras = [card("Blue Card", "buy"), card("Banned Card", "buy"), card("Made Up Card", "buy")]
        out = run_verify(raw_doc(base_groups(extras, drop=SPELLS[:3]), cuts=SPELLS[:3]))
        cards = all_cards(out)
        for n in ("Blue Card", "Banned Card", "Made Up Card"):
            self.assertNotIn(n, cards)
        self.assertTrue({"color_identity", "illegal", "unknown_card"} <= set(kinds(out)))

    def test_singleton_enforced_basics_exempt(self):
        groups = base_groups([card(SPELLS[10], "kept")], drop=[SPELLS[0]])
        out = run_verify(raw_doc(groups, cuts=[SPELLS[0]]))
        self.assertIn("duplicate", kinds(out))
        self.assertEqual(all_cards(out)["Plains"]["qty"], 13)

    def test_bounded_copy_limits_and_multi_copy_buy_total(self):
        FACTS.update({"Seven Dwarves": "R", "Hare Apparent": "W"})
        PRICES.update({"Seven Dwarves": 0.25, "Hare Apparent": 0.5})
        self.addCleanup(lambda: [d.pop(k, None) for d in (PRICES, FACTS) for k in ("Seven Dwarves", "Hare Apparent")])
        extras = [card("Seven Dwarves", "buy", qty=9), card("Hare Apparent", "buy", qty=4)]
        out = run_verify(raw_doc(base_groups(extras, drop=SPELLS[:11]), cuts=SPELLS[:11]))
        cards = all_cards(out)
        self.assertEqual(cards["Seven Dwarves"]["qty"], 7)  # "up to seven", not any number
        self.assertEqual(cards["Hare Apparent"]["qty"], 4)
        self.assertIn("duplicate", kinds(out))
        bt = sec(out, "buy_total")["data"]
        self.assertEqual(bt["total_usd"], round(7 * 0.25 + 4 * 0.5, 2))  # every copy is paid for
        self.assertEqual(bt["count"], 11)
        self.assertEqual(out["verification"]["counts"]["buy"], 11)

    def test_cuts_recomputed_from_the_real_diff(self):
        out = run_verify(raw_doc(base_groups([card("Cheap Buy", "buy")], drop=[SPELLS[0]]),
                                 cuts=["Not In Deck", SPELLS[5]]))
        cut_names = [c["name"] for c in sec(out, "cuts")["data"]["cards"]]
        self.assertEqual(cut_names, [SPELLS[0]])
        msgs = " ".join(c["message"] for c in out["verification"]["corrections"] if c["kind"] == "cut")
        self.assertIn("Not In Deck", msgs)
        self.assertIn(SPELLS[5], msgs)
        self.assertIn(SPELLS[0], msgs)

    def test_cut_pet_is_flagged(self):
        groups = base_groups([card("Cheap Buy", "buy")])
        groups[1]["cards"] = [c for c in groups[1]["cards"] if c["name"] != "Dragon Tempest"]
        out = run_verify(raw_doc(groups, cuts=["Dragon Tempest"]))
        self.assertIn("pet_missing", kinds(out))
        self.assertTrue(sec(out, "cuts")["data"]["cards"][0]["pet"])

    def test_missing_known_sections_are_created(self):
        raw = raw_doc(base_groups())
        raw["sections"] = [s for s in raw["sections"] if s["type"] in ("decklist",)]
        out = run_verify(raw)
        self.assertEqual([s["type"] for s in out["sections"]], ["decklist", "cuts", "buy_total"])


class CommanderModeTests(unittest.TestCase):
    def test_keep_mode_strips_a_commander_change(self):
        cc = {"type": "commander_change", "title": "New commander",
              "data": {"new_commanders": ["Isshin, Two Heavens as One"], "reason": "more attacks"}}
        out = run_verify(raw_doc(base_groups(), sections_extra=[cc]))
        self.assertNotIn("commander_change", [s["type"] for s in out["sections"]])
        self.assertIn("commander_kept", kinds(out))
        self.assertFalse(out["verification"]["commander_changed"])

    def test_open_mode_without_a_swap_has_no_commander_change(self):
        out = run_verify(raw_doc(base_groups(), commander_mode="open"))
        self.assertNotIn("commander_change", [s["type"] for s in out["sections"]])
        cc = {"type": "commander_change", "title": "x", "data": {"new_commanders": ["Kaalia of the Vast"]}}
        out = run_verify(raw_doc(base_groups(), commander_mode="open", sections_extra=[cc]))
        self.assertNotIn("commander_change", [s["type"] for s in out["sections"]])
        self.assertIn("commander_unchanged", kinds(out))

    def test_open_mode_swap_recomputes_reuse_and_calls_out_illegal_pets(self):
        # Sram is mono-W: Dragon Tempest (R, a pet) and the B/R basics become illegal.
        groups = base_groups()
        groups[0]["cards"] = [card("Sram, Senior Edificer", "buy")]
        groups[1]["cards"] = [c for c in groups[1]["cards"] if c["name"] != "Dragon Tempest"] + \
            [card("Cheap Buy", "buy")]  # Cheap Buy is R -> dropped as off-identity
        groups[3]["cards"] = [card("Plains", "kept", qty=39)]
        cc = {"type": "commander_change", "title": "Swap",
              "data": {"new_commanders": ["Sram, Senior Edificer"], "reason": "mono-W angels", "reuse_count": 3}}
        out = run_verify(raw_doc(groups, commander_mode="open", sections_extra=[cc],
                                 cuts=["Kaalia of the Vast", "Dragon Tempest"]))
        cc_out = sec(out, "commander_change")["data"]
        self.assertEqual(cc_out["new_commanders"], ["Sram, Senior Edificer"])
        self.assertEqual(cc_out["old_commanders"], ["Kaalia of the Vast"])
        self.assertEqual(cc_out["reason"], "mono-W angels")
        self.assertEqual(cc_out["pets_lost"], ["Dragon Tempest"])
        # kept = Lyra + 58 spells + 13 Plains from the deck = 71... Plains are qty 39 here but only 13 were in the
        # deck; the source is still "kept" because the card is in the deck (basics are interchangeable).
        kept = out["verification"]["counts"]["kept"]
        self.assertEqual(cc_out["reuse_count"], kept)
        self.assertIn("reuse_count", kinds(out))
        self.assertNotIn("pet_missing", kinds(out))  # illegal pets are called out, not flagged as wrongly cut
        self.assertNotIn("Cheap Buy", all_cards(out))
        self.assertTrue(out["verification"]["commander_changed"])
        cut_names = [c["name"] for c in sec(out, "cuts")["data"]["cards"]]
        self.assertEqual(cut_names[0], "Kaalia of the Vast")
        self.assertEqual(cut_names.count("Kaalia of the Vast"), 1)  # deck.cards carries the commander too
        self.assertTrue(sec(out, "cuts")["data"]["cards"][0]["commander"])
        self.assertIn("Swamp", cut_names)

    def _swap_doc(self, new_cmdr):
        groups = base_groups()
        groups[0]["cards"] = [card(new_cmdr, "buy")]
        cc = {"type": "commander_change", "title": "Swap", "data": {"new_commanders": [new_cmdr], "reason": "r",
                                                                      "reuse_count": 1}}
        return raw_doc(groups, commander_mode="open", sections_extra=[cc], cuts=["Kaalia of the Vast"])

    def test_swap_to_illegal_or_ineligible_commander_is_reverted(self):
        for bad, kind in (("Banned Card", "commander_illegal"), ("Sol Ring", "commander_ineligible"),
                          ("Made Up Commander", "commander_unknown")):
            out = run_verify(self._swap_doc(bad))
            self.assertIn(kind, kinds(out), bad)
            self.assertNotIn("commander_change", [s["type"] for s in out["sections"]])
            self.assertEqual(out["verification"]["commanders"], ["Kaalia of the Vast"])
            self.assertIn("Kaalia of the Vast", all_cards(out))  # the real commander is back in the list
            self.assertNotIn(bad, all_cards(out) if bad != "Sol Ring" else {})

    def test_swap_to_unpriced_or_over_cap_commander_is_reverted(self):
        PRICES["Pricey Commander"] = 99.0
        FACTS["Pricey Commander"] = "WBR"
        FACTS["Unpriced Commander"] = "WBR"
        self.addCleanup(lambda: [d.pop(k, None) for d in (PRICES, FACTS)
                                 for k in ("Pricey Commander", "Unpriced Commander")])
        for bad, kind in (("Pricey Commander", "commander_hard_cap"), ("Unpriced Commander", "commander_unpriced")):
            out = run_verify(self._swap_doc(bad))
            self.assertIn(kind, kinds(out))
            self.assertFalse(out["verification"]["commander_changed"])
            self.assertEqual(out["verification"]["commanders"], ["Kaalia of the Vast"])
            self.assertIn("Kaalia of the Vast", all_cards(out))


class StorageTests(unittest.TestCase):
    def test_store_list_load_delete(self):
        r = FakeRedis()
        a = run_verify(raw_doc(base_groups(), doc_id="aaa", theme="x"))
        a["created_at"] = "2026-10-12T10:00:00+00:00"
        b = run_verify(raw_doc(base_groups(), doc_id="bbb"))
        b["created_at"] = "2026-10-12T11:00:00+00:00"
        theory.store(r, a)
        theory.store(r, b)
        self.assertIn(f"theory:{DECK_ID}:aaa", r.store)
        self.assertEqual(set(r.zsets[f"theory_index:{DECK_ID}"]), {"aaa", "bbb"})
        lst = theory.list_docs(r, DECK_ID)
        self.assertEqual([d["doc_id"] for d in lst], ["bbb", "aaa"])  # newest first
        self.assertEqual(lst[0]["counts"]["total"], 100)
        self.assertIn("decklist", lst[0]["section_types"])
        self.assertTrue(theory.delete(r, DECK_ID, "aaa"))
        self.assertIsNone(theory.load(r, DECK_ID, "aaa"))
        self.assertFalse(theory.delete(r, DECK_ID, "aaa"))
        self.assertEqual([d["doc_id"] for d in theory.list_docs(r, DECK_ID)], ["bbb"])

    def test_dangling_index_entry_self_heals(self):
        r = FakeRedis()
        r.zadd(theory.index_key(DECK_ID), {"ghost": 1.0})
        self.assertEqual(theory.list_docs(r, DECK_ID), [])
        self.assertEqual(r.zsets[theory.index_key(DECK_ID)], {})


# --- generation with a mocked oracle --------------------------------------------------


def _seed(r):
    r.set("decks", json.dumps([DECK]))
    r.set(f"journal:deck:{DECK_ID}", json.dumps({"pet_cards": PETS, "budget_constraints": {"hard_cap": 40,
                                                                                           "soft_cap": 15}}))


def _oracle_transport(reply_doc, seen):
    def handler(request: httpx.Request):
        seen.append(request)
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "oracle"}]})
        content = "Overhaul written.\n```json\n" + json.dumps(reply_doc) + "\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    return httpx.MockTransport(handler)


def _fake_prices(names):
    return {n: PRICES.get(n) for n in names}


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        _seed(self.r)
        self.seen = []
        lying = raw_doc(base_groups([card("Sleeved Demon", "owned"), card("Free Agent Angel", "owned")],
                                    drop=SPELLS[:2]), cuts=SPELLS[:2],
                        sections_extra=[{"type": "flavor_text", "title": "Vibes", "data": "**angels**"}])
        lying.update(deck_id="WRONG", theme="ignored", created_at="1999-01-01")
        self.client = OracleClient("http://oracle.test", "k", transport=_oracle_transport(lying, self.seen))
        patcher = patch.object(theory, "_owned", lambda r: OWNED)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_generate_verifies_and_stores(self):
        doc = theory.generate(self.r, DECK, "angels", "keep", self.client, lookup=lookup,
                              fetch_prices=_fake_prices, doc_id="gen1")
        self.assertEqual(doc["deck_id"], DECK_ID)  # app-owned fields override oracle's
        self.assertEqual(doc["theme"], "angels")
        self.assertNotEqual(doc["created_at"], "1999-01-01")
        self.assertEqual(all_cards(doc)["Sleeved Demon"]["source"], "buy")
        self.assertEqual(sec(doc, "flavor_text")["data"], "**angels**")
        self.assertEqual(theory.load(self.r, DECK_ID, "gen1")["doc_id"], "gen1")
        # the request carried the skill input contract
        body = json.loads(self.seen[-1].content)
        self.assertIn("deck-theory", body["messages"][0]["content"])
        req = json.loads(re.search(r"```json\n(.*)```", body["messages"][1]["content"], re.S).group(1))
        self.assertEqual(req["commander_mode"], "keep")
        self.assertEqual(req["journal"]["pet_cards"], PETS)
        self.assertEqual(req["caps"]["hard_cap"], 40.0)
        self.assertIn("Free Agent Angel", req["free_agent_owned"])
        self.assertIn("Sol Ring", req["free_agent_owned"])  # one free copy
        self.assertNotIn("Sleeved Demon", req["free_agent_owned"])  # never other decks
        self.assertEqual(sum(c["qty"] for c in req["decklist"]), 100)

    def test_generate_never_writes_journal_or_decklist(self):
        before = {k: v for k, v in self.r.store.items()}
        theory.generate(self.r, DECK, "angels", "keep", self.client, lookup=lookup, fetch_prices=_fake_prices)
        for k in ("decks", f"journal:deck:{DECK_ID}"):
            self.assertEqual(self.r.store[k], before[k])
        new = set(self.r.store) - set(before)
        self.assertTrue(all(k.startswith(("theory:", "price:card:")) for k in new), new)

    def test_job_offline_is_clean(self):
        job = theory.new_job(self.r, DECK, "angels", "keep")

        def boom():
            raise OracleUnavailable("cannot reach x")
        out = theory.run_job(self.r, job, DECK, boom)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["error"], "oracle offline, try again later")
        self.assertEqual(out["error_kind"], "oracle_offline")

    def test_job_success_and_bad_reply(self):
        job = theory.new_job(self.r, DECK, "angels", "keep")
        out = theory.run_job(self.r, job, DECK, lambda: self.client, lookup=lookup, fetch_prices=_fake_prices)
        self.assertEqual(out["status"], "done", out)
        self.assertEqual(out["summary"]["counts"]["total"], 100)
        self.assertEqual(theory.load_job(self.r, job["job_id"])["status"], "done")
        self.assertEqual(self.r.ttl[theory.job_key(job["job_id"])], theory.JOB_TTL_S)

        bad = OracleClient("http://oracle.test", "k", transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"choices": [{"message": {"content": "sorry, no"}}]})))
        job2 = theory.new_job(self.r, DECK, "angels", "keep")
        out2 = theory.run_job(self.r, job2, DECK, lambda: bad, lookup=lookup, fetch_prices=_fake_prices)
        self.assertEqual(out2["status"], "failed")
        self.assertEqual(out2["error_kind"], "TheoryParseError")

    def test_new_job_validation(self):
        with self.assertRaises(theory.TheoryError):
            theory.new_job(self.r, DECK, "  ", "keep")
        with self.assertRaises(theory.TheoryError):
            theory.new_job(self.r, DECK, "x", "swap")
        with self.assertRaises(theory.TheoryError):
            theory.new_job(self.r, DECK, "x" * 201, "keep")
        with self.assertRaises(theory.TheoryError):  # a deck with no synced cards can't be overhauled
            theory.new_job(self.r, {**DECK, "cards": []}, "x", "keep")

    def test_scryfall_price_outage_fails_the_job_instead_of_dropping_buys(self):
        def down(names):
            raise ConnectionError("scryfall down")
        job = theory.new_job(self.r, DECK, "angels", "keep")
        out = theory.run_job(self.r, job, DECK, lambda: self.client, lookup=lookup, fetch_prices=down)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["error_kind"], "PricesUnavailable")
        self.assertEqual(theory.list_docs(self.r, DECK_ID), [])

    def test_one_job_per_deck_and_global_limit(self):
        job = theory.new_job(self.r, DECK, "angels", "keep")
        with self.assertRaises(theory.TheoryBusy) as cm:
            theory.new_job(self.r, DECK, "dragons", "keep")
        self.assertTrue(cm.exception.per_deck)
        others = []
        for i in range(theory.MAX_INFLIGHT - 1):
            others.append(theory.new_job(self.r, {**DECK, "registry_id": f"test:other{i}"}, "x", "keep"))
        with self.assertRaises(theory.TheoryBusy) as cm:
            theory.new_job(self.r, {**DECK, "registry_id": "test:one-too-many"}, "x", "keep")
        self.assertFalse(cm.exception.per_deck)
        for j in [job] + others:
            theory.release(j["deck_id"], j["job_id"])
        theory.release(theory.new_job(self.r, DECK, "again", "keep")["deck_id"])  # slot is free again


# --- API -----------------------------------------------------------------------------


class ApiTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        import src.server.app.main as app_mod

        self.app_mod = app_mod
        self.r = FakeRedis()
        _seed(self.r)
        self.patches = [patch.object(app_mod, "r", self.r), patch.object(theory, "_owned", lambda r: OWNED)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.client = TestClient(app_mod.app)
        doc = raw_doc(base_groups([card("Sleeved Demon", "owned")], drop=[SPELLS[0]]), cuts=[SPELLS[0]])
        self.seen = []
        self.oracle = OracleClient("http://oracle.test", "k", transport=_oracle_transport(doc, self.seen))

    def _sync_start(self):
        def start(r, job, deck, factory, **kw):
            theory.run_job(r, job, deck, factory, lookup=lookup, fetch_prices=_fake_prices)
        return patch.object(theory, "start_job", start)

    def test_full_lifecycle(self):
        with patch.object(self.app_mod, "_oracle_client", lambda: self.oracle), self._sync_start():
            resp = self.client.post(f"/api/decks/{DECK_ID}/theory", json={"theme": "angels", "commander_mode": "keep"})
        self.assertEqual(resp.status_code, 202, resp.text)
        job_id = resp.json()["job_id"]
        self.assertIn(job_id, resp.json()["status_url"])
        st = self.client.get(f"/api/decks/{DECK_ID}/theory/jobs/{job_id}").json()["job"]
        self.assertEqual(st["status"], "done", st)
        doc_id = st["doc_id"]
        lst = self.client.get(f"/api/decks/{DECK_ID}/theory").json()["docs"]
        self.assertEqual([d["doc_id"] for d in lst], [doc_id])
        self.assertEqual(lst[0]["counts"]["buy"], 1)
        # lookup by display name works too (the deck page only knows the name)
        doc = self.client.get(f"/api/decks/Theory%20Fixture/theory/{doc_id}").json()["doc"]
        self.assertEqual(all_cards(doc)["Sleeved Demon"]["source"], "buy")
        self.assertEqual(self.client.delete(f"/api/decks/{DECK_ID}/theory/{doc_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/decks/{DECK_ID}/theory/{doc_id}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/decks/{DECK_ID}/theory/{doc_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/decks/{DECK_ID}/theory").json()["docs"], [])

    def test_oracle_unreachable_is_a_clean_503(self):
        def refused(request):
            raise httpx.ConnectError("refused")
        down = OracleClient("http://oracle.test", "k", transport=httpx.MockTransport(refused))
        with patch.object(self.app_mod, "_oracle_client", lambda: down), self._sync_start():
            resp = self.client.post(f"/api/decks/{DECK_ID}/theory", json={"theme": "angels"})
        self.assertEqual(resp.status_code, 503)
        body = resp.json()
        self.assertEqual(body["error"], "oracle offline, try again later")
        self.assertEqual(body["error_kind"], "oracle_offline")
        self.assertNotIn("Traceback", resp.text)
        self.assertFalse([k for k in self.r.store if k.startswith("theory_job:")])  # no orphan job
        self.assertNotIn(DECK_ID, theory._INFLIGHT)  # slot released

    def test_second_request_for_the_same_deck_is_409(self):
        job = theory.new_job(self.r, DECK, "angels", "keep")
        self.addCleanup(theory.release, job["deck_id"], job["job_id"])
        with patch.object(self.app_mod, "_oracle_client", lambda: self.oracle):
            resp = self.client.post(f"/api/decks/{DECK_ID}/theory", json={"theme": "dragons"})
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error_kind"], "busy")

    def test_oracle_not_configured_is_503(self):
        with patch.dict(os.environ, {"ORACLE_URL": "", "ORACLE_KEY": "", "INTERACTIONS_SECRETS_DIR": "/nonexistent"}):
            resp = self.client.post(f"/api/decks/{DECK_ID}/theory", json={"theme": "angels"})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error_kind"], "oracle_offline")

    def test_bad_input_and_unknowns(self):
        self.assertEqual(self.client.post(f"/api/decks/{DECK_ID}/theory", json={"theme": ""}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/decks/{DECK_ID}/theory",
                                          json={"theme": "x", "commander_mode": "swap"}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/decks/{DECK_ID}/theory", content=b"[1]",
                                          headers={"content-type": "application/json"}).status_code, 400)
        self.assertEqual(self.client.post("/api/decks/nope/theory", json={"theme": "x"}).status_code, 404)
        self.assertEqual(self.client.get(f"/api/decks/{DECK_ID}/theory/jobs/nope").status_code, 404)
        self.assertEqual(self.client.get("/api/decks/nope/theory").status_code, 404)


class SkillContractTests(unittest.TestCase):
    """The repo mirror of oracle's deck-theory skill: its example validates against TheoryDoc."""

    SKILL = ROOT / "docs" / "oracle" / "deck-theory" / "SKILL.md"
    SCHEMA = ROOT / "docs" / "oracle" / "deck-theory" / "references" / "theory.schema.json"

    def test_skill_example_validates(self):
        import jsonschema

        text = self.SKILL.read_text(encoding="utf-8")
        self.assertIn("name: deck-theory", text)
        example = theory.parse_reply(text.split("## Output shape", 1)[1])
        schema = json.loads(self.SCHEMA.read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.Draft202012Validator(schema).validate(example)  # the contract oracle is held to
        # and the app's (more lenient) model accepts it
        example.update(deck_id=DECK_ID, created_at="2026-10-12T00:00:00+00:00")
        doc = theory.validate_doc(example)
        self.assertEqual([s.type for s in doc.sections][:4], ["thesis_pillars", "decklist", "cuts", "buy_total"])

    def test_json_schema_rejects_bad_known_sections_but_allows_unknown_types(self):
        import jsonschema

        v = jsonschema.Draft202012Validator(json.loads(self.SCHEMA.read_text(encoding="utf-8")))
        ok = {"schema_version": "1", "sections": [{"type": "future_thing", "title": "x", "data": [1, 2]}]}
        v.validate(ok)
        for bad in ({"schema_version": "2", "sections": ok["sections"]},
                    {"schema_version": "1", "sections": [{"type": "buy_total", "title": "t", "data": {}}]},
                    {"schema_version": "1", "sections": [{"type": "decklist", "title": "t", "data": {"groups": [
                        {"role": "x", "cards": [{"name": "a", "source": "stolen"}]}]}}]}):
            self.assertFalse(v.is_valid(bad), bad)

    def test_json_schema_lists_v1_types(self):
        schema = json.loads(self.SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1")
        blob = json.dumps(schema)
        for t in theory.KNOWN_TYPES:
            self.assertIn(t, blob)


if __name__ == "__main__":
    unittest.main()
