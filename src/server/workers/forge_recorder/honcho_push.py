"""Push the play-style profile to Honcho so the oracle peer's reviews and
recommendations reflect how the user actually plays.

Honcho is the delivery channel only — Commander Lab's Postgres stays the
source of truth. Pure-stdlib (urllib) so the watcher needs no extra deps on
the Framework desktop.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Dict, List, Optional

DEFAULT_BASE = "http://tower:8100"
DEFAULT_WORKSPACE = "oracle"
OBSERVER_PEER = "forge-recorder"


def _req(method: str, url: str, payload: Optional[dict] = None, timeout: int = 15):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def push_profile(profile: Dict, base_url: str = DEFAULT_BASE,
                 workspace: str = DEFAULT_WORKSPACE,
                 observed_peer: Optional[str] = None) -> Dict:
    """Write the profile's peer card + conclusions to Honcho.

    Returns a small result dict; raises on transport failure so the watcher
    can log and retry on its next cycle.
    """
    player = profile.get("player") or "andrewgari"
    observed = observed_peer or player
    ws = f"{base_url.rstrip('/')}/v3/workspaces/{workspace}"
    result = {"workspace": workspace, "observed": observed}

    card = profile.get("peer_card") or []
    if card:
        _req("PUT", f"{ws}/peers/{observed}/card", {"peer_card": card})
        result["peer_card_lines"] = len(card)

    conclusions = profile.get("conclusions") or []
    if conclusions:
        batch = {
            "conclusions": [
                {"content": c, "observer_id": OBSERVER_PEER, "observed_id": observed}
                for c in conclusions[:100]
            ]
        }
        _req("POST", f"{ws}/conclusions", batch)
        result["conclusions"] = len(batch["conclusions"])
    return result
