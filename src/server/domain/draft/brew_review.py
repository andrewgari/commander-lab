"""Oracle review gate for Brew results.

Every finished brew draft is reviewed before it is returned. Two layers:

1. Deterministic gate (always runs, no network): exactly 100 cards, no
   UUID-shaped names, and the draft passes deck_validator (legality, colour
   identity, singleton).
2. Oracle review: the finished deck list only, sent to the `oracle` Hermes
   profile via analytics.oracle_client against brew-domain-v1 (MTG rules and
   power/bracket fit). Oracle replies with JSON {"verdict": "pass"|"fail",
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
from src.server.domain.llm.port import LLMError

PASS, FAIL, UNAVAILABLE = "pass", "fail", "unavailable"
DECK_SIZE = 100

SYSTEM = (
    "You review a finished Magic: The Gathering Commander deck list against "
    "brew-domain-v1. Check MTG rules (legality, colour identity, singleton, "
    "100 cards) and power/bracket fit for the requested target power. Review "
    "the deck list only. Reply with JSON only: "
    '{"verdict": "pass" | "fail", "reasons": ["..."]}.'
)

Reviewer = Callable[[dict, dict], dict]  # (draft, session) -> verdict dict


def _total(draft: dict) -> int:
    return sum(int(c.get("quantity", 1) or 1) for c in draft.get("cards") or [])


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


def _oracle_reviewer(draft: dict, session: dict) -> dict:
    from src.server.domain.analytics.oracle_client import OracleClient

    try:
        client = OracleClient.from_env()
        lines = [f"{c.get('quantity', 1)} {c.get('name')}" for c in draft.get("cards") or []]
        user = (
            f"Commander(s): {', '.join(draft.get('commanders') or [])}\n"
            f"Target power (1-10): {draft.get('target_power')}\n"
            f"Estimated power: {draft.get('estimated_power')}\n"
            "Deck list:\n" + "\n".join(lines)
        )
        text = client.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}])
    except LLMError as exc:
        return {"status": UNAVAILABLE, "reasons": [str(exc)], "source": "oracle"}
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except ValueError:
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
    draft["oracle_verdict"] = verdict
    return verdict
