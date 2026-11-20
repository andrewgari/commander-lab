"""Slice 3 tests: review pipeline v1 (ARCHITECTURE.md §6, §8 Slice 3).

Acceptance coverage:
- Rebuild a review twice on an unchanged deck -> identical facts (canonical
  fact pack + fingerprint), at both the domain and the service level.
- Every card name in the stored analysis resolves (verifier drops unknowns).
- LLM killed -> the review still builds and renders with "explanation offline".
- Document and dashboard panels show the same numbers (same fact pack).

unittest.TestCase style so CI's unittest discovery collects every case.
Runs on in-memory SQLite with stubbed providers — no Postgres, Redis,
Scryfall, EDHREC, Spellbook, or LLM network calls.
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.review import doc as doc_mod
from src.server.domain.review import fact_pack as fact_pack_mod
from src.server.domain.review import pipeline as pipeline_mod
from src.server.domain.review import verifier as verifier_mod
from src.server.domain.review import writer as writer_mod
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import Base
from src.server.infra.services import review_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CARDS = {
    "Atraxa, Praetors' Voice": {
        "oracle_id": "o-atraxa", "type_line": "Legendary Creature — Phyrexian Angel",
        "oracle_text": "Flying, vigilance, deathtouch, lifelink\nAt the beginning of your end step, proliferate.",
        "mana_cost": "{G}{W}{U}{B}", "cmc": 4.0,
        "colors": ["G", "W", "U", "B"], "color_identity": ["G", "W", "U", "B"],
        "keywords": ["Flying", "Vigilance", "Deathtouch", "Lifelink"],
    },
    "Sol Ring": {
        "oracle_id": "o-ring", "type_line": "Artifact",
        "oracle_text": "{T}: Add {C}{C}.", "mana_cost": "{1}", "cmc": 1.0,
        "colors": [], "color_identity": [], "keywords": [],
    },
    "Command Tower": {
        "oracle_id": "o-tower", "type_line": "Land",
        "oracle_text": "{T}: Add one mana of any color in your commander's color identity.",
        "mana_cost": "", "cmc": 0.0, "colors": [],
        "color_identity": ["G", "W", "U", "B"], "keywords": [],
    },
    "Forest": {
        "oracle_id": "o-forest", "type_line": "Basic Land — Forest",
        "oracle_text": "{T}: Add {G}.", "mana_cost": "", "cmc": 0.0,
        "colors": [], "color_identity": ["G"], "keywords": [],
    },
    "Llanowar Elves": {
        "oracle_id": "o-elves", "type_line": "Creature — Elf Druid",
        "oracle_text": "{T}: Add {G}.", "mana_cost": "{G}", "cmc": 1.0,
        "colors": ["G"], "color_identity": ["G"], "keywords": [],
    },
    "Wrath of God": {
        "oracle_id": "o-wrath", "type_line": "Sorcery",
        "oracle_text": "Destroy all creatures. They can't be regenerated.",
        "mana_cost": "{2}{W}{W}", "cmc": 4.0,
        "colors": ["W"], "color_identity": ["W"], "keywords": [],
    },
}

DECK = {
    "registry_id": "deck-1",
    "name": "Atraxa Test Deck",
    "commanders": ["Atraxa, Praetors' Voice"],
    "cards": [
        {"name": "Sol Ring", "quantity": 1},
        {"name": "Command Tower", "quantity": 1},
        {"name": "Forest", "quantity": 10},
        {"name": "Llanowar Elves", "quantity": 1},
        {"name": "Wrath of God", "quantity": 1},
    ],
}


def _texts():
    return {name: dict(facts) for name, facts in CARDS.items()}


def _resolve_all(name):
    """A Scryfall resolver that resolves every name as-is."""
    facts = CARDS.get(name) or {}
    return {"name": name, "oracle_id": facts.get("oracle_id"),
            "color_identity": facts.get("color_identity") or [], "source": "test"}


def _known_resolver(known):
    def resolve(name):
        if name in known:
            return {"name": known[name], "oracle_id": "o-x", "color_identity": []}
        return None
    return resolve


def make_factory():
    eng = create_engine("sqlite://", poolclass=StaticPool,
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


def seed_deck(session, deck_id="deck-1"):
    for name, facts in CARDS.items():
        repo.upsert_card(session, {"name": name, **facts})
    repo.upsert_deck(session, {
        "id": deck_id, "name": DECK["name"], "status": "physical",
        "color_identity": ["G", "W", "U", "B"], "commanders": DECK["commanders"],
        "source": "archidekt",
    })
    rows = []
    for entry in DECK["cards"]:
        facts = CARDS[entry["name"]]
        rows.append({"oracle_id": facts["oracle_id"], "qty": entry["quantity"],
                     "board": "main", "category": None})
    facts = CARDS["Atraxa, Praetors' Voice"]
    rows.append({"oracle_id": facts["oracle_id"], "qty": 1,
                 "board": "commander", "category": "Commander"})
    repo.replace_deck_cards(session, deck_id, rows)
    session.commit()


# ---------------------------------------------------------------------------
# 1. Fact-pack determinism (rebuild twice -> identical facts)
# ---------------------------------------------------------------------------

class TestFactPackDeterminism(unittest.TestCase):
    def test_fact_pack_identical_across_rebuilds(self):
        pack1 = fact_pack_mod.build_fact_pack(DECK, _texts())
        pack2 = fact_pack_mod.build_fact_pack(DECK, _texts())
        self.assertEqual(fact_pack_mod.canonical(pack1), fact_pack_mod.canonical(pack2))
        self.assertEqual(pack1["fingerprint"], pack2["fingerprint"])

    def test_pipeline_fact_pack_deterministic(self):
        kwargs = dict(deck_id="deck-1", writer=None, resolver=_resolve_all)
        r1 = pipeline_mod.run(DECK, _texts(), **kwargs)
        r2 = pipeline_mod.run(DECK, _texts(), **kwargs)
        self.assertEqual(fact_pack_mod.canonical(r1["fact_pack"]),
                         fact_pack_mod.canonical(r2["fact_pack"]))
        self.assertEqual(r1["fact_pack"]["fingerprint"], r2["fact_pack"]["fingerprint"])

    def test_fingerprint_changes_with_decklist(self):
        pack1 = fact_pack_mod.build_fact_pack(DECK, _texts())
        other = {**DECK, "cards": DECK["cards"] + [{"name": "Llanowar Elves", "quantity": 2}]}
        pack2 = fact_pack_mod.build_fact_pack(other, _texts())
        self.assertNotEqual(pack1["fingerprint"], pack2["fingerprint"])


# ---------------------------------------------------------------------------
# 2. LLM offline -> review still renders with "explanation offline"
# ---------------------------------------------------------------------------

class TestWriterOffline(unittest.TestCase):
    def test_no_writer_produces_offline_review(self):
        result = pipeline_mod.run(DECK, _texts(), deck_id="deck-1",
                                  writer=None, resolver=_resolve_all)
        self.assertEqual(result["status"], "partial")
        writer_jobs = [j for j in result["jobs"] if j["stage"] == "writer"]
        self.assertEqual(len(writer_jobs), 1)
        self.assertEqual(writer_jobs[0]["status"], "degraded")
        self.assertTrue(writer_jobs[0]["error"])
        # Every writer section renders the explicit offline marker, never blank.
        sections = {s["type"]: s for s in result["doc"]["sections"]}
        for key in doc_mod.WRITER_SECTIONS:
            self.assertIn(key, sections, f"missing section {key}")
            expl = sections[key]["data"].get("explanation")
            self.assertIsNotNone(expl, f"section {key} has no explanation block")
            self.assertEqual(expl["status"], "offline")
            self.assertEqual(expl["text"], doc_mod.OFFLINE_TEXT)
            self.assertIn("offline", expl["text"])

    def test_throwing_writer_degrades_never_kills(self):
        def boom(payload):
            raise RuntimeError("oracle exploded")

        result = pipeline_mod.run(DECK, _texts(), deck_id="deck-1",
                                  writer=boom, resolver=_resolve_all)
        self.assertEqual(result["status"], "partial")
        job = [j for j in result["jobs"] if j["stage"] == "writer"][0]
        self.assertEqual(job["status"], "degraded")
        self.assertIn("oracle exploded", job["error"])
        sections = {s["type"]: s for s in result["doc"]["sections"]}
        self.assertEqual(sections["mana_math"]["data"]["explanation"]["text"],
                         doc_mod.OFFLINE_TEXT)


# ---------------------------------------------------------------------------
# 3. Verifier: every stored name resolves; unknowns are dropped
# ---------------------------------------------------------------------------

class TestVerifier(unittest.TestCase):
    def test_unknown_adds_and_candidates_rejected(self):
        analysis = {
            "candidates": [{"name": "Rhystic Study"}, {"name": "Blorf the Unreal"}],
            "adds": [{"name": "Rhystic Study"}, {"name": "Blorf the Unreal"}],
            "cuts": [{"name": "Forest"}],
        }
        resolver = _known_resolver({"Rhystic Study": "Rhystic Study", "Forest": "Forest"})
        out, audit = verifier_mod.verify_analysis(analysis, resolver)
        self.assertEqual([r["name"] for r in out["candidates"]], ["Rhystic Study"])
        self.assertEqual([r["name"] for r in out["adds"]], ["Rhystic Study"])
        self.assertEqual(audit["rejected"], ["Blorf the Unreal"])
        self.assertEqual(audit["checked"], 3)
        # cuts are deck-owned: kept, but flagged when unverified
        self.assertEqual(out["cuts"][0]["name"], "Forest")
        self.assertTrue(out["cuts"][0]["verified"])

    def test_canonical_rename_recorded(self):
        analysis = {"candidates": [], "adds": [{"name": "forest"}], "cuts": []}
        resolver = _known_resolver({"forest": "Forest"})
        out, audit = verifier_mod.verify_analysis(analysis, resolver)
        self.assertEqual(out["adds"][0]["name"], "Forest")
        self.assertEqual(audit["renamed"], {"forest": "Forest"})

    def test_llm_may_not_introduce_cards(self):
        # "Not Invented Here" resolves nowhere; "Rhystic Study" resolves but was
        # NOT in the verified input set -> both dropped. "Sol Ring" is allowed.
        explanations = {
            "mana_math": {"status": "ok", "text": "ramp is fine",
                          "cards": ["Sol Ring", "Rhystic Study", "Not Invented Here"]},
        }
        resolver = _known_resolver({"Sol Ring": "Sol Ring", "Rhystic Study": "Rhystic Study"})
        out, audit = verifier_mod.verify_explanations(
            explanations, {"Sol Ring"}, resolver)
        self.assertEqual(out["mana_math"]["cards"], ["Sol Ring"])
        self.assertEqual(sorted(out["mana_math"]["rejected_cards"]),
                         ["Not Invented Here", "Rhystic Study"])
        self.assertEqual(sorted(audit["rejected"]), ["Not Invented Here", "Rhystic Study"])

    def test_pipeline_stores_only_resolvable_names(self):
        def writer(payload):
            return json.dumps({"sections": {
                "mana_math": {"text": "Sol Ring accelerates; Blorf is made up.",
                              "cards": ["Sol Ring", "Blorf the Unreal"]}}})

        edhrec_page = {"cards": {"rhystic study": {"name": "Rhystic Study", "inclusion": 0.9,
                                                   "synergy": 0.5},
                                 "blorf": {"name": "Blorf the Unreal", "inclusion": 0.8,
                                           "synergy": 0.4}}}
        card_info = {"rhystic study": {"name": "Rhystic Study", "color_identity": ["U"]},
                     "blorf": {"name": "Blorf the Unreal", "color_identity": ["U"]}}
        known = {name: name for name in CARDS} | {"Rhystic Study": "Rhystic Study"}
        result = pipeline_mod.run(
            DECK, _texts(), deck_id="deck-1",
            edhrec_page=edhrec_page, card_info=card_info,
            color_identity=["G", "W", "U", "B"],
            writer=writer, resolver=_known_resolver(known))
        # The invented card is dropped from every rendered list; it survives
        # only in the audit trail as a rejected name (never silently kept).
        self.assertEqual([r["name"] for r in result["analysis"]["adds"]],
                         ["Rhystic Study"])
        self.assertEqual([r["name"] for r in result["analysis"]["candidates"]],
                         ["Rhystic Study"])
        audit = result["doc"]["verification"]["analysis"]
        self.assertEqual(audit["rejected"], ["Blorf the Unreal"])
        sections = {s["type"]: s["data"] for s in result["doc"]["sections"]}
        expl = sections["mana_math"]["explanation"]
        self.assertEqual(expl["cards"], ["Sol Ring"])
        self.assertEqual(expl["rejected_cards"], ["Blorf the Unreal"])
        recs = sections["recommendations"]
        self.assertNotIn("Blorf the Unreal", json.dumps(recs["adds"]) + json.dumps(recs["cuts"]))


# ---------------------------------------------------------------------------
# 4. Writer round-trip + doc/dashboard render the same numbers
# ---------------------------------------------------------------------------

class TestWriterAndDoc(unittest.TestCase):
    def test_writer_reply_schema_validation(self):
        with self.assertRaises(writer_mod.WriterReplyError):
            writer_mod.parse_reply("not json at all {")
        with self.assertRaises(writer_mod.WriterReplyError):
            writer_mod.parse_reply('{"nope": {}}')
        blocks = writer_mod.parse_reply(
            'Some prose.\n```json\n{"sections": {"mana_math": {"text": "ok", "cards": ["Sol Ring"]}}}\n```')
        self.assertIn("mana_math", blocks)
        self.assertEqual(blocks["mana_math"].cards, ["Sol Ring"])

    def test_doc_and_dashboard_share_numbers(self):
        result = pipeline_mod.run(DECK, _texts(), deck_id="deck-1",
                                  writer=None, resolver=_resolve_all)
        pack, analysis, doc = result["fact_pack"], result["analysis"], result["doc"]
        panels = {p["key"]: p for p in doc_mod.dashboard_panels(pack, analysis)}
        sections = {s["type"]: s["data"] for s in doc["sections"]}
        # document vs dashboard: identical source numbers
        self.assertEqual(sections["mana_math"]["lands"], panels["lands"]["value"])
        self.assertEqual(sections["mana_math"]["ramp"], panels["ramp"]["value"])
        self.assertEqual(sections["bracket_salt"]["minimum_bracket"],
                         panels["min_bracket"]["value"])
        self.assertEqual(len(analysis.get("adds") or []), panels["adds"]["value"])
        self.assertEqual(len(analysis.get("cuts") or []), panels["cuts"]["value"])
        self.assertEqual(doc["fingerprint"], pack["fingerprint"])


# ---------------------------------------------------------------------------
# 5. Service end-to-end on SQLite: stored review, versioning, rebuild
# ---------------------------------------------------------------------------

class _StubSpellbook:
    def find_my_combos(self, commanders, main, refresh=False):
        return {"included": [], "almost_included": [], "identity": ["G", "W", "U", "B"]}


def _edhrec_cache():
    cache = review_service.DictCache()
    from src.server.domain.analytics import edhrec_inclusion
    from datetime import datetime, timezone
    slug = edhrec_inclusion.commander_slug(DECK["commanders"])
    cache.set(edhrec_inclusion.cache_key(slug), json.dumps({
        "slug": slug,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "decks_sampled": 1234,
        "cards": {"rhystic study": {"name": "Rhystic Study", "inclusion": 0.75,
                                    "synergy": 0.4, "label": "Top Cards"}},
    }))
    return cache


class TestReviewService(unittest.TestCase):
    def setUp(self):
        self.factory = make_factory()

    def _build(self, session, deck_id="deck-1"):
        return review_service.build_review(
            session, deck_id, cache=_edhrec_cache(), llm_writer=False,
            scryfall_lookup=_resolve_all, spellbook_client=_StubSpellbook())

    def test_build_store_and_rebuild_deterministic(self):
        session = self.factory()
        seed_deck(session)
        r1 = self._build(session)
        session.commit()
        self.assertIsNotNone(r1)
        self.assertEqual(r1["version"], 1)
        self.assertEqual(r1["deck_id"], "deck-1")
        self.assertEqual(r1["status"], "partial")  # llm_writer=False -> offline
        stages = [j["stage"] for j in r1["jobs"]]
        self.assertEqual(stages[:1], ["fact_pack"])
        self.assertIn("store", stages)
        self.assertTrue(all(j["status"] in ("ok", "degraded") for j in r1["jobs"]))

        r2 = self._build(session)
        session.commit()
        # Rebuild on an unchanged deck: new version row, identical facts.
        self.assertEqual(r2["version"], 2)
        self.assertEqual(r2["fingerprint"], r1["fingerprint"])
        self.assertEqual(fact_pack_mod.canonical(r1["fact_pack"]),
                         fact_pack_mod.canonical(r2["fact_pack"]))

        # History + latest read back from the same rows.
        history = repo.list_reviews_for_deck(session, "deck-1")
        self.assertEqual([h["version"] for h in history], [2, 1])
        latest = repo.latest_review_for_deck(session, "deck-1")
        self.assertEqual(latest["id"], r2["id"])
        jobs = repo.list_review_jobs(session, r2["id"])
        self.assertEqual({j["stage"] for j in jobs},
                         {"fact_pack", "candidates", "verifier", "writer", "store"})
        # Document renders offline explanations, never blanks.
        sections = {s["type"]: s for s in latest["doc"]["sections"]}
        for key in doc_mod.WRITER_SECTIONS:
            self.assertEqual(sections[key]["data"]["explanation"]["status"], "offline")

    def test_unknown_deck_returns_none(self):
        session = self.factory()
        self.assertIsNone(self._build(session, deck_id="deck-nope"))

    def test_every_stored_candidate_name_resolves(self):
        session = self.factory()
        seed_deck(session)
        review = self._build(session)
        session.commit()
        audit = review["doc"]["verification"]["analysis"]
        self.assertEqual(audit["rejected"], [])
        self.assertGreater(audit["checked"], 0)
        # The EDHREC candidate made it through verification into adds.
        self.assertIn("Rhystic Study",
                      [r["name"] for r in review["analysis"]["adds"]])


# ---------------------------------------------------------------------------
# 6. API surface
# ---------------------------------------------------------------------------

def _canned_review(review_id=7, deck_id="deck-1"):
    return {
        "id": review_id, "deck_id": deck_id, "version": 1,
        "fingerprint": "f" * 64, "status": "partial",
        "fact_pack": {"fingerprint": "f" * 64, "deck": {"name": "Atraxa Test Deck"}},
        "analysis": {"adds": [], "cuts": []},
        "doc": {"deck_id": deck_id, "fingerprint": "f" * 64, "sections": []},
        "created_at": "2026-10-26T01:00:00+00:00",
        "jobs": [{"id": 1, "review_id": review_id, "stage": "store",
                  "status": "ok", "error": None, "ts": "2026-10-26T01:00:00+00:00"}],
    }


class TestReviewApi(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from src.server.app.api import deps
        from src.server.app.main import app

        self.factory = make_factory()

        def _override():
            s = self.factory()
            try:
                yield s
                s.commit()
            finally:
                s.close()

        app.dependency_overrides[deps.db_session] = _override
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_build_review_endpoint(self):
        with mock.patch("src.server.app.api.reviews.review_service.build_review",
                        return_value=_canned_review()) as build:
            r = self.client.post("/api/v2/decks/deck-1/reviews")
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["id"], 7)
        self.assertEqual(body["status"], "partial")
        self.assertEqual(body["jobs"][0]["stage"], "store")
        self.assertEqual(body["fact_pack"]["deck"]["name"], "Atraxa Test Deck")

    def test_build_review_unknown_deck_404(self):
        with mock.patch("src.server.app.api.reviews.review_service.build_review",
                        return_value=None):
            r = self.client.post("/api/v2/decks/deck-nope/reviews")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["error"], "deck_not_found")

    def test_latest_review_404_then_200(self):
        with mock.patch("src.server.app.api.reviews.repo.latest_review_for_deck",
                        return_value=None):
            r = self.client.get("/api/v2/decks/deck-1/reviews/latest")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["error"], "review_not_found")

        with mock.patch("src.server.app.api.reviews.repo.latest_review_for_deck",
                        return_value=_canned_review()), \
             mock.patch("src.server.app.api.reviews.repo.list_review_jobs",
                        return_value=[]):
            r = self.client.get("/api/v2/decks/deck-1/reviews/latest")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["id"], 7)

    def test_list_reviews(self):
        with mock.patch("src.server.app.api.reviews.repo.get_deck",
                        return_value={"id": "deck-1"}), \
             mock.patch("src.server.app.api.reviews.repo.list_reviews_for_deck",
                        return_value=[{"id": 7, "deck_id": "deck-1", "version": 1,
                                       "fingerprint": "f" * 64, "status": "partial",
                                       "created_at": "2026-10-26T01:00:00+00:00"}]):
            r = self.client.get("/api/v2/decks/deck-1/reviews")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(len(r.json()["reviews"]), 1)

        with mock.patch("src.server.app.api.reviews.repo.get_deck", return_value=None):
            r = self.client.get("/api/v2/decks/deck-nope/reviews")
        self.assertEqual(r.status_code, 404)

    def test_get_review_by_id(self):
        with mock.patch("src.server.app.api.reviews.repo.get_review",
                        return_value=_canned_review()), \
             mock.patch("src.server.app.api.reviews.repo.list_review_jobs",
                        return_value=[]):
            r = self.client.get("/api/v2/reviews/7")
        self.assertEqual(r.status_code, 200, r.text)
        with mock.patch("src.server.app.api.reviews.repo.get_review", return_value=None):
            r = self.client.get("/api/v2/reviews/999")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
