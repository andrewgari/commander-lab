#!/usr/bin/env python3
"""Forge log watcher: zero-touch shipping of Forge games to Commander Lab.

Tails ~/.forge/forge.log (and rotated forge.*.log), buffers lines until a
complete game ends (MATCH_RESULTS event or "Game Result" line), then POSTs the
block to Commander Lab's /api/forge/ingest. Re-delivery is safe: the server
dedupes on the game content hash. After new games land, pushes the refreshed
play-style profile text to Honcho (peer card + a session message so Honcho's
deriver can form conclusions) for the oracle peer.

Stdlib only. Env overrides:
  FORGE_LOG_DIR      default ~/.forge
  COMMANDER_LAB_URL  default http://tower:8020
  HONCHO_URL         default http://tower:8100
  HONCHO_WORKSPACE   default oracle
  HONCHO_PEER        default andrewgari
  HONCHO_SESSION     default forge-play-style
  HONCHO_PUSH        default 1 (set 0 to disable)
  POLL_SECONDS       default 30
  STATE_FILE         default ~/.cache/forge-recorder/state.json

Flags: --once (single pass, then exit), --backfill (process whole logs from
offset 0 once — existing state is kept for files it already has).
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

FORGELOG_RE = re.compile(r"^FORGELOG\|([A-Z_]+)\|")
GAME_RESULT_RE = re.compile(r"^Game Result: Game \d+ ended")
TURN_ONE_RE = re.compile(r"^FORGELOG\|TURN\|Turn 1 \(")
OUTCOME_RE = re.compile(r"^FORGELOG\|GAME_OUTCOME\|")

LOG_DIR = os.path.expanduser(os.environ.get("FORGE_LOG_DIR", "~/.forge"))
CL_URL = os.environ.get("COMMANDER_LAB_URL", "http://tower:8020").rstrip("/")
HONCHO_URL = os.environ.get("HONCHO_URL", "http://tower:8100").rstrip("/")
HONCHO_WS = os.environ.get("HONCHO_WORKSPACE", "oracle")
HONCHO_PEER = os.environ.get("HONCHO_PEER", "andrewgari")
HONCHO_SESSION = os.environ.get("HONCHO_SESSION", "forge-play-style")
HONCHO_PUSH = os.environ.get("HONCHO_PUSH", "1").lower() not in ("0", "false", "no")
POLL = float(os.environ.get("POLL_SECONDS", "30"))
STATE_FILE = os.path.expanduser(
    os.environ.get("STATE_FILE", "~/.cache/forge-recorder/state.json"))

MAX_POST_LINES = 20000  # safety cap; a huge Commander game is ~3-4k lines
FLUSH_IDLE_SECONDS = float(os.environ.get("FLUSH_IDLE_SECONDS", "120"))


def log(msg: str) -> None:
    print(f"[forge-watcher {datetime.now(timezone.utc).isoformat(timespec='seconds')}] {msg}",
          flush=True)


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"files": {}, "buffers": {}}


def save_state(state: dict) -> None:
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_FILE)


def http_json(method: str, url: str, payload: dict | None = None,
              timeout: float = 20.0) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except ValueError:
            return e.code, {}
    except (urllib.error.URLError, OSError) as e:
        return 0, {"error": str(e)}


def log_files() -> list[str]:
    files = sorted(glob.glob(os.path.join(LOG_DIR, "forge.*.log")))
    current = os.path.join(LOG_DIR, "forge.log")
    if os.path.exists(current):
        files.append(current)
    return files


def drain_new_lines(state: dict, path: str, backfill: bool) -> None:
    """Read new bytes from `path` into its buffer; track offset."""
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    fstate = state["files"].get(path, {})
    offset = 0 if backfill and path not in state["files"] else fstate.get("offset", 0)
    if size < offset:  # rotated/truncated
        offset = 0
        state["buffers"][path] = {"lines": [], "last_append": 0.0}
    if size == offset:
        return
    with open(path, "r", errors="replace") as f:
        f.seek(offset)
        chunk = f.read(size - offset)
    state["files"][path] = {"offset": size, "size": size}
    buf = state["buffers"].setdefault(path, {"lines": [], "last_append": 0.0})
    if isinstance(buf, list):  # legacy state shape
        buf = state["buffers"][path] = {"lines": buf, "last_append": 0.0}
    buf["lines"].extend(chunk.splitlines())
    buf["last_append"] = time.time()


def extract_ready_games(buf: list[str], idle_for: float) -> tuple[list[list[str]], list[str]]:
    """Split the buffer into games ready to ship. Returns (games, rest).

    A game is shipped whole (including any strays flushed late by Forge's
    worker threads) when:
    - the NEXT game starts (Turn 1 after an outcome): everything before it is
      one complete game; or
    - the buffer ends with a finished game (outcome seen) and no new lines
      have arrived for FLUSH_IDLE_SECONDS (match over / Forge closed).
    """
    boundaries: list[int] = []  # indices where a new game starts
    seen_outcome = False
    for i, line in enumerate(buf):
        if TURN_ONE_RE.match(line) and i > 0 and seen_outcome:
            boundaries.append(i)
            seen_outcome = False
        if OUTCOME_RE.match(line):
            seen_outcome = True
    games: list[list[str]] = []
    rest = buf
    for b in boundaries:
        games.append(rest[:b])
        rest = rest[b:]
    if rest and idle_for >= FLUSH_IDLE_SECONDS:
        has_terminator = any(
            OUTCOME_RE.match(ln) or GAME_RESULT_RE.match(ln)
            or ln.startswith("FORGELOG|MATCH_RESULTS|") for ln in rest)
        if has_terminator:
            games.append(rest)
            rest = []
    return games, rest


def post_game(block: list[str], played_at: str) -> tuple[bool, bool]:
    """POST one game block. Returns (accepted, stored_new). accepted=False on
    network/5xx failure — the caller keeps the block buffered for the next
    pass. 4xx means the data itself is rejected: accepted=True, not stored."""
    if len(block) > MAX_POST_LINES:
        block = block[-MAX_POST_LINES:]
    status, resp = http_json("POST", f"{CL_URL}/api/forge/ingest",
                             {"lines": block, "played_at": played_at})
    if status == 200 and resp.get("success"):
        log(f"ingest ok: stored={len(resp.get('stored', []))} "
            f"dupes={len(resp.get('duplicates', []))}")
        return True, bool(resp.get("stored"))
    if 400 <= status < 500:
        # Poison block: the server rejected the data itself. Drop it rather
        # than retry forever; --backfill can re-send after a fix.
        log(f"ingest REJECTED status={status} resp={resp} (dropping block)")
        return True, False
    log(f"ingest FAILED status={status} resp={resp} (will retry)")
    return False, False


def push_honcho() -> None:
    status, prof = http_json("GET", f"{CL_URL}/api/forge/play-style")
    if status != 200 or not prof.get("success"):
        log(f"honcho push skipped: profile fetch status={status}")
        return
    text = prof.get("text") or ""
    if not text.strip():
        return
    # Workspace + session are idempotent-ish creates; tolerate 'exists' errors.
    http_json("POST", f"{HONCHO_URL}/v3/workspaces",
              {"id": HONCHO_WS, "metadata": {"purpose": "oracle peer memory"}})
    http_json("POST", f"{HONCHO_URL}/v3/workspaces/{HONCHO_WS}/sessions",
              {"id": HONCHO_SESSION,
               "metadata": {"purpose": "Forge play-style profile updates"},
               "peers": {HONCHO_PEER: {}, "forge-recorder": {}}})
    card_lines = [ln for ln in text.splitlines() if ln.strip()][:25]
    st, resp = http_json(
        "PUT", f"{HONCHO_URL}/v3/workspaces/{HONCHO_WS}/peers/{HONCHO_PEER}/card",
        {"peer_card": card_lines})
    log(f"honcho peer card PUT status={st}")
    st, resp = http_json(
        "POST", f"{HONCHO_URL}/v3/workspaces/{HONCHO_WS}/sessions/{HONCHO_SESSION}/messages",
        {"messages": [{"peer_id": "forge-recorder",
                       "content": f"Play-style profile update for {HONCHO_PEER} "
                                  f"(auto-recorded from Forge games):\n{text}",
                       "metadata": {"source": "forge-recorder",
                                    "games_analyzed": prof.get("games_analyzed")}}]})
    log(f"honcho message POST status={st}")


def run_pass(state: dict, backfill: bool = False) -> None:
    any_stored = False
    now = time.time()
    for path in log_files():
        drain_new_lines(state, path, backfill)
        buf = state["buffers"].get(path)
        if not buf:
            continue
        if isinstance(buf, list):  # legacy state shape
            buf = state["buffers"][path] = {"lines": buf, "last_append": 0.0}
        if not buf["lines"]:
            continue
        games, rest = extract_ready_games(buf["lines"], now - buf["last_append"])
        if games:
            played_at = datetime.fromtimestamp(
                os.path.getmtime(path), tz=timezone.utc).isoformat()
            shipped = 0
            for i, block in enumerate(games):
                accepted, stored_new = post_game(block, played_at)
                if not accepted:
                    # Keep this and later blocks buffered for the next pass.
                    buf["lines"] = games[i:] + rest
                    break
                any_stored = any_stored or stored_new
                shipped += 1
            else:
                buf["lines"] = rest
            if shipped:
                log(f"{os.path.basename(path)}: shipped {shipped} game block(s)")
    if any_stored and HONCHO_PUSH:
        push_honcho()


def main() -> int:
    once = "--once" in sys.argv
    backfill = "--backfill" in sys.argv
    log(f"watching {LOG_DIR} -> {CL_URL} (honcho={HONCHO_URL}/{HONCHO_WS}:{HONCHO_PEER})")
    while True:
        state = load_state()
        try:
            run_pass(state, backfill)
        except Exception as e:  # never die on one bad pass
            log(f"pass error: {e!r}")
        save_state(state)
        if once:
            return 0
        backfill = False
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main())
