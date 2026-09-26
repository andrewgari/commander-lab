"""Moxfield write client — create decks from Archidekt decklists.

Moxfield has no public write API. These endpoints were read out of Moxfield's
own app bundle (`assets.moxfield.net/assets/assets/moxfield.*.js`) and are
what the site itself calls:

    POST /v1/deck-folders                 {"name": str}          -> folder
    GET  /v1/deck-folders?pageNumber&pageSize                    -> folders
    POST /v3/decks                        create deck (see CREATE_DECK below)
    PUT  /v2/decks/{id}                   update deck (description etc.)
         extra headers: X-Public-Deck-ID, X-Deck-Version

CREATE_DECK body keys (verbatim from the bundle's CREATE_DECK action):
    name, format, visibility, commanderCardId, partnerCardId, mainboard,
    sideboard, maybeboard, importText, commanderSignatureSpellCardId,
    partnerSignatureSpellCardId, pricingProvider, setId, deckFolderId,
    usePrefPrintings

Auth is a bearer token: the app sends `Authorization: Bearer {accessToken}`
plus `X-Session-User: {userId}`, both obtained from the login session. We get
them by replaying `POST /v1/account/token/refresh` with the Playwright
session cookies captured by scripts/setup_auth.py (see
docs/MOXFIELD_TRANSCRIBE.md) — we never handle the password ourselves.

Read paths (deck/folder listing) stay in providers/moxfield.py; this module is
writes only.
"""
import json
import os
import re
import time
from typing import Optional

import requests

API_BASE = "https://api2.moxfield.com"

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# Moxfield rejects folder names outside this set with
# "Folder name must only contain letters, numbers, spaces, and hyphens."
FOLDER_NAME_RE = re.compile(r"^[A-Za-z0-9 \-]+$")

VISIBILITIES = {"public", "unlisted", "private"}

# Writes are heavier than reads; Moxfield rate-limits aggressively.
WRITE_DELAY_SECONDS = 1.5

DEFAULT_SESSION_PATH = ".auth/state.json"


class MoxfieldAuthError(ValueError):
    """Raised when no usable Moxfield session/token is available."""


class MoxfieldWriteError(ValueError):
    """Raised when Moxfield rejects a create/update call."""


def _load_session_cookies(session_path: Optional[str] = None) -> dict:
    """Cookie name->value for moxfield.com out of a Playwright storage_state
    file (scripts/setup_auth.py writes .auth/state.json).

    We only read cookies here — no password ever passes through this code.
    """
    path = session_path or os.getenv("MOXFIELD_SESSION_FILE", DEFAULT_SESSION_PATH)
    if not os.path.exists(path):
        raise MoxfieldAuthError(
            f"no Moxfield session at {path} — run `python scripts/setup_auth.py` "
            "and log in to Moxfield in the browser it opens "
            "(see docs/MOXFIELD_TRANSCRIBE.md)"
        )

    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError) as exc:
        raise MoxfieldAuthError(f"could not read Moxfield session file {path}: {exc}") from exc

    cookies = {
        c["name"]: c["value"]
        for c in state.get("cookies", [])
        if "moxfield.com" in (c.get("domain") or "")
    }
    if not cookies:
        raise MoxfieldAuthError(
            f"{path} has no moxfield.com cookies — re-run scripts/setup_auth.py "
            "and make sure you are fully logged in to Moxfield before pressing ENTER"
        )
    return cookies


def _user_id_from_state(session_path: Optional[str] = None) -> Optional[str]:
    """Find the signed-in Moxfield userId.

    Two sources, in order:
      1. `.auth/moxfield.json` — written explicitly by scripts/setup_auth.py,
         which reads it out of the live page's localStorage. This is the
         reliable one.
      2. The Playwright storage_state `origins[].localStorage` — best-effort,
         since Playwright only captures localStorage for origins the browser
         actually visited and Moxfield's key name changes between releases.
    """
    path = session_path or os.getenv("MOXFIELD_SESSION_FILE", DEFAULT_SESSION_PATH)

    sidecar = os.path.join(os.path.dirname(path) or ".", "moxfield.json")
    if os.path.exists(sidecar):
        try:
            with open(sidecar, "r", encoding="utf-8") as handle:
                user_id = (json.load(handle) or {}).get("userId")
            if user_id:
                return str(user_id)
        except (OSError, ValueError):
            pass

    try:
        with open(path, "r", encoding="utf-8") as handle:
            state = json.load(handle)
    except (OSError, ValueError):
        return None

    for origin in state.get("origins", []):
        if "moxfield.com" not in (origin.get("origin") or ""):
            continue
        for item in origin.get("localStorage", []):
            try:
                value = json.loads(item.get("value") or "")
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("userId"):
                return str(value["userId"])
    return None


class MoxfieldClient:
    """Authenticated Moxfield write client.

    The access token is short-lived, so it is fetched on construction via the
    session cookies and refreshed automatically on a 401 retry.
    """

    def __init__(self, session_path: Optional[str] = None, user_id: Optional[str] = None):
        self.session_path = session_path
        self.cookies = _load_session_cookies(session_path)
        self.user_id = (
            user_id
            or os.getenv("MOXFIELD_USER_ID")
            or _user_id_from_state(session_path)
        )
        self.access_token = None
        self.refresh()

    # -- auth ------------------------------------------------------------

    def refresh(self) -> str:
        """Exchange the session cookies for a bearer access token."""
        if not self.user_id:
            raise MoxfieldAuthError(
                "could not determine your Moxfield userId — set MOXFIELD_USER_ID "
                "in .env (it is the `userId` in the Moxfield session) or re-run "
                "scripts/setup_auth.py"
            )

        res = requests.post(
            f"{API_BASE}/v1/account/token/refresh",
            json={"userId": self.user_id, "isAppLogin": False},
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
            cookies=self.cookies,
            timeout=30,
        )
        if res.status_code != 200:
            raise MoxfieldAuthError(
                f"Moxfield token refresh failed ({res.status_code}: {res.text[:120]}) — "
                "the saved session has probably expired; re-run scripts/setup_auth.py"
            )

        token = (res.json() or {}).get("access_token")
        if not token:
            raise MoxfieldAuthError("Moxfield token refresh returned no access_token")

        self.access_token = token
        return token

    def _headers(self, extra: Optional[dict] = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Origin": "https://moxfield.com",
            "Referer": "https://moxfield.com/",
        }
        if self.user_id:
            headers["X-Session-User"] = str(self.user_id)
        if extra:
            headers.update(extra)
        return headers

    def _request(self, method: str, path: str, *, retry_auth: bool = True, **kwargs):
        res = requests.request(
            method,
            f"{API_BASE}{path}",
            headers=self._headers(kwargs.pop("extra_headers", None)),
            cookies=self.cookies,
            timeout=60,
            **kwargs,
        )
        if res.status_code == 401 and retry_auth:
            # Token expired mid-run: refresh once and replay.
            self.refresh()
            return self._request(method, path, retry_auth=False, **kwargs)
        return res

    # -- folders ---------------------------------------------------------

    def list_folders(self) -> list:
        res = self._request("GET", "/v1/deck-folders", params={"pageNumber": 1, "pageSize": 1000})
        if res.status_code != 200:
            raise MoxfieldWriteError(f"could not list folders ({res.status_code}: {res.text[:120]})")
        payload = res.json()
        # The endpoint has returned both a bare list and a {data: [...]} page
        # wrapper depending on version; accept either.
        if isinstance(payload, dict):
            return payload.get("data") or []
        return payload or []

    def create_folder(self, name: str) -> dict:
        if not FOLDER_NAME_RE.match(name or ""):
            raise MoxfieldWriteError(
                f"invalid folder name {name!r}: Moxfield allows only letters, "
                "numbers, spaces and hyphens"
            )
        res = self._request("POST", "/v1/deck-folders", json={"name": name})
        if res.status_code not in (200, 201):
            raise MoxfieldWriteError(f"could not create folder {name!r} ({res.status_code}: {res.text[:160]})")
        return res.json()

    def ensure_folder(self, name: str) -> dict:
        """Find the folder named `name` (case-insensitive) or create it.
        Idempotent: re-running a transcribe never makes a second folder."""
        target = (name or "").strip().casefold()
        for folder in self.list_folders():
            if (folder.get("name") or "").strip().casefold() == target:
                return folder
        return self.create_folder(name)

    # -- decks -----------------------------------------------------------

    def create_deck(
        self,
        name: str,
        import_text: str,
        visibility: str = "private",
        folder_id: Optional[str] = None,
        deck_format: str = "commander",
    ) -> dict:
        """Create a deck from MTGO-format `import_text`.

        Moxfield resolves the card names itself from importText, which is why
        we don't have to map every name to a Moxfield card id.
        """
        if visibility not in VISIBILITIES:
            raise MoxfieldWriteError(f"invalid visibility: {visibility}")
        if not (name or "").strip():
            raise MoxfieldWriteError("deck name is required")
        if not (import_text or "").strip():
            raise MoxfieldWriteError("import_text is required (nothing to transcribe)")

        body = {
            "name": name,
            "format": deck_format,
            "visibility": visibility,
            "importText": import_text,
            "usePrefPrintings": True,
        }
        if folder_id:
            body["deckFolderId"] = folder_id

        res = self._request("POST", "/v3/decks", json=body)
        if res.status_code not in (200, 201):
            raise MoxfieldWriteError(
                f"could not create deck {name!r} ({res.status_code}: {res.text[:200]})"
            )
        return res.json()

    def update_deck_description(self, deck: dict, description: str) -> dict:
        """Set a deck's description. Moxfield's update endpoint is a full PUT
        that also needs the optimistic-concurrency headers its own client
        sends (X-Public-Deck-ID + X-Deck-Version), so unknown fields are
        echoed back from the created deck rather than invented here.
        """
        deck_id = deck.get("id")
        public_id = deck.get("publicId")
        if not deck_id or not public_id:
            raise MoxfieldWriteError("deck payload is missing id/publicId")

        body = {
            "name": deck.get("name"),
            "format": deck.get("format", "commander"),
            "description": description,
            "visibility": deck.get("visibility", "private"),
            "areCommentsEnabled": deck.get("areCommentsEnabled", True),
        }
        res = self._request(
            "PUT",
            f"/v2/decks/{deck_id}",
            json=body,
            extra_headers={
                "X-Public-Deck-ID": str(public_id),
                "X-Deck-Version": str(deck.get("version", 0)),
            },
        )
        if res.status_code not in (200, 204):
            raise MoxfieldWriteError(
                f"could not set description on {deck.get('name')!r} "
                f"({res.status_code}: {res.text[:200]})"
            )
        return deck


def throttle() -> None:
    """Sleep between write calls. Moxfield rate-limits writes harder than
    reads; callers loop over decks, so this keeps a bulk transcribe from
    tripping a 429 halfway through."""
    time.sleep(WRITE_DELAY_SECONDS)
