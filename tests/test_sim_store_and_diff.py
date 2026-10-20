"""SM-2: sim_client (.dck export + mocked HTTP), sim_store persistence/staleness, sim_diff on Holy Hellfire."""
import copy
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import review_metrics as rm
from analytics import sim_client as sc
from analytics import sim_diff as sd
from analytics import sim_store as ss

SPELLS = (
    ["Sol Ring", "Arcane Signet", "Demonic Tutor", "Helm of the Host", "Strionic Resonator", "Teferi's Protection",
     "Liesa, Forgotten Archangel", "Blast-Furnace Hellkite", "Bloodthirster", "Burning-Rune Demon", "Vilis, Broker of Blood",
     "Isshin, Two Heavens as One", "Lyra Dawnbringer", "Archangel Avacyn // Avacyn, the Purifier",
     "Fire // Ice", "Bonecrusher Giant // Stomp", "Ancient Copper Dragon", "Akroma, Angel of Fury"]
)


def make_deck(extra=None):
    cards = [{"name": "Plains", "quantity": 12}, {"name": "Swamp", "quantity": 12}, {"name": "Mountain", "quantity": 11}]
    cards += [{"name": n, "quantity": 1} for n in SPELLS]
    filler = 99 - sum(c["quantity"] for c in cards)
    cards += [{"name": f"Filler {i}", "quantity": 1} for i in range(filler)]
    cards.append({"name": "Kaalia of the Vast", "quantity": 1})  # commander also listed in cards
    cards += extra or []
    return {"id": 417707, "registry_id": "archidekt:417707", "name": "Holy Hellfire",
            "commanders": ["Kaalia of the Vast"], "cards": cards}


def make_review():
    return {
        "curve_tempo": {"library_size": 99,
                        "commander_on_curve": {"commander": "Kaalia of the Vast", "cmc": 3, "p_lands_only": 0.8, "p_with_ramp": 0.9},
                        "mana": {"lands": 35, "unspent_avg_t2_t6": 1.0,
                                 "per_turn": [{"turn": t, "mana": float(t), "spend": float(t) - 0.5} for t in range(2, 7)]}},
        "stragglers": {"cards": [{"name": "Strionic Resonator", "straggler_score": 0.8}, {"name": "Isshin, Two Heavens as One", "straggler_score": 0.7},
                                 {"name": "Fire // Ice", "straggler_score": 0.6}],
                       "protected_pets": [{"name": "Vilis, Broker of Blood"}]},
        "strong_cards": {"cards": [{"name": "Helm of the Host"}, {"name": "Burning-Rune Demon"}]},
        "inputs": {"pets": ["Vilis, Broker of Blood"]},
    }


def row(card, u, drawn, cast=None, turn=None, flag=None):
    return {"card": card, "utilization_rate": u, "games_drawn": drawn, "games_cast": cast if cast is not None else round(u * drawn),
            "avg_turn_cast": turn, "diagnostic_flag": flag}


def make_report():
    return {
        "deck_id": 417707, "params": {"opponent_mode": "random"},
        "land_drop_rates_by_turn": {"T1": 1.0, "T2": 0.95, "T3": 0.8, "T4": 0.6, "T5": 0.45, "T6": 0.3},
        "t5_mana_starvation_rate": 0.35, "avg_first_commander_turn": 5.5, "avg_mana_utilization_t2_t6": 0.4,
        "card_diagnostics": [
            row("Strionic Resonator", 0.1, 10, turn=None, flag="dead_draw"),       # confirmed straggler
            row("Isshin, Two Heavens as One", 0.5, 10, 5, 6.0),                     # static straggler, not low U
            row("Fire // Ice", 0.3, 8),                                             # boundary: confirmed (<=.30)
            row("Burning-Rune Demon", 0.0, 7, flag="dead_draw"),                   # strong card that bricked -> surprise
            row("Bloodthirster", 0.25, 5),                                          # boundary: surprise
            row("Lyra Dawnbringer", 0.26, 9),                                       # not low enough
            row("Demonic Tutor", 0.0, 4),                                           # too few draws
            row("Helm of the Host", 0.9, 10, 9, 4.2),                               # workhorse
            row("Sol Ring", 0.7, 6, 4, 1.5),                                        # boundary workhorse
            row("Vilis, Broker of Blood", 0.2, 6),                                  # pet, surprise
            row("Kaalia of the Vast", 0.9, 10, 9, 5.0),                             # commander ignored
            row("Not In Deck", 0.0, 20),                                            # unmatched
        ],
        "games_log": [
            {"game": 1, "opponents": [{"seat": 2, "deck": "Deck A", "ai": "Reckless"}, {"seat": 3, "deck": "Deck B", "ai": "Default"},
                                      {"seat": 4, "deck": "Deck C", "ai": "Cautious"}],
             "winner": "Holy Hellfire", "user_won": True, "total_turns": 20, "win_reason": "all opponents have lost"},
            {"game": 2, "opponents": [{"seat": 2, "deck": "Deck A", "ai": "Reckless"}, {"seat": 3, "deck": "Deck D", "ai": "Default"},
                                      {"seat": 4, "deck": "Deck C", "ai": "Cautious"}],
             "winner": "Deck D", "user_won": False, "total_turns": 30, "win_reason": "commander damage"},
        ],
    }


class FakeRedis:
    def __init__(self):
        self.kv = {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, **kw):
        self.kv[k] = v
        return True

    def delete(self, *keys):
        return sum(self.kv.pop(k, None) is not None for k in keys)


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body):
    return urllib.error.HTTPError("http://x", code, "err", {}, io.BytesIO(json.dumps(body).encode()))


class TestDckExport(unittest.TestCase):
    def test_commander_dedup_1_plus_99(self):
        name, cmdr, dck = sc.export_deck_to_dck(make_deck())
        self.assertEqual((name, cmdr), ("Holy Hellfire", "Kaalia of the Vast"))
        self.assertIn("[Commander]\n1 Kaalia of the Vast\n[Main]", dck)
        main = dck.split("[Main]\n")[1].strip().splitlines()
        self.assertEqual(sum(int(l.split(" ", 1)[0]) for l in main), 99)
        self.assertNotIn("Kaalia", "\n".join(main))
        self.assertIn("12 Plains", main)

    def test_commander_missing_from_cards(self):
        d = make_deck()
        d["cards"] = [c for c in d["cards"] if c["name"] != "Kaalia of the Vast"]
        _, _, dck = sc.export_deck_to_dck(d)
        self.assertEqual(dck.count("Kaalia of the Vast"), 1)

    def test_dfc_front_face_but_split_kept(self):
        _, _, dck = sc.export_deck_to_dck(make_deck())
        self.assertIn("1 Archangel Avacyn\n", dck)
        self.assertIn("1 Bonecrusher Giant\n", dck)
        self.assertIn("1 Fire // Ice\n", dck)
        self.assertNotIn("Avacyn, the Purifier", dck)

    def test_layout_split_and_dfc_commander(self):
        self.assertEqual(sc.normalize_card_name("Foo // Bar", {"layout": "split"}), "Foo // Bar")
        self.assertEqual(sc.normalize_card_name("Foo // Bar", {"layout": "transform"}), "Foo")
        d = make_deck()
        d["commanders"] = ["Edgar Markov // Back"]
        d["cards"] = [c for c in d["cards"] if c["name"] != "Kaalia of the Vast"] + [{"name": "Edgar Markov // Back", "quantity": 1}]
        name, cmdr, dck = sc.export_deck_to_dck(d)
        self.assertEqual(cmdr, "Edgar Markov")
        self.assertEqual(dck.count("Edgar Markov"), 1)

    def test_wrong_size_and_no_commander_raise(self):
        with self.assertRaises(ValueError):
            sc.export_deck_to_dck(make_deck([{"name": "Extra", "quantity": 1}]))
        d = make_deck()
        d["commanders"] = []
        with self.assertRaises(ValueError):
            sc.export_deck_to_dck(d)


class TestSimClient(unittest.TestCase):
    def test_default_url_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FORGE_SIM_URL", None)
            self.assertEqual(sc.SimClient().base_url, "http://mtg-forge:8765")
        with patch.dict(os.environ, {"FORGE_SIM_URL": "http://h:1/"}):
            self.assertEqual(sc.SimClient().base_url, "http://h:1")

    def test_endpoints(self):
        calls = []

        def fake(req, timeout=None):
            calls.append((req.get_method(), req.full_url, req.data))
            return FakeResp(json.dumps({"ok": True, "job_id": "j1"}).encode())

        c = sc.SimClient("http://d:8765")
        with patch("urllib.request.urlopen", fake):
            self.assertTrue(c.health()["ok"])
            c.list_opponents()
            c.start_job({"deck_id": 1})
            c.get_job("j1")
            self.assertTrue(c.is_up())
        self.assertEqual([(m, u) for m, u, _ in calls[:4]],
                         [("GET", "http://d:8765/health"), ("GET", "http://d:8765/opponents"),
                          ("POST", "http://d:8765/jobs"), ("GET", "http://d:8765/jobs/j1")])
        self.assertEqual(json.loads(calls[2][2]), {"deck_id": 1})

    def test_unreachable(self):
        def boom(req, timeout=None):
            raise urllib.error.URLError("refused")

        c = sc.SimClient("http://d:8765")
        with patch("urllib.request.urlopen", boom):
            with self.assertRaises(sc.SimUnavailable):
                c.health()
            self.assertFalse(c.is_up())
        with patch("urllib.request.urlopen", lambda r, timeout=None: (_ for _ in ()).throw(TimeoutError("t"))):
            with self.assertRaises(sc.SimUnavailable):
                c.get_job("x")

    def test_busy_and_http_errors_and_bad_json(self):
        c = sc.SimClient("http://d:8765")
        with patch("urllib.request.urlopen", lambda r, timeout=None: (_ for _ in ()).throw(http_error(409, {"error": "busy", "job_id": "abc"}))):
            with self.assertRaises(sc.SimBusy) as cm:
                c.start_job({})
            self.assertEqual(cm.exception.job_id, "abc")
        with patch("urllib.request.urlopen", lambda r, timeout=None: (_ for _ in ()).throw(http_error(400, {"error": "bad deck"}))):
            with self.assertRaises(sc.SimError) as cm:
                c.start_job({})
            self.assertEqual(cm.exception.status, 400)
            self.assertIn("bad deck", str(cm.exception))
        with patch("urllib.request.urlopen", lambda r, timeout=None: FakeResp(b"<html>")):
            with self.assertRaises(sc.SimError):
                c.health()

    def test_build_job_payload(self):
        p = sc.build_job_payload(make_deck(), games=5, opponents=["a", "b", "c"], opponent_mode="specific")
        self.assertEqual((p["deck_id"], p["games"], p["commander"], p["opponent_mode"]), (417707, 5, "Kaalia of the Vast", "specific"))
        self.assertEqual(p["opponents"], ["a", "b", "c"])
        self.assertIn("[Commander]", p["dck_text"])


class TestSimStore(unittest.TestCase):
    def done_job(self, jid="j1", win_rate=0.5):
        return {"job_id": jid, "status": "done", "report": {**make_report(), "summary": {"games": 2, "win_rate": win_rate}}}

    def test_save_load_not_stale(self):
        r, deck = FakeRedis(), make_deck()
        doc = ss.save_completed(r, deck, self.done_job())
        self.assertIn("deck:sim:archidekt:417707", r.kv)
        got = ss.load_latest(r, deck)
        self.assertEqual(got["meta"]["job_id"], "j1")
        self.assertEqual(got["meta"]["games"], 2)
        self.assertFalse(got["meta"]["stale"])
        self.assertEqual(got["report"]["summary"]["win_rate"], 0.5)
        self.assertEqual(doc["meta"]["fingerprint"], rm.fingerprint(deck))

    def test_none_when_absent(self):
        self.assertIsNone(ss.load_latest(FakeRedis(), make_deck()))

    def test_new_job_start_keeps_old_report(self):
        r, deck = FakeRedis(), make_deck()
        ss.save_completed(r, deck, self.done_job("old", 0.5))
        before = r.kv["deck:sim:archidekt:417707"]
        ss.record_job_started(r, deck, {"job_id": "new", "status": "running"})
        self.assertEqual(r.kv["deck:sim:archidekt:417707"], before)
        self.assertEqual(ss.load_job(r, deck)["job_id"], "new")
        for st in ("running", "error", "failed", "cancelled"):
            self.assertIsNone(ss.save_completed(r, deck, {"job_id": "new", "status": st, "report": {"x": 1}, "error": "boom"}))
            self.assertEqual(r.kv["deck:sim:archidekt:417707"], before)
        self.assertEqual(ss.load_job(r, deck)["status"], "cancelled")
        self.assertEqual(ss.load_latest(r, deck)["meta"]["job_id"], "old")
        ss.save_completed(r, deck, self.done_job("new", 0.9))
        self.assertEqual(ss.load_latest(r, deck)["meta"]["job_id"], "new")
        self.assertEqual(ss.load_job(r, deck)["status"], "done")

    def test_done_without_report_does_not_overwrite(self):
        r, deck = FakeRedis(), make_deck()
        ss.save_completed(r, deck, self.done_job("old"))
        self.assertIsNone(ss.save_completed(r, deck, {"job_id": "n", "status": "done", "report": None}))
        self.assertEqual(ss.load_latest(r, deck)["meta"]["job_id"], "old")

    def test_stale_on_decklist_change_and_recovers(self):
        r, deck = FakeRedis(), make_deck()
        ss.save_completed(r, deck, self.done_job())
        changed = copy.deepcopy(deck)
        changed["cards"][3]["name"] = "Swapped Card"
        got = ss.load_latest(r, changed)
        self.assertTrue(got["meta"]["stale"])
        self.assertEqual(got["meta"]["stale_reason"], "Decklist changed since this simulation was run")
        self.assertTrue(json.loads(r.kv["deck:sim:archidekt:417707"])["meta"]["stale"])  # persisted
        back = ss.load_latest(r, deck)
        self.assertFalse(back["meta"]["stale"])
        self.assertIsNone(back["meta"]["stale_reason"])

    def test_quantity_change_is_stale(self):
        r, deck = FakeRedis(), make_deck()
        ss.save_completed(r, deck, self.done_job())
        changed = copy.deepcopy(deck)
        changed["cards"][0]["quantity"] = 11
        self.assertTrue(ss.load_latest(r, changed)["meta"]["stale"])


class TestSimDiff(unittest.TestCase):
    def setUp(self):
        self.out = sd.build_sim_comparison(make_deck(), make_report(), make_review())

    def names(self, key):
        return [c["name"] for c in self.out["card_comparison"][key]]

    def test_confirmed_stragglers(self):
        self.assertEqual(self.names("confirmed_stragglers"), ["Strionic Resonator", "Fire // Ice"])
        self.assertEqual(self.out["card_comparison"]["confirmed_stragglers"][0]["straggler_score"], 0.8)
        self.assertIn("Isshin, Two Heavens as One", self.out["card_comparison"]["static_stragglers_not_confirmed"])

    def test_surprise_dead_draws(self):
        cc = self.out["card_comparison"]
        self.assertEqual(self.names("surprise_dead_draws"), ["Burning-Rune Demon", "Vilis, Broker of Blood", "Bloodthirster"])
        by = {c["name"]: c for c in cc["surprise_dead_draws"]}
        self.assertTrue(by["Burning-Rune Demon"]["static_strong"])
        self.assertTrue(by["Vilis, Broker of Blood"]["pet"])
        self.assertNotIn("Demonic Tutor", by)      # < 5 draws
        self.assertNotIn("Lyra Dawnbringer", by)   # 0.26 > 0.25

    def test_workhorses_and_unmatched(self):
        cc = self.out["card_comparison"]
        self.assertEqual(self.names("confirmed_workhorses"), ["Helm of the Host", "Sol Ring"])
        self.assertEqual(cc["confirmed_workhorses"][0]["avg_turn_cast"], 4.2)
        self.assertTrue(cc["confirmed_workhorses"][0]["static_strong"])
        self.assertEqual(cc["unmatched_cards"], ["Not In Deck"])
        self.assertNotIn("Kaalia of the Vast", self.names("confirmed_workhorses"))

    def test_tempo(self):
        t = self.out["tempo_comparison"]
        self.assertEqual(t["static"]["lands"], 35)
        self.assertEqual(t["static"]["commander_cmc"], 3)
        self.assertEqual(t["static"]["expected_lands_t4"], round(10 * 35 / 99, 2))
        self.assertEqual(t["static"]["expected_lands_t5"], round(11 * 35 / 99, 2))
        self.assertEqual([p["turn"] for p in t["land_drops"]], [1, 2, 3, 4, 5, 6])
        t4 = t["land_drops"][3]
        self.assertEqual(t4["sim_land_drop_rate"], 0.6)
        self.assertEqual(t4["delta"], round(0.6 - t4["static_land_drop_probability"], 4))
        self.assertEqual(t["sim"]["t5_mana_starvation_rate"], 0.35)
        self.assertEqual(t["deltas"]["commander_turn_vs_cmc"], 2.5)
        self.assertAlmostEqual(t["static"]["mana_utilization_t2_t6"], 1 - 2.5 / 20, places=4)
        self.assertAlmostEqual(t["static"]["t5_starvation_probability"], 0.4078, places=3)
        self.assertAlmostEqual(t["deltas"]["t5_mana_starvation"], 0.35 - 0.4078, places=3)
        self.assertFalse(any("starvation" in n for n in t["notes"]))  # sim is below the static expectation
        self.assertTrue(any("mana use" in n for n in t["notes"]))
        self.assertTrue(any("Commander" in n for n in t["notes"]))

    def test_pod_breakdown(self):
        p = self.out["pod_breakdown"]
        self.assertEqual((p["games_played"], p["user_wins"], p["win_rate"], p["avg_total_turns"]), (2, 1, 0.5, 25.0))
        self.assertEqual(p["opponent_mode"], "random")
        self.assertEqual(p["by_opponent_deck"]["Deck A"], {"games": 2, "wins": 1, "win_rate": 0.5})
        self.assertEqual(p["by_opponent_deck"]["Deck D"]["wins"], 0)
        self.assertEqual(p["by_opponent_ai"]["Reckless"]["games"], 2)
        self.assertEqual(p["win_reasons"], {"all opponents have lost": 1, "commander damage": 1})
        self.assertEqual(p["games"][1]["winner"], "Deck D")

    def test_deterministic(self):
        again = sd.build_sim_comparison(make_deck(), make_report(), make_review())
        self.assertEqual(json.dumps(self.out, sort_keys=True), json.dumps(again, sort_keys=True))

    def test_accepts_review_doc_and_saved_sim_doc(self):
        doc = {"sections": [{"type": k, "data": v} for k, v in make_review().items()]}
        saved = {"meta": {}, "report": make_report()}
        self.assertEqual(sd.build_sim_comparison(make_deck(), saved, doc), self.out)

    def test_daemon_report_shape(self):
        rep = {"curve_tempo_metrics": {"land_drop_rate_t1_t6": {"T1": 1.0, "T6": 0.5}, "mana_starvation_rate_t5": 0.1,
                                       "commander_first_cast_mean_turn": 4.0, "mana_utilization_ratio_t2_t6": 0.5},
               "card_diagnostics": {"expected_draws_per_card": 10.9,
                                    "dead_draw_stragglers": [{"card": "Strionic Resonator", "utilization_ratio": 0.0, "games_cast": 0}],
                                    "top_performers": [{"card": "Helm of the Host", "utilization_ratio": 1.2, "games_cast": 12}]},
               "games_log": []}
        out = sd.build_sim_comparison(make_deck(), rep, make_review())
        self.assertEqual(out["tempo_comparison"]["land_drops"][5]["sim_land_drop_rate"], 0.5)
        self.assertEqual(out["tempo_comparison"]["sim"]["avg_first_commander_turn"], 4.0)
        cs = out["card_comparison"]["confirmed_stragglers"]
        self.assertEqual([c["name"] for c in cs], ["Strionic Resonator"])
        self.assertTrue(cs[0]["games_drawn_estimated"])
        self.assertEqual(out["pod_breakdown"]["games_played"], 0)
        self.assertIsNone(out["pod_breakdown"]["win_rate"])

    def test_empty_inputs_do_not_crash(self):
        out = sd.build_sim_comparison(make_deck(), {}, None)
        self.assertEqual(out["card_comparison"]["confirmed_stragglers"], [])
        self.assertIsNone(out["tempo_comparison"]["sim"]["t5_mana_starvation_rate"])


if __name__ == "__main__":
    unittest.main()
