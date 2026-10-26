"""RV-3: saved Deep Review doc, oracle prose validation, rebuild + stale marking."""
import copy
import json
import os
import sys
import time
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from src.server.domain.analytics import review_doc as rd
from src.server.domain.analytics import review_metrics as rm
from src.server.domain.analytics.oracle_client import OracleUnavailable
from src.server.app.main import app
from tests.test_review_metrics import make_deck, make_inputs


class FakeRedis:
    def __init__(self):
        self.kv, self.z = {}, {}

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v, **kw):
        self.kv[k] = v
        return True

    def delete(self, *ks):
        for k in ks:
            self.kv.pop(k, None)

    def zadd(self, k, m):
        self.z.setdefault(k, {}).update(m)

    def zrevrange(self, k, a, b):
        items = [m for m, _ in sorted(self.z.get(k, {}).items(), key=lambda kv: (-kv[1], kv[0]))]
        return items[a:] if b == -1 else items[a:b + 1]

    def zrem(self, k, m):
        self.z.get(k, {}).pop(m, None)

    def zcard(self, k):
        return len(self.z.get(k, {}))


class FakeOracle:
    def __init__(self, reply=None, fail=False):
        self.reply, self.fail, self.calls = reply, fail, []

    def chat(self, messages, **kw):
        self.calls.append(messages)
        if self.fail:
            raise OracleUnavailable("down")
        return self.reply(messages) if callable(self.reply) else self.reply

    def models(self):
        if self.fail:
            raise OracleUnavailable("down")
        return ["oracle"]


def sections(**over):
    s = {k: f"Prose for {k}." for k in rd.SECTION_ORDER}
    s.update(over)
    return s


def reply_for(fp, secs=None, **over):
    body = {"schema_version": "1", "fingerprint": fp, "sections": secs or sections()}
    body.update(over)
    return "Here you go.\n```json\n" + json.dumps(body) + "\n```"


METRICS = rm.compute_review(make_inputs())
FP = METRICS["fingerprint"]
RID = "archidekt:1"


def run(r, client, deck=None, metrics=None):
    return rd.build_review(r, deck or make_deck(), lambda: client, metrics=copy.deepcopy(metrics or METRICS))


class TestSchema(unittest.TestCase):
    def test_valid_reply_parses(self):
        p = rd.validate_prose(rd.parse_reply(reply_for(FP)), FP)
        self.assertEqual(p.sections.genre, "Prose for genre.")

    def test_rejects_missing_section_extra_keys_and_bad_version(self):
        secs = sections()
        del secs["focus"]
        with self.assertRaises(rd.ReviewParseError):
            rd.validate_prose(rd.parse_reply(reply_for(FP, secs)), FP)
        with self.assertRaises(rd.ReviewParseError):  # oracle may not add labels / picks of its own
            rd.validate_prose(rd.parse_reply(reply_for(FP, picks=["Sol Ring"])), FP)
        with self.assertRaises(rd.ReviewParseError):
            rd.validate_prose(rd.parse_reply(reply_for(FP, sections(extra_pick="x"))), FP)
        with self.assertRaises(rd.ReviewParseError):
            rd.validate_prose(rd.parse_reply(reply_for(FP, schema_version="2")), FP)

    def test_rejects_wrong_fingerprint_and_no_json(self):
        with self.assertRaises(rd.ReviewParseError):
            rd.validate_prose(rd.parse_reply(reply_for("deadbeef")), FP)
        with self.assertRaises(rd.ReviewParseError):
            rd.parse_reply("no block here")
        with self.assertRaises(rd.ReviewParseError):
            rd.parse_reply("```json\n[1]\n```")

    def test_schema_file_matches_model(self):
        path = os.path.join(os.path.dirname(__file__), "..", "docs", "oracle", "deck-review", "references",
                            "review.schema.json")
        schema = json.load(open(path))
        self.assertEqual(set(schema["properties"]["sections"]["required"]), set(rd.SECTION_ORDER))
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["sections"]["additionalProperties"])


class TestNumberGuard(unittest.TestCase):
    def test_strips_invented_numbers_keeps_metric_numbers(self):
        allowed = rd.allowed_numbers({"a": 0.8123, "b": 36, "c": {"x": 2.5}})
        text = "The deck runs 36 lands. It hits 81% on curve. It has a 93% win rate. Turn 4 matters. Sol Ring 2 helps."
        clean, dropped = rd.strip_invented_numbers(text, allowed, ["Sol Ring 2"])
        self.assertEqual(dropped, 1)
        self.assertIn("36 lands", clean)
        self.assertIn("81%", clean)
        self.assertNotIn("93", clean)
        self.assertIn("Turn 4", clean)

    def test_invented_number_in_saved_prose_is_stripped(self):
        r = FakeRedis()
        secs = sections(genre="Solid aristocrats core. It wins 97 percent of games.")
        doc = run(r, FakeOracle(reply_for(FP, secs)))
        g = next(s for s in doc["sections"] if s["type"] == "genre")
        self.assertEqual(g["prose"], "Solid aristocrats core.")
        summ = doc["sections"][-1]["data"]
        self.assertEqual(summ["status"], "ok")
        self.assertEqual(summ["stripped_sentences"], 1)

    def test_summary_all_invented_is_invalid(self):
        r = FakeRedis()
        doc = run(r, FakeOracle(reply_for(FP, sections(oracle_summary="Rated 9999 out of 10000."))))
        self.assertEqual(doc["sections"][-1]["data"]["status"], "invalid")


class TestBuild(unittest.TestCase):
    def test_doc_shape(self):
        r = FakeRedis()
        doc = run(r, FakeOracle(reply_for(FP)))
        for k in ("schema_version", "deck_id", "fingerprint", "genre_registry_version", "created_at", "sections"):
            self.assertIn(k, doc)
        self.assertEqual([s["type"] for s in doc["sections"]], list(rd.SECTION_ORDER))
        self.assertEqual(doc["deck_id"], RID)
        self.assertEqual(doc["fingerprint"], FP)
        self.assertEqual(doc["genre_registry_version"], 7)
        self.assertFalse(doc["stale"])
        self.assertEqual(rd.load_latest(r, RID)["doc_id"], doc["doc_id"])
        self.assertEqual([h["doc_id"] for h in rd.history(r, RID)], [doc["doc_id"]])

    def test_oracle_only_gets_metrics_and_journal(self):
        r = FakeRedis()
        o = FakeOracle(reply_for(FP))
        run(r, o)
        user = o.calls[0][1]["content"]
        self.assertIn(FP, user)
        self.assertIn('"metrics"', user)
        self.assertIn('"journal"', user)

    def test_two_rebuilds_identical_math(self):
        r = FakeRedis()
        a = run(r, FakeOracle(reply_for(FP)))
        time.sleep(0.01)
        b = run(r, FakeOracle(reply_for(FP, sections(genre="Different wording."))))
        self.assertNotEqual(a["doc_id"], b["doc_id"])
        self.assertEqual(json.dumps(rd.math_sections(a), sort_keys=True), json.dumps(rd.math_sections(b), sort_keys=True))
        self.assertEqual(len(rd.history(r, RID)), 2)

    def test_math_sections_are_metrics_verbatim(self):
        doc = run(FakeRedis(), FakeOracle(reply_for(FP)))
        for s in doc["sections"][:-1]:
            self.assertEqual(s["data"], METRICS[s["type"]])

    def test_history_is_bounded(self):
        r = FakeRedis()
        for i in range(rd.HISTORY_KEEP + 3):
            d = rd.build_doc(RID, METRICS, None, oracle_status="offline", created_at=f"2026-01-01T00:00:{i:02d}+00:00")
            rd.save(r, d)
        self.assertEqual(len(r.z[rd.index_key(RID)]), rd.HISTORY_KEEP)


class TestOfflineFallback(unittest.TestCase):
    def test_offline_saves_math_and_note(self):
        r = FakeRedis()
        doc = run(r, FakeOracle(fail=True))
        summ = doc["sections"][-1]
        self.assertEqual(summ["data"]["status"], "offline")
        self.assertIn("offline", summ["data"]["text"].lower())
        self.assertEqual(summ["data"]["note"], "down")
        self.assertTrue(all(s["prose"] is None for s in doc["sections"]))
        self.assertEqual([s["data"] for s in doc["sections"][:-1]], [METRICS[k] for k in rd.MATH_SECTIONS])
        self.assertIsNotNone(rd.load_latest(r, RID))

    def test_invalid_reply_saves_math_and_retry_replaces(self):
        r = FakeRedis()
        bad = run(r, FakeOracle("garbage"))
        self.assertEqual(bad["sections"][-1]["data"]["status"], "invalid")
        time.sleep(0.01)
        good = run(r, FakeOracle(reply_for(FP)))
        self.assertEqual(rd.load_latest(r, RID)["doc_id"], good["doc_id"])
        self.assertEqual(good["sections"][-1]["data"]["status"], "ok")
        self.assertEqual(json.dumps(rd.math_sections(bad)), json.dumps(rd.math_sections(good)))

    def test_not_configured_is_offline(self):
        from src.server.domain.analytics.oracle_client import OracleNotConfigured

        def boom():
            raise OracleNotConfigured("ORACLE_URL not set")

        doc = rd.build_review(FakeRedis(), make_deck(), boom, metrics=copy.deepcopy(METRICS))
        self.assertEqual(doc["sections"][-1]["data"]["status"], "offline")


class TestStale(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck = make_deck()
        run(self.r, FakeOracle(reply_for(FP)), self.deck)

    def patched(self, version=7):
        return patch("src.server.domain.analytics.genres.get_version", return_value=version)

    def test_unchanged_deck_is_fresh(self):
        with self.patched():
            doc = rd.refresh_stale(self.r, self.deck)
        self.assertFalse(doc["stale"])
        self.assertIsNone(doc["stale_reason"])

    def test_changed_fingerprint_marks_stale_and_persists(self):
        deck = make_deck()
        deck["cards"][0]["quantity"] = 2
        with self.patched():
            rd.mark_stale_on_sync(self.r, deck)
        latest = rd.load_latest(self.r, RID)
        self.assertTrue(latest["stale"])
        self.assertIn("decklist changed", latest["stale_reason"])
        self.assertTrue(rd.load(self.r, RID, latest["doc_id"])["stale"])

    def test_genre_version_change_marks_stale(self):
        with self.patched(8):
            doc = rd.refresh_stale(self.r, self.deck)
        self.assertTrue(doc["stale"])
        self.assertIn("genre registry changed (v7 -> v8)", doc["stale_reason"])

    def test_reverting_clears_stale(self):
        changed = make_deck()
        changed["cards"][0]["quantity"] = 2
        with self.patched():
            rd.refresh_stale(self.r, changed)
            doc = rd.refresh_stale(self.r, self.deck)
        self.assertFalse(doc["stale"])

    def test_no_doc_is_noop(self):
        with self.patched():
            self.assertIsNone(rd.refresh_stale(FakeRedis(), self.deck))
            rd.mark_stale_on_sync(FakeRedis(), self.deck)

    def test_registry_update_deck_cards_marks_stale(self):
        import src.server.domain.decks.registry as registry

        deck = make_deck()
        deck.update(source="archidekt", source_id="1")
        new_cards = [dict(c) for c in deck["cards"]] + [{"name": "Extra Card", "quantity": 1}]
        with patch.object(registry, "_load_decks", return_value=[deck]), \
                patch.object(registry, "_save_decks"), self.patched():
            registry.update_deck_cards(self.r, RID, new_cards)
        self.assertTrue(rd.load_latest(self.r, RID)["stale"])


class TestApi(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck = make_deck()
        self.client = TestClient(app)
        self.patches = [patch("src.server.app.main.r", self.r), patch("src.server.app.main._find_deck_any", return_value=self.deck),
                        patch("src.server.domain.analytics.genres.get_version", return_value=7)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_get_without_doc_never_builds(self):
        with patch.object(rm, "review_deck") as metrics, patch("src.server.app.main._oracle_client") as oc, \
                patch.object(rd, "build_review") as build, patch.object(rd, "start_job") as sj:
            res = self.client.get(f"/api/decks/{RID}/review")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json(), {"success": True, "exists": False, "review": None})
            metrics.assert_not_called()
            oc.assert_not_called()
            build.assert_not_called()
            sj.assert_not_called()

    def test_get_with_stale_doc_never_rebuilds_but_flags(self):
        run(self.r, FakeOracle(reply_for(FP)))
        self.deck["cards"][0]["quantity"] = 3
        with patch.object(rm, "review_deck") as metrics, patch.object(rd, "build_review") as build:
            body = self.client.get(f"/api/decks/{RID}/review").json()
            metrics.assert_not_called()
            build.assert_not_called()
        self.assertTrue(body["review"]["stale"])
        self.assertEqual(body["review"]["fingerprint"], FP)
        self.assertIsNone(body["review"].get("error"))

    def test_unknown_deck_404(self):
        with patch("src.server.app.main._find_deck_any", return_value=None):
            self.assertEqual(self.client.get("/api/decks/nope/review").status_code, 404)
            self.assertEqual(self.client.post("/api/decks/nope/review/rebuild").status_code, 404)

    def test_rebuild_job_end_to_end(self):
        oracle = FakeOracle(lambda m: reply_for(rm.fingerprint(self.deck)))
        with patch("src.server.app.main._oracle_client", return_value=oracle), \
                patch.object(rm, "review_deck", side_effect=lambda r, d, *a, **k: copy.deepcopy(METRICS)):
            res = self.client.post(f"/api/decks/{RID}/review/rebuild")
            self.assertEqual(res.status_code, 202)
            job_id = res.json()["job_id"]
            for _ in range(100):
                job = self.client.get(f"/api/decks/{RID}/review/jobs/{job_id}").json()["job"]
                if job["status"] in ("done", "failed"):
                    break
                time.sleep(0.05)
        self.assertEqual(job["status"], "done", job)
        self.assertEqual(job["oracle_status"], "ok")
        got = self.client.get(f"/api/decks/{RID}/review").json()
        self.assertTrue(got["exists"])
        self.assertEqual(got["review"]["doc_id"], job["doc_id"])
        self.assertEqual(self.client.get(f"/api/decks/{RID}/review/history").json()["history"][0]["doc_id"], job["doc_id"])
        self.assertEqual(self.client.get(f"/api/decks/{RID}/review/jobs/zzz").status_code, 404)

    def test_rebuild_with_oracle_down_still_saves(self):
        with patch("src.server.app.main._oracle_client", return_value=FakeOracle(fail=True)), \
                patch.object(rm, "review_deck", side_effect=lambda r, d, *a, **k: copy.deepcopy(METRICS)):
            job_id = self.client.post(f"/api/decks/{RID}/review/rebuild").json()["job_id"]
            for _ in range(100):
                job = self.client.get(f"/api/decks/{RID}/review/jobs/{job_id}").json()["job"]
                if job["status"] in ("done", "failed"):
                    break
                time.sleep(0.05)
        self.assertEqual((job["status"], job["oracle_status"]), ("done", "offline"))
        self.assertEqual(self.client.get(f"/api/decks/{RID}/review").json()["review"]["sections"][-1]["data"]["status"], "offline")

    def test_second_rebuild_while_running_is_409(self):
        rd.reserve(RID, "busyjob")
        try:
            res = self.client.post(f"/api/decks/{RID}/review/rebuild")
        finally:
            rd.release(RID, "busyjob")
        self.assertEqual(res.status_code, 409)


if __name__ == "__main__":
    unittest.main()
