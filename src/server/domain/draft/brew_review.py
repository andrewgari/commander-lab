"""Oracle review gate for Brew results.

Every finished brew draft is reviewed before it is returned. Two layers:

1. Deterministic gate (always runs, no network): exactly 100 cards, no
   UUID-shaped names, and the draft passes deck_validator (legality, colour
   identity, singleton).
2. LLM review: the finished deck list only, sent through the in-repo LLM
   factory (infra.llm.factory, task "brew_review") with the prompt held in
   knowledge/tasks/brew_review.md (MTG rules and power/bracket fit). Oracle replies with JSON {"verdict": "pass"|"fail",
   "reasons": [...]}.

The result is stored on the draft as ``oracle_verdict``:
``{"status": "pass"|"fail"|"unavailable", "reasons": [...], "source": ...}``.
Anything but "pass" means the brew is failed, not shown as finished.
The reviewer is pluggable (set_reviewer) so tests need no network.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, List, Optional

from src.server.domain.draft.card_resolution import looks_like_id
from src.server.domain.llm.port import LLMError, LLMTruncatedError

PASS, FAIL, UNAVAILABLE = "pass", "fail", "unavailable"
DECK_SIZE = 100
RETRY_MAX_TOKENS = 16000

Reviewer = Callable[[dict, dict], dict]  # (draft, session) -> verdict dict


def _total(draft: dict) -> int:
    return sum(int(c.get("quantity", 1) or 1) for c in draft.get("cards") or [])


def fingerprint(draft: dict) -> str:
    """Hash of what a verdict is about: cards, commanders and target power."""
    import hashlib

    cards = sorted((str(c.get("name")), int(c.get("quantity", 1) or 1)) for c in draft.get("cards") or [])
    payload = json.dumps([cards, sorted(draft.get("commanders") or []), draft.get("target_power")],
                         sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def verdict_is_current(draft: dict) -> bool:
    """True only for a stored pass issued for exactly this deck."""
    v = draft.get("oracle_verdict") or {}
    return v.get("status") == PASS and v.get("fingerprint") == fingerprint(draft)


def deterministic_check(draft: dict, r: Any = None) -> List[str]:
    """Hard gate reasons (empty list = clean)."""
    reasons: List[str] = []
    cards = draft.get("cards") or []
    names = [c.get("name") for c in cards]
    total = _total(draft)
    if total != DECK_SIZE:
        reasons.append(f"deck has {total} cards, expected {DECK_SIZE}")
    bad = [n for n in names if not isinstance(n, str) or not n.strip() or looks_like_id(n)]
    if bad:
        reasons.append(f"{len(bad)} card(s) have an id instead of a name: {bad[:3]}")
    if r is not None and not bad:
        try:
            from src.server.domain.draft.deck_validator import (
                DraftDeck, catalog_from_redis, validate_decks, draft_card_names,
            )
            deck = DraftDeck.from_builder_draft(draft)
            catalog = catalog_from_redis(r, draft_card_names([deck]))
            result = validate_decks([deck], catalog, exempt_basics_from_pool=True)
            for e in getattr(result, "errors", [])[:10]:
                if getattr(getattr(e, "code", None), "value", "") in _HARD_CODES:
                    reasons.append(str(getattr(e, "message", e)))
        except Exception as exc:  # validator trouble must not pass silently
            reasons.append(f"validator error: {type(exc).__name__}: {exc}")
    return reasons


# Rule codes that fail a brew (pool/availability codes are not legality).
_HARD_CODES = {"BANNED", "NOT_LEGAL", "COLOR_IDENTITY", "SINGLETON", "UNKNOWN_LEGALITY"}


_client_factory: Optional[Callable[[str], Any]] = None


def set_client_factory(factory: Optional[Callable[[str], Any]]) -> None:
    """Inject the chat-client factory (task -> client); wired by the app layer at startup."""
    global _client_factory
    _client_factory = factory


def _default_client():
    if _client_factory is None:
        raise LLMError("no chat client factory configured")
    return _client_factory("brew_review")


def _game_changers_line(draft: dict) -> str:
    """Game Changers in this deck, from the checked-in official snapshot, so the reviewer never has to
    claim the list is unavailable. Degrades to an explicit 'unknown, do not fail on it' note."""
    try:
        from src.server.domain.draft import brew_power

        gcs = brew_power.game_changer_names()
        if not gcs:
            raise ValueError("empty snapshot")
        found = sorted({str(c.get("name")) for c in draft.get("cards") or []
                        if brew_power._key(c.get("name")) in gcs})
        return "Game Changers in this deck (official list): " + (", ".join(found) if found else "none")
    except Exception:
        return ("Game Changers in this deck: could not be determined. Do not fail the deck for this; "
                "judge bracket fit from the card list only.")


def _oracle_reviewer(draft: dict, session: dict, client: Any = None) -> dict:
    from src.server.domain.knowledge import build_messages

    try:
        client = client or _default_client()
        lines = [f"{c.get('quantity', 1)} {c.get('name')}" for c in draft.get("cards") or []]
        user = (
            f"Commander(s): {', '.join(draft.get('commanders') or [])}\n"
            f"Target bracket (1-5, hard limit): {draft.get('target_bracket', 3)}\n"
            f"Target power (1-10, soft guide): {draft.get('target_power')}\n"
            f"Estimated power: {draft.get('estimated_power')}\n"
            f"{_game_changers_line(draft)}\n"
            "Deck list:\n" + "\n".join(lines)
        )
        messages, _ = build_messages("brew_review", user_text=user)
        try:
            text = client.chat(messages)
        except LLMTruncatedError:
            # Reasoning tokens or a wordy reply can eat the budget: retry once with a larger one.
            base = getattr(client, "max_tokens", None) or 6000
            text = client.chat(messages, max_tokens=min(base * 2, RETRY_MAX_TOKENS))
    except LLMError as exc:  # includes LLMNotConfigured / LLMUnavailable
        return {"status": UNAVAILABLE, "reasons": [str(exc)], "source": "oracle"}
    m = re.search(r"\{.*\}", text or "", re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    verdict = str(data.get("verdict", "")).lower()
    reasons = [str(x) for x in data.get("reasons") or []]
    if verdict not in (PASS, FAIL):
        return {"status": FAIL, "reasons": ["oracle reply was not a valid verdict"], "source": "oracle"}
    return {"status": verdict, "reasons": reasons, "source": "oracle"}


_reviewer: Reviewer = _oracle_reviewer


def get_reviewer() -> Reviewer:
    return _reviewer


def set_reviewer(reviewer: Optional[Reviewer]) -> None:
    """Swap the reviewer (tests); None restores the oracle reviewer."""
    global _reviewer
    _reviewer = reviewer or _oracle_reviewer


def review_draft(draft: dict, session: dict, r: Any = None) -> dict:
    """Attach and return ``draft['oracle_verdict']``. Never raises."""
    reasons = deterministic_check(draft, r)
    if reasons:
        verdict = {"status": FAIL, "reasons": reasons, "source": "deterministic"}
    else:
        try:
            verdict = get_reviewer()(draft, session)
        except Exception as exc:
            verdict = {"status": UNAVAILABLE, "reasons": [f"{type(exc).__name__}: {exc}"], "source": "reviewer"}
    verdict.setdefault("reasons", [])
    verdict["fingerprint"] = fingerprint(draft)
    draft["oracle_verdict"] = verdict
    return verdict
