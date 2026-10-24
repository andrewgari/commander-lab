"""
AN-3: pillar fit via local embeddings.

Every card is embedded (name + type line + oracle text, reminder text
stripped) and scored against the deck's live pillars (pinned / accepted /
draft; rejected pillars don't count). A card's fit is its best pillar's
cosine similarity, rescaled to 0..1 with the embedder's calibration.
Buckets (UX_SPEC §3.1.9, pillars.html): `pillar` when the best similarity
clears the embedder's on-pillar threshold, else `glue` when the card is in a
non-plan composition row (lands, ramp, card advantage, disruption), else
`generic`. Bucket counts are by quantity, so they add up to 100.

Card vectors are cached in one Redis hash per model, so a card is embedded
once per model.

The bundled ONNX model (bge-small-en-v1.5) is exercised only when
EMBEDDINGS_MODEL_DIR points at it (the Docker image sets it); otherwise a
deterministic bag-of-words stub stands in.

Run: python -m pytest tests/test_pillar_fit.py
"""
import hashlib
import json
import math
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import embeddings
from analytics import pillar_fit
from tests.test_interactions import FakeRedis

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "holy_hellfire_cards.json")

STOP = {"a", "an", "the", "of", "to", "and", "or", "you", "your", "it", "its", "this", "that", "with", "for", "on",
        "in", "into", "from", "each", "at", "is", "be", "are", "as", "by", "if", "until", "end", "turn", "card",
        "cards", "creature", "creatures", "control", "whenever", "target", "may", "put", "one", "any", "all"}


def load_hh():
    with open(FIXTURE, encoding="utf-8") as fh:
        data = json.load(fh)
    return data["deck"], data["tags"]


class HashEmbedder(embeddings.Embedder):
    """Deterministic bag-of-words embedder for tests (no model, no numpy)."""

    model_id = "test-hash-v1"
    dim = 256
    on_pillar_sim = 0.15
    sim_floor = 0.0
    sim_ceil = 0.5
    dup_sim = 0.85

    def __init__(self):
        self.calls = 0
        self.texts = 0

    def available(self):
        return True

    def embed(self, texts, kind="passage"):
        self.calls += 1
        self.texts += len(texts)
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for w in re.findall(r"[a-z]+", t.lower()):
                w = w[:-1] if w.endswith("s") and len(w) > 3 else w
                if w in STOP:
                    continue
                v[int(hashlib.md5(w.encode()).hexdigest(), 16) % self.dim] += 1.0
            n = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / n for x in v])
        return out


def pillar(pid, name, description, state="pinned", order=1):
    return {"id": pid, "name": name, "description": description, "state": state, "order": order}


PILLARS = [
    pillar("p1", "Dragons", "Dragon dragons flying damage haste", order=1),
    pillar("p2", "Life into cards", "Lose life pay life draw", order=2),
    pillar("p3", "Rejected idea", "Signet mana artifact", state="rejected", order=3),
]


class TestCardText(unittest.TestCase):
    def test_text_strips_reminder_text_and_includes_type(self):
        t = pillar_fit.card_text("Strionic Resonator", {
            "type_line": "Artifact", "oracle_text": "{2}, {T}: Copy target triggered ability you control. "
                                                   "(A triggered ability uses the words \"when,\" \"whenever,\" or \"at.\")"})
        self.assertTrue(t.startswith("Strionic Resonator. Artifact."))
        self.assertNotIn("uses the words", t)

    def test_missing_text_falls_back_to_name(self):
        self.assertEqual(pillar_fit.card_text("Mystery", None), "Mystery.")


class TestDeckFit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deck, cls.tags = load_hh()
        cls.cards = {n: t["card"] for n, t in cls.tags.items()}

    def setUp(self):
        self.r = FakeRedis()
        self.emb = HashEmbedder()

    def fit(self, pillars=PILLARS):
        return pillar_fit.deck_fit(self.r, self.deck, {"pillars": pillars}, self.cards, self.emb)

    def test_every_card_is_scored_and_buckets_sum_to_100(self):
        out = self.fit()
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["per_card"]), 87)
        self.assertEqual(out["pillar"] + out["glue"] + out["generic"], 100)
        for row in out["per_card"]:
            self.assertGreaterEqual(row["score"], 0.0)
            self.assertLessEqual(row["score"], 1.0)
            self.assertIn(row["bucket"], ("pillar", "glue", "generic"))
        # sorted best fit first
        scores = [row["score"] for row in out["per_card"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(out["model"], "test-hash-v1")

    def test_rejected_pillars_do_not_score(self):
        out = self.fit()
        self.assertNotIn("p3", {row["pillar_id"] for row in out["per_card"]})
        self.assertEqual([p["id"] for p in out["pillars"]], ["p1", "p2"])
        sol = next(row for row in out["per_card"] if row["name"] == "Sol Ring")
        self.assertNotEqual(sol["bucket"], "pillar")

    def test_scores_follow_the_pillar_text(self):
        out = self.fit()
        by = {row["name"]: row for row in out["per_card"]}
        self.assertEqual(by["Utvara Hellkite"]["pillar_id"], "p1")
        self.assertEqual(by["Utvara Hellkite"]["bucket"], "pillar")
        self.assertEqual(by["Phyrexian Arena"]["pillar_id"], "p2")
        self.assertGreater(by["Utvara Hellkite"]["score"], by["Grand Abolisher"]["score"])
        # glue: a basic land fits no pillar but is a composition row
        self.assertEqual(by["Mountain"]["bucket"], "glue")
        self.assertEqual(by["Mountain"]["quantity"], 5)
        p1 = next(p for p in out["pillars"] if p["id"] == "p1")
        self.assertEqual(len(p1["top_cards"]), 3)
        self.assertGreater(p1["count"], 0)

    def test_card_vectors_are_cached_per_model(self):
        self.fit()
        texts_first = self.emb.texts
        self.fit()
        # second run embeds only the 2 live pillar texts
        self.assertEqual(self.emb.texts - texts_first, 2)
        self.assertIn("embeddings:card:test-hash-v1", self.r.hashes)

    def test_editing_a_pillar_changes_scores(self):
        a = {row["name"]: row["score"] for row in self.fit()["per_card"]}
        edited = [pillar("p1", "Dragons", "Signet mana artifact"), PILLARS[1]]
        b = {row["name"]: row["score"] for row in self.fit(edited)["per_card"]}
        self.assertGreater(b["Boros Signet"], a["Boros Signet"])

    def test_no_live_pillars(self):
        out = self.fit([PILLARS[2]])
        self.assertEqual(out["status"], "no_pillars")
        self.assertEqual(out["pillar"], 0)
        self.assertEqual(out["glue"] + out["generic"], 100)
        self.assertTrue(all(row["score"] is None for row in out["per_card"]))

    def test_embedder_unavailable(self):
        class Off(HashEmbedder):
            def available(self):
                return False
        out = pillar_fit.deck_fit(self.r, self.deck, {"pillars": PILLARS}, self.cards, Off())
        self.assertEqual(out["status"], "embedder_unavailable")


class TestVectorCodec(unittest.TestCase):
    def test_round_trip(self):
        v = [0.1, -0.25, 0.5, 0.0]
        got = embeddings.decode_vector(embeddings.encode_vector(v))
        for a, b in zip(v, got):
            self.assertAlmostEqual(a, b, places=6)

    def test_cosine(self):
        self.assertAlmostEqual(embeddings.cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(embeddings.cosine([1, 0], [0, 1]), 0.0)


MODEL_DIR = os.getenv("EMBEDDINGS_MODEL_DIR")


@unittest.skipUnless(MODEL_DIR and os.path.exists(os.path.join(MODEL_DIR or "", "model_quantized.onnx")),
                     "bundled ONNX model not present (set EMBEDDINGS_MODEL_DIR)")
class TestOnnxEmbedder(unittest.TestCase):
    """The real local model (CPU). Runs in the Docker image / when the model is downloaded."""

    def test_real_model_ranks_on_theme_cards_higher(self):
        emb = embeddings.OnnxEmbedder(MODEL_DIR)
        self.assertTrue(emb.available())
        deck, tags = load_hh()
        cards = {n: t["card"] for n, t in tags.items()}
        pillars = [pillar("p1", "Dragon storm", "Dragons entering and attacking deal damage and make more dragons."),
                   pillar("p2", "Life into cards", "Pay life and lose life to draw extra cards.", order=2)]
        out = pillar_fit.deck_fit(FakeRedis(), deck, {"pillars": pillars}, cards, emb)
        by = {row["name"]: row for row in out["per_card"]}
        self.assertEqual(len(emb.embed(["x"])[0]), 384)
        self.assertEqual(by["Utvara Hellkite"]["pillar_id"], "p1")
        self.assertEqual(by["Phyrexian Arena"]["pillar_id"], "p2")
        self.assertGreater(by["Utvara Hellkite"]["score"], by["Sol Ring"]["score"])


if __name__ == "__main__":
    unittest.main()
