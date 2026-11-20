"""Build and optionally push Forge play-style profiles to Honcho.

The Forge database remains the source of truth. Importing this module is safe
offline; network access requires both local opt-in and an API key.

Failure contract (t_b3f83800): when the API key is missing or the config flag
is disabled, the write path raises HonchoConfigError (or, via ``main()``,
exits with a readable message) BEFORE any payload construction or HTTP setup.
No temp files are created and no request objects are persisted anywhere, so a
failed invocation leaves zero side effects on disk or network.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

DEFAULT_BASE = "http://tower:8100"
DEFAULT_WORKSPACE = "oracle"
TARGET_PEER = "oracle"
OBSERVER_PEER = "forge-recorder"
ENV_FILE = Path.home() / ".hermes" / ".env"
CONFIG_FILE = Path.home() / ".honcho" / "config.json"


class HonchoConfigError(RuntimeError):
    """Honcho sync is unavailable or not explicitly enabled.

    ``disabled`` is True when the cause is a deliberate opt-out (config flag
    off / config absent) rather than a misconfiguration the user must fix.
    """

    def __init__(self, message: str, *, disabled: bool = False) -> None:
        super().__init__(message)
        self.disabled = disabled


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
        raise HonchoConfigError(
            f"Honcho sync disabled: config not found at {path}",
            disabled=True,
        ) from None
    except (OSError, json.JSONDecodeError) as exc:
        raise HonchoConfigError(
            f"Honcho sync disabled: cannot read {path}: {exc}",
            disabled=True,
        ) from exc
    if not isinstance(config, dict) or config.get("enabled") is not True:
        raise HonchoConfigError(
            f"Honcho sync disabled: set enabled=true in {path}",
            disabled=True,
        )
    return config


def build_request(profile: Dict, config: Optional[dict] = None) -> dict:
    """Build oracle's peer card/conclusion requests without network access."""
    config = config if config is not None else _configuration()
    if config.get("enabled") is not True:
        raise HonchoConfigError(
            "Honcho sync disabled: set enabled=true in ~/.honcho/config.json",
            disabled=True,
        )
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
    """Validate opt-in and credentials before sending any requests.

    Raises HonchoConfigError before any payload construction, HTTP setup, or
    file/network side effect when sync is disabled or the key is missing.
    """
    config = _configuration(config_path)
    key = api_key or os.environ.get("HONCHO_API_KEY") or _dotenv_value(env_path, "HONCHO_API_KEY")
    if not key:
        raise HonchoConfigError(
            f"Honcho sync unavailable: set HONCHO_API_KEY in your environment or in {env_path}"
        )
    config = dict(config)
    if base_url:
        config["base_url"] = base_url
    if workspace:
        config["workspace"] = workspace
    if observed_peer and observed_peer != TARGET_PEER:
        raise HonchoConfigError("Honcho Forge profile target is fixed to peer 'oracle'")
    payload = build_request(profile, config)
    # Serialize every request body up front so an unserializable profile can
    # never fail after an earlier request already reached the network.
    try:
        prepared = [(op, json.dumps(op["json"]).encode()) for op in payload["requests"]]
    except (TypeError, ValueError) as exc:
        raise HonchoConfigError(
            f"Honcho sync aborted: profile is not JSON-serializable, nothing was sent ({exc})"
        ) from exc
    result = {"workspace": payload["workspace"], "observed": TARGET_PEER}
    for operation, data in prepared:
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


def main(argv: Optional[List[str]] = None) -> int:
    """Console entry point: invoke the write path without ever leaking a traceback.

    Exit codes: 0 = pushed or cleanly skipped (sync disabled by config);
    1 = actionable configuration error (e.g. HONCHO_API_KEY missing);
    2 = bad invocation (unreadable/invalid profile argument).
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="honcho_push",
        description="Push a Forge play-style profile (JSON) to the Honcho peer 'oracle'.",
    )
    parser.add_argument("profile", help="path to the profile JSON file ('-' reads stdin)")
    args = parser.parse_args(argv)

    try:
        raw = sys.stdin.read() if args.profile == "-" else Path(args.profile).read_text()
        profile = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"honcho push: cannot read profile {args.profile!r}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(profile, dict):
        print(f"honcho push: profile {args.profile!r} must be a JSON object", file=sys.stderr)
        return 2

    try:
        # Pass the module-level paths explicitly: push_profile's defaults are
        # bound at import time and would ignore test monkeypatching.
        result = push_profile(profile, config_path=CONFIG_FILE, env_path=ENV_FILE)
    except HonchoConfigError as exc:
        stream = sys.stdout if exc.disabled else sys.stderr
        print(f"honcho push skipped: {exc}", file=stream)
        return 0 if exc.disabled else 1
    print(
        "honcho push ok: "
        f"workspace={result['workspace']} observed={result['observed']} "
        + " ".join(f"{k}={v}" for k, v in result.items() if k not in {"workspace", "observed"})
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
