"""
Deck reconciliation — pure diffing engine (no Redis/IO).

Compares an Archidekt decklist's required card counts against the card
Instances currently assigned (`ownership_status == "in_deck"`) to that deck
in the Lab, and reports shortfalls/overages/exact matches per card. This is
the "reconcile" view called out in docs/CARD_DATABASE.md #2 — decklists
(Archidekt-owned) and instance assignments (Lab-owned) can drift, and this
module is the diff between them.

Deliberately has zero Redis/HTTP dependencies so it's trivially unit
testable and reusable from both the future API endpoint (t_0d8bcabc) and
any offline/CLI tooling. Callers are responsible for fetching the decklist
(providers.fetch_deck) and the deck's in_deck instances
(instances.list_instances(r, deck_id=..., ownership_status="in_deck"))
and handing both to `reconcile_deck`.

Card identity
-------------
A card is identified primarily by `oracle_id` when present on both the
decklist entry and the instance record (see cards.py — Scryfall oracle_id,
stable across printings/reprints), falling back to case-sensitive `name`
match when oracle_id is unavailable on either side (today's decklist shape
from providers/archidekt.py and providers/moxfield.py only carries `name`;
oracle_id support is additive/forward-compatible here).

Printing preferences
---------------------
A decklist entry MAY optionally carry `preferred_set` / `preferred_collector_number`
(not populated by the current providers, but supported here so a future
"pin this deck to a specific printing" feature doesn't need engine changes).
When a preference is given, assigned instances matching that printing are
counted first; instances of the same card but a different printing still
count toward `assigned`/shortfall math (a card is a card for deckbuilding
purposes) but are flagged via `preferred_printing_satisfied = False` and
listed under `wrong_printing_instance_ids` so the UI can surface "you own
enough copies, but not in the printing you asked for."
"""
from typing import Optional


def _card_key(entry: dict) -> str:
    """Identity key for a decklist entry or instance record: prefer
    oracle_id (stable across reprints) and fall back to name."""
    oracle_id = entry.get("oracle_id")
    if oracle_id:
        return f"oracle:{oracle_id}"
    name = entry.get("name") or entry.get("card_name")
    return f"name:{name}"


def _display_name(entry: dict) -> str:
    return entry.get("name") or entry.get("card_name") or ""


def _matches_preferred_printing(instance: dict, preferred_set: Optional[str],
                                 preferred_collector_number: Optional[str]) -> bool:
    if not preferred_set and not preferred_collector_number:
        return True
    if preferred_set and instance.get("set") != preferred_set:
        return False
    if preferred_collector_number and instance.get("collector_number") != preferred_collector_number:
        return False
    return True


def reconcile_deck(decklist: list, instances: list) -> dict:
    """Diff a decklist's required counts against assigned instances.

    Args:
        decklist: [{"name": str, "quantity": int, "oracle_id": str? ,
                     "preferred_set": str?, "preferred_collector_number": str?}, ...]
                  (mainboard entries only — callers should already have
                  excluded structural boards, matching NORMALIZED_DECK_SHAPE
                  from providers/__init__.py)
        instances: the deck's currently-assigned card Instances (i.e.
                  already filtered by caller to ownership_status=="in_deck"
                  and deck_id==this deck; this function does not re-filter
                  by deck so it stays pure/IO-free).

    Returns:
        {
          "cards": [
            {
              "card_key": str,            # internal identity key used for grouping
              "card_name": str,           # display name
              "oracle_id": str | None,
              "required": int,
              "assigned": int,
              "shortfall": int,           # max(0, required - assigned)
              "overage": int,             # max(0, assigned - required)
              "status": "exact" | "shortfall" | "overage" | "unassigned",
              "assigned_instance_ids": [str, ...],
              "preferred_printing": {"set": str?, "collector_number": str?} | None,
              "preferred_printing_satisfied": bool,
              "wrong_printing_instance_ids": [str, ...],
            },
            ...
          ],
          "summary": {
            "total_cards": int,          # distinct cards considered
            "exact_matches": int,
            "shortfall_cards": int,
            "overage_cards": int,
            "total_shortfall_count": int,  # sum of per-card shortfalls
            "total_overage_count": int,    # sum of per-card overages
          },
        }
    """
    required_by_key: dict = {}
    meta_by_key: dict = {}

    for entry in decklist or []:
        quantity = entry.get("quantity", 1)
        if quantity is None or quantity <= 0:
            continue
        key = _card_key(entry)
        required_by_key[key] = required_by_key.get(key, 0) + quantity
        if key not in meta_by_key:
            meta_by_key[key] = {
                "card_name": _display_name(entry),
                "oracle_id": entry.get("oracle_id"),
                "preferred_set": entry.get("preferred_set"),
                "preferred_collector_number": entry.get("preferred_collector_number"),
            }

    assigned_by_key: dict = {}
    instance_ids_by_key: dict = {}
    wrong_printing_by_key: dict = {}

    for inst in instances or []:
        key = _card_key(inst)
        assigned_by_key[key] = assigned_by_key.get(key, 0) + 1
        instance_ids_by_key.setdefault(key, []).append(inst.get("id"))
        if key not in meta_by_key:
            # Instance assigned to this deck for a card no longer (or never)
            # in the decklist -- still worth surfacing as a pure overage.
            meta_by_key[key] = {
                "card_name": _display_name(inst),
                "oracle_id": inst.get("oracle_id"),
                "preferred_set": None,
                "preferred_collector_number": None,
            }
        meta = meta_by_key[key]
        if not _matches_preferred_printing(
            inst, meta.get("preferred_set"), meta.get("preferred_collector_number")
        ):
            wrong_printing_by_key.setdefault(key, []).append(inst.get("id"))

    all_keys = set(required_by_key) | set(assigned_by_key)

    cards = []
    exact_matches = shortfall_cards = overage_cards = 0
    total_shortfall_count = total_overage_count = 0

    for key in sorted(all_keys, key=lambda k: meta_by_key[k]["card_name"] or k):
        meta = meta_by_key[key]
        required = required_by_key.get(key, 0)
        assigned = assigned_by_key.get(key, 0)
        shortfall = max(0, required - assigned)
        overage = max(0, assigned - required)

        preferred_set = meta.get("preferred_set")
        preferred_collector_number = meta.get("preferred_collector_number")
        has_preference = bool(preferred_set or preferred_collector_number)
        wrong_printing_ids = wrong_printing_by_key.get(key, [])
        # Preference is "satisfied" only if we have enough instances that
        # actually match the requested printing to cover `required`.
        matching_count = assigned - len(wrong_printing_ids)
        preferred_printing_satisfied = (
            True if not has_preference else matching_count >= required
        )

        if required == 0 and assigned > 0:
            status = "overage"
            overage_cards += 1
        elif shortfall > 0 and assigned == 0:
            status = "unassigned"
            shortfall_cards += 1
        elif shortfall > 0:
            status = "shortfall"
            shortfall_cards += 1
        elif overage > 0:
            status = "overage"
            overage_cards += 1
        else:
            status = "exact"
            exact_matches += 1

        total_shortfall_count += shortfall
        total_overage_count += overage

        cards.append({
            "card_key": key,
            "card_name": meta["card_name"],
            "oracle_id": meta.get("oracle_id"),
            "required": required,
            "assigned": assigned,
            "shortfall": shortfall,
            "overage": overage,
            "status": status,
            "assigned_instance_ids": instance_ids_by_key.get(key, []),
            "preferred_printing": (
                {"set": preferred_set, "collector_number": preferred_collector_number}
                if has_preference else None
            ),
            "preferred_printing_satisfied": preferred_printing_satisfied,
            "wrong_printing_instance_ids": wrong_printing_ids,
        })

    summary = {
        "total_cards": len(cards),
        "exact_matches": exact_matches,
        "shortfall_cards": shortfall_cards,
        "overage_cards": overage_cards,
        "total_shortfall_count": total_shortfall_count,
        "total_overage_count": total_overage_count,
    }

    return {"cards": cards, "summary": summary}
