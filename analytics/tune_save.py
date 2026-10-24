"""Deck Tuner dual save (DT-2).

Two paths for a tune-up (a set of cuts and adds):

* **For later** (`save_draft`): stores the tune-up as a revisitable draft. Paper-only: it
  never touches an instance or the deck's decklist.
* **Save to deck** (`apply_to_deck`): performs the swap. Each cut card goes back to the
  collection as unused (`in_deck` -> `in_collection`); each add is marked in-deck (an unused
  copy is reused, otherwise a new instance is created and flagged `to_buy`). The decklist is
  updated to match. The caller then offers to update Moxfield / Archidekt.

Draft shape (JSON):
    {id, deck_id, status: "draft"|"applied"|"discarded", label, created_at, updated_at,
     applied_at|null, swaps: [{cut, add, source}], cuts: [name], adds: [{name, qty, source}],
     result|null}
`swaps` pair a cut with the card accepted for it (so a draft can be re-loaded onto the Tune
page); `cuts` are cuts with no paired add; `adds` are extra adds (basics, combo pieces).

Redis keys:
    tunedraft:rec:{id}         one draft
    tunedraft:index:{deck_id}  list of draft ids, newest first
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional

import instances as instance_store
import registry

REC_PREFIX = "tunedraft:rec:"
INDEX_PREFIX = "tunedraft:index:"

DRAFT, APPLIED, DISCARDED = "draft", "applied", "discarded"
MAX_LINES = 200


class TuneSaveError(ValueError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _name(v: Any, what: str) -> str:
    if not isinstance(v, str) or not v.strip():
        raise TuneSaveError(f"{what} needs a card name")
    return v.strip()


def normalize(body: Mapping) -> dict:
    """Validate a tune-up body into {swaps, cuts, adds}."""
    if not isinstance(body, Mapping):
        raise TuneSaveError("Body must be a JSON object.")
    swaps, cuts, adds = [], [], []
    for s in body.get("swaps") or []:
        if not isinstance(s, Mapping):
            raise TuneSaveError("each swap must be an object")
        swaps.append({"cut": _name(s.get("cut"), "swap cut"), "add": _name(s.get("add"), "swap add"),
                      "source": str(s.get("source") or "")[:40]})
    for c in body.get("cuts") or []:
        cuts.append(_name(c, "cut"))
    for a in body.get("adds") or []:
        if not isinstance(a, Mapping):
            raise TuneSaveError("each add must be an object")
        q = a.get("qty", 1)
        if not isinstance(q, int) or isinstance(q, bool) or not 1 <= q <= 99:
            raise TuneSaveError("qty must be an integer 1-99")
        adds.append({"name": _name(a.get("name"), "add"), "qty": q, "source": str(a.get("source") or "")[:40]})
    if len(swaps) + len(cuts) + len(adds) > MAX_LINES:
        raise TuneSaveError("too many changes in one tune-up")
    if not (swaps or cuts or adds):
        raise TuneSaveError("nothing to save: the change list is empty")
    return {"swaps": swaps, "cuts": cuts, "adds": adds}


def flatten(t: Mapping) -> tuple:
    """-> (cut names, add lines [{name, qty, source}]) for a normalized tune-up."""
    cut_names = [s["cut"] for s in t["swaps"]] + list(t["cuts"])
    add_lines = [{"name": s["add"], "qty": 1, "source": s.get("source") or ""} for s in t["swaps"]] + list(t["adds"])
    return cut_names, add_lines


# -- drafts ("for later") --------------------------------------------------------------------

def _load(r, draft_id: Optional[str]) -> Optional[dict]:
    raw = r.get(REC_PREFIX + draft_id) if draft_id else None
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _store(r, rec: dict) -> dict:
    r.set(REC_PREFIX + rec["id"], json.dumps(rec))
    return rec


def save_draft(r, deck_id: str, body: Mapping, label: str = "") -> dict:
    """Store a tune-up for later. Paper-only: no instance, decklist or inventory write."""
    t = normalize(body)
    now = _now()
    rec = {"id": uuid.uuid4().hex, "deck_id": deck_id, "status": DRAFT,
           "label": (label or "").strip()[:80] or "Tune-up " + now[:16].replace("T", " "),
           "created_at": now, "updated_at": now, "applied_at": None, **t, "result": None}
    _store(r, rec)
    r.lpush(INDEX_PREFIX + deck_id, rec["id"])
    return rec


def get_draft(r, deck_id: str, draft_id: str) -> Optional[dict]:
    rec = _load(r, draft_id)
    return rec if rec and rec.get("deck_id") == deck_id else None


def list_drafts(r, deck_id: str, include_discarded: bool = False) -> List[dict]:
    out = []
    for did in r.lrange(INDEX_PREFIX + deck_id, 0, -1):
        rec = _load(r, did)
        if rec and (include_discarded or rec.get("status") != DISCARDED):
            out.append(rec)
    return out


def discard_draft(r, deck_id: str, draft_id: str) -> dict:
    rec = get_draft(r, deck_id, draft_id)
    if not rec:
        raise KeyError(draft_id)
    if rec["status"] == DRAFT:
        rec.update(status=DISCARDED, updated_at=_now())
        _store(r, rec)
    return rec


# -- save to deck ----------------------------------------------------------------------------

def apply_to_deck(r, deck: Mapping, tune: Mapping) -> dict:
    """Perform the swap on the real deck + inventory. `tune` is a normalized tune-up.

    Cuts: an `in_deck` instance bound to the deck returns to `in_collection` (unused) and one
    copy comes off the decklist. Adds: an unused `in_collection` copy is bound to the deck, else
    a new instance is created and flagged to_buy; the card goes on the decklist."""
    rid = registry.registry_id_of(deck)
    deck_name = deck.get("name", "")
    ids = registry.deck_instance_ids(deck)
    cut_names, add_lines = flatten(tune)

    freed, missing, added, to_buy = [], [], [], []
    bound = instance_store.list_deck_instances(r, ids, ownership_status="in_deck")
    for name in cut_names:
        inst = next((i for i in bound if i.get("card_name") == name), None)
        if inst:
            bound.remove(inst)
            instance_store.transition_status(r, inst["id"], "in_collection", _allow_physical_lock_bypass=True)
            freed.append({"name": name, "instance_id": inst["id"]})
        else:
            missing.append(name)
        registry.remove_card_from_decklist(r, rid, name)

    for line in add_lines:
        for _ in range(line["qty"]):
            spare = instance_store.list_instances(r, card_name=line["name"], ownership_status="in_collection")
            buy = not spare
            inst = spare[0] if spare else instance_store.create_instance(
                r, card_name=line["name"], ownership_status="in_collection", source="tune",
                notes="Added via Tune; still to buy")
            inst = instance_store.transition_status(r, inst["id"], "in_deck", deck_id=rid, deck_name=deck_name,
                                                    _allow_physical_lock_bypass=True)
            added.append({"name": line["name"], "instance_id": inst["id"], "to_buy": buy})
            if buy:
                to_buy.append(line["name"])
        registry.bump_decklist_card(r, rid, line["name"], line["qty"])
    return {"freed": freed, "added": added, "to_buy": sorted(set(to_buy)), "not_in_deck": missing}


def mark_applied(r, deck_id: str, draft_id: str, result: Mapping) -> Optional[dict]:
    rec = get_draft(r, deck_id, draft_id)
    if rec:
        rec.update(status=APPLIED, applied_at=_now(), updated_at=_now(), result=dict(result))
        _store(r, rec)
    return rec
