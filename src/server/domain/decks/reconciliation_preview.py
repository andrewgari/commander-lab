"""Read-only Archidekt reconciliation preview (development slice, STA-10 A).

Commander Lab is the source of truth; Archidekt folder 588380 is a temporary
roster of physical decks. This module NEVER writes anything: it turns source
snapshots (raw Archidekt deck payloads / folder rosters) plus local state
into explicit per-deck differences, proposals, and exceptions.

Guarantees encoded here (see STA-9 source-validation contract):

- Identity is stable and provider-scoped: ``archidekt:{source_deck_id}``.
  Local decks are matched on that identity only -- never by name.
- Raw source facts are preserved without interpretation: row counts vs
  quantity sums, per-category unit counts, category inclusion flags,
  printing/finish metadata, ``owned`` hints, and source timestamps.
- Missing or conflicting mappings surface as visible reconciliation
  exceptions. Nothing is guessed, trimmed, or "corrected" -- in particular
  a deck with ambiguous category inclusion (the Atla case) is flagged and
  its main deck is left explicitly ambiguous; it is NEVER normalized to 100.
- Everything this module returns is a proposal. Local edits, intent,
  status, and inventory win and are never overwritten by a preview.

Pure logic: no FastAPI, no SQLAlchemy, no infra imports. Callers fetch
sources and local state and hand them in; idempotency is structural (same
inputs -> same output, zero side effects, so re-running a preview changes
nothing locally and creates no duplicate identities).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.server.domain.decks import deck_import

PROVIDER = "archidekt"

#: What a preview always promises (and tests assert): it writes nothing.
WRITES = "none"

#: Reconciliation exception codes (kept machine-checkable).
EX_AMBIGUOUS_INCLUSION = "ambiguous_inclusion"
EX_MAPPING_CONFLICT = "mapping_conflict"
EX_SOURCE_SNAPSHOT_MISSING = "source_snapshot_missing"
EX_PRINTING_LOCAL_UNKNOWN = "local_printing_unknown"


def registry_identity(source_deck_id, provider: str = PROVIDER) -> str:
    """Stable provider-scoped identity for a source deck id."""
    return f"{provider}:{source_deck_id}"


def source_id_from_local(deck: Optional[dict]) -> Optional[str]:
    """The Archidekt source id a local deck is identified by, if any."""
    if not deck:
        return None
    archidekt_id = deck.get("archidekt_id")
    if archidekt_id:
        return str(archidekt_id)
    local_id = str(deck.get("id") or "")
    if local_id.startswith(f"{PROVIDER}:"):
        return local_id.split(":", 1)[1]
    return None


def _category_inclusion(payload: dict) -> Dict[str, bool]:
    """Category name -> includedInDeck flag, from deck-level category
    metadata. When the payload carries none, the mapping is empty and every
    category counts as included (the existing importer's assumption)."""
    inclusion: Dict[str, bool] = {}
    for cat in payload.get("categories") or []:
        if isinstance(cat, dict) and cat.get("name") is not None:
            inclusion[str(cat["name"])] = bool(cat.get("includedInDeck", True))
    return inclusion


def snapshot_deck_facts(payload: dict) -> dict:
    """Capture the raw source facts of one Archidekt deck payload.

    No normalization, no mapping decisions: rows are counted as rows,
    quantities as quantities, categories and inclusion flags are recorded
    verbatim, and ambiguous rows are listed instead of resolved.
    """
    cards = [c for c in (payload.get("cards") or []) if isinstance(c, dict)]
    inclusion = _category_inclusion(payload)

    rows: List[dict] = []
    row_count = 0
    quantity_sum = 0
    included_unit_count = 0
    units_by_category: Dict[str, int] = {}
    mixed_rows: List[dict] = []
    commanders: List[str] = []

    for index, entry in enumerate(cards):
        card = entry.get("card") or {}
        oracle = card.get("oracleCard") or {}
        name = oracle.get("name") or card.get("name")
        if not name:
            continue  # not a card row we can identify; excluded from facts
        row_count += 1
        quantity = int(entry.get("quantity") or 1)
        quantity_sum += quantity
        categories = [str(c) for c in (entry.get("categories") or [])]
        if "Commander" in categories and name not in commanders:
            commanders.append(name)
        for cat in categories:
            units_by_category[cat] = units_by_category.get(cat, 0) + quantity

        included_flags = [inclusion.get(c, True) for c in categories]
        has_included = any(included_flags)
        has_excluded = bool(inclusion) and any(not f for f in included_flags)
        if has_included:
            included_unit_count += quantity
        if has_included and has_excluded:
            mixed_rows.append({
                "row": index,
                "name": name,
                "quantity": quantity,
                "categories": categories,
            })

        edition = card.get("edition") or {}
        rows.append({
            "row": index,
            "name": name,
            "oracle_id": str(oracle.get("uid") or ""),
            "quantity": quantity,
            "categories": categories,
            "owned": entry.get("owned"),
            "printing": {
                "scryfall_id": str(card.get("uid") or ""),
                "set_code": str(edition.get("editioncode") or ""),
                "collector_number": str(card.get("collectorNumber") or ""),
                "finish": entry.get("finish") or card.get("finish"),
            },
        })

    parent = payload.get("parentFolderId", payload.get("parentFolder"))
    if isinstance(parent, dict):
        parent = parent.get("id")

    exceptions: List[str] = []
    if mixed_rows:
        exceptions.append(EX_AMBIGUOUS_INCLUSION)

    return {
        "provider": PROVIDER,
        "source_deck_id": str(payload.get("id")),
        "registry_id": registry_identity(payload.get("id")),
        "url": payload.get("url") or f"https://archidekt.com/decks/{payload.get('id')}",
        "name": payload.get("name"),
        "parent_folder_id": parent,
        "updated_at": payload.get("updatedAt") or payload.get("updated"),
        "created_at": payload.get("createdAt") or payload.get("created"),
        "commanders": commanders,
        "row_count": row_count,
        "quantity_sum": quantity_sum,
        # None when the payload carries no deck-level inclusion metadata.
        "category_inclusion": inclusion or None,
        "included_unit_count": included_unit_count,
        "units_by_category": units_by_category,
        "mixed_inclusion_rows": mixed_rows,
        "cards": rows,
        "exceptions": exceptions,
    }


def _board_index(cards: List[dict]) -> Dict[tuple, int]:
    return {(c["oracle_id"], c["board"]): int(c.get("qty") or 0) for c in cards or []}


def _card_changes(source_cards: List[dict], local_cards: List[dict]) -> List[dict]:
    """Per-(oracle_id, board) quantity differences, source vs local.

    The SOURCE side is the proposal; local quantities are reported, never
    overwritten. Rows are ordered deterministically (board, oracle_id).
    """
    source = _board_index(source_cards)
    local = _board_index(local_cards)
    changes = []
    for key in sorted(set(source) | set(local), key=lambda k: (k[1], k[0])):
        src_qty, loc_qty = source.get(key, 0), local.get(key, 0)
        if src_qty == loc_qty:
            status = "unchanged"
        elif loc_qty == 0:
            status = "source_addition"
        elif src_qty == 0:
            status = "source_removal"
        else:
            status = "quantity_change"
        if status != "unchanged":
            changes.append({
                "oracle_id": key[0],
                "board": key[1],
                "source_qty": src_qty,
                "local_qty": loc_qty,
                "status": status,
                # Explicit: adopting this is a user action, not ours.
                "action": "proposed_only",
            })
    return changes


def preview_deck_refresh(
    payload: dict,
    local_deck: Optional[dict],
    local_cards: Optional[list],
) -> dict:
    """One deck's reconciliation preview: source facts + proposed diff vs
    the locally matched deck. Writes nothing; adopting anything is explicit.

    ``local_deck`` is the local record matched by stable provider identity
    (``archidekt:{source_id}`` / ``archidekt_id``); pass None when the
    upstream deck has no local counterpart yet.
    """
    facts = snapshot_deck_facts(payload)
    normalized = deck_import.normalize_archidekt_deck(payload)

    exceptions = list(facts["exceptions"])

    # Mapping-conflict check: when the source says which units are in the
    # main deck (inclusion metadata), the importer's board mapping must
    # agree with it. If auxiliary/ambiguous units would land in the main
    # board, that is a visible mapping conflict -- never a silent fix.
    source_main_qty = sum(
        c["qty"] for c in normalized["cards"] if c["board"] in ("main", "commander")
    )
    if facts["category_inclusion"] is not None:
        if source_main_qty != facts["included_unit_count"]:
            exceptions.append(EX_MAPPING_CONFLICT)

    source_printing_present = any(c["printing"]["scryfall_id"] for c in facts["cards"])
    if local_deck and source_printing_present:
        # deck_cards rows carry no printing/finish columns; the local side of
        # a printing comparison is genuinely unknown (SRC-06), never guessed.
        exceptions.append(EX_PRINTING_LOCAL_UNKNOWN)

    changed: Dict[str, Any] = {}
    matched = local_deck is not None
    if matched:
        card_changes = _card_changes(normalized["cards"], local_cards or [])
        changed = {
            "fingerprint_match": bool(
                local_deck.get("fingerprint")
                and local_deck.get("fingerprint") == normalized["fingerprint"]
            ),
            "name": {
                "source": facts["name"],
                "local": local_deck.get("name"),
                "differs": facts["name"] != local_deck.get("name"),
            },
            "commanders": {
                "source": facts["commanders"],
                "local": local_deck.get("commanders"),
                "differs": facts["commanders"] != (local_deck.get("commanders") or []),
            },
            "status": {
                # Proposal only: the folder rule says what the source implies;
                # the local status is never touched by a preview.
                "source_proposed": normalized["status"],
                "local": local_deck.get("status"),
                "differs": normalized["status"] != local_deck.get("status"),
            },
            "cards": card_changes,
            "card_change_count": len(card_changes),
        }

    local_updated = local_deck.get("updated_at") if local_deck else None
    return {
        "writes": WRITES,
        "identity": {
            "provider": PROVIDER,
            "source_deck_id": facts["source_deck_id"],
            "registry_id": facts["registry_id"],
            "matched_locally": matched,
            "local_deck_id": local_deck.get("id") if local_deck else None,
        },
        "source_facts": facts,
        "proposed_import": {
            # The shape the existing importer would write, shown for review.
            # Proposed only: nothing here is applied by a preview.
            "status": normalized["status"],
            "color_identity": normalized["color_identity"],
            "fingerprint": normalized["fingerprint"],
            "main_and_commander_qty": source_main_qty,
            "parse_flags": normalized["parse_flags"],
        },
        "main_deck_selection": (
            "ambiguous" if EX_AMBIGUOUS_INCLUSION in exceptions else "included_categories"
        ),
        "timestamps": {
            "source_updated_at": facts["updated_at"],
            "local_updated_at": local_updated,
            "source_newer_than_local": (
                None
                if not (facts["updated_at"] and local_updated)
                else str(facts["updated_at"]) > str(local_updated)
            ),
        },
        "printing_metadata": {
            "preserved_in_preview": {
                c["name"]: c["printing"] for c in facts["cards"] if c["printing"]["scryfall_id"]
            },
            "local_comparison": (
                "unavailable: deck_cards rows carry no printing/finish columns"
                if matched and source_printing_present
                else None
            ),
        },
        "differences": changed,
        "protected": [
            "local_deck_records", "local_edits", "deck_intent", "status",
            "inventory_instances", "archidekt_account",
        ],
        "exceptions": exceptions,
    }


def _roster_entry_id(entry: dict) -> Optional[str]:
    for key in ("source_id", "id", "deck_id"):
        if entry.get(key) is not None:
            return str(entry[key])
    return None


def preview_folder_roster(
    roster: List[dict],
    local_decks: List[dict],
    payloads: Optional[Dict[str, dict]] = None,
    local_cards_by_deck: Optional[Dict[str, list]] = None,
) -> dict:
    """Reconcile a folder roster snapshot against local decks.

    - Roster rows are deduplicated by numeric source id (SRC-01); duplicates
      are reported, never imported twice.
    - Local decks are matched only by stable provider identity.
    - A local deck absent from the roster becomes a proposed roster change
      (SRC-05), never a deletion.
    - ``payloads`` holds raw source snapshots fetched for the roster (only
      some decks may have one); decks without one get an explicit
      source-snapshot-missing exception instead of guessed facts.

    Writes nothing.
    """
    payloads = payloads or {}
    local_cards_by_deck = local_cards_by_deck or {}

    seen: Dict[str, dict] = {}
    duplicates: List[str] = []
    for entry in roster or []:
        sid = _roster_entry_id(entry)
        if sid is None:
            continue
        if sid in seen:
            duplicates.append(sid)
        else:
            seen[sid] = entry

    local_by_source: Dict[str, dict] = {}
    for deck in local_decks or []:
        sid = source_id_from_local(deck)
        if sid is not None:
            local_by_source[sid] = deck
    matched, new_upstream, per_deck = [], [], []
    for sid in sorted(seen):
        entry = seen[sid]
        local_deck = local_by_source.get(sid)
        payload = payloads.get(sid) or payloads.get(f"archidekt:{sid}")
        if local_deck is not None:
            matched.append({
                "source_deck_id": sid,
                "name": entry.get("name"),
                "local_deck_id": local_deck.get("id"),
            })
        else:
            new_upstream.append({
                "source_deck_id": sid,
                "name": entry.get("name"),
                "url": entry.get("url"),
                "action": "propose_import",
            })

        if payload is not None:
            per_deck.append(preview_deck_refresh(
                payload, local_deck, local_cards_by_deck.get(str(local_deck.get("id")) if local_deck else "")
            ))
        else:
            per_deck.append({
                "writes": WRITES,
                "identity": {
                    "provider": PROVIDER,
                    "source_deck_id": sid,
                    "registry_id": registry_identity(sid),
                    "matched_locally": local_deck is not None,
                    "local_deck_id": local_deck.get("id") if local_deck else None,
                },
                "source_facts": None,
                "differences": None,
                "exceptions": [EX_SOURCE_SNAPSHOT_MISSING],
                "coverage": (
                    "unavailable: no source snapshot captured for this deck in "
                    "the validated discovery capture; card-level facts not inspected"
                ),
            })

    matched_local_ids = {
        str(d.get("id")) for d in local_decks or []
        if source_id_from_local(d) in seen
    }
    local_not_in_roster = [
        {
            "local_deck_id": deck.get("id"),
            "name": deck.get("name"),
            "status": deck.get("status"),
            "action": "propose_roster_change",
            "reason": "not_in_folder_snapshot",
            "note": "the local record is kept; nothing is deleted automatically",
        }
        for deck in local_decks or []
        if str(deck.get("id")) not in matched_local_ids
    ]

    return {
        "writes": WRITES,
        "roster_count": len(roster or []),
        "distinct_source_deck_count": len(seen),
        "duplicate_source_ids": sorted(set(duplicates)),
        "matched_count": len(matched),
        "new_upstream_count": len(new_upstream),
        "matched": matched,
        "new_upstream": new_upstream,
        "local_not_in_roster": local_not_in_roster,
        "per_deck": per_deck,
        "coverage_limits": (
            "roster membership is complete for the captured snapshot; card-level "
            "facts exist only for decks with a captured source snapshot"
        ),
    }
