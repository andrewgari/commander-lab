"""Deck reviewer card 11: review-document jobs, history, PDF export, print token, swap feedback and the card-image cache.

Everything runs offline on in-memory SQLite: the pipeline is a scripted runner, the PDF container and Scryfall are fakes.
"""
from __future__ import annotations

import copy
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.app.api import deps, reviews
from src.server.app.main import app
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import Base, ReviewDocumentRow
from src.server.infra.services import card_images, review_document_service as docs, review_pdf

EXAMPLE = json.loads((Path(__file__).parent / "fixtures" / "review_document" / "example.json").read_text())
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def make_factory():
    eng = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


def scripted_runner(doc=None, *, fail: str | None = None, seen: list | None = None):
    def runner(deck_id, progress):
        progress("Analysing the deck (Pass B)")
        if seen is not None:
            seen.append(deck_id)
        if fail:
            raise docs.JobFailed(fail)
        out = copy.deepcopy(doc or EXAMPLE)
        out["meta"]["deck_id"] = deck_id
        return docs.RunnerResult(out)
    return runner


class ApiCase(unittest.TestCase):
    def setUp(self):
        self.factory = make_factory()
        with self.factory() as s:
            repo.upsert_deck(s, {"id": "deck-1", "name": "Atraxa Counters", "commanders": ["Atraxa, Praetors' Voice"]})
            s.commit()
        self.launched: list[int] = []
        self.runner = scripted_runner()

        def db():
            session = self.factory()
            try:
                yield session
                session.commit()
            finally:
                session.close()

        def launcher():
            def launch(document_id):                 # synchronous: the worker body runs inline for determinism
                self.launched.append(document_id)
                docs.run_job(document_id, session_factory=self.factory, runner=self.runner)
            return launch

        app.dependency_overrides[deps.db_session] = db
        app.dependency_overrides[reviews.job_launcher] = launcher
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def generate(self, deck="deck-1"):
        return self.client.post(f"/api/v2/decks/{deck}/review-documents")


class GenerateTests(ApiCase):
    def test_generate_returns_202_then_the_stored_document(self):
        r = self.generate()
        self.assertEqual(r.status_code, 202, r.text)
        first = r.json()
        self.assertEqual(first["deck_id"], "deck-1")
        self.assertFalse(first["already_running"])
        got = self.client.get(f"/api/v2/review-documents/{first['id']}").json()
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["doc"]["schema_version"], "review_document.v1")
        self.assertEqual(got["doc"]["meta"]["deck_id"], "deck-1")
        self.assertEqual(got["grade"], EXAMPLE["verdict"]["grade"])
        self.assertEqual(got["stage"], "")

    def test_the_request_does_not_run_the_pipeline_inline(self):
        """With the real launcher the POST must return while the worker is still running."""
        gate = __import__("threading").Event()

        def slow(deck_id, progress):
            progress("Analysing the deck (Pass B)")
            gate.wait(5)
            return docs.RunnerResult(copy.deepcopy(EXAMPLE))

        from concurrent.futures import ThreadPoolExecutor
        pool = ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown, wait=True)
        self.addCleanup(gate.set)

        def launcher():
            return lambda did: docs.start_job(did, session_factory=self.factory, runner=slow, executor=pool)

        app.dependency_overrides[reviews.job_launcher] = launcher
        started = time.monotonic()
        first = self.generate().json()
        self.assertLess(time.monotonic() - started, 2)
        status: dict = {}
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            status = self.client.get(f"/api/v2/review-documents/{first['id']}").json()
            if status["stage"] == "Analysing the deck (Pass B)":
                break
            time.sleep(0.02)
        self.assertEqual(status["status"], "running")
        self.assertEqual(status["stage"], "Analysing the deck (Pass B)")
        self.assertIsNone(status["doc"])
        again = self.generate().json()                    # one deck, one live run
        self.assertTrue(again["already_running"])
        self.assertEqual(again["id"], first["id"])
        gate.set()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and status["status"] != "ok":
            status = self.client.get(f"/api/v2/review-documents/{first['id']}").json()
            time.sleep(0.02)
        self.assertEqual(status["status"], "ok")

    def test_failure_is_recorded_with_its_reason(self):
        self.runner = scripted_runner(fail="no model provider is set up for 'x:y'")
        first = self.generate().json()
        got = self.client.get(f"/api/v2/review-documents/{first['id']}").json()
        self.assertEqual(got["status"], "failed")
        self.assertIn("no model provider", got["error"])
        self.assertIsNone(got["doc"])
        self.assertEqual(self.client.get("/api/v2/decks/deck-1/review-documents/latest").status_code, 404)
        # a failed run does not block the next one
        self.runner = scripted_runner()
        self.assertEqual(self.client.get(f"/api/v2/review-documents/{self.generate().json()['id']}").json()["status"], "ok")

    def test_unexpected_exception_is_recorded_not_raised(self):
        def boom(deck_id, progress):
            raise ZeroDivisionError("kaboom")
        self.runner = boom
        got = self.client.get(f"/api/v2/review-documents/{self.generate().json()['id']}").json()
        self.assertEqual(got["status"], "failed")
        self.assertIn("ZeroDivisionError", got["error"])

    def test_unknown_deck_is_404(self):
        r = self.generate("nope")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["error"], "deck_not_found")

    def test_a_running_row_from_a_dead_process_is_reported_failed(self):
        with self.factory() as s:
            s.add(ReviewDocumentRow(deck_id="deck-1", status="running", stage="Analysing"))
            s.commit()
        listing = self.client.get("/api/v2/decks/deck-1/review-documents").json()
        self.assertIsNone(listing["active"])
        self.assertEqual(listing["documents"][0]["status"], "failed")
        self.assertIn("interrupted", listing["documents"][0]["error"])
        self.assertFalse(self.generate().json()["already_running"])

    def test_history_is_newest_first_with_grade_and_headline(self):
        ids = [self.generate().json()["id"] for _ in range(3)]
        listing = self.client.get("/api/v2/decks/deck-1/review-documents").json()
        self.assertEqual([d["id"] for d in listing["documents"]], sorted(ids, reverse=True))
        self.assertEqual(listing["documents"][0]["headline"], EXAMPLE["verdict"]["headline"])
        self.assertNotIn("doc", listing["documents"][0])
        self.assertEqual(self.client.get("/api/v2/decks/deck-1/review-documents/latest").json()["id"], ids[-1])
        self.assertEqual(self.client.get("/api/v2/decks/nope/review-documents").status_code, 404)


class PdfTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.doc_id = self.generate().json()["id"]
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = card_images.CardImageCache(self.tmp.name, offline=True)
        p = mock.patch.object(card_images, "get_cache", return_value=self.cache)
        p.start()
        self.addCleanup(p.stop)

    def test_pdf_is_rendered_from_the_print_url_with_a_valid_token(self):
        seen = {}

        def fake_render(url, **_):
            seen["url"] = url
            return review_pdf.RenderedPdf(b"%PDF-1.7 fake", pages=3, missing_images=0)

        with mock.patch.object(review_pdf, "render", fake_render):
            r = self.client.get(f"/api/v2/review-documents/{self.doc_id}/pdf")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "application/pdf")
        self.assertTrue(r.content.startswith(b"%PDF"))
        self.assertIn("attachment", r.headers["content-disposition"])
        self.assertEqual(r.headers["x-pdf-pages"], "3")
        url = seen["url"]
        self.assertIn(f"doc={self.doc_id}", url)
        self.assertIn("print=1", url)
        token = url.split("token=")[1]
        ok = self.client.get(f"/api/v2/review-documents/{self.doc_id}/print-view", params={"token": token})
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["doc"]["schema_version"], "review_document.v1")

    def test_pdf_warms_the_image_cache_before_rendering(self):
        warmed = []
        with mock.patch.object(self.cache, "warm", side_effect=lambda names, size="small": warmed.extend(names) or {}), \
                mock.patch.object(review_pdf, "render", return_value=review_pdf.RenderedPdf(b"%PDF-1.7", 1, 0)):
            self.client.get(f"/api/v2/review-documents/{self.doc_id}/pdf")
        self.assertIn(EXAMPLE["swaps"][0]["add"]["name"], warmed)
        self.assertIn(EXAMPLE["meta"]["commanders"][0]["name"], warmed)

    def test_pdf_service_not_configured_is_503_with_a_reason(self):
        with mock.patch.dict("os.environ", {"PDF_SERVICE_URL": ""}):
            r = self.client.get(f"/api/v2/review-documents/{self.doc_id}/pdf")
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.json()["detail"]["error"], "pdf_unavailable")
        self.assertIn("PDF_SERVICE_URL", r.json()["detail"]["reason"])

    def test_pdf_of_an_unfinished_or_unknown_review(self):
        with self.factory() as s:
            s.add(ReviewDocumentRow(deck_id="deck-1", status="failed", error="x"))
            s.commit()
        failed_id = self.client.get("/api/v2/decks/deck-1/review-documents").json()["documents"][0]["id"]
        self.assertEqual(self.client.get(f"/api/v2/review-documents/{failed_id}/pdf").status_code, 409)
        self.assertEqual(self.client.get("/api/v2/review-documents/9999/pdf").status_code, 404)

    def test_print_view_rejects_bad_and_expired_tokens(self):
        url = f"/api/v2/review-documents/{self.doc_id}/print-view"
        self.assertEqual(self.client.get(url, params={"token": "garbage"}).status_code, 403)
        self.assertEqual(self.client.get(url).status_code, 422)
        expired = review_pdf.mint_token(self.doc_id, now=time.time() - 1000)
        self.assertEqual(self.client.get(url, params={"token": expired}).status_code, 403)
        other = review_pdf.mint_token(self.doc_id + 1)
        self.assertEqual(self.client.get(url, params={"token": other}).status_code, 403)

    def test_token_roundtrip_and_tamper(self):
        t = review_pdf.mint_token(7)
        self.assertTrue(review_pdf.verify_token(7, t))
        self.assertFalse(review_pdf.verify_token(8, t))
        exp, sig = t.split(".")
        self.assertFalse(review_pdf.verify_token(7, f"{int(exp) + 999}.{sig}"))

    def test_render_client_reports_failures(self):
        with self.assertRaises(review_pdf.PdfUnavailable):
            review_pdf.render("http://web/x", service_url="")
        transport = httpx.MockTransport(lambda req: httpx.Response(500, text="chromium crashed"))
        with self.assertRaises(review_pdf.PdfUnavailable) as ctx:
            review_pdf.render("http://web/x", service_url="http://pdf:8100", client=httpx.Client(transport=transport))
        self.assertEqual(ctx.exception.status, 502)
        ok = httpx.MockTransport(lambda req: httpx.Response(200, content=b"%PDF-1.7 x", headers={"X-Pdf-Pages": "4", "X-Missing-Images": "1"}))
        out = review_pdf.render("http://web/x", service_url="http://pdf:8100", client=httpx.Client(transport=ok))
        self.assertEqual((out.pages, out.missing_images), (4, 1))


class FeedbackTests(ApiCase):
    def setUp(self):
        super().setUp()
        self.doc_id = self.generate().json()["id"]
        self.url = f"/api/v2/review-documents/{self.doc_id}/feedback"
        self.swap = EXAMPLE["swaps"][0]

    def test_feedback_is_stored_against_the_swap_actually_shown(self):
        r = self.client.post(self.url, json={"swap_id": self.swap["id"], "decision": "rejected", "reason": "too pricey"})
        self.assertEqual(r.status_code, 200, r.text)
        fb = r.json()["feedback"][self.swap["id"]]
        self.assertEqual((fb["cut"], fb["add"]), (self.swap["cut"]["name"], self.swap["add"]["name"]))
        self.assertEqual((fb["decision"], fb["reason"]), ("rejected", "too pricey"))
        with self.factory() as s:                      # card 04's store
            from src.server.infra.services import player_profile_service as pps
            rows = pps.get_feedback(s, "andrew", "deck-1")
        self.assertEqual([(e.cut, e.add, e.decision, e.review_id) for e in rows],
                         [(self.swap["cut"]["name"], self.swap["add"]["name"], "rejected", str(self.doc_id))])

    def test_latest_decision_wins_and_survives_reload(self):
        for decision in ("maybe", "accepted"):
            self.client.post(self.url, json={"swap_id": self.swap["id"], "decision": decision})
        self.assertEqual(self.client.get(self.url).json()["feedback"][self.swap["id"]]["decision"], "accepted")

    def test_invalid_decision_and_unknown_swap(self):
        bad = self.client.post(self.url, json={"swap_id": self.swap["id"], "decision": "love-it"})
        self.assertEqual(bad.status_code, 422)
        gone = self.client.post(self.url, json={"swap_id": "sw-404", "decision": "accepted"})
        self.assertEqual(gone.status_code, 404)
        self.assertEqual(self.client.get(self.url).json()["feedback"], {})


class CardImageCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.calls: list[httpx.Request] = []
        self.now = [0.0]
        self.sleeps: list[float] = []
        self.handler = lambda req: httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})

    def cache(self, **kw):
        def transport(req):
            self.calls.append(req)
            return self.handler(req)

        def sleep(s):
            self.sleeps.append(s)
            self.now[0] += s

        kw.setdefault("offline", False)
        return card_images.CardImageCache(self.tmp.name, client=httpx.Client(transport=httpx.MockTransport(transport)),
                                          clock=lambda: self.now[0], sleep=sleep, **kw)

    def test_fetched_once_then_served_from_disk(self):
        c = self.cache()
        first = c.get("Sol Ring", "small")
        second = c.get("sol  ring", "small")
        self.assertEqual(first, second)
        self.assertEqual(first.read_bytes(), JPEG)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].url.params["exact"], "Sol Ring")
        self.assertEqual(self.calls[0].url.params["version"], "small")
        self.assertEqual(self.calls[0].url.params["format"], "image")
        c.get("Sol Ring", "normal")                       # another size is another image
        self.assertEqual(len(self.calls), 2)

    def test_a_new_process_reuses_the_disk_cache_without_network(self):
        self.cache().get("Sol Ring", "small")
        self.calls.clear()
        again = self.cache(offline=True)
        self.assertTrue(again.get("Sol Ring", "small").is_file())
        self.assertEqual(self.calls, [])

    def test_requests_are_spaced_for_scryfalls_two_per_second_limit_and_identify_us(self):
        c = self.cache()
        c.warm(["Sol Ring", "Arcane Signet", "Command Tower"], "small")
        self.assertEqual(len(self.calls), 3)
        self.assertGreaterEqual(self.now[0], 2 * card_images.MIN_INTERVAL - 1e-9)
        for req in self.calls:
            self.assertTrue(req.headers["user-agent"].startswith("commander-lab/"))
            self.assertIn("image/", req.headers["accept"])

    def test_429_pauses_all_fetches_for_the_lockout_period(self):
        self.handler = lambda req: httpx.Response(429, headers={"Retry-After": "45"})
        c = self.cache()
        with self.assertRaises(card_images.ImageUnavailable) as ctx:
            c.get("Sol Ring", "small")
        self.assertEqual(ctx.exception.status, 503)
        n = len(self.calls)
        with self.assertRaises(card_images.ImageUnavailable):
            c.get("Arcane Signet", "small")
        self.assertEqual(len(self.calls), n, "no Scryfall call while locked out")
        self.now[0] += 46
        self.handler = lambda req: httpx.Response(200, content=JPEG)
        self.assertTrue(c.get("Arcane Signet", "small").is_file())

    def test_unknown_card_is_404_and_remembered(self):
        self.handler = lambda req: httpx.Response(404, json={"object": "error"})
        c = self.cache()
        with self.assertRaises(card_images.ImageUnavailable) as ctx:
            c.get("Not A Card", "small")
        self.assertEqual(ctx.exception.status, 404)
        n = len(self.calls)
        self.assertEqual({r.url.params.get("exact") or r.url.params.get("fuzzy") for r in self.calls}, {"Not A Card"})
        with self.assertRaises(card_images.ImageUnavailable):
            c.get("Not A Card", "small")
        self.assertEqual(len(self.calls), n)

    def test_fuzzy_rescues_a_name_exact_misses(self):
        self.handler = lambda req: httpx.Response(404) if "exact" in req.url.params else httpx.Response(200, content=JPEG)
        self.assertTrue(self.cache().get("Delver of Secrets // Insectile Aberration", "small").is_file())

    def test_offline_mode_never_calls_scryfall(self):
        c = self.cache(offline=True)
        with self.assertRaises(card_images.ImageUnavailable) as ctx:
            c.get("Sol Ring", "small")
        self.assertEqual(ctx.exception.status, 503)
        self.assertEqual(self.calls, [])

    def test_bad_input_and_bad_bodies_are_rejected(self):
        c = self.cache()
        for name, size in (("", "small"), ("x" * 500, "small"), ("Sol Ring", "huge")):
            with self.assertRaises(card_images.ImageUnavailable) as ctx:
                c.get(name, size)
            self.assertEqual(ctx.exception.status, 422)
        self.handler = lambda req: httpx.Response(200, content=b"<html>not an image</html>")
        with self.assertRaises(card_images.ImageUnavailable):
            c.get("Sol Ring", "small")
        self.assertIsNone(c.cached("Sol Ring", "small"))

    def test_endpoint_serves_cached_jpeg_with_cache_headers(self):
        c = self.cache()
        with mock.patch.object(card_images, "get_cache", return_value=c):
            client = TestClient(app)
            ok = client.get("/api/v2/card-images", params={"name": "Sol Ring", "size": "small"})
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.headers["content-type"], "image/jpeg")
            self.assertIn("max-age", ok.headers["cache-control"])
            self.assertEqual(ok.content, JPEG)
            self.handler = lambda req: httpx.Response(404)
            missing = client.get("/api/v2/card-images", params={"name": "Nope", "size": "small"})
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json()["detail"]["error"], "image_unavailable")
            self.assertEqual(client.get("/api/v2/card-images", params={"name": "Sol Ring", "size": "huge"}).status_code, 422)


class BridgeAndRunnerTests(unittest.TestCase):
    def test_derive_verdict_grade_floors(self):
        from src.server.domain.analytics.review_pipeline.bridge import derive_verdict

        axes = lambda score: [{"score": score}]
        self.assertEqual([derive_verdict(axes(s), [])["grade"] for s in (9, 8.5, 7, 5.5, 4, 3.9)], list("AABCDF"))
        self.assertEqual(derive_verdict(axes(9), [{"confidence": 0.8}, {"confidence": 0.6}])["confidence"], 0.7)
        self.assertEqual(derive_verdict(axes(9), [])["confidence"], 0.5)

    def test_runner_names_the_missing_provider_before_any_model_call(self):
        from src.server.domain.analytics.review_pipeline import config as cfg

        runner = docs.LiveReviewRunner(make_factory(), env={})
        with self.assertRaises(docs.JobFailed) as ctx:
            runner._llm(cfg.PipelineConfig.from_env({}), "anthropic:claude-x")
        self.assertIn("no model provider is set up", str(ctx.exception))

    def test_runner_fails_for_a_missing_deck(self):
        runner = docs.LiveReviewRunner(make_factory(), env={})
        with self.assertRaises(docs.JobFailed) as ctx:
            runner(  "nope", lambda _s: None)
        self.assertIn("no deck", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
