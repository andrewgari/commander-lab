"""Unit tests for analytics.oracle_client against a real local mock HTTP server."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics.oracle_client import (  # noqa: E402
    DEFAULT_TIMEOUT_S,
    OracleAuthError,
    OracleClient,
    OracleError,
    OracleNotConfigured,
    OracleResponseError,
    OracleUnavailable,
)

KEY = "test-key-123"


class _Handler(BaseHTTPRequestHandler):
    # The test sets these on the server instance.
    def log_message(self, *args):  # silence
        pass

    def _send(self, status, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _auth_ok(self):
        return self.headers.get("Authorization") == f"Bearer {KEY}"

    def do_GET(self):
        srv = self.server
        srv.requests.append(("GET", self.path, dict(self.headers), None))
        if not self._auth_ok():
            return self._send(401, {"error": {"message": "bad key"}})
        if self.path == "/v1/models":
            return self._send(200, {"object": "list", "data": [{"id": "oracle", "object": "model"}]})
        self._send(404, {"error": "nope"})

    def do_POST(self):
        srv = self.server
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        srv.requests.append(("POST", self.path, dict(self.headers), body))
        if srv.delay:
            time.sleep(srv.delay)
        if not self._auth_ok():
            return self._send(401, {"error": {"message": "bad key"}})
        if self.path != "/v1/chat/completions":
            return self._send(404, {"error": "nope"})
        if srv.mode == "ok":
            reply = "echo: " + body["messages"][-1]["content"]
            return self._send(200, {
                "id": "chatcmpl-1", "object": "chat.completion", "model": "oracle",
                "choices": [{"index": 0, "message": {"role": "assistant", "content": reply},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
            })
        if srv.mode == "503":
            return self._send(503, {"error": {"message": "overloaded"}})
        if srv.mode == "500":
            return self._send(500, {"error": {"message": "boom"}})
        if srv.mode == "400":
            return self._send(400, {"error": {"message": "bad request: messages required"}})
        if srv.mode == "html":
            return self._send(200, b"<html>not json</html>", ctype="text/html")
        if srv.mode == "no_choices":
            return self._send(200, {"id": "x", "choices": []})


class _QuietServer(ThreadingHTTPServer):
    def handle_error(self, request, client_address):
        pass  # the timeout test closes the socket mid-reply on purpose


class _MockOracle:
    def __enter__(self):
        self.server = _QuietServer(("127.0.0.1", 0), _Handler)
        self.server.requests = []
        self.server.mode = "ok"
        self.server.delay = 0
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class OracleClientTests(unittest.TestCase):
    def test_default_timeout_is_at_least_ten_minutes(self):
        self.assertGreaterEqual(DEFAULT_TIMEOUT_S, 600)
        self.assertGreaterEqual(OracleClient("http://x", KEY).timeout, 600)

    def test_chat_returns_reply_text_and_sends_openai_payload(self):
        with _MockOracle() as m:
            client = OracleClient(m.url, KEY)
            reply = client.chat([{"role": "user", "content": "hi"}])
            self.assertEqual(reply, "echo: hi")
            method, path, headers, body = m.server.requests[-1]
            self.assertEqual((method, path), ("POST", "/v1/chat/completions"))
            self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
            self.assertEqual(body["model"], "oracle")
            self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
            self.assertFalse(body.get("stream", False))

    def test_ask_with_system_prompt(self):
        with _MockOracle() as m:
            reply = OracleClient(m.url, KEY).ask("deck?", system="be brief")
            self.assertEqual(reply, "echo: deck?")
            body = m.server.requests[-1][3]
            self.assertEqual(body["messages"][0], {"role": "system", "content": "be brief"})
            self.assertEqual(body["messages"][1], {"role": "user", "content": "deck?"})

    def test_url_with_or_without_v1_suffix(self):
        with _MockOracle() as m:
            for url in (m.url, m.url + "/", m.url + "/v1", m.url + "/v1/"):
                self.assertEqual(OracleClient(url, KEY).ask("x"), "echo: x")
                self.assertEqual(m.server.requests[-1][1], "/v1/chat/completions")

    def test_models(self):
        with _MockOracle() as m:
            self.assertEqual(OracleClient(m.url, KEY).models(), ["oracle"])

    def test_unreachable_raises_oracle_unavailable(self):
        client = OracleClient(f"http://127.0.0.1:{_free_port()}", KEY, timeout=2)
        with self.assertRaises(OracleUnavailable) as ctx:
            client.ask("hi")
        self.assertIsInstance(ctx.exception, OracleError)
        self.assertIn("oracle offline", str(ctx.exception).lower())

    def test_timeout_raises_oracle_unavailable(self):
        with _MockOracle() as m:
            m.server.delay = 1.5
            with self.assertRaises(OracleUnavailable):
                OracleClient(m.url, KEY, timeout=0.3).ask("hi")

    def test_gateway_5xx_is_unavailable(self):
        with _MockOracle() as m:
            for mode in ("503", "500"):
                m.server.mode = mode
                with self.assertRaises(OracleUnavailable):
                    OracleClient(m.url, KEY).ask("hi")

    def test_bad_key_raises_auth_error(self):
        with _MockOracle() as m:
            with self.assertRaises(OracleAuthError) as ctx:
                OracleClient(m.url, "wrong").ask("hi")
            self.assertNotIsInstance(ctx.exception, OracleUnavailable)
            self.assertNotIn("wrong", str(ctx.exception))

    def test_client_error_raises_response_error(self):
        with _MockOracle() as m:
            m.server.mode = "400"
            with self.assertRaises(OracleResponseError) as ctx:
                OracleClient(m.url, KEY).ask("hi")
            self.assertEqual(ctx.exception.status, 400)

    def test_malformed_responses_raise_response_error(self):
        with _MockOracle() as m:
            for mode in ("html", "no_choices"):
                m.server.mode = mode
                with self.assertRaises(OracleResponseError):
                    OracleClient(m.url, KEY).ask("hi")

    def test_key_never_in_repr(self):
        self.assertNotIn(KEY, repr(OracleClient("http://x", KEY)))


class OracleFromEnvTests(unittest.TestCase):
    def test_from_env_reads_url_key_and_timeout(self):
        c = OracleClient.from_env({"ORACLE_URL": "http://h:8645", "ORACLE_KEY": KEY,
                                   "ORACLE_TIMEOUT_S": "900", "INTERACTIONS_SECRETS_DIR": "/nonexistent"})
        self.assertEqual(c.base_url, "http://h:8645/v1")
        self.assertEqual(c.timeout, 900.0)

    def test_from_env_timeout_floor(self):
        c = OracleClient.from_env({"ORACLE_URL": "http://h", "ORACLE_KEY": KEY, "ORACLE_TIMEOUT_S": "30",
                                   "INTERACTIONS_SECRETS_DIR": "/nonexistent"})
        self.assertEqual(c.timeout, DEFAULT_TIMEOUT_S)

    def test_from_env_reads_key_from_secrets_dir(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "ORACLE_KEY"), "w") as fh:
                fh.write(KEY + "\n")
            with _MockOracle() as m:
                c = OracleClient.from_env({"ORACLE_URL": m.url, "INTERACTIONS_SECRETS_DIR": d})
                self.assertEqual(c.ask("x"), "echo: x")

    def test_missing_config_is_not_configured_and_unavailable(self):
        for env in ({}, {"ORACLE_URL": "http://h"}, {"ORACLE_KEY": KEY}):
            env = {**env, "INTERACTIONS_SECRETS_DIR": "/nonexistent"}
            with self.assertRaises(OracleNotConfigured) as ctx:
                OracleClient.from_env(env)
            self.assertIsInstance(ctx.exception, OracleUnavailable)


if __name__ == "__main__":
    unittest.main()
