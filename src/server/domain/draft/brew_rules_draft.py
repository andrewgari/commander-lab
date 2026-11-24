"""Draft Brew hard rules from the session's notes (pluggable, like brew_review).

``draft_rules(session)`` returns ``{"rules": [...valid rules...], "dropped": [{"rule", "error"}], "note": str}``.
The default drafter asks the LLM task "brew_rules" (prompt: knowledge/tasks/brew_rules.md) and works
with no LLM credentials by returning an empty draft plus a note. Whatever the drafter returns is
validated by brew_rules; invalid items are dropped and reported, never silently enforced.
Nothing here is enforced: the user must approve rules first (brew.approve_rules).
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, List, Optional

from src.server.domain.draft import brew_rules
from src.server.domain.llm.port import LLMError

Drafter = Callable[[dict], Any]  # session -> list of rule dicts | {"rules": [...], "note": str}

_client_factory: Optional[Callable[[str], Any]] = None


def set_client_factory(factory: Optional[Callable[[str], Any]]) -> None:
    """Inject the chat-client factory (task -> client); wired by the app layer at startup."""
    global _client_factory
    _client_factory = factory


def _default_client():
    if _client_factory is None:
        raise LLMError("no chat client factory configured")
    return _client_factory("brew_rules")


def _llm_drafter(session: dict, client: Any = None) -> dict:
    from src.server.domain.knowledge import build_messages

    notes = (session.get("notes") or "").strip()
    if not notes:
        return {"rules": [], "note": "No notes to draft rules from."}
    try:
        client = client or _default_client()
        user = f"Commander(s): {', '.join(session.get('commanders') or [])}\nNotes:\n{notes}"
        messages, _ = build_messages("brew_rules", user_text=user)
        text = client.chat(messages)
    except LLMError as exc:  # includes LLMNotConfigured / LLMUnavailable
        return {"rules": [], "note": f"Rule drafting unavailable ({exc}); add any hard rules by hand."}
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    rules = data.get("rules") if isinstance(data, dict) else None
    if not isinstance(rules, list):
        return {"rules": [], "note": "The drafter reply was not valid; add any hard rules by hand."}
    return {"rules": rules, "note": ""}


_drafter: Drafter = _llm_drafter


def get_drafter() -> Drafter:
    return _drafter


def set_drafter(drafter: Optional[Drafter]) -> None:
    """Swap the drafter (tests); None restores the LLM drafter."""
    global _drafter
    _drafter = drafter or _llm_drafter


def draft_rules(session: dict) -> dict:
    """Run the drafter and validate its output. Never raises."""
    try:
        out = get_drafter()(session)
    except Exception as exc:
        out = {"rules": [], "note": f"Rule drafting failed ({type(exc).__name__}: {exc}); add any hard rules by hand."}
    if isinstance(out, list):
        out = {"rules": out, "note": ""}
    if not isinstance(out, dict):
        out = {"rules": [], "note": "The drafter returned nothing usable."}
    rules, errors = brew_rules.validate_rules(out.get("rules"))
    dropped = [{"rule": e["rule"], "error": e["error"]} for e in errors]
    note = str(out.get("note") or "")
    if dropped:
        note = (note + " " if note else "") + f"{len(dropped)} drafted rule(s) were invalid and dropped."
    return {"rules": rules, "dropped": dropped, "note": note}
