"""Build and optionally push Forge play-style profiles to Honcho.

The Forge database remains the source of truth. Importing this module is safe
offline; network access requires both local opt-in and an API key.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Dict, Optional

DEFAULT_BASE = "http://tower:8100"
DEFAULT_WORKSPACE = "oracle"
TARGET_PEER = "oracle"
OBSERVER_PEER = "forge-recorder"
ENV_FILE = Path.home() / ".hermes" / ".env"
CONFIG_FILE = Path.home() / ".honcho" / "config.json"


class HonchoConfigError(RuntimeError):
    """Honcho sync is unavailable or not explicitly enabled."""


def _dotenv_value(path: Path, name: str) -> Optional[str]:
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("\"'") or None
    return None


def _configuration(path: Path = CONFIG_FILE) -> dict:
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        raise HonchoConfigError(f"Honcho sync disabled: config not found at {path}") from None
    except (OSError, json.JSONDecodeError) as exc:
        raise HonchoConfigError(f"Honcho sync disabled: cannot read {path}: {exc}") from exc
    if not isinstance(config, dict) or config.get("enabled") is not True:
        raise HonchoConfigError("Honcho sync disabled: set enabled=true in ~/.honcho/config.json")
    return config


def build_request(profile: Dict, config: Optional[dict] = None) -> dict:
    """Build oracle's peer card/conclusion requests without network access."""
    config = config if config is not None else _configuration()
    if config.get("enabled") is not True:
        raise HonchoConfigError("Honcho sync disabled: set enabled=true in ~/.honcho/config.json")
    workspace = config.get("workspace") or DEFAULT_WORKSPACE
    base_url = (config.get("base_url") or config.get("host") or DEFAULT_BASE).rstrip("/")
    ws = f"{base_url}/v3/workspaces/{workspace}"
    result = {"workspace": workspace, "observed": TARGET_PEER, "requests": []}
    card = profile.get("peer_card") or []
    if card:
        result["requests"].append({"method": "PUT", "url": f"{ws}/peers/{TARGET_PEER}/card", "json": {"peer_card": card}})
    conclusions = profile.get("conclusions") or []
    if conclusions:
        result["requests"].append({"method": "POST", "url": f"{ws}/conclusions", "json": {"conclusions": [
            {"content": c, "observer_id": OBSERVER_PEER, "observed_id": TARGET_PEER}
            for c in conclusions[:100]
        ]}})
    return result


def push_profile(profile: Dict, base_url: Optional[str] = None,
                 workspace: Optional[str] = None,
                 observed_peer: Optional[str] = None,
                 api_key: Optional[str] = None,
                 config_path: Path = CONFIG_FILE,
                 env_path: Path = ENV_FILE) -> Dict:
    """Validate opt-in and credentials before sending any requests."""
    config = _configuration(config_path)
    key = api_key or os.environ.get("HONCHO_API_KEY") or _dotenv_value(env_path, "HONCHO_API_KEY")
    if not key:
        raise HonchoConfigError(f"Honcho sync unavailable: HONCHO_API_KEY missing from environment or {env_path}")
    config = dict(config)
    if base_url:
        config["base_url"] = base_url
    if workspace:
        config["workspace"] = workspace
    if observed_peer and observed_peer != TARGET_PEER:
        raise HonchoConfigError("Honcho Forge profile target is fixed to peer 'oracle'")
    payload = build_request(profile, config)
    result = {"workspace": payload["workspace"], "observed": TARGET_PEER}
    for operation in payload["requests"]:
        data = json.dumps(operation["json"]).encode()
        req = urllib.request.Request(operation["url"], data=data, method=operation["method"])
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {key}")
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
        if operation["url"].endswith("/card"):
            result["peer_card_lines"] = len(operation["json"]["peer_card"])
        else:
            result["conclusions"] = len(operation["json"]["conclusions"])
    return result
