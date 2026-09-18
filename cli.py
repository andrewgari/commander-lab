"""
Commander Lab CLI — non-interactive card-to-deck assignment tool.

Usage:
    python cli.py assign --card "Sol Ring" --deck 12345
    python cli.py assign --instance-id <uuid> --deck moxfield:abc123
    python cli.py assign --card "Sol Ring" --deck 12345 --format json

Exit codes:
    0  success
    1  usage / validation error (bad args, ambiguous card, no match)
    2  not found (deck or instance doesn't exist)
    3  illegal transition / other InstanceError from the assignment helpers

This subcommand only implements the non-interactive, flag-driven path
(scriptable). The interactive prompt flow lives in a separate module and
is wired into `main()` as a fallback when required flags are omitted.
"""
import argparse
import json
import os
import sys

from typing import Optional

import redis

import instances as instance_store
import registry
from instances import InstanceError

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def get_redis():
    return redis.from_url(REDIS_URL, decode_responses=True)


def _resolve_deck(r, deck_ref: str):
    """Look up a deck by registry_id, source_id, or numeric/legacy id."""
    return registry.find_deck(r, deck_ref)


def _candidate_instances(r, card: Optional[str] = None, instance_id: Optional[str] = None):
    """Return the 'in_collection' instances eligible for assignment.

    If instance_id is given, resolve that single instance (regardless of
    its current status — the transition helper enforces legality).
    Otherwise, search in_collection instances by card name (case-insensitive
    substring match against card_name).
    """
    if instance_id:
        record = instance_store.get_instance(r, instance_id)
        return [record] if record else []

    all_in_collection = instance_store.list_instances(r, ownership_status="in_collection")
    if not card:
        return all_in_collection

    needle = card.strip().lower()
    exact = [i for i in all_in_collection if i["card_name"].strip().lower() == needle]
    if exact:
        return exact
    return [i for i in all_in_collection if needle in i["card_name"].strip().lower()]


def assign_card(r, card: Optional[str] = None, instance_id: Optional[str] = None, deck_ref: Optional[str] = None):
    """Core non-interactive assignment routine.

    Returns (exit_code, result_dict) where result_dict always has a
    "success" bool plus either an "instance"/"deck" payload or an "error".
    """
    if not deck_ref:
        return 1, {"success": False, "error": "destination deck is required"}
    if not card and not instance_id:
        return 1, {"success": False, "error": "one of --card or --instance-id is required"}

    deck = _resolve_deck(r, deck_ref)
    if not deck:
        return 2, {"success": False, "error": f"deck not found: {deck_ref}"}

    candidates = _candidate_instances(r, card=card, instance_id=instance_id)

    if instance_id and not candidates:
        return 2, {"success": False, "error": f"instance not found: {instance_id}"}

    if not instance_id:
        candidates = [i for i in candidates if i.get("ownership_status") == "in_collection"]
        if not candidates:
            return 2, {
                "success": False,
                "error": f"no in_collection instance found for card: {card!r}",
            }
        if len(candidates) > 1:
            return 1, {
                "success": False,
                "error": (
                    f"{len(candidates)} in_collection instances match {card!r}; "
                    "disambiguate with --instance-id"
                ),
                "candidates": [
                    {"id": i["id"], "card_name": i["card_name"], "set": i.get("set", ""),
                     "condition": i.get("condition", "")}
                    for i in candidates
                ],
            }

    instance = candidates[0]
    registry_id = registry.registry_id_of(deck)
    deck_name = deck.get("name", "")

    try:
        updated = instance_store.transition_status(
            r,
            instance["id"],
            new_status="in_deck",
            deck_id=registry_id,
            deck_name=deck_name,
        )
    except InstanceError as e:
        return 3, {"success": False, "error": str(e)}

    registry.add_card_to_decklist(r, registry_id, updated["card_name"])

    return 0, {
        "success": True,
        "instance": updated,
        "deck_id": registry_id,
        "deck_name": deck_name,
        "card_name": updated["card_name"],
    }


def _print_result(result: dict, fmt: str):
    if fmt == "json":
        print(json.dumps(result, indent=2))
        return

    if result.get("success"):
        print(
            f"Assigned {result['card_name']!r} (instance {result['instance']['id']}) "
            f"-> deck {result['deck_name']!r} ({result['deck_id']})"
        )
    else:
        print(f"Error: {result.get('error')}", file=sys.stderr)
        for cand in result.get("candidates", []):
            print(
                f"  - {cand['id']}  {cand['card_name']}  set={cand['set']!r} "
                f"condition={cand['condition']!r}",
                file=sys.stderr,
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cli.py",
        description="Commander Lab card/deck management CLI.",
    )
    sub = parser.add_subparsers(dest="command")

    assign = sub.add_parser(
        "assign",
        help="Assign an in_collection card instance to a deck (status -> in_deck).",
    )
    card_group = assign.add_mutually_exclusive_group(required=False)
    card_group.add_argument(
        "--card", help="Card name (or substring) to search among in_collection instances."
    )
    card_group.add_argument(
        "--instance-id", help="Exact instance id to assign, bypassing card search."
    )
    assign.add_argument(
        "--deck", required=False, help="Destination deck: registry_id, source_id, or legacy id."
    )
    assign.add_argument(
        "--format", choices=["text", "json"], default="text", help="Output format (default: text)."
    )

    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command != "assign":
        parser.print_help()
        return 1

    # Non-interactive path requires --deck and (--card or --instance-id).
    # If either is missing, fall back to the interactive flow when running
    # in a real TTY; otherwise fail with a clear usage error for scripting.
    if not args.deck or not (args.card or args.instance_id):
        if sys.stdin.isatty() and sys.stdout.isatty():
            try:
                import cli_interactive
            except ImportError:
                print(
                    "Error: --deck and one of --card/--instance-id are required "
                    "(interactive mode not available)",
                    file=sys.stderr,
                )
                return 1
            return cli_interactive.run(get_redis())
        print(
            "Error: --deck and one of --card/--instance-id are required for non-interactive use",
            file=sys.stderr,
        )
        return 1

    r = get_redis()
    code, result = assign_card(r, card=args.card, instance_id=args.instance_id, deck_ref=args.deck)
    _print_result(result, args.format)
    return code


if __name__ == "__main__":
    sys.exit(main())
