import os
import unittest
from unittest.mock import MagicMock, patch

import datastore
from datastore import GuardedRedis, WritesDisabledError


class TestDatastoreFlag(unittest.TestCase):
    def _env(self, val):
        return patch.dict(os.environ, {datastore.MODE_ENV: val} if val is not None else {}, clear=False)

    def test_default_allows_writes(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(datastore.MODE_ENV, None)
            c = MagicMock(); g = GuardedRedis(c)
            g.set("a", "1")
            c.set.assert_called_once_with("a", "1")

    def test_readonly_blocks_writes_allows_reads(self):
        with self._env("readonly"):
            c = MagicMock(); g = GuardedRedis(c)
            g.get("a"); c.get.assert_called_once_with("a")
            for op in ("set", "delete", "sadd", "hset"):
                with self.assertRaises(WritesDisabledError):
                    getattr(g, op)("k", "v")
                getattr(c, op).assert_not_called()

    def test_pipeline_guarded(self):
        with self._env("readonly"):
            c = MagicMock(); g = GuardedRedis(c)
            p = g.pipeline()
            p.get("a")
            with self.assertRaises(WritesDisabledError):
                p.set("a", "1")

    def test_invalid_value_fails_loudly(self):
        with self._env("banana"):
            with self.assertRaises(ValueError):
                datastore.get_mode()

    def test_logs_blocked_write(self):
        with self._env("readonly"), self.assertLogs("commander_lab.datastore", "WARNING"):
            with self.assertRaises(WritesDisabledError):
                GuardedRedis(MagicMock()).set("a", "1")

    def test_api_returns_503(self):
        from fastapi.testclient import TestClient
        import app as app_mod
        with self._env("readonly"), patch.object(app_mod, "r", GuardedRedis(MagicMock())):
            c = TestClient(app_mod.app, raise_server_exceptions=False)
            m = app_mod.r._client
            m.get.return_value = None
            resp = c.post("/api/tags/core", json={"tag": "x", "action": "add"})
            self.assertEqual(resp.status_code, 503)
            self.assertEqual(resp.json()["error"], "writes_disabled")
            m.set.assert_not_called()


if __name__ == "__main__":
    unittest.main()
