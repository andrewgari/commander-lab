"""SM-2: client for the ForgeMTG simulation daemon (SM-1) plus the Commander Lab -> Forge `.dck` exporter.

The daemon lives at ``FORGE_SIM_URL`` (default ``http://mtg-forge:8765``):
    GET /health, GET /opponents, POST /jobs, GET /jobs/{job_id}

Pure stdlib (urllib). Every failure surfaces as a SimClientError subclass so callers never see raw
URLError/JSONDecodeError: SimUnavailable (daemon unreachable / timed out), SimBusy (409, a job is
already running), SimError (any other non-2xx or malformed reply).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Tuple

DEFAULT_URL = "http://mtg-forge:8765"
DEFAULT_TIMEOUT_S = 15.0
COMMANDER_DECK_SIZE = 100

# True split / aftermath cards keep "A // B" in Forge. Transforming/modal DFCs and adventures are
# indexed by front-face name only.
SPLIT_CARD_ALLOWLIST = frozenset(s.casefold() for s in (
    "Wear // Tear", "Fire // Ice", "Chaos // Order", "Assure // Assemble", "Commit // Memory",
    "Consign // Oblivion", "Cut // Ribbons", "Dusk // Dawn", "Farm // Market", "Grind // Dust",
    "Heaven // Earth", "Never // Return", "Rags // Riches", "Refuse // Cooperate", "Status // Statue",
    "Find // Finality", "Expansion // Explosion", "Response // Resurgence", "Connive // Concoct",
    "Discovery // Dispersal", "Invert // Invent", "Integrity // Intervention", "Flower // Flourish",
    "Bedeck // Bedazzle", "Carnival // Carnage", "Collision // Colossus", "Consecrate // Consume",
    "Depose // Deploy", "Incubation // Incongruity", "Repudiate // Replicate", "Revival // Revenge",
    "Thrash // Threat", "Warrant // Warden", "Turn // Burn",
    "Life // Death", "Alive // Well", "Beck // Call", "Down // Dirty", "Supply // Demand", "Illusion // Reality",
    "Spite // Malice", "Rise // Fall", "Failure // Comply", "Hit // Run", "Odds // Ends", "Pain // Suffering",
    "Boom // Bust", "Ready // Willing", "Give // Take", "Protect // Serve",
))


class SimClientError(Exception):
    """Base error for the sim client."""


class SimUnavailable(SimClientError):
    """The daemon could not be reached (connection refused, DNS, timeout)."""


class SimError(SimClientError):
    def __init__(self, message: str, status: Optional[int] = None, body: Any = None):
        super().__init__(message)
        self.status = status
        self.body = body


class SimBusy(SimError):
    """409: the daemon is already running a job."""

    def __init__(self, message: str, job_id: Optional[str] = None, body: Any = None):
        super().__init__(message, 409, body)
        self.job_id = job_id


# ---------------------------------------------------------------------------------------------
# .dck export
# ---------------------------------------------------------------------------------------------

def normalize_card_name(raw_name: str, card: Optional[Mapping] = None) -> str:
    """Front-face name for DFC/MDFC/adventure cards; true split/aftermath/room cards keep `A // B`."""
    name = (raw_name or "").strip()
    if " // " not in name:
        return name
    card = card or {}
    layout = str(card.get("layout") or "").lower()
    type_line = str(card.get("type_line") or card.get("type") or "").lower()
    if (name.casefold() in SPLIT_CARD_ALLOWLIST or layout in {"split", "aftermath", "flip"}
            or ("room" in type_line and "enchantment" in type_line)):
        return name
    return name.split(" // ")[0].strip()


def _qty(card: Mapping) -> int:
    try:
        return max(int(card.get("quantity") or card.get("qty") or 1), 1)
    except (TypeError, ValueError):
        return 1


def export_deck_to_dck(deck: dict) -> Tuple[str, str, str]:
    """Return ``(deck_name, commander_name, dck_text)`` for a Commander Lab deck dict.

    Commanders are written once under [Commander] and removed from [Main] so the totals are
    1 + 99 = 100 (2 + 98 for partners). DFC/adventure names are front-face normalised. Raises
    ValueError when the deck does not total 100 cards or has no commander.
    """
    deck_name = str(deck.get("name") or f"Deck {deck.get('id', '')}").strip()
    raw_cmdrs = [c for c in (deck.get("commanders") or []) if c]
    if not raw_cmdrs and deck.get("commander"):
        raw_cmdrs = [deck["commander"]]
    if not raw_cmdrs:
        raise ValueError(f"deck {deck_name!r} has no commander")
    cmdrs: List[str] = []
    for c in raw_cmdrs:
        n = normalize_card_name(c)
        if n not in cmdrs:
            cmdrs.append(n)
    cmdr_keys = {c.casefold() for c in cmdrs} | {c.strip().casefold() for c in raw_cmdrs}

    main: Dict[str, int] = {}
    for card in deck.get("cards") or []:
        raw = (card.get("name") or "").strip()
        if not raw:
            continue
        name = normalize_card_name(raw, card)
        if name.casefold() in cmdr_keys or raw.casefold() in cmdr_keys:
            continue
        main[name] = main.get(name, 0) + _qty(card)

    total = len(cmdrs) + sum(main.values())
    if total != COMMANDER_DECK_SIZE:
        raise ValueError(f"deck {deck_name!r} exports {total} cards (commanders={len(cmdrs)}, "
                         f"main={sum(main.values())}); expected {COMMANDER_DECK_SIZE}")
    ident = deck.get("id", deck.get("source_id", deck_name))
    lines = ["[metadata]", f"Name=user_{ident}", "[Commander]"]
    lines += [f"1 {c}" for c in cmdrs]
    lines.append("[Main]")
    lines += [f"{q} {n}" for n, q in main.items()]
    return deck_name, " + ".join(cmdrs), "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------------------------

class SimClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT_S):
        self.base_url = (base_url or os.environ.get("FORGE_SIM_URL", DEFAULT_URL)).rstrip("/")
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: Optional[Mapping] = None) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Accept": "application/json", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            body = _parse(exc.read() if hasattr(exc, "read") else b"")
            msg = (body.get("error") if isinstance(body, dict) else None) or exc.reason or f"HTTP {exc.code}"
            if exc.code == 409:
                raise SimBusy(f"simulation daemon is busy: {msg}",
                              job_id=body.get("job_id") if isinstance(body, dict) else None, body=body) from exc
            raise SimError(f"simulation daemon returned {exc.code} for {method} {path}: {msg}", exc.code, body) from exc
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise SimUnavailable(f"simulation daemon unreachable at {self.base_url}: {getattr(exc, 'reason', exc)}") from exc
        body = _parse(raw)
        if body is None:
            raise SimError(f"simulation daemon sent a non-JSON reply for {method} {path}")
        return body

    def health(self) -> dict:
        return self._request("GET", "/health")

    def is_up(self) -> bool:
        try:
            return bool(self.health().get("ok"))
        except SimClientError:
            return False

    def list_opponents(self) -> dict:
        return self._request("GET", "/opponents")

    def start_job(self, payload: Mapping) -> dict:
        return self._request("POST", "/jobs", dict(payload))

    def get_job(self, job_id: str) -> dict:
        return self._request("GET", f"/jobs/{job_id}")


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8")) if raw else None
    except (ValueError, UnicodeDecodeError):
        return None


def build_job_payload(deck: dict, *, games: int = 10, opponent_mode: str = "random",
                      opponents: Optional[List[str]] = None, clock_timeout: int = 180, **extra: Any) -> dict:
    """POST /jobs body for a Commander Lab deck."""
    name, cmdr, dck = export_deck_to_dck(deck)
    payload: Dict[str, Any] = {"deck_id": int(deck["id"]), "deck_name": name, "commander": cmdr, "dck_text": dck,
                               "games": games, "clock_timeout": clock_timeout, "opponent_mode": opponent_mode}
    if opponents:
        payload["opponents"] = list(opponents)
    payload.update(extra)
    return payload
