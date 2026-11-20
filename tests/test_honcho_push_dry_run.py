"""Offline dry-run tests for the Honcho play-style profile writer (t_b49401d0).

Every test here runs with zero network access: the HTTP layer
(``urllib.request.urlopen``) is replaced by a capturing stub that records the
exact outgoing requests, and any test that would touch the network fails
loudly. No live Honcho calls are ever made and the real
``~/.honcho/config.json`` is never read (all config/env paths point into
tmp_path), so the operator's config flag stays OFF.

Checks covered:
  1. happy-path dry run captures the correct oracle payload (peer id +
     play-style profile fields per the module schema: peer_card, conclusions)
  2. HONCHO_API_KEY absent -> clean failure, expected message, no HTTP call
  3. enabled=false        -> clean failure, expected message, no HTTP call
  4. unserializable profile -> aborts before any request is sent
"""
from __future__ import annotations

import json

import pytest

from src.server.workers.forge_recorder import honcho_push


class _FakeResponse:
    """Minimal stand-in for an urllib response context manager."""

    def __init__(self, body: bytes = b"{}"):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._body


@pytest.fixture
def captured_requests(monkeypatch):
    """Replace the HTTP layer entirely; return the list of requests sent."""
    seen = []

    def capture(req, timeout):
        seen.append(req)
        return _FakeResponse()

    monkeypatch.setattr(honcho_push.urllib.request, "urlopen", capture)
    return seen


@pytest.fixture
def network_blocked(monkeypatch):
    """Any attempt to open an HTTP connection fails the test immediately."""
    monkeypatch.setattr(
        honcho_push.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("network call attempted in offline dry-run"),
    )


@pytest.fixture
def enabled_config(tmp_path):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"enabled": True}))
    return config


def _play_style_profile():
    """A play-style profile in the module's schema (peer_card + conclusions)."""
    return {
        "peer_card": [
            "Andrew's Forge play-style profile (games analyzed: 12).",
            "Plays patiently; favors card draw and board wipes",
            "Low mulligan rate; keeps greedy hands",
        ],
        "conclusions": [
            "Values card advantage over tempo",
            "Holds interaction for opponents' combo turns",
        ],
    }


# --- Check 1: happy-path dry run captures the correct oracle payload --------


def test_dry_run_captures_exact_oracle_payload(
    tmp_path, monkeypatch, capsys, enabled_config, captured_requests
):
    """End-to-end push with stubbed key and mocked HTTP: the captured
    requests must be exactly the play-style profile request for peer 'oracle'.
    """
    env = tmp_path / ".env"
    env.write_text("OTHER=value\n")
    monkeypatch.setenv("HONCHO_API_KEY", "stub-dry-run-key")

    result = honcho_push.push_profile(
        _play_style_profile(), config_path=enabled_config, env_path=env
    )

    # Exactly two requests, nothing else reached the (mocked) network.
    assert len(captured_requests) == 2

    put_card, post_conclusions = captured_requests
    base = f"{honcho_push.DEFAULT_BASE}/v3/workspaces/oracle"

    # Request 1: PUT peer card for the 'oracle' peer.
    assert put_card.get_method() == "PUT"
    assert put_card.full_url == f"{base}/peers/oracle/card"
    assert put_card.get_header("Authorization") == "Bearer stub-dry-run-key"
    assert put_card.get_header("Content-type") == "application/json"
    assert json.loads(put_card.data) == {"peer_card": _play_style_profile()["peer_card"]}

    # Request 2: POST conclusions, each stamped observer=forge-recorder,
    # observed=oracle.
    assert post_conclusions.get_method() == "POST"
    assert post_conclusions.full_url == f"{base}/conclusions"
    assert post_conclusions.get_header("Authorization") == "Bearer stub-dry-run-key"
    assert json.loads(post_conclusions.data) == {
        "conclusions": [
            {
                "content": c,
                "observer_id": "forge-recorder",
                "observed_id": "oracle",
            }
            for c in _play_style_profile()["conclusions"]
        ]
    }

    # Result summary targets the oracle peer in the oracle workspace.
    assert result == {
        "workspace": "oracle",
        "observed": "oracle",
        "peer_card_lines": 3,
        "conclusions": 2,
    }

    # Surface the captured payload in the test output for inspection.
    print("\ncaptured oracle payload:")
    for req in captured_requests:
        print(f"  {req.get_method()} {req.full_url}")
        print(f"    body={req.data.decode()}")


def test_dry_run_rejects_non_oracle_peer(tmp_path, monkeypatch, enabled_config, network_blocked):
    """The write path is pinned to peer 'oracle': any other observed peer is a
    config error raised before any HTTP call."""
    monkeypatch.setenv("HONCHO_API_KEY", "stub-dry-run-key")
    with pytest.raises(honcho_push.HonchoConfigError, match="fixed to peer 'oracle'"):
        honcho_push.push_profile(
            _play_style_profile(),
            observed_peer="not-oracle",
            config_path=enabled_config,
            env_path=tmp_path / ".env",
        )


# --- Check 2: key absent fails cleanly, no HTTP attempted --------------------


def test_missing_key_fails_cleanly_with_no_http_call(
    tmp_path, monkeypatch, enabled_config, network_blocked
):
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("UNRELATED=value\n")

    with pytest.raises(honcho_push.HonchoConfigError, match="set HONCHO_API_KEY") as excinfo:
        honcho_push.push_profile(
            _play_style_profile(), config_path=enabled_config, env_path=env
        )

    exc = excinfo.value
    assert not exc.disabled  # missing key is an actionable error, not an opt-out
    assert str(env) in str(exc)  # message tells the user exactly where to set it


# --- Check 3: enabled=false fails cleanly, no HTTP attempted ------------------


def test_disabled_flag_fails_cleanly_with_no_http_call(
    tmp_path, monkeypatch, network_blocked
):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"enabled": False}))
    monkeypatch.setenv("HONCHO_API_KEY", "stub-dry-run-key")  # key present: flag must still win

    with pytest.raises(honcho_push.HonchoConfigError, match="disabled") as excinfo:
        honcho_push.push_profile(
            _play_style_profile(),
            config_path=config,
            env_path=tmp_path / ".env",
        )

    exc = excinfo.value
    assert exc.disabled  # deliberate opt-out, not a misconfiguration
    assert "enabled=true" in str(exc)


# --- Check 4: nothing is sent when the profile cannot be serialized ----------


def test_unserializable_profile_sends_nothing(
    tmp_path, monkeypatch, enabled_config, network_blocked
):
    monkeypatch.setenv("HONCHO_API_KEY", "stub-dry-run-key")
    with pytest.raises(honcho_push.HonchoConfigError, match="not JSON-serializable"):
        honcho_push.push_profile(
            {"peer_card": ["ok"], "conclusions": [object()]},
            config_path=enabled_config,
            env_path=tmp_path / ".env",
        )
