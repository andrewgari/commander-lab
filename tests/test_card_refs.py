"""Printing identity (CardRef) for the hover preview: card_refs.py, the
inventory row fields it relies on, the deck page wiring and the HUD.cardRef
JS twin. See docs/scryfall-hover-preview.md."""
import html as html_mod
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from fakeredis import FakeRedis
from fastapi.testclient import TestClient

import app as app_module
import card_refs
import instances as instance_store
import inventory_view
import registry

ROOT = Path(__file__).resolve().parent.parent
HUD_JS = ROOT / "static" / "js" / "hud.js"
SID = "716D0B3B-BAC9-4FB8-882E-BD6171864043"

REF_KEYS = {"name", "scryfallId", "oracleId", "set", "collectorNumber", "layout",
            "imageUris", "cardFaces", "printingSpecific", "source"}


def _data_ref(markup):
    m = re.search(r'data-card-ref="([^"]*)"', str(markup))
    return json.loads(html_mod.unescape(m.group(1)))


class TestMakeRef(unittest.TestCase):
    def test_full_shape_always_present(self):
        ref = card_refs.make_ref("Sol Ring")
        self.assertEqual(set(ref), REF_KEYS)
        self.assertFalse(ref["printingSpecific"])
        self.assertEqual(ref["source"], "name")

    def test_scryfall_id_is_printing_specific_and_normalised(self):
        ref = card_refs.make_ref("Sol Ring", scryfall_id=f"  {SID} ", set_code="C21")
        self.assertEqual(ref["scryfallId"], SID.lower())
        self.assertEqual(ref["set"], "c21")
        self.assertTrue(ref["printingSpecific"])

    def test_set_plus_collector_number_is_printing_specific(self):
        self.assertTrue(card_refs.make_ref("X", set_code="cmr", collector_number="472")["printingSpecific"])
        # Set alone is not a printing.
        self.assertFalse(card_refs.make_ref("X", set_code="cmr")["printingSpecific"])

    def test_instance_ref(self):
        inst = {"card_name": "Arcane Signet", "archidekt_uid": "", "set": "M3C", "collector_number": "283"}
        ref = card_refs.ref_from_instance(inst)
        self.assertIsNone(ref["scryfallId"])
        self.assertEqual((ref["set"], ref["collectorNumber"]), ("m3c", "283"))
        self.assertTrue(ref["printingSpecific"])

    def test_card_key_matches_front_face(self):
        self.assertEqual(card_refs.card_key("Fire // Ice"), "fire")
        self.assertEqual(card_refs.card_key("  Sol Ring "), "sol ring")


class TestBuildIndex(unittest.TestCase):
    DECK = {"name": "D", "source": "archidekt", "source_id": "1", "id": 1,
            "commanders": ["Atraxa, Praetors' Voice", "Yorion, Sky Nomad"], "commander_uids": ["aaa", "bbb"]}

    def test_deck_copy_beats_binder_copy_and_skips_not_owned(self):
        insts = [
            {"card_name": "Sol Ring", "ownership_status": "in_collection", "archidekt_uid": "binder"},
            {"card_name": "Sol Ring", "ownership_status": "in_deck", "deck_id": "archidekt:1", "archidekt_uid": "deck"},
            {"card_name": "Wish", "ownership_status": "not_owned", "archidekt_uid": "nope"},
        ]
        idx = card_refs.build_index(insts, self.DECK)
        self.assertEqual(idx["sol ring"]["scryfallId"], "deck")
        self.assertEqual(idx["sol ring"]["source"], "deck")
        self.assertNotIn("wish", idx)

    def test_commanders_fill_missing_names(self):
        idx = card_refs.build_index([], self.DECK)
        self.assertEqual(idx["yorion, sky nomad"]["scryfallId"], "bbb")
        self.assertEqual(idx["yorion, sky nomad"]["source"], "commander")

    def test_unpaired_commander_uids_are_dropped(self):
        deck = dict(self.DECK, commander_uids=["aaa"])
        idx = card_refs.build_index([], deck)
        self.assertFalse(idx["atraxa, praetors' voice"]["printingSpecific"])

    def test_deck_instances_filters_to_the_deck(self):
        insts = [
            {"card_name": "A", "ownership_status": "in_deck", "deck_id": "1"},
            {"card_name": "B", "ownership_status": "in_deck", "deck_id": "archidekt:1"},
            {"card_name": "C", "ownership_status": "in_deck", "deck_id": "archidekt:2"},
            {"card_name": "D", "ownership_status": "in_collection"},
        ]
        self.assertEqual([i["card_name"] for i in card_refs.deck_instances(insts, self.DECK)], ["A", "B"])


class TestLookupAndAttrs(unittest.TestCase):
    REFS = {"fire": card_refs.compact(card_refs.make_ref("Fire // Ice", scryfall_id="abc"))}

    def test_lookup_hit_and_name_only_fallback(self):
        self.assertEqual(card_refs.lookup(self.REFS, "Fire // Ice")["scryfallId"], "abc")
        miss = card_refs.lookup(self.REFS, "Unknown Card")
        self.assertFalse(miss["printingSpecific"])
        self.assertEqual(miss["name"], "Unknown Card")

    def test_html_attrs_from_map(self):
        out = card_refs.html_attrs("Fire // Ice", self.REFS)
        self.assertIn('data-card-name="Fire // Ice"', str(out))
        self.assertEqual(_data_ref(out)["scryfallId"], "abc")

    def test_html_attrs_escapes_and_drops_nulls(self):
        out = card_refs.html_attrs('Bad "<name>"')
        self.assertNotIn("<name>", str(out))
        ref = _data_ref(out)
        self.assertEqual(ref["name"], 'Bad "<name>"')
        self.assertNotIn("scryfallId", ref)
        self.assertFalse(ref["printingSpecific"])

    def test_explicit_ref_and_uid_win(self):
        self.assertEqual(_data_ref(card_refs.html_attrs("X", self.REFS, uid="UUU"))["scryfallId"], "uuu")
        explicit = card_refs.make_ref("X", set_code="lea", collector_number="1")
        self.assertEqual(_data_ref(card_refs.html_attrs("X", None, ref=explicit))["set"], "lea")


class FakeRedisCase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self._orig_r = app_module.r
        app_module.r = self.r
        self.deck = registry.upsert_deck(self.r, {
            "source": "archidekt", "source_id": "999", "name": "Ref Deck", "color": "G",
            "commanders": ["Omnath, Locus of Mana"], "commander_uids": ["cmdr-uid"],
            "folder": "x", "description": "", "cards": [{"name": "Sol Ring", "quantity": 1}], "url": "",
        }, default_status="testing")
        rid = registry.registry_id_of(self.deck)
        instance_store.create_instance(self.r, "Sol Ring", ownership_status="in_deck", deck_id=rid,
                                       set="C21", set_name="Commander 2021", collector_number="263",
                                       archidekt_uid=SID)
        instance_store.create_instance(self.r, "Lightning Bolt", ownership_status="in_collection",
                                       set="2X2", collector_number="117", archidekt_uid="binder-uid")
        self.r.set("idx:card_name_to_oracle:Sol Ring", "oracle-sol")

    def tearDown(self):
        app_module.r = self._orig_r


class TestPayloads(FakeRedisCase):
    def test_inventory_rows_carry_collector_number_and_oracle_id(self):
        rows = inventory_view.build_inventory(self.r, decks=["Ref Deck"])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual((row["uid"], row["set"], row["collector_number"], row["oracle_id"]),
                         (SID, "C21", "263", "oracle-sol"))

    def test_deck_card_refs_is_deck_scoped(self):
        refs = card_refs.deck_card_refs(self.r, self.deck)
        self.assertEqual(refs["sol ring"]["scryfallId"], SID.lower())
        self.assertEqual(refs["sol ring"]["collectorNumber"], "263")
        self.assertEqual(refs["omnath, locus of mana"]["scryfallId"], "cmdr-uid")
        self.assertNotIn("lightning bolt", refs)  # binder copy, not in this deck

    def test_deck_page_embeds_printing_map_and_wires_render_sites(self):
        page = TestClient(app_module.app).get("/deck/Ref%20Deck")
        self.assertEqual(page.status_code, 200)
        m = re.search(r"const CARD_REFS = (\{.*?\});\n", page.text)
        self.assertIsNotNone(m, "deck.html must embed the printing map")
        refs = json.loads(m.group(1))
        self.assertEqual(refs["sol ring"]["scryfallId"], SID.lower())
        # Each JS render site goes through refOf / cardRefAttrs.
        self.assertIn("HUD.cardRefAttrs(refOf(card))", page.text)                    # grid + modal
        self.assertIn('data-card-ref="${refAttr(c)}"', page.text)                    # list view
        self.assertIn("HUD.cardRefAttrs(HUD.cardRef({ name: p.name, uid: p.uid }))", page.text)  # commanders w/o row


@unittest.skipUnless(shutil.which("node"), "node not installed: hud.js tests need it")
class TestHudCardRefJs(unittest.TestCase):
    HARNESS = r"""
const fs = require('fs');
global.document = { readyState: 'complete', addEventListener: () => {}, querySelectorAll: () => [], getElementById: () => null };
global.window = {};
// Loads the repo's own static/js/hud.js (same pattern as test_hud_shell_deck).
eval(fs.readFileSync(process.argv[1], 'utf8'));
const H = window.HUD;
const refs = { 'fire': { name: 'Fire // Ice', scryfallId: 'abc', printingSpecific: true, source: 'deck' } };
const out = {
  row: H.cardRef({ name: 'Sol Ring', uid: 'ABC-1', set: 'C21', collector_number: '263', oracle_id: 'o1' }),
  setCn: H.cardRef({ name: 'X', uid: '', set: 'LEA', collector_number: '1' }),
  viaMap: H.cardRef('Fire // Ice', refs),
  rowMissPrinting: H.cardRef({ name: 'Fire // Ice', uid: '', set: 'mh2', oracle_id: 'o2' }, refs),
  nameOnly: H.cardRef('Nope', refs),
  attrs: H.cardRefAttrs(H.cardRef('Nope')),
  art: H.art('Sol Ring', '', { attrs: H.cardRefAttrs(H.cardRef({ name: 'Sol Ring', uid: 'u"1' })) }),
};
process.stdout.write(JSON.stringify(out));
"""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", cls.HARNESS, str(HUD_JS)], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_row_ref(self):
        r = self.out["row"]
        self.assertEqual(set(r), REF_KEYS)
        self.assertEqual((r["scryfallId"], r["set"], r["collectorNumber"], r["oracleId"]), ("abc-1", "c21", "263", "o1"))
        self.assertTrue(r["printingSpecific"])

    def test_set_and_collector_number_fallback(self):
        self.assertTrue(self.out["setCn"]["printingSpecific"])
        self.assertIsNone(self.out["setCn"]["scryfallId"])

    def test_map_lookup_when_row_has_no_printing(self):
        self.assertEqual(self.out["viaMap"]["scryfallId"], "abc")
        self.assertEqual(self.out["rowMissPrinting"]["scryfallId"], "abc")
        self.assertEqual(self.out["rowMissPrinting"]["oracleId"], "o2")

    def test_name_only_fallback(self):
        r = self.out["nameOnly"]
        self.assertFalse(r["printingSpecific"])
        self.assertEqual(r["source"], "name")
        self.assertEqual(json.loads(self.out["attrs"]["data-card-ref"]),
                         {"name": "Nope", "printingSpecific": False, "source": "name"})

    def test_attrs_are_escaped_in_markup(self):
        self.assertIn("data-card-ref=", self.out["art"])
        self.assertEqual(_data_ref(self.out["art"])["scryfallId"], 'u"1')


if __name__ == "__main__":
    unittest.main()
