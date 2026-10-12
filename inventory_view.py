"""
Read model for /api/inventory: builds the flat per-copy rows that
inventory.html, deck.html and tags.html render, from the instance registry
(instance:*) joined with card_meta:*, lab_tags and per-deck category
overrides.

This replaces the old scan of legacy `card:{name}` blobs. Those keys were
purged by scripts/migrate_to_instances.py, so the old endpoint returned zero
rows on production data (see docs/DESIGN_REVIEW.md, Phase 0).

Row shape is unchanged from the legacy endpoint so the templates need no
changes: name, deck, type, super_types, sub_types, keywords, oracle_text,
color, set, set_name, modifier, categories, primary_tag, uid, alt_name,
price, is_commander, cmc, status, is_core.

`status` keeps the legacy inventory vocabulary the filter dropdown uses:
    in_deck / in_collection -> "have"
    in_mail                 -> "pending"
    not_owned               -> "possible"
"""
import json
from typing import Iterable, Optional

import instances as instance_store
import registry

STATUS_LABELS = {
    "in_deck": "have",
    "in_collection": "have",
    "in_mail": "pending",
    "not_owned": "possible",
}

STRUCTURAL = {"Commander", "Sideboard", "Maybeboard", "Considering"}


def _json(raw, default):
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def build_inventory(r, query: str = "", decks: Optional[Iterable[str]] = None) -> list:
    """Return inventory rows.

    No `decks` filter: every owned copy (not_owned wishlist placeholders are
    excluded, like the legacy endpoint excluded "virtual" copies).
    With `decks` (deck display names, or ids/registry_ids): copies bound to
    those decks, plus not_owned copies being considered for them.
    """
    target = [d for d in (decks or []) if d]
    all_decks = registry.list_decks(r)

    # Any id form an instance may carry -> deck dict
    deck_by_ref = {}
    for d in all_decks:
        for ref in registry.deck_instance_ids(d) + [d.get("name", "")]:
            if ref:
                deck_by_ref[str(ref)] = d

    wanted_refs = None
    if target:
        wanted_refs = set()
        for t in target:
            d = deck_by_ref.get(str(t))
            if d:
                wanted_refs.update(registry.deck_instance_ids(d))
                wanted_refs.add(d.get("name", ""))
            else:
                wanted_refs.add(str(t))

    needle = (query or "").strip().lower()

    rows_src = []
    for inst in instance_store.load_all_instances(r):
        name = inst.get("card_name") or ""
        if needle and needle not in name.lower():
            continue
        status = inst.get("ownership_status")
        ref = inst.get("deck_id") if status == "in_deck" else inst.get("considered_for_deck")
        ref = str(ref) if ref not in (None, "") else ""
        if wanted_refs is None:
            if status == "not_owned":
                continue
        elif ref not in wanted_refs:
            continue
        rows_src.append((inst, deck_by_ref.get(ref), ref))

    if not rows_src:
        return []

    names = sorted({inst["card_name"] for inst, _, _ in rows_src})
    mget = r.mget if hasattr(r, "mget") else (lambda ks: [r.get(k) for k in ks])
    name_meta = mget([f"card_meta:{n}" for n in names])
    # Prod also holds card_meta:{oracle_id} (written by the oracle backfill)
    # with correct cmc, while some name-keyed records carry a stale cmc of 0.
    # Prefer the oracle-keyed record when the name index resolves.
    oracle_ids = mget([f"idx:card_name_to_oracle:{n}" for n in names])
    oracle_meta = mget([f"card_meta:{o}" if o else "card_meta:" for o in oracle_ids])
    meta_by_name = {
        n: _json(om, None) or _json(nm, {})
        for n, nm, om in zip(names, name_meta, oracle_meta)
    }

    lab_tags = _json(r.get("lab_tags"), {})
    core_tags = set(_json(r.get("core_tags"), []))
    overrides_cache = {}

    def deck_overrides(deck):
        if not deck:
            return {}
        key = str(deck.get("id"))
        if key not in overrides_cache:
            merged = {}
            for did in reversed(registry.deck_instance_ids(deck)):
                merged.update(_json(r.get(f"deck_categories:{did}"), {}))
            overrides_cache[key] = merged
        return overrides_cache[key]

    rows = []
    for inst, deck, ref in rows_src:
        name = inst["card_name"]
        meta = meta_by_name.get(name, {})
        is_commander = bool(deck) and name in (deck.get("commanders") or [])
        categories = list(deck_overrides(deck).get(name) or lab_tags.get(name) or [])
        if is_commander and "Commander" not in categories:
            categories.insert(0, "Commander")
        primary = next((c for c in categories if c not in STRUCTURAL), "")
        rows.append({
            "name": name,
            "deck": (deck or {}).get("name") or inst.get("deck_name") or ref or "Unassigned",
            "type": meta.get("type", "Unknown"),
            "super_types": meta.get("super_types", []),
            "sub_types": meta.get("sub_types", []),
            "keywords": meta.get("keywords", []),
            "oracle_text": meta.get("oracle_text", ""),
            "color": meta.get("color", "C"),
            "set": inst.get("set", ""),
            "set_name": inst.get("set_name", ""),
            "modifier": "Foil" if inst.get("finish") in ("foil", "etched") else "Normal",
            "categories": categories,
            "primary_tag": primary,
            "uid": inst.get("archidekt_uid", ""),
            "alt_name": "",
            "price": inst.get("price_paid", 0.0) or 0.0,
            "is_commander": is_commander,
            "cmc": meta.get("cmc", 0),
            "status": STATUS_LABELS.get(inst.get("ownership_status"), "have"),
            "ownership_status": inst.get("ownership_status"),
            "instance_id": inst.get("id"),
            "is_core": any(c in core_tags for c in categories),
        })

    rows.sort(key=lambda x: (x["name"], x["deck"]))
    return rows
