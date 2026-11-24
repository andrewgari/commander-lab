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


class TestVerdictBoundToDeck(unittest.TestCase):
    def tearDown(self):
        reset_reviewer()

    def test_pass_is_invalidated_by_edit(self):
        d = {"cards": _cards(100), "commanders": ["X"], "target_power": 5}
        brew_review.review_draft(d, {})
        self.assertTrue(brew_review.verdict_is_current(d))
        d["cards"][0]["name"] = "Other"
        self.assertFalse(brew_review.verdict_is_current(d))
        d2 = dict(d, target_power=7)
        self.assertFalse(brew_review.verdict_is_current(d2))


class TestRejectedVerdictPersisted(TestBrewCore):
    def tearDown(self):
        reset_reviewer()
        super().tearDown()

    def test_rejected_review_kept_on_session(self):
        brew_review.set_reviewer(lambda d, s: {"status": "fail", "reasons": ["too strong"], "source": "fake"})
        from unittest import mock
        with mock.patch.object(brew_review, "deterministic_check", return_value=[]):
            with self.assertRaises(ValueError):
                self._built_session()
        sess = [brew.get_session(self.r, k.split("brew:session:")[-1]) for k in self.r.keys("*") if "session" in str(k)]
        sess = [x for x in sess if x]
        self.assertTrue(any((x.get("review_rejected") or {}).get("reasons") == ["too strong"] for x in sess))


class FactoryReviewerTests(unittest.TestCase):
    def _run(self, reply=None, exc=None):
        from unittest.mock import MagicMock
        c = MagicMock()
        if exc:
            c.chat.side_effect = exc
        else:
            c.chat.return_value = reply
        draft = {"cards": [{"name": "Sol Ring", "quantity": 1}], "commanders": ["X"], "target_power": 7}
        return brew_review._oracle_reviewer(draft, {}, client=c), c

    def test_pass_and_fail_parsed(self):
        v, c = self._run('{"verdict": "pass", "reasons": []}')
        self.assertEqual(v["status"], "pass")
        self.assertIn("Brackets", c.chat.call_args.args[0][0]["content"][0]["text"])
        v, _ = self._run('{"verdict": "fail", "reasons": ["too strong"]}')
        self.assertEqual((v["status"], v["reasons"]), ("fail", ["too strong"]))

    def test_garbage_is_fail(self):
        self.assertEqual(self._run("nope")[0]["status"], "fail")

    def test_not_configured_is_unavailable(self):
        from src.server.domain.llm.port import LLMNotConfigured
        self.assertEqual(self._run(exc=LLMNotConfigured("x"))[0]["status"], "unavailable")

    def test_default_client_from_factory(self):
        from unittest.mock import MagicMock
        f = MagicMock()
        f.return_value.chat.return_value = '{"verdict":"pass","reasons":[]}'
        brew_review.set_client_factory(f)
        try:
            brew_review._oracle_reviewer({"cards": []}, {})
        finally:
            brew_review.set_client_factory(None)
        f.assert_called_once_with("brew_review")


class _TruncThenOk:
    """Chat client whose first reply is cut off at max_tokens."""
    max_tokens = 6000

    def __init__(self, always=False):
        self.calls = []
        self.always = always

    def chat(self, messages, **extra):
        from src.server.domain.llm.port import LLMTruncatedError
        self.calls.append(extra)
        if self.always or len(self.calls) == 1:
            raise LLMTruncatedError("reply truncated at max_tokens")
        return '{"verdict": "pass", "reasons": []}'


class TestTruncationRetry(unittest.TestCase):
    def test_truncated_reply_retries_with_bigger_budget(self):
        c = _TruncThenOk()
        v = brew_review._oracle_reviewer({"cards": _cards(100), "commanders": ["X"]}, {}, client=c)
        self.assertEqual(v["status"], "pass")
        self.assertEqual(c.calls, [{}, {"max_tokens": 12000}])

    def test_persistent_truncation_is_unavailable_not_crash(self):
        c = _TruncThenOk(always=True)
        v = brew_review._oracle_reviewer({"cards": _cards(100)}, {}, client=c)
        self.assertEqual(v["status"], "unavailable")
        self.assertEqual(len(c.calls), 2)


class TestMaxTokensConfig(unittest.TestCase):
    def test_default_and_env_override(self):
        from src.server.infra.llm.factory import build_chat_client
        base = {"OPENROUTER_API_KEY": "k"}
        self.assertEqual(build_chat_client("brew_review", base).max_tokens, 6000)
        self.assertEqual(build_chat_client("brew_review", {**base, "LLM_MAX_TOKENS": "9000"}).max_tokens, 9000)
        self.assertEqual(build_chat_client("brew_review", {**base, "LLM_MAX_TOKENS": "9000",
                                                           "LLM_MAX_TOKENS_BREW_REVIEW": "7000"}).max_tokens, 7000)
        self.assertEqual(build_chat_client("brew_review", {**base, "LLM_MAX_TOKENS": "junk"}).max_tokens, 6000)
