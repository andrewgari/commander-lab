"""Client for the `oracle` Hermes profile's OpenAI-compatible API server.

Oracle runs on the Framework desktop as its own Hermes gateway (see
docs/THEORY_SPEC.md and docs/GITOPS.md). Each chat completion runs a full
agent loop on oracle's side, so replies take 30 s to several minutes; the
default timeout is 10 minutes.

Config (env):
    ORACLE_URL        base URL, e.g. http://192.168.50.2:8645 (a trailing /v1 is fine)
    ORACLE_KEY        oracle's API_SERVER_KEY. Prod can also drop it as a file named
                      ORACLE_KEY in the secrets dir (INTERACTIONS_SECRETS_DIR,
                      default /run/commander-lab-secrets). Never commit it.
    ORACLE_TIMEOUT_S  optional request timeout; values under 600 are raised to 600.
    ORACLE_MODEL      optional model name (default "oracle").

Errors: every failure is an OracleError. "Can't reach oracle right now" (not
configured, connection refused, timeout, gateway 5xx) is OracleUnavailable, which
the UI shows as "oracle offline, try again later".
"""
from __future__ import annotations

import os
from typing import Dict, List, Mapping, Optional

import httpx

DEFAULT_TIMEOUT_S = 600.0
DEFAULT_MODEL = "oracle"
SECRETS_DIR_DEFAULT = "/run/commander-lab-secrets"
OFFLINE_MESSAGE = "oracle offline, try again later"


class OracleError(Exception):
    """Base class for every oracle client failure."""


class OracleUnavailable(OracleError):
    """Oracle can't be reached right now (down, timed out, overloaded, not configured)."""

    def __init__(self, detail: str = ""):
        self.detail = detail
        super().__init__(f"{OFFLINE_MESSAGE}" + (f" ({detail})" if detail else ""))


class OracleNotConfigured(OracleUnavailable):
    """ORACLE_URL or ORACLE_KEY is missing."""


class OracleAuthError(OracleError):
    """Oracle rejected the key (401/403). A config problem, not an outage."""


class OracleResponseError(OracleError):
    """Oracle answered, but with a client error or a reply we can't parse."""

    def __init__(self, message: str, status: Optional[int] = None):
        self.status = status
        super().__init__(message)


def _normalize_base(url: str) -> str:
    url = url.strip().rstrip("/")
    if not url.endswith("/v1"):
        url += "/v1"
    return url


def _read_secret(name: str, secrets_dir: str) -> Optional[str]:
    try:
        with open(os.path.join(secrets_dir, name), encoding="utf-8") as fh:
            value = fh.read().strip().strip('"').strip("'")
    except OSError:
        return None
    return value or None


class OracleClient:
    def __init__(self, url: str, key: str, *, timeout: float = DEFAULT_TIMEOUT_S,
                 model: str = DEFAULT_MODEL, transport: Optional[httpx.BaseTransport] = None):
        self.base_url = _normalize_base(url)
        self._key = key
        self.timeout = float(timeout)
        self.model = model
        self._transport = transport

    def __repr__(self) -> str:
        return f"OracleClient(base_url={self.base_url!r}, model={self.model!r}, timeout={self.timeout})"

    @classmethod
    def from_env(cls, env: Optional[Mapping[str, str]] = None) -> "OracleClient":
        env = os.environ if env is None else env
        secrets_dir = env.get("INTERACTIONS_SECRETS_DIR") or SECRETS_DIR_DEFAULT
        url = env.get("ORACLE_URL") or _read_secret("ORACLE_URL", secrets_dir)
        key = env.get("ORACLE_KEY") or _read_secret("ORACLE_KEY", secrets_dir)
        missing = [n for n, v in (("ORACLE_URL", url), ("ORACLE_KEY", key)) if not v]
        if not url or not key:
            raise OracleNotConfigured(f"{' and '.join(missing)} not set")
        try:
            timeout = float(env.get("ORACLE_TIMEOUT_S") or DEFAULT_TIMEOUT_S)
        except ValueError:
            timeout = DEFAULT_TIMEOUT_S
        return cls(url, key, timeout=max(timeout, DEFAULT_TIMEOUT_S),
                   model=env.get("ORACLE_MODEL") or DEFAULT_MODEL)

    # -- HTTP -------------------------------------------------------------------------------

    def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> dict:
        headers = {"Authorization": f"Bearer {self._key}"}
        try:
            with httpx.Client(timeout=self.timeout, transport=self._transport) as http:
                resp = http.request(method, self.base_url + path, json=json_body, headers=headers)
        except httpx.TimeoutException as exc:
            raise OracleUnavailable(f"timed out after {self.timeout:.0f}s") from exc
        except httpx.TransportError as exc:
            raise OracleUnavailable(f"cannot reach {self.base_url}: {type(exc).__name__}") from exc

        if resp.status_code in (401, 403):
            raise OracleAuthError(f"oracle rejected the API key (HTTP {resp.status_code}); check ORACLE_KEY")
        if resp.status_code >= 500:
            raise OracleUnavailable(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise OracleResponseError(f"oracle returned HTTP {resp.status_code}: {resp.text[:300]}",
                                      status=resp.status_code)
        try:
            data = resp.json()
        except ValueError as exc:
            raise OracleResponseError("oracle returned a non-JSON reply", status=resp.status_code) from exc
        if not isinstance(data, dict):
            raise OracleResponseError("oracle returned an unexpected JSON shape", status=resp.status_code)
        return data

    # -- API --------------------------------------------------------------------------------

    def models(self) -> List[str]:
        data = self._request("GET", "/models")
        return [str(m["id"]) for m in data.get("data") or [] if isinstance(m, dict) and m.get("id")]

    def chat(self, messages: List[Dict[str, str]], **extra) -> str:
        """POST /v1/chat/completions; return the assistant's reply text."""
        body = {"model": self.model, "messages": messages, "stream": False, **extra}
        data = self._request("POST", "/chat/completions", body)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OracleResponseError("oracle reply has no choices[0].message.content") from exc
        if not isinstance(content, str):
            raise OracleResponseError("oracle reply content is not text")
        return content

    def ask(self, prompt: str, *, system: Optional[str] = None, **extra) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + \
            [{"role": "user", "content": prompt}]
        return self.chat(messages, **extra)


if __name__ == "__main__":  # one-off smoke test: python -m analytics.oracle_client "question"
    import sys
    try:
        print(OracleClient.from_env().ask(" ".join(sys.argv[1:]) or "Reply with exactly: oracle online"))
    except OracleError as err:
        print(f"{type(err).__name__}: {err}", file=sys.stderr)
        sys.exit(1)
