"""Postgres persistence for the player profile, feedback and proposals
(deck reviewer card 04, DECISIONS.md D2). Domain rules live in
domain/profile/player_profile.py; this module only stores and sequences.

Every state change appends a ``player_profile_versions`` row (history is never
rewritten). Learned proposals only reach the profile through ``confirm``.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from src.server.domain.profile import player_profile as pp
from src.server.infra.db.orm_models import (
    PlayerProfileProposalRow,
    PlayerProfileVersionRow,
    RecommendationFeedbackRow,
)

DEFAULT_SEED = Path(__file__).resolve().parents[4] / "profiles" / "andrew.json"
DEFAULT_NAME = "andrew"


def _latest_row(session: Session, name: str) -> Optional[PlayerProfileVersionRow]:
    return session.scalars(
        select(PlayerProfileVersionRow)
        .where(PlayerProfileVersionRow.name == name)
        .order_by(PlayerProfileVersionRow.version.desc()).limit(1)
    ).first()


def _append_version(session: Session, profile: pp.PlayerProfile, kind: str) -> PlayerProfileVersionRow:
    prev = _latest_row(session, profile.name)
    data = profile.to_dict()
    prov = data.pop("provenance")
    row = PlayerProfileVersionRow(
        name=profile.name, version=(prev.version + 1) if prev else 1, kind=kind,
        data=data, provenance=prov, supersedes_id=prev.id if prev else None,
    )
    session.add(row)
    session.flush()
    return row


def _profile_from_row(row: PlayerProfileVersionRow) -> pp.PlayerProfile:
    return pp.parse_profile({**row.data, "provenance": row.provenance})


def get_feedback(session: Session, name: str, deck_id: Optional[str] = None) -> List[pp.FeedbackEntry]:
    q = select(RecommendationFeedbackRow).where(RecommendationFeedbackRow.profile == name)
    if deck_id is not None:
        q = q.where(RecommendationFeedbackRow.deck_id == deck_id)
    rows = session.scalars(q.order_by(RecommendationFeedbackRow.id)).all()
    return [pp.FeedbackEntry(
        deck_id=r.deck_id, review_id=r.review_id, cut=r.cut, add=r.add,
        decision=r.decision, reason=r.reason,
        # id-padded so entries written within the same second keep write order
        at=f"{r.created_at.strftime('%Y-%m-%dT%H:%M:%S') if r.created_at else ''}#{r.id:012d}",
    ) for r in rows]


def load_profile(session: Session, name: str) -> pp.PlayerProfile:
    row = _latest_row(session, name)
    if row is None:
        raise pp.ProfileNotFound(f"no player profile named '{name}' (seed it first)")
    profile = _profile_from_row(row)
    profile.feedback = get_feedback(session, name)
    return profile


def seed_profile(session: Session, name: str = DEFAULT_NAME,
                 seed_path: Optional[Path] = None) -> pp.PlayerProfile:
    """Create version 1 from the seed file. No-op if the profile exists
    (seeds never overwrite stored history)."""
    if _latest_row(session, name) is not None:
        return load_profile(session, name)
    seed = json.loads((seed_path or DEFAULT_SEED).read_text(encoding="utf-8"))
    seed["name"] = name
    profile = pp.profile_from_seed(seed)
    _append_version(session, profile, "override")
    return load_profile(session, name)


def save_stated_edit(session: Session, name: str, new_values: Dict[str, Any]) -> pp.PlayerProfile:
    current = load_profile(session, name)
    updated = pp.apply_stated_edit(current, new_values)
    updated.name = name
    _append_version(session, updated, "override")
    return load_profile(session, name)


def record_feedback(session: Session, name: str, *, deck_id: str, review_id: str,
                    cut: str, add: str, decision: str, reason: str = "") -> None:
    if decision not in pp.DECISIONS:
        raise ValueError(f"decision must be one of {sorted(pp.DECISIONS)}")
    session.add(RecommendationFeedbackRow(
        profile=name, deck_id=deck_id, review_id=review_id, cut=cut, add=add,
        decision=decision, reason=reason or ""))
    session.flush()


def previously_rejected(session: Session, name: str, deck_id: str):
    profile = load_profile(session, name)
    return pp.previously_rejected(profile, deck_id)


def _proposal_dict(r: PlayerProfileProposalRow) -> Dict[str, Any]:
    return {"id": r.id, "path": r.path, "op": r.op, "value": r.value,
            "rationale": r.rationale, "evidence": list(r.evidence or []),
            "status": r.status}


def list_proposals(session: Session, name: str, status: str = "pending") -> List[Dict[str, Any]]:
    rows = session.scalars(
        select(PlayerProfileProposalRow)
        .where(PlayerProfileProposalRow.profile == name, PlayerProfileProposalRow.status == status)
        .order_by(PlayerProfileProposalRow.id)).all()
    return [_proposal_dict(r) for r in rows]


def generate_proposals(session: Session, name: str) -> List[Dict[str, Any]]:
    """Run the summarizer and store NEW pending proposals. The profile is not
    touched. A proposal already pending, or dismissed earlier, is not re-added."""
    profile = load_profile(session, name)
    existing = {(r.path, r.op, json.dumps(r.value, sort_keys=True))
                for r in session.scalars(select(PlayerProfileProposalRow)
                .where(PlayerProfileProposalRow.profile == name,
                       PlayerProfileProposalRow.status.in_(["pending", "dismissed"])))}
    for prop in pp.summarize_feedback(profile):
        key = (prop.path, prop.op, json.dumps(prop.value, sort_keys=True))
        if key in existing:
            continue
        session.add(PlayerProfileProposalRow(
            profile=name, path=prop.path, op=prop.op, value=prop.value,
            rationale=prop.rationale, evidence=prop.evidence))
    session.flush()
    return list_proposals(session, name)


def _get_pending(session: Session, name: str, proposal_id: int) -> PlayerProfileProposalRow:
    row = session.get(PlayerProfileProposalRow, proposal_id)
    if row is None or row.profile != name:
        raise LookupError(f"proposal {proposal_id} not found")
    if row.status != "pending":
        raise ValueError(f"proposal {proposal_id} is already {row.status}")
    return row


def confirm_proposal(session: Session, name: str, proposal_id: int) -> pp.PlayerProfile:
    """The only path by which a learned value joins the profile."""
    row = _get_pending(session, name, proposal_id)
    prop = pp.Proposal(row.path, row.op, row.value, row.rationale, list(row.evidence or []))
    updated = pp.apply_confirmed_proposal(load_profile(session, name), prop)  # may raise ProvenanceConflict
    updated.name = name
    _append_version(session, updated, "inferred")
    row.status = "confirmed"
    row.resolved_at = func.now()
    session.flush()
    return load_profile(session, name)


def dismiss_proposal(session: Session, name: str, proposal_id: int) -> None:
    row = _get_pending(session, name, proposal_id)
    row.status = "dismissed"
    row.resolved_at = func.now()
    session.flush()


def list_versions(session: Session, name: str, limit: int = 50) -> List[Dict[str, Any]]:
    rows = session.scalars(select(PlayerProfileVersionRow)
        .where(PlayerProfileVersionRow.name == name)
        .order_by(PlayerProfileVersionRow.version.desc()).limit(limit)).all()
    return [{"version": r.version, "kind": r.kind,
             "created_at": r.created_at.isoformat() if r.created_at else None} for r in rows]
