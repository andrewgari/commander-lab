from __future__ import annotations

import json

import pytest

from forge_recorder import honcho_push


def test_build_request_targets_oracle_and_formats_profile_without_network():
    payload = honcho_push.build_request(
        {"player": "Andrew", "peer_card": ["Plays patiently"], "conclusions": ["Values card draw"]},
        {"enabled": True, "workspace": "oracle", "base_url": "http://localhost:8100/"},
    )
    assert payload == {
        "workspace": "oracle",
        "observed": "oracle",
        "requests": [
            {"method": "PUT", "url": "http://localhost:8100/v3/workspaces/oracle/peers/oracle/card", "json": {"peer_card": ["Plays patiently"]}},
            {"method": "POST", "url": "http://localhost:8100/v3/workspaces/oracle/conclusions", "json": {"conclusions": [{"content": "Values card draw", "observer_id": "forge-recorder", "observed_id": "oracle"}]}},
        ],
    }


def test_disabled_config_fails_before_network(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"enabled": False}))
    monkeypatch.setattr(honcho_push.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(honcho_push.HonchoConfigError, match="disabled"):
        honcho_push.push_profile({"peer_card": ["x"]}, api_key="stub", config_path=config)


def test_missing_key_fails_before_network(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"enabled": True}))
    env = tmp_path / ".env"
    env.write_text("OTHER=value\n")
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    monkeypatch.setattr(honcho_push.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(honcho_push.HonchoConfigError, match="HONCHO_API_KEY missing"):
        honcho_push.push_profile({"peer_card": ["x"]}, config_path=config, env_path=env)


def test_push_uses_dotenv_key_and_targets_oracle(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"enabled": True, "workspace": "oracle", "base_url": "http://localhost:8100"}))
    env = tmp_path / ".env"
    env.write_text("HONCHO_API_KEY=stub-secret\n")
    seen = []

    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self):
            return b"{}"

    def capture(req, timeout):
        seen.append(req)
        return Response()

    monkeypatch.setattr(honcho_push.urllib.request, "urlopen", capture)
    result = honcho_push.push_profile({"peer_card": ["Plays patiently"]}, config_path=config, env_path=env)
    assert result == {"workspace": "oracle", "observed": "oracle", "peer_card_lines": 1}
    assert seen[0].full_url == "http://localhost:8100/v3/workspaces/oracle/peers/oracle/card"
    assert seen[0].get_header("Authorization") == "Bearer stub-secret"
    assert json.loads(seen[0].data) == {"peer_card": ["Plays patiently"]}
