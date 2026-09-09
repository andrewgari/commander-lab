"""
Deck Salvager -- inventory matching logic.

Given a decklist (from a paste, or a fetched provider deck), checks it
against the existing instance registry to answer "could I build this deck
today from what I already own" -- without writing anything to Redis.

Kept out of app.py/instances.py since this is read-only/derived reporting
over the instance layer, not a core inventory or registry concern.

See docs/DECK_SALVAGER.md for the full design rationale.
"""
import instances


def match_decklist_to_inventory(r, cards: list[dict]) -> dict:
    """Match a decklist against current inventory.

    Args:
        r: redis client (or compatible).
        cards: [{"name": str, "quantity": int}, ...]

    Returns:
        {
            "matches": [
                {"name": str, "quantity": int, "status": str,
                 "free": int, "committed": int, "in_mail": int,
                 "committed_decks": [str, ...]},
                ...
            ],
            "summary": {"have": int, "steal": int, "incoming": int, "missing": int},
        }
    """
    matches = []
    summary = {"have": 0, "steal": 0, "incoming": 0, "missing": 0}

    for card in cards:
        name = card["name"]
        quantity = card.get("quantity", 1)

        free_instances = instances.list_instances(
            r, card_name=name, ownership_status="in_collection"
        )
        committed_instances = instances.list_instances(
            r, card_name=name, ownership_status="in_deck"
        )
        in_mail_instances = instances.list_instances(
            r, card_name=name, ownership_status="in_mail"
        )

        free = len(free_instances)
        committed = len(committed_instances)
        in_mail = len(in_mail_instances)

        committed_decks = sorted(
            {
                inst.get("deck_name") or inst.get("deck_id")
                for inst in committed_instances
                if inst.get("deck_name") or inst.get("deck_id")
            }
        )

        if free >= quantity:
            status = "have"
        elif free > 0:
            status = "steal"
        elif committed > 0:
            status = "steal"
        elif in_mail > 0:
            status = "incoming"
        else:
            status = "missing"

        summary[status] += 1

        matches.append(
            {
                "name": name,
                "quantity": quantity,
                "status": status,
                "free": free,
                "committed": committed,
                "in_mail": in_mail,
                "committed_decks": committed_decks,
            }
        )

    return {"matches": matches, "summary": summary}
