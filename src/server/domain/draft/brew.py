"""
Brew flow for Commander Lab.

Brew is a sibling flow to Reconstruct (docs/architecture/brew-flow-spec.md):
a commander-led virtual-deck generator. The differences from Reconstruct:

  1. Donor decks are explicitly NAMED by the user and are never decommissioned,
     mutated, or bound at generation time. All donor claims are soft.
  2. Exactly one target deck is produced, as a virtual ("testing") deck record.
  3. Per-card provenance ({source: donor_deck_id | "free_inventory" | "buy"})
     and parent decks are tracked on the session and persisted under
     brew:provenance:{deck_registry_id}.

Reused unchanged: reconstruct_pools.build_candidate_pools / tag_cards /
compute_deck_cost, registry.upsert_deck, the BaseReconstructBuilder contract.
The donor snapshots are stored under BOTH "donor_decks" (brew vocabulary) and
"source_decks" (what reconstruct_pools reads) so pool building needs no
changes; "dry_run" is never set, so the decommission simulation code paths in
reconstruct_pools stay inactive.
"""
import copy
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import src.server.domain.decks.registry as registry
import src.server.domain.draft.reconstruct as reconstruct
import src.server.domain.draft.reconstruct_pools as reconstruct_pools

BREW_SESSION_PREFIX = "brew:session:"
BREW_SESSIONS_INDEX = "brew:sessions"
BREW_PROVENANCE_PREFIX = "brew:provenance:"

STATUS_PENDING_PREFERENCES = reconstruct.STATUS_PENDING_PREFERENCES
STATUS_BUILDING = reconstruct.STATUS_BUILDING
STATUS_DRAFTS_READY = reconstruct.STATUS_DRAFTS_READY
STATUS_ACCEPTED = reconstruct.STATUS_ACCEPTED
STATUS_ABANDONED = reconstruct.STATUS_ABANDONED
STATUS_FAILED = reconstruct.STATUS_FAILED

BUILD_MODES = {"owned_first", "best_build"}

# Donor statuses eligible to be sourced from (retired decks are excluded).
DONOR_STATUSES = {"physical", "digital", "testing"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_key(session_id: str) -> str:
    return f"{BREW_SESSION_PREFIX}{session_id}"


def _save_session(r: Any, session: dict) -> None:
    session_id = session["session_id"]
    r.set(_session_key(session_id), json.dumps(session))
    r.sadd(BREW_SESSIONS_INDEX, session_id)


# ---------------------------------------------------------------------------
# Session lifecycle
# ---------------------------------------------------------------------------

def start_brew(
    r: Any,
    commander: Any,
    donor_deck_ids: Any,
    build_mode: str,
    *,
    name: str = "",
    power_level: Any = "",
    deck_idea: str = "",
    themes: Any = None,
    notes: str = "",
) -> dict:
    """Start a Brew session: validate inputs, snapshot donors, build pool summary.

    - commander: required, a card name string or list (partners/background).
    - donor_deck_ids: explicitly named decks ONLY. No implicit expansion —
      unnamed decks never contribute cards to the pools.
    - build_mode: "owned_first" | "best_build".

    Raises ValueError on a missing commander, an unknown build mode, or a
    donor id that does not resolve to a non-retired, non-decommissioned deck.
    Nothing is written to decks, statuses, tags, or instances.
    """
    pref = reconstruct.normalize_preference({
        "commanders": commander,
        "name": name,
        "themes": themes or [],
        "notes": notes,
    })
    commanders = pref["commanders"]
    if not commanders:
        raise ValueError("a commander is required to brew a deck")

    if build_mode not in BUILD_MODES:
        raise ValueError(
            f"build_mode must be one of {sorted(BUILD_MODES)} (got {build_mode!r})"
        )

    if isinstance(donor_deck_ids, str):
        donor_deck_ids = [donor_deck_ids]
    if donor_deck_ids is None:
        donor_deck_ids = []
    if not isinstance(donor_deck_ids, list):
        raise ValueError("donor_deck_ids must be a list of deck IDs")
    clean_ids = list(dict.fromkeys(str(i).strip() for i in donor_deck_ids if str(i).strip()))

    decks = registry._load_decks(r)

    # Validate all donors upfront (read-only; brew never mutates donors).
    snapshots = []
    canonical_ids = []
    for did in clean_ids:
        target = registry._find_in(decks, did)
        if not target:
            raise ValueError(f"deck not found: {did}")
        if target.get("decommissioned_at"):
            raise ValueError(f"deck is decommissioned: {did}")
        if target.get("status") not in DONOR_STATUSES:
            raise ValueError(
                f"deck {did} cannot be a donor (status is {target.get('status')!r}; "
                f"expected one of {sorted(DONOR_STATUSES)})"
            )
        reg_id = registry.registry_id_of(target)
        canonical_ids.append(reg_id)
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
            "updated_at": target.get("updated_at") or target.get("synced_at") or "",
        })

    now = _now()
    session_id = f"brew_{uuid.uuid4().hex[:12]}"
    session = {
        "session_id": session_id,
        "status": STATUS_PENDING_PREFERENCES,
        "commander": commanders[0],
        "commanders": commanders,
        "donor_deck_ids": canonical_ids,
        "donor_decks": snapshots,
        # Alias so reconstruct_pools.build_candidate_pools reads the snapshots
        # unchanged (it keys on "source_decks"). Never set "dry_run".
        "source_decks": snapshots,
        "build_mode": build_mode,
        "power_level": str(power_level or ""),
        "deck_idea": (deck_idea or "").strip() if isinstance(deck_idea, str) else str(deck_idea),
        "themes": pref["themes"],
        "notes": pref["notes"],
        "preferences": [pref],
        "drafts": [],
        "created_at": now,
        "updated_at": now,
    }
    session["pool_summary"] = _pool_summary(_build_pools(r, session, price_lookup=lambda _: {}))

    _save_session(r, session)
    return session


def get_session(r: Any, session_id: str) -> Optional[dict]:
    """Fetch a Brew session record by ID."""
    raw = r.get(_session_key(session_id))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def list_sessions(r: Any, limit: int = 50) -> list[dict]:
    """List recent Brew sessions sorted by created_at descending."""
    ids = r.smembers(BREW_SESSIONS_INDEX) or set()
    sessions = []
    for sid in ids:
        if isinstance(sid, bytes):
            sid = sid.decode("utf-8")
        s = get_session(r, sid)
        if s:
            sessions.append(s)
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return sessions[:limit]


def submit_preferences(r: Any, session_id: str, preference: Any) -> dict:
    """Replace the session's single preference (parity with reconstruct; used
    for re-brew edits). Brew has exactly ONE target deck: a list longer than
    1 is rejected."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    if session["status"] in {STATUS_ACCEPTED, STATUS_ABANDONED}:
        raise ValueError(f"cannot submit preferences on a session in status '{session['status']}'")

    if isinstance(preference, dict) and "preferences" in preference:
        preference = preference["preferences"]
    if isinstance(preference, list):
        if len(preference) != 1:
            raise ValueError("brew supports exactly 1 target deck preference")
        preference = preference[0]
    if not isinstance(preference, dict):
        raise ValueError("preference must be a JSON object")

    normalized = reconstruct.normalize_preference(preference)
    if not normalized["commanders"]:
        normalized["commanders"] = list(session.get("commanders") or [])
    session["preferences"] = [normalized]
    session["commanders"] = normalized["commanders"]
    session["commander"] = normalized["commanders"][0]
    session["themes"] = normalized["themes"]
    session["notes"] = normalized["notes"]
    session["updated_at"] = _now()
    _save_session(r, session)
    return session


def trigger_build(r: Any, session_id: str, sync: bool = False) -> dict:
    """Trigger the build of the brew draft. The session already carries its
    single preference, so no separate preferences step is required."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    if session["status"] in {STATUS_ACCEPTED, STATUS_ABANDONED}:
        raise ValueError(f"cannot trigger build on a session in status '{session['status']}'")
    if not session.get("preferences"):
        raise ValueError("preferences must be submitted before triggering build")

    session["status"] = STATUS_BUILDING
    session["progress"] = "Reading donor pools…"
    session["updated_at"] = _now()
    _save_session(r, session)

    if sync:
        return execute_build(r, session_id)
    return session


def execute_build(r: Any, session_id: str) -> dict:
    """Run the brew builder, store exactly ONE draft, set 'drafts_ready'."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")

    from src.server.domain.draft.brew_builder import get_builder

    builder = get_builder()
    try:
        drafts = builder.build(session, r)
        if not isinstance(drafts, list) or not drafts:
            raise ValueError("brew builder returned no drafts")
        session["drafts"] = drafts[:1]  # brew produces exactly one draft
        session["status"] = STATUS_DRAFTS_READY
        session.pop("error", None)
        session.pop("error_message", None)
        session["progress"] = ""
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
    """Fetch the generated draft for a Brew session."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    return session.get("drafts", [])


def abandon_brew(r: Any, session_id: str, reason: str = "") -> dict:
    """Abandon a Brew session."""
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


# ---------------------------------------------------------------------------
# Pools
# ---------------------------------------------------------------------------

def _pool_config(r: Any, session: dict) -> reconstruct_pools.PoolConfig:
    """Tier 3 filtered by the commander's color identity."""
    catalog = reconstruct_pools.RedisCardCatalog(r)
    identity = reconstruct_pools.identity_of_cards(session.get("commanders") or [], catalog)
    return reconstruct_pools.PoolConfig(color_identity=identity)


def _build_pools(r: Any, session: dict, price_lookup=None) -> reconstruct_pools.CandidatePools:
    if price_lookup is None:
        price_lookup = reconstruct_pools.redis_price_lookup(r)
    return reconstruct_pools.build_candidate_pools(
        session, r, price_lookup=price_lookup, config=_pool_config(r, session)
    )


def _pool_summary(pools: Any) -> dict:
    return {
        "tier1_unique": len(pools.tier1),
        "tier1_total_qty": sum(c.available_qty or 0 for c in pools.tier1),
        "tier2_unique": len(pools.tier2),
        "tier2_total_qty": sum(c.available_qty or 0 for c in pools.tier2),
    }


def get_session_pools(r: Any, session_id: str) -> dict:
    """Tier 1 / Tier 2 pools for a brew session (donor decks are read-only)."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    pools = _build_pools(r, session, price_lookup=lambda _: {})
    return {
        "session_id": session_id,
        "summary": _pool_summary(pools),
        "tier1": [
            {
                "name": c.name,
                "tier": int(c.tier),
                "tier_label": c.tier_label,
                "available_qty": c.available_qty,
                "is_commander": c.is_commander,
                "source_decks": [list(sd) for sd in c.source_decks],
            }
            for c in pools.tier1
        ],
        "tier2": [
            {
                "name": c.name,
                "tier": int(c.tier),
                "tier_label": c.tier_label,
                "available_qty": c.available_qty,
            }
            for c in pools.tier2
        ],
    }


# ---------------------------------------------------------------------------
# Provenance + availability
# ---------------------------------------------------------------------------

def _donor_id_by_name(session: dict) -> Dict[str, str]:
    out = {}
    for sd in session.get("donor_decks") or []:
        name = sd.get("name")
        if name and name not in out:
            out[name] = sd.get("registry_id") or ""
    return out


def derive_provenance(session: dict, draft: dict, pools: Any) -> List[dict]:
    """Per-card provenance: Tier 1 -> donor registry id, Tier 2 -> free_inventory,
    Tier 3 / not in pool -> buy. When several donors hold copies, attribute to
    the donor with the most copies, tie-broken by donor list order."""
    donor_id = _donor_id_by_name(session)
    donor_order = {name: i for i, name in enumerate(donor_id.keys())}
    tagged = reconstruct_pools.tag_cards(draft.get("cards", []), pools)
    out = []
    for t in tagged:
        if t.tier == reconstruct_pools.Tier.PREVIOUS_DECK:
            pc = pools.get(t.name)
            best_name, best_copies = "", -1
            for deck_name, copies in (pc.source_decks if pc else ()):
                if copies > best_copies or (
                    copies == best_copies
                    and donor_order.get(deck_name, 1 << 30) < donor_order.get(best_name, 1 << 30)
                ):
                    best_name, best_copies = deck_name, copies
            source = donor_id.get(best_name) or best_name or "donor"
        elif t.tier == reconstruct_pools.Tier.OWNED:
            source = "free_inventory"
        else:
            source = "buy"
        out.append({"name": t.name, "quantity": t.quantity, "source": source})
    return out


def get_availability(r: Any, session_id: str) -> dict:
    """Availability report: owned (donors / free inventory) vs buy list.

    Pools are recomputed on each call so a donor card removed after generation
    is flagged stale on the owned entry whose pool copies no longer cover the
    draft quantity."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    drafts = session.get("drafts") or []
    if not drafts:
        raise ValueError("no draft available (build has not completed)")
    draft = drafts[0]

    pools = _build_pools(r, session)
    tagged = reconstruct_pools.tag_cards(draft.get("cards", []), pools)
    provenance = derive_provenance(session, draft, pools)
    src_by_name = {reconstruct_pools.norm_name(p["name"]): p["source"] for p in provenance}
    cost = reconstruct_pools.compute_deck_cost(
        draft.get("cards", []), pools, price_lookup=reconstruct_pools.redis_price_lookup(r)
    )

    owned, buy_list = [], []
    donor_id = _donor_id_by_name(session)
    donor_names = {v: k for k, v in donor_id.items()}
    for t in tagged:
        source = src_by_name.get(reconstruct_pools.norm_name(t.name), "buy")
        if source == "buy":
            cc = next((c for c in cost.cards if reconstruct_pools.norm_name(c.name) == reconstruct_pools.norm_name(t.name)), None)
            buy_list.append({
                "name": t.name,
                "quantity": t.quantity,
                "needs_purchase": True,
                "unit_price": (cc.unit_price if cc else None),
                "price_known": bool(cc and cc.price_known),
            })
        else:
            owned.append({
                "name": t.name,
                "quantity": t.quantity,
                "source": source,
                "source_deck_name": donor_names.get(source, ""),
                "stale": bool(t.exceeds_available),
            })

    # Per-donor projections (claimed counts; "size after" is a projection only —
    # nothing is applied).
    donor_projections = []
    for sd in session.get("donor_decks") or []:
        rid = sd.get("registry_id") or ""
        claimed = sum(p["quantity"] for p in provenance if p["source"] == rid)
        size = sum(int(c.get("quantity", 1) or 0) for c in (sd.get("cards") or []))
        donor_projections.append({
            "deck_id": rid,
            "deck_name": sd.get("name") or "",
            "claimed": claimed,
            "size_after": size - claimed,
            "size_before": size,
        })

    return {
        "session_id": session_id,
        "owned": owned,
        "buy_list": buy_list,
        "total_buy_cost": cost.total_new_cost,
        "is_complete": cost.is_complete,
        "unpriced_cards": list(cost.unpriced_cards),
        "donor_projections": donor_projections,
    }


# ---------------------------------------------------------------------------
# Accept
# ---------------------------------------------------------------------------

def accept_draft(r: Any, session_id: str, draft_index: int = 0) -> dict:
    """Accept the brew draft: create the virtual deck (status "testing") and
    persist provenance. NO instance writes: "testing" never auto-binds
    (registry.upsert_deck only auto-binds on "physical"), so donors, instances,
    statuses and tags are untouched by construction."""
    session = get_session(r, session_id)
    if not session:
        raise ValueError(f"session not found: {session_id}")
    if session["status"] != STATUS_DRAFTS_READY:
        raise ValueError(
            f"cannot accept draft when session status is '{session['status']}' "
            f"(expected '{STATUS_DRAFTS_READY}')"
        )
    drafts = session.get("drafts", [])
    if not drafts:
        raise ValueError("no drafts available to accept")
    if not isinstance(draft_index, int) or draft_index < 0 or draft_index >= len(drafts):
        raise ValueError(f"invalid draft index: {draft_index}")

    draft = drafts[draft_index]
    normalized = {
        "source": "brew",
        "source_id": f"{session_id}_{draft_index}",
        "name": draft.get("name") or f"{session.get('commander', 'Brew')} brew",
        "color": draft.get("color", ""),
        "commanders": list(draft.get("commanders", [])),
        "commander_uids": list(draft.get("commander_uids", [])),
        "folder": "Brew",
        "description": draft.get("explanation", f"Created from brew session {session_id}"),
        "cards": [
            {"name": c["name"], "quantity": c.get("quantity", 1)}
            for c in draft.get("cards", [])
            if c.get("name")
        ],
        "url": "",
    }
    # NOTE: upsert_deck matches on provider links / commander keys, so
    # re-accepting a brew draft with the same commander may merge into an
    # existing deck record (accepted for v1).
    new_deck = registry.upsert_deck(r, normalized, default_status="testing")

    pools = _build_pools(r, session)
    provenance = derive_provenance(session, draft, pools)
    deck_id = registry.registry_id_of(new_deck)
    r.set(f"{BREW_PROVENANCE_PREFIX}{deck_id}", json.dumps(provenance))

    draft["provenance"] = provenance
    session["drafts"][draft_index] = draft
    session["status"] = STATUS_ACCEPTED
    session["accepted_draft_index"] = draft_index
    session["created_deck_ids"] = [deck_id]
    session["parent_deck_ids"] = list(session.get("donor_deck_ids") or [])
    session["updated_at"] = _now()
    _save_session(r, session)

    return {"session": session, "created_decks": [new_deck]}


def get_provenance(r: Any, deck_registry_id: str) -> List[dict]:
    """Persisted per-card provenance for a brewed deck (survives the session)."""
    raw = r.get(f"{BREW_PROVENANCE_PREFIX}{deck_registry_id}")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception:
        return []
