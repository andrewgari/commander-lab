"""Every Brew result carries an oracle verdict; non-pass decks are not finished."""
import unittest

import src.server.domain.draft.brew as brew
import src.server.domain.draft.brew_review as brew_review
from tests import reset_reviewer
from tests.test_brew import TestBrewCore


def _cards(n, prefix="Card"):
    return [{"name": f"{prefix} {i}", "quantity": 1} for i in range(n)]


class TestDeterministicGate(unittest.TestCase):
    def tearDown(self):
        reset_reviewer()

    def test_wrong_size_fails(self):
        v = brew_review.review_draft({"cards": _cards(40)}, {})
        self.assertEqual(v["status"], "fail")
        self.assertIn("expected 100", v["reasons"][0])

    def test_guid_name_fails(self):
        cards = _cards(99) + [{"name": "9f1c2d3e-aaaa-bbbb-cccc-1234567890ab", "quantity": 1}]
        v = brew_review.review_draft({"cards": cards}, {})
        self.assertEqual(v["status"], "fail")
        self.assertTrue(any("id instead of a name" in x for x in v["reasons"]))

    def test_clean_deck_uses_reviewer_and_stores_verdict(self):
        brew_review.set_reviewer(lambda d, s: {"status": "pass", "reasons": [], "source": "fake"})
        d = {"cards": _cards(100)}
        brew_review.review_draft(d, {})
        self.assertEqual(d["oracle_verdict"]["status"], "pass")

    def test_reviewer_exception_is_unavailable(self):
        def boom(d, s):
            raise RuntimeError("down")
        brew_review.set_reviewer(boom)
        d = {"cards": _cards(100)}
        self.assertEqual(brew_review.review_draft(d, {})["status"], "unavailable")


class TestBrewResultCarriesVerdict(TestBrewCore):
    def tearDown(self):
        reset_reviewer()
        super().tearDown()

    def test_build_result_always_has_verdict(self):
        brew_review.set_reviewer(lambda d, s: {"status": "pass", "reasons": [], "source": "fake"})
        from unittest import mock
        with mock.patch.object(brew_review, "deterministic_check", return_value=[]):
            session = self._built_session()
        self.assertEqual(session["status"], brew.STATUS_DRAFTS_READY)
        self.assertEqual(session["drafts"][0]["oracle_verdict"]["status"], "pass")

    def test_failed_verdict_blocks_finished_result(self):
        brew_review.set_reviewer(lambda d, s: {"status": "fail", "reasons": ["too strong"], "source": "fake"})
        from unittest import mock
        with mock.patch.object(brew_review, "deterministic_check", return_value=[]):
            with self.assertRaises(ValueError):
                self._built_session()
        sid = self.r.keys("*")  # session persisted as failed
        self.assertTrue(sid)


if __name__ == "__main__":
    unittest.main()
