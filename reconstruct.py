"""
Reconstruct flow for Commander Lab.

Reconstruct is a decommission variant:
  1. One or two physical source decks are selected.
  2. Snapshots of both source decklists are captured BEFORE retirement.
  3. Source decks are decommissioned through the existing decommission process
     (status -> "retired", tag removed, instances unassigned from in_deck to in_collection).
  4. A Reconstruct session is created in Redis linking to the source decks.
  5. User submits target deck preferences (for up to 2 decks).
  6. Build is triggered, invoking a pluggable builder interface to generate drafts.
  7. User can view drafts, regenerate, and accept or abandon.
  8. Accepting creates the new deck(s) and auto-binds cards as dedicated (in_deck).
"""
import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

import instances as instance_store
import registry

RECONSTRUCT_SESSION_PREFIX = "reconstruct:session:"
RECONSTRUCT_SESSIONS_INDEX = "reconstruct:sessions"

STATUS_PENDING_PREFERENCES = "pending_preferences"
STATUS_BUILDING = "building"
STATUS_DRAFTS_READY = "drafts_ready"
STATUS_ACCEPTED = "accepted"
STATUS_ABANDONED = "abandoned"
STATUS_FAILED = "failed"

VALID_STATUSES = {
    STATUS_PENDING_PREFERENCES,
    STATUS_BUILDING,
    STATUS_DRAFTS_READY,
    STATUS_ACCEPTED,
    STATUS_ABANDONED,
    STATUS_FAILED,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_key(session_id: str) -> str:
    return f"{RECONSTRUCT_SESSION_PREFIX}{session_id}"


def _save_session(r: Any, session: dict) -> None:
    session_id = session["session_id"]
    r.set(_session_key(session_id), json.dumps(session))
    r.sadd(RECONSTRUCT_SESSIONS_INDEX, session_id)


class BaseReconstructBuilder:
    """Pluggable builder interface for generating Reconstruct draft decks.
    A separate task (t_c5954272) implements the full LLM-based agent builder.
    """

    def build(self, session: dict, r: Any) -> list[dict]:
        """Given a Reconstruct session dict (with source_decks and preferences)
        and Redis client r, return a list of draft deck dicts (up to 2).

        Each draft deck dict format:
        {
            "id": str,
            "name": str,
            "commanders": [str, ...],
            "themes": [str, ...],
            "strategies": [str, ...],
            "cards": [
                {
                    "name": str,
                    "quantity": int,
                    "tier": 1 | 2 | 3,
                    "tier_label": "previous_deck" | "owned" | "new",
                    "cost": float,
                    "rationale": str
                }, ...
            ],
            "total_new_cost": float,
            "explanation": str
        }
        """
        raise NotImplementedError("Builder must implement build(session, r)")


class StubReconstructBuilder(BaseReconstructBuilder):
    """Default stub builder that generates mock draft decks matching target
    preferences and source deck card pools.
    """

    def build(self, session: dict, r: Any) -> list[dict]:
        preferences = session.get("preferences", [])
        source_decks = session.get("source_decks", [])

        # Collect Tier 1 card candidates from source decks
        source_cards: list[str] = []
        for d in source_decks:
            for c in d.get("cards", []):
                name = c.get("name")
                if name and name not in source_cards:
                    source_cards.append(name)

        drafts = []
        for idx, pref in enumerate(preferences):
            commanders = list(pref.get("commanders", []))
            if not commanders:
                # Fallback to source deck commander if available
                for sd in source_decks:
                    if sd.get("commanders"):
                        commanders = [sd["commanders"][0]]
                        break
                if not commanders:
                    commanders = ["Sol, the Guiding Light"]

            name = pref.get("name") or (
                f"{commanders[0]} Draft" if commanders else f"Reconstructed Draft {idx + 1}"
            )

            cards: list[dict] = []
            used_names = set()

            # Commanders (Tier 1 if from source, else Tier 1/2/3)
            for cmd in commanders:
                is_source = cmd in source_cards
                cards.append({
                    "name": cmd,
                    "quantity": 1,
                    "tier": 1 if is_source else 3,
                    "tier_label": "previous_deck" if is_source else "new",
                    "cost": 0.0 if is_source else 4.00,
                    "rationale": "Designated commander from preferences",
                })
                used_names.add(cmd)

            # Add Tier 1 cards from source decks
            tier_1_added = 0
            for cname in source_cards:
                if cname not in used_names and tier_1_added < 6:
                    cards.append({
                        "name": cname,
                        "quantity": 1,
                        "tier": 1,
                        "tier_label": "previous_deck",
                        "cost": 0.0,
                        "rationale": "Sourced from retired source deck snapshot",
                    })
                    used_names.add(cname)
                    tier_1_added += 1

            # Check collection for Tier 2 candidates if r is available
            tier_2_added = 0
            if r is not None:
                try:
                    collection = instance_store.list_instances(r, ownership_status="in_collection")
                    for inst in collection:
                        cname = inst.get("card_name")
                        if cname and cname not in used_names and tier_2_added < 4:
                            cards.append({
                                "name": cname,
                                "quantity": 1,
                                "tier": 2,
                                "tier_label": "owned",
                                "cost": 0.0,
                                "rationale": "Sourced from undedicated collection",
                            })
                            used_names.add(cname)
                            tier_2_added += 1
                except Exception:
                    pass

            # Add Tier 3 staples (new purchases)
            tier_3_candidates = [
                ("Arcane Signet", 1.25),
                ("Command Tower", 0.50),
                ("Sol Ring", 1.50),
            ]
            for cname, price in tier_3_candidates:
                if cname not in used_names:
                    cards.append({
                        "name": cname,
                        "quantity": 1,
                        "tier": 3,
                        "tier_label": "new",
                        "cost": price,
                        "rationale": "Recommended staple for color identity",
                    })
                    used_names.add(cname)

            total_new_cost = round(
                sum(c.get("cost", 0.0) for c in cards if c.get("tier") == 3), 2
            )

            themes = list(pref.get("themes", []))
            drafts.append({
                "id": f"draft_{idx}",
                "name": name,
                "commanders": commanders,
                "themes": themes,
                "strategies": list(pref.get("strategies", [])),
                "art_style": pref.get("art_style", ""),
                "flavor": pref.get("flavor", ""),
                "notes": pref.get("notes", ""),
                "cards": cards,
                "total_new_cost": total_new_cost,
                "explanation": (
                    f"Draft constructed based on {', '.join(themes) if themes else 'general preferences'}."
                ),
            })

        return drafts


from reconstruct_builder import ReconstructBuildError, ReconstructBuilder

_builder_instance: BaseReconstructBuilder = ReconstructBuilder()


def get_builder() -> BaseReconstructBuilder:
    """Return the active Reconstruct builder implementation."""
    return _builder_instance


def set_builder(builder: BaseReconstructBuilder) -> None:
    """Register a custom or agent-based builder implementation."""
    global _builder_instance
    if not isinstance(builder, BaseReconstructBuilder):
        raise TypeError("builder must inherit from BaseReconstructBuilder")
    _builder_instance = builder


def normalize_preference(pref: dict) -> dict:
    """Validate and normalize a single target deck preference dictionary.

    Schema:
      - commanders: list of str (supports partner, background, friends forever)
      - themes: list of str
      - strategies: list of str (e.g. aggro, control, combo, stax-lite)
      - art_style: str or list of str
      - flavor: str
      - notes: str
    """
    if not isinstance(pref, dict):
        raise ValueError("Each target deck preference must be a JSON object")

    # commanders
    raw_cmd = pref.get("commanders")
    if raw_cmd is None:
        raw_cmd = pref.get("commander")
    if raw_cmd is None:
        commanders = []
    elif isinstance(raw_cmd, str):
        commanders = [raw_cmd.strip()] if raw_cmd.strip() else []
    elif isinstance(raw_cmd, list):
        commanders = [str(c).strip() for c in raw_cmd if str(c).strip()]
    else:
        raise ValueError("commanders must be a list of strings or a single string")

    # themes
    raw_themes = pref.get("themes")
    if raw_themes is None:
        raw_themes = pref.get("theme")
    if raw_themes is None:
        themes = []
    elif isinstance(raw_themes, str):
        themes = [raw_themes.strip()] if raw_themes.strip() else []
    elif isinstance(raw_themes, list):
        themes = [str(t).strip() for t in raw_themes if str(t).strip()]
    else:
        raise ValueError("themes must be a list of strings")

    # strategies
    raw_strategies = pref.get("strategies")
    if raw_strategies is None:
        raw_strategies = pref.get("strategy")
    if raw_strategies is None:
        strategies = []
    elif isinstance(raw_strategies, str):
        strategies = [raw_strategies.strip()] if raw_strategies.strip() else []
    elif isinstance(raw_strategies, list):
        strategies = [str(s).strip() for s in raw_strategies if str(s).strip()]
    else:
        raise ValueError("strategies must be a list of strings")

    # art_style (free text or tags)
    art_style = pref.get("art_style")
    if art_style is None:
        art_style = pref.get("art style", "")
    if isinstance(art_style, list):
        art_style = [str(a).strip() for a in art_style if str(a).strip()]
    elif isinstance(art_style, str):
        art_style = art_style.strip()
    else:
        raise ValueError("art_style must be a string or list of strings")

    # flavor
    flavor = pref.get("flavor", "")
    if not isinstance(flavor, str):
        flavor = str(flavor)
    flavor = flavor.strip()

    # notes
    notes = pref.get("notes")
    if notes is None:
        notes = pref.get("free_form_notes")
    if notes is None:
        notes = pref.get("free-form notes", "")
    if not isinstance(notes, str):
        notes = str(notes)
    notes = notes.strip()

    name = pref.get("name") or pref.get("target_deck_name") or ""
    if not isinstance(name, str):
        name = str(name)
    name = name.strip()

    return {
        "name": name,
        "commanders": commanders,
        "themes": themes,
        "strategies": strategies,
        "art_style": art_style,
        "flavor": flavor,
        "notes": notes,
    }


def start_reconstruct(
    r: Any,
    deck_ids: Any,
    tag: str = registry.DEFAULT_DECOMMISSION_TAG,
) -> dict:
    """Start a Reconstruct flow on 1 or 2 physical decks.

    1. Validates that 1 or 2 decks are requested.
    2. Validates that all requested decks exist, are physical, and not already decommissioned.
    3. Snapshots the source decklists before retirement.
    4. Decommissions each source deck using registry.decommission_decks
       (moves to "retired", removes tag, unassigns in_deck instances).
    5. Creates and stores a Reconstruct session with status 'pending_preferences'.
    """
    if isinstance(deck_ids, str):
        deck_ids = [deck_ids]
    if not isinstance(deck_ids, list) or not deck_ids:
        raise ValueError("deck_ids must be a non-empty list of 1 or 2 deck IDs")
    clean_ids = list(dict.fromkeys(str(i).strip() for i in deck_ids if str(i).strip()))
    if len(clean_ids) == 0:
        raise ValueError("deck_ids must contain valid deck IDs")
    if len(clean_ids) > 2:
        raise ValueError("Reconstruct supports a maximum of 2 source decks")

    decks = registry._load_decks(r)

    # Validate all source decks upfront before mutating anything
    snapshots = []
    canonical_ids = []
    for did in clean_ids:
        target = registry._find_in(decks, did)
        if not target:
            raise ValueError(f"deck not found: {did}")
        if target.get("decommissioned_at"):
            raise registry.DecommissionError(f"deck is already decommissioned: {did}")
        if target.get("status") != "physical":
            raise registry.DecommissionError(
                f"only physical decks can be decommissioned (deck status is {target.get('status')!r})"
            )

        reg_id = registry.registry_id_of(target)
        canonical_ids.append(reg_id)
        # Snapshot captured BEFORE retirement
        snapshots.append({
            "id": target.get("id"),
            "registry_id": reg_id,
            "name": target.get("name"),
            "commanders": list(target.get("commanders") or []),
            "commander_uids": list(target.get("commander_uids") or []),
            "cards": copy.deepcopy(target.get("cards") or []),
            "color": target.get("color", ""),
            "tags": list(target.get("tags") or []),
            "status": target.get("status"),
            "source": target.get("source"),
            "source_id": target.get("source_id"),
            "url": target.get("url", ""),
        })

    # Execute existing decommission on all source decks atomically
    decom_result = registry.decommission_decks(r, canonical_ids, tag=tag)
    if decom_result.get("failed"):
        first_fail = decom_result["failed"][0]
        raise registry.DecommissionError(f"decommission failed for {first_fail['deck_id']}: {first_fail['reason']}")

    now = _now()
    session_id = f"recon_{uuid.uuid4().hex[:12]}"
    session = {
        "session_id": session_id,
        "status": STATUS_PENDING_PREFERENCES,
        "source_deck_ids": canonical_ids,
        "source_decks": snapshots,
        "decommission_results": decom_result["succeeded"],
        "preferences": [],
        "drafts": [],
        "created_at": now,
        "updated_at": now,
    }

    _save_session(r, session)
    return session


def get_session(r: Any, session_id: str) -> Optional[dict]:
    """Fetch a Reconstruct session record by ID."""
    raw = r.get(_session_key(session_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def list_sessions(r: Any, limit: int = 50) -> list[dict]:
    """List recent Reconstruct sessions sorted by created_at descending."""
    ids = r.smembers(RECONSTRUCT_SESSIONS_INDEX) or set()
    sessions = []
    for sid in ids:
        if isinstance(sid, bytes):
            sid = sid.decode("utf-8")
        s = get_session(r, sid)
        if s:
            sessions.append(s)
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return sessions[:limit]


def submit_preferences(r: Any, session_id: str, preferences: Any) -> dict:
    """Submit target deck preferences (for 1 or 2 decks)."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    if session["status"] in {STATUS_ACCEPTED, STATUS_ABANDONED}:
        raise ValueError(f"cannot submit preferences on a session in status '{session['status']}'")

    if isinstance(preferences, dict):
        if "preferences" in preferences and isinstance(preferences["preferences"], list):
            preferences = preferences["preferences"]
        else:
            preferences = [preferences]

    if not isinstance(preferences, list) or not preferences:
        raise ValueError("preferences must be a list containing 1 or 2 target deck definitions")
    if len(preferences) > 2:
        raise ValueError("preferences may contain at most 2 target deck definitions")

    normalized = [normalize_preference(p) for p in preferences]
    session["preferences"] = normalized
    session["updated_at"] = _now()
    _save_session(r, session)
    return session


def trigger_build(r: Any, session_id: str, sync: bool = False) -> dict:
    """Trigger the build of draft decks.

    If sync=True, runs the builder inline and transitions to 'drafts_ready'.
    If sync=False, transitions to 'building'; background runner calls execute_build.
    """
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    if session["status"] in {STATUS_ACCEPTED, STATUS_ABANDONED}:
        raise ValueError(f"cannot trigger build on a session in status '{session['status']}'")
    if not session.get("preferences"):
        raise ValueError("preferences must be submitted before triggering build")

    session["status"] = STATUS_BUILDING
    session["updated_at"] = _now()
    _save_session(r, session)

    if sync:
        return execute_build(r, session_id)
    return session


def execute_build(r: Any, session_id: str) -> dict:
    """Run the pluggable builder, store drafts, and set status to 'drafts_ready'."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")

    builder = get_builder()
    try:
        drafts = builder.build(session, r)
        session["drafts"] = drafts
        session["status"] = STATUS_DRAFTS_READY
        session.pop("error", None)
        session.pop("error_message", None)
        session["updated_at"] = _now()
        _save_session(r, session)
        return session
    except Exception as exc:
        session["status"] = STATUS_FAILED
        session["error"] = str(exc)
        session["error_message"] = str(exc)
        session["updated_at"] = _now()
        _save_session(r, session)
        raise


def get_drafts(r: Any, session_id: str) -> list[dict]:
    """Fetch the generated drafts for a Reconstruct session."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    return session.get("drafts", [])


def accept_draft(
    r: Any,
    session_id: str,
    draft_indices: Optional[Union[list[int], int]] = None,
    status: str = "physical",
) -> dict:
    """Accept one or more drafts, creating the new decks and dedicating their cards."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    if session["status"] != STATUS_DRAFTS_READY:
        raise ValueError(
            f"cannot accept draft when session status is '{session['status']}' (expected '{STATUS_DRAFTS_READY}')"
        )

    drafts = session.get("drafts", [])
    if not drafts:
        raise ValueError("no drafts available to accept")

    if draft_indices is None:
        indices = list(range(len(drafts)))
    elif isinstance(draft_indices, int):
        indices = [draft_indices]
    elif isinstance(draft_indices, list):
        indices = draft_indices
    else:
        raise ValueError("draft_indices must be an integer, list of integers, or omitted")

    for idx in indices:
        if not isinstance(idx, int) or idx < 0 or idx >= len(drafts):
            raise ValueError(f"invalid draft index: {idx}")

    created_decks = []
    for idx in indices:
        draft = drafts[idx]
        normalized = {
            "source": "reconstruct",
            "source_id": f"{session_id}_{idx}",
            "name": draft.get("name") or f"Reconstructed Deck {idx + 1}",
            "color": draft.get("color", ""),
            "commanders": list(draft.get("commanders", [])),
            "commander_uids": list(draft.get("commander_uids", [])),
            "folder": "Reconstruct",
            "description": draft.get("explanation", f"Created from reconstruct session {session_id}"),
            "cards": [
                {"name": c["name"], "quantity": c.get("quantity", 1)}
                for c in draft.get("cards", [])
                if c.get("name")
            ],
            "url": "",
        }
        new_deck = registry.upsert_deck(r, normalized, default_status=status)
        if status == "physical":
            instance_store.auto_bind_physical(
                r,
                deck_registry_id=registry.registry_id_of(new_deck),
                deck_name=new_deck["name"],
                decklist=new_deck.get("cards", []),
            )
        created_decks.append(new_deck)

    session["status"] = STATUS_ACCEPTED
    session["accepted_draft_indices"] = indices
    session["created_deck_ids"] = [registry.registry_id_of(d) for d in created_decks]
    session["updated_at"] = _now()
    _save_session(r, session)

    return {"session": session, "created_decks": created_decks}


def abandon_reconstruct(r: Any, session_id: str, reason: str = "") -> dict:
    """Abandon a Reconstruct session."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    if session["status"] == STATUS_ACCEPTED:
        raise ValueError("cannot abandon a session that has already been accepted")

    session["status"] = STATUS_ABANDONED
    if reason:
        session["abandon_reason"] = reason
    session["updated_at"] = _now()
    _save_session(r, session)
    return session
