"""GN-TXT-1: AI 'improve wording' for a Journal genre text value (theme/kindred custom entries),
plus app.py's /api/deck-genre/improve-text wiring. Stateless, never saves."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analytics import genre_text_improve as gti


class FakeChain:
    def __init__(self, reply=None, ok=True, provider="fake", model="fake-1"):
        self.reply, self.ok, self.provider, self.model = reply, ok, provider, model

    def available(self):
        return self.ok

    def generate_json(self, system, prompt, schema=None, purpose=None, on_call=None):
        if self.reply is None:
            return None, self.provider, self.model
        return self.reply, self.provider, self.model


class CleanRequestTests(unittest.TestCase):
    def test_rejects_bad_kind(self):
        with self.assertRaises(gti.GenreTextImproveError):
            gti.clean_request({"kind": "nope", "text": "x"})

    def test_rejects_missing_text(self):
        with self.assertRaises(gti.GenreTextImproveError):
            gti.clean_request({"kind": "theme"})

    def test_rejects_too_long(self):
        with self.assertRaises(gti.GenreTextImproveError):
            gti.clean_request({"kind": "theme", "text": "x" * 40})

    def test_collapses_whitespace(self):
        req = gti.clean_request({"kind": "kindred", "text": "  Zom   bie  "})
        self.assertEqual(req["text"], "Zom bie")


class ImproveTextTests(unittest.TestCase):
    def test_no_llm_configured(self):
        with self.assertRaises(gti.GenreTextImproveUnavailable) as ctx:
            gti.improve_text(FakeChain(ok=False), {"kind": "theme", "text": "undead horror"})
        self.assertEqual(ctx.exception.kind, "no_llm")

    def test_llm_failed(self):
        with self.assertRaises(gti.GenreTextImproveUnavailable) as ctx:
            gti.improve_text(FakeChain(reply=None), {"kind": "theme", "text": "undead horror"})
        self.assertEqual(ctx.exception.kind, "llm_failed")

    def test_bad_response_no_text(self):
        with self.assertRaises(gti.GenreTextImproveUnavailable) as ctx:
            gti.improve_text(FakeChain(reply={}), {"kind": "theme", "text": "undead horror"})
        self.assertEqual(ctx.exception.kind, "bad_response")

    def test_success_trims_and_returns_original(self):
        out = gti.improve_text(FakeChain(reply={"text": "  Gothic Horror  "}),
                                {"kind": "theme", "text": "undead horror but cuter"})
        self.assertEqual(out["text"], "Gothic Horror")
        self.assertEqual(out["original"], "undead horror but cuter")
        self.assertEqual(out["kind"], "theme")
        self.assertEqual(out["provider"], "fake")


if __name__ == "__main__":
    unittest.main()
