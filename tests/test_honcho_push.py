from __future__ import annotations

import json

import pytest

from src.server.workers.forge_recorder import honcho_push


def _write_profile(tmp_path):
    profile = tmp_path / "profile.json"
    profile.write_text(json.dumps({"player": "Andrew", "peer_card": ["Plays patiently"]}))
    return profile


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
    with pytest.raises(honcho_push.HonchoConfigError, match="set HONCHO_API_KEY"):
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


def test_cli_missing_key_exits_cleanly_with_actionable_message(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"enabled": True}))
    env = tmp_path / ".env"
    env.write_text("OTHER=value\n")
    profile = _write_profile(tmp_path)
    monkeypatch.delenv("HONCHO_API_KEY", raising=False)
    monkeypatch.setattr(honcho_push, "CONFIG_FILE", config)
    monkeypatch.setattr(honcho_push, "ENV_FILE", env)
    monkeypatch.setattr(honcho_push.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    before = sorted(p.name for p in tmp_path.iterdir())

    code = honcho_push.main([str(profile)])

    out, err = capsys.readouterr()
    assert code == 1
    assert "Traceback" not in out + err
    assert "HONCHO_API_KEY" in err
    assert str(env) in err
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # zero disk side effects


def test_cli_disabled_flag_exits_cleanly_with_actionable_message(tmp_path, monkeypatch, capsys):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"enabled": False}))
    profile = _write_profile(tmp_path)
    monkeypatch.setattr(honcho_push, "CONFIG_FILE", config)
    monkeypatch.setattr(honcho_push, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setattr(honcho_push.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    before = sorted(p.name for p in tmp_path.iterdir())

    code = honcho_push.main([str(profile)])

    out, err = capsys.readouterr()
    assert code == 0
    assert "Traceback" not in out + err
    assert "enabled=true" in out
    assert str(config) in out
    assert sorted(p.name for p in tmp_path.iterdir()) == before  # zero disk side effects


def test_cli_missing_config_exits_cleanly(tmp_path, monkeypatch, capsys):
    profile = _write_profile(tmp_path)
    monkeypatch.setattr(honcho_push, "CONFIG_FILE", tmp_path / "nope.json")
    monkeypatch.setattr(honcho_push.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))

    code = honcho_push.main([str(profile)])

    out, err = capsys.readouterr()
    assert code == 0
    assert "Traceback" not in out + err
    assert "config not found" in out


def test_unserializable_profile_aborts_before_any_request(tmp_path, monkeypatch):
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"enabled": True}))
    monkeypatch.setattr(honcho_push.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network called"))
    with pytest.raises(honcho_push.HonchoConfigError, match="not JSON-serializable"):
        honcho_push.push_profile(
            {"peer_card": ["ok"], "conclusions": [object()]},
            api_key="stub",
            config_path=config,
        )
