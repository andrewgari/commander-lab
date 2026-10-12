"""
Deck registry — the source-of-truth list of decks Commander Lab knows about,
across both providers (Archidekt full-account sync, Moxfield/Archidekt
single-deck imports).

Storage: reuses the existing `decks` Redis key (JSON array), same key
sync.py has always written. Each deck dict now additionally carries:
    source        "archidekt" | "moxfield"
    source_id     provider-native id (string)
    registry_id   f"{source}:{source_id}" — stable, provider-agnostic key
                  used as instances.deck_id for decks that don't have an
                  Archidekt integer id (e.g. Moxfield-native imports).
    cards         [{"name": str, "quantity": int}, ...] mainboard cardlist,
                  used only to auto-bind physical-deck instances.
    status        "physical" | "digital" | "retired" | "testing"

Lifecycle status values (renamed from the old physical/virtual/retired/test
vocabulary — same field, same mechanism, just relabeled):
    physical  — deck is physically assembled; every card is bound 1:1 to an
                owned card Instance (see instances.py). Immutable in the
                sense that instances currently in_deck for this deck cannot
                be moved out while it stays physical (enforced in
                instances.transition_status via the is_deck_physical check).
    digital   — exists only as a decklist (was "virtual").
    retired   — no longer played/maintained.
    testing   — being brewed/tested (was "test").
"""
import json
import re
import unicodedata
from typing import Optional

import instances as instance_store

VALID_STATUSES = {"physical", "digital", "retired", "testing"}

DECKS_KEY = "decks"


def _load_decks(r) -> list:
    raw = r.get(DECKS_KEY)
    return json.loads(raw) if raw else []


def _save_decks(r, decks: list) -> None:
    r.set(DECKS_KEY, json.dumps(decks))


def registry_id_of(deck: dict) -> str:
    if deck.get("registry_id"):
        return deck["registry_id"]
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


def deck_instance_ids(deck: dict) -> list:
    """Every deck_id value an instance bound to `deck` may carry.

    Instances are written with two different deck_id shapes:
    auto_bind_physical / the instance migration use the deck's raw `id`
    (an int for Archidekt decks), while the manual add-card path uses
    registry_id ("archidekt:417707"). Readers must check both, or a deck
    whose cards were bound by one path looks empty to the other.
    """
    ids = [registry_id_of(deck)]
    if deck.get("id") is not None and str(deck["id"]) not in ids:
        ids.append(str(deck["id"]))
    return ids


def list_decks(r) -> list:
    return _load_decks(r)


def find_deck(r, registry_id: str) -> Optional[dict]:
    for deck in _load_decks(r):
        if registry_id_of(deck) == registry_id or str(deck.get("id")) == registry_id:
            return deck
    return None


def is_deck_physical(r, registry_id: str) -> bool:
    deck = find_deck(r, registry_id)
    return bool(deck) and deck.get("status") == "physical"


_PROVIDER_DECK_URLS = {
    "archidekt": "https://archidekt.com/decks/{}",
    "moxfield": "https://moxfield.com/decks/{}",
}


def provider_link(normalized: dict) -> dict:
    """The deck_links.py-shaped link entry for a normalized provider deck:
    {"identifier": <deck url>, "deck_id": <provider-native id>}."""
    source = normalized["source"]
    source_id = str(normalized["source_id"])
    url = normalized.get("url") or _PROVIDER_DECK_URLS.get(source, "{}").format(source_id)
    return {"identifier": url, "deck_id": source_id}


def merge_link(links: Optional[dict], provider: str, entry: dict) -> dict:
    """Return a copy of `links` with `provider` set to `entry`. Other
    providers' links are always kept."""
    out = dict(links or {})
    out[provider] = dict(entry)
    return out


def _norm_text(s: Optional[str]) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _commander_key(commanders) -> frozenset:
    # Front face only: providers disagree on whether MDFC commanders carry
    # the " // Back Face" suffix.
    return frozenset(_norm_text((c or "").split(" // ")[0]) for c in commanders or [] if c)


def match_provider_deck(decks: list, normalized: dict) -> tuple:
    """Find the existing registry deck a provider deck (normally Moxfield)
    is a copy of. Returns (index, None) on a match or (None, reason).

    Order: an existing link/moxfield_id for this exact provider deck id
    wins; otherwise exactly one deck with the same commander set AND the
    same normalized name. Anything else (no candidate, several candidates,
    or the one candidate already linked to a different deck on this
    provider) is left unmatched so a human can link it by hand.
    """
    provider = normalized["source"]
    pid = str(normalized["source_id"])
    for idx, d in enumerate(decks):
        link = (d.get("links") or {}).get(provider) or {}
        if str(link.get("deck_id") or "") == pid:
            return idx, None
        if provider == "moxfield" and str(d.get("moxfield_id") or "") == pid:
            return idx, None

    ckey = _commander_key(normalized.get("commanders"))
    nkey = _norm_text(normalized.get("name"))
    if not ckey:
        return None, "no commanders on the provider deck"
    same_cmd = [i for i, d in enumerate(decks)
                if d.get("source") != provider and _commander_key(d.get("commanders")) == ckey]
    if not same_cmd:
        return None, "no registry deck with these commanders"
    hits = [i for i in same_cmd if _norm_text(decks[i].get("name")) == nkey]
    if not hits:
        names = ", ".join(sorted(decks[i].get("name", "") for i in same_cmd))
        return None, f"same commanders but different name ({names})"
    if len(hits) > 1:
        return None, f"ambiguous: {len(hits)} registry decks with these commanders and name"
    idx = hits[0]
    other = ((decks[idx].get("links") or {}).get(provider) or {}).get("deck_id")
    if other and str(other) != pid:
        return None, f"'{decks[idx].get('name')}' is already linked to {provider} deck {other}"
    return idx, None


def attach_provider_deck(r, normalized: dict, apply: bool = True) -> dict:
    """Attach a provider deck to the registry deck it matches (see
    match_provider_deck): sets links[provider] (and moxfield_id for
    Moxfield) on that deck and touches nothing else -- no cards, status,
    instances, journal, identity, or deck_status key changes.

    A deck already registered under this provider's own registry id (a
    Moxfield-native /api/import) is refreshed with upsert_deck as before.

    Returns {"result": "matched"|"upserted"|"unmatched", "deck": deck|None,
    "reason": str|None}. With apply=False nothing is written (dry run).
    """
    reg_id = f"{normalized['source']}:{normalized['source_id']}"
    decks = _load_decks(r)
    if any(registry_id_of(d) == reg_id for d in decks):
        if not apply:
            return {"result": "upserted", "deck": find_deck(r, reg_id), "reason": None}
        return {"result": "upserted", "deck": upsert_deck(r, normalized), "reason": None}

    idx, reason = match_provider_deck(decks, normalized)
    if idx is None:
        return {"result": "unmatched", "deck": None, "reason": reason}

    target = decks[idx]
    target["links"] = merge_link(target.get("links"), normalized["source"], provider_link(normalized))
    if normalized["source"] == "moxfield":
        target["moxfield_id"] = str(normalized["source_id"])
    if apply:
        _save_decks(r, decks)
    return {"result": "matched", "deck": target, "reason": None}


def merge_full_sync(existing: list, fetched: list) -> list:
    """Merge a full-account Archidekt pull (sync.py) into the existing
    registry instead of replacing it. Each fetched deck updates the stored
    deck with the same id, keeping every field the pull doesn't produce
    (links, source/registry_id, cards, moxfield_id, ...); stored decks the
    pull didn't return (Moxfield imports, decks deleted upstream) are kept.
    """
    by_id = {str(d.get("id")): i for i, d in enumerate(existing)}
    out = [dict(d) for d in existing]
    for f in fetched:
        f = dict(f)
        sid = str(f["id"])
        f.setdefault("source", "archidekt")
        f.setdefault("source_id", sid)
        f.setdefault("registry_id", f"archidekt:{sid}")
        f.setdefault("url", _PROVIDER_DECK_URLS["archidekt"].format(sid))
        if sid in by_id:
            cur = out[by_id[sid]]
            links = cur.get("links")
            cur.update(f)
            cur["links"] = links or {}
            target = cur
        else:
            out.append(f)
            target = f
        if "archidekt" not in (target.get("links") or {}):
            target["links"] = merge_link(target.get("links"), "archidekt",
                                         {"identifier": target["url"], "deck_id": sid})
    return sorted(out, key=lambda d: d.get("name") or "")


def upsert_deck(r, normalized: dict, default_status: str = "testing") -> dict:
    """Insert or update a deck from a normalized provider payload
    (see providers/__init__.py NORMALIZED_DECK_SHAPE). Preserves an
    existing deck's status/id on re-import; new decks get default_status.
    """
    reg_id = f"{normalized['source']}:{normalized['source_id']}"
    decks = _load_decks(r)

    existing = None
    existing_idx = None
    for idx, d in enumerate(decks):
        if registry_id_of(d) == reg_id:
            existing, existing_idx = d, idx
            break

    deck_id = existing.get("id") if existing else (
        int(normalized["source_id"]) if normalized["source"] == "archidekt" and normalized["source_id"].isdigit()
        else reg_id
    )

    saved_status = r.get(f"deck_status:{deck_id}")
    status = saved_status or (existing.get("status") if existing else default_status)
    if status not in VALID_STATUSES:
        status = default_status

    deck_obj = {
        "id": deck_id,
        "name": normalized["name"],
        "color": normalized["color"],
        "commanders": normalized["commanders"],
        "commander_uids": normalized["commander_uids"],
        "folder": normalized.get("folder", "Imported"),
        "status": status,
        "description": normalized.get("description", ""),
        "source": normalized["source"],
        "source_id": normalized["source_id"],
        "registry_id": reg_id,
        "cards": normalized.get("cards", []),
        "url": normalized.get("url", ""),
    }
    if normalized["source"] == "moxfield":
        deck_obj["moxfield_id"] = normalized["source_id"]

    # Record where this deck was pulled from, so the deck modal's Sync
    # Sources fields and deck_sync.sync_deck (which only fetches linked
    # providers) can find it. Merged into existing links; never clobbers
    # another provider's link.
    deck_obj["links"] = merge_link(
        (existing or {}).get("links"), normalized["source"], provider_link(normalized)
    )

    if existing_idx is not None:
        # Preserve any Archidekt-account-sync-only fields (e.g. description
        # already set) unless the new payload provides a better value.
        merged = dict(existing or {})
        merged.update(deck_obj)
        decks[existing_idx] = merged
    else:
        decks.append(deck_obj)

    _save_decks(r, decks)
    r.set(f"deck_status:{deck_id}", status)

    result_deck = decks[existing_idx] if existing_idx is not None else decks[-1]
    # Instance creation is NOT triggered here at all, for any deck status.
    # Non-physical decks (digital/testing/retired) are hypothetical/wishlist
    # decklists that exist purely as deck.cards[] metadata and must have
    # zero inventory footprint. Physical decks get their instances from
    # instances.auto_bind_physical at the one-time status transition
    # (registry.set_status) -- calling ensure_wishlist_instances here on
    # every resync would violate the "no automatic instance creation for
    # remotely-added cards" rule for physical-deck resyncs. See
    # docs/PHYSICAL_RESYNC_ADJUDICATION.md sections 2-3 (reverses #18).
    if result_deck.get("status") == "physical":
        # Physical deck resync: surface a diff only. Flag instances that are
        # still bound (in_deck) to this deck but are no longer on the remote
        # decklist, and do NOT create placeholders for newly added remote
        # cards — a physical deck's inventory is real cardboard, so additions
        # and removals both wait for human adjudication.
        new_card_names = {c.get("name") for c in result_deck.get("cards", []) if c.get("name")}
        for did in deck_instance_ids(result_deck):
            instance_store.flag_resync_removed(r, did, new_card_names)

    return result_deck


def update_deck_cards(
    r,
    registry_id: str,
    cards: list,
    commanders: Optional[list] = None,
    commander_uids: Optional[list] = None,
    color: Optional[str] = None,
) -> dict:
    """Overwrite a deck's cardlist (and optionally commanders/commander_uids/
    color) in place, without touching its source/source_id/registry_id or
    lifecycle status. Used by deck_sync.py after merging cardlists pulled
    from more than one linked provider (see docs/DUAL_SOURCE_SYNC.md) --
    unlike upsert_deck, this never creates a new deck and never changes
    which provider(s) a deck is considered to originate from.

    Applies the same physical-deck resync diff as upsert_deck: for a deck
    whose status is "physical", bound instances no longer present in the
    new cardlist are flagged pending_removal rather than silently dropped
    or auto-added.

    Raises ValueError if registry_id doesn't match any deck.
    """
    decks = _load_decks(r)
    target = None
    for d in decks:
        if registry_id_of(d) == registry_id or str(d.get("id")) == registry_id:
            target = d
            break
    if target is None:
        raise ValueError(f"deck not found: {registry_id}")

    target["cards"] = cards
    if commanders is not None:
        target["commanders"] = commanders
    if commander_uids is not None:
        target["commander_uids"] = commander_uids
    if color is not None:
        target["color"] = color

    _save_decks(r, decks)

    if target.get("status") == "physical":
        new_card_names = {c.get("name") for c in cards if c.get("name")}
        instance_store.flag_resync_removed(r, target["id"], new_card_names)
        target_registry_id = registry_id_of(target)
        if str(target["id"]) != target_registry_id:
            instance_store.flag_resync_removed(r, target_registry_id, new_card_names)

    return target


def add_card_to_decklist(r, registry_id: str, card_name: str) -> dict:
    """Append `card_name` (quantity 1) to a deck's local decklist if it isn't
    already present. Used by the manual add-card path (POST
    /api/decks/{id}/cards) — layers manual adds on top of a synced decklist
    without duplicating an existing entry. Returns the updated deck dict.
    """
    decks = _load_decks(r)
    target = None
    for d in decks:
        if registry_id_of(d) == registry_id or str(d.get("id")) == registry_id:
            target = d
            break
    if not target:
        raise ValueError(f"deck not found: {registry_id}")

    cards = target.setdefault("cards", [])
    already_present = any(c.get("name") == card_name for c in cards)
    if not already_present:
        cards.append({"name": card_name, "quantity": 1})
        _save_decks(r, decks)

    return target


def set_status(r, registry_id: str, new_status: str) -> dict:
    if new_status not in VALID_STATUSES:
        raise ValueError(f"invalid status: {new_status}")

    decks = _load_decks(r)
    target = None
    for d in decks:
        if registry_id_of(d) == registry_id or str(d.get("id")) == registry_id:
            target = d
            break
    if not target:
        raise ValueError(f"deck not found: {registry_id}")

    was_physical = target.get("status") == "physical"
    target["status"] = new_status
    _save_decks(r, decks)
    r.set(f"deck_status:{target['id']}", new_status)

    bind_report = None
    if new_status == "physical" and not was_physical:
        bind_report = instance_store.auto_bind_physical(
            r,
            deck_registry_id=target["id"],
            deck_name=target["name"],
            decklist=target.get("cards", []),
        )

    return {"deck": target, "auto_bind": bind_report}
