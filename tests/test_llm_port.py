"""Oracle* errors subclass the provider-neutral LLM* errors."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import oracle_client as oc  # noqa: E402
from src.server.domain.llm import port  # noqa: E402


class LLMPortErrors(unittest.TestCase):
    def test_subclassing(self):
        pairs = [(oc.OracleError, port.LLMError), (oc.OracleUnavailable, port.LLMUnavailable),
                 (oc.OracleNotConfigured, port.LLMNotConfigured), (oc.OracleAuthError, port.LLMAuthError),
                 (oc.OracleResponseError, port.LLMResponseError)]
        for o, l in pairs:
            self.assertTrue(issubclass(o, l), o)
        self.assertTrue(issubclass(port.LLMNotConfigured, port.LLMUnavailable))

    def test_messages_unchanged(self):
        self.assertEqual(str(oc.OracleUnavailable("x")), "oracle offline, try again later (x)")
        e = oc.OracleResponseError("bad", status=400)
        self.assertEqual((str(e), e.status), ("bad", 400))
        self.assertEqual(oc.OracleNotConfigured("y").detail, "y")

    def test_llm_catch_covers_oracle(self):
        with self.assertRaises(port.LLMUnavailable):
            raise oc.OracleNotConfigured("z")


if __name__ == "__main__":
    unittest.main()
