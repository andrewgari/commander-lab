"""
Commander Lab CLI — interactive prompt flow for card-to-deck assignment.

This module implements the TTY-driven counterpart to the flag-based
`cli.py assign` path: it prompts the user to search for a card, lists the
matching 'in_collection' instances with condition/set details, prompts for
an instance and destination deck, shows a confirmation, and then performs
the same status transition (in_collection -> in_deck) as the non-interactive
path via instances.transition_status / registry.add_card_to_decklist.

Entry point: run(r, input_fn=input, print_fn=print) -> int (exit code)

`input_fn`/`print_fn` are injected so tests can drive the flow with scripted
input lists instead of a real terminal.
"""
import sys
from typing import Callable, List

import instances as instance_store
import registry
from instances import InstanceError


class Cancelled(Exception):
    """Raised internally when the user aborts a prompt via 'q'/'quit'/'exit'
    or EOF (Ctrl-D). Blank input does NOT abort the flow — prompts that
    require a value (see _prompt_nonblank) re-prompt on blank input instead.
    """


def _prompt(input_fn: Callable[[str], str], prompt: str) -> str:
    try:
        return input_fn(prompt)
    except EOFError:
        raise Cancelled()


def _prompt_nonblank(input_fn, print_fn, prompt: str) -> str:
    while True:
        raw = _prompt(input_fn, prompt).strip()
        if raw.lower() in ("q", "quit", "exit"):
            raise Cancelled()
        if raw:
            return raw
        print_fn("Please enter a value (or 'q' to cancel).")


def _search_card(r, print_fn, needle: str) -> List[dict]:
    """Search in_collection instances by card name (exact match preferred,
    falling back to case-insensitive substring), mirroring cli.py's
    non-interactive matching so both paths behave identically."""
    all_in_collection = instance_store.list_instances(r, ownership_status="in_collection")
    needle_lc = needle.strip().lower()
    exact = [i for i in all_in_collection if i["card_name"].strip().lower() == needle_lc]
    if exact:
        print_fn(f"Found {len(exact)} exact match(es) for {needle!r}.")
        return exact
    matches = [i for i in all_in_collection if needle_lc in i["card_name"].strip().lower()]
    print_fn(f"Found {len(matches)} substring match(es) for {needle!r}.")
    return matches


def _format_instance_row(idx: int, inst: dict) -> str:
    deck_bit = f" deck={inst['deck_name']!r}" if inst.get("deck_name") else ""
    return (
        f"  [{idx}] {inst['card_name']}  "
        f"set={inst.get('set') or '?'} #{inst.get('collector_number') or '?'}  "
        f"finish={inst.get('finish', '')}  condition={inst.get('condition', '')}  "
        f"id={inst['id']}{deck_bit}"
    )


def _choose_instance(input_fn, print_fn, candidates: List[dict]) -> dict:
    print_fn(f"Found {len(candidates)} in_collection instance(s):")
    for i, inst in enumerate(candidates, start=1):
        print_fn(_format_instance_row(i, inst))

    while True:
        raw = _prompt(input_fn, f"Choose an instance [1-{len(candidates)}] (or 'q' to cancel): ").strip()
        if raw.lower() in ("q", "quit", "exit"):
            raise Cancelled()
        if raw.isdigit() and 1 <= int(raw) <= len(candidates):
            return candidates[int(raw) - 1]
        print_fn(f"Enter a number between 1 and {len(candidates)}.")


def _format_deck_row(idx: int, deck: dict) -> str:
    reg_id = registry.registry_id_of(deck)
    return f"  [{idx}] {deck.get('name', '?')}  status={deck.get('status', '?')}  id={reg_id}"


def _choose_deck(r, input_fn, print_fn) -> dict:
    decks = registry.list_decks(r)
    if not decks:
        raise Cancelled()

    print_fn("Available decks:")
    for i, deck in enumerate(decks, start=1):
        print_fn(_format_deck_row(i, deck))

    while True:
        raw = _prompt(
            input_fn,
            "Choose a destination deck by number, name, or id (or 'q' to cancel): ",
        ).strip()
        if raw.lower() in ("q", "quit", "exit"):
            raise Cancelled()
        if not raw:
            print_fn("Please enter a value.")
            continue

        if raw.isdigit() and 1 <= int(raw) <= len(decks):
            return decks[int(raw) - 1]

        deck = registry.find_deck(r, raw)
        if deck:
            return deck

        needle = raw.strip().lower()
        matches = [d for d in decks if needle in d.get("name", "").strip().lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            print_fn(f"{len(matches)} decks match {raw!r}; be more specific or use the number.")
            continue

        print_fn(f"No deck found for {raw!r}. Try again.")


def _confirm(input_fn, print_fn, instance: dict, deck: dict) -> bool:
    deck_name = deck.get("name", "")
    reg_id = registry.registry_id_of(deck)
    print_fn("")
    print_fn("About to assign:")
    print_fn(f"  Card:     {instance['card_name']}")
    print_fn(
        f"  Instance: {instance['id']}  "
        f"set={instance.get('set') or '?'} condition={instance.get('condition', '')} "
        f"finish={instance.get('finish', '')}"
    )
    print_fn(f"  Deck:     {deck_name!r} ({reg_id})")
    print_fn("  Transition: in_collection -> in_deck")
    print_fn("")
    raw = _prompt(input_fn, "Confirm? [y/N]: ").strip().lower()
    return raw in ("y", "yes")


def run(r, input_fn: Callable[[str], str] = input, print_fn: Callable[[str], None] = print) -> int:
    """Drive the full interactive assign flow. Returns a process exit code
    using the same convention as cli.py's non-interactive path:
        0  success
        1  usage / no match / cancelled by user
        2  not found (no matching instances or no decks)
        3  illegal transition / other InstanceError
    """
    try:
        needle = _prompt_nonblank(input_fn, print_fn, "Search for a card (name or substring): ")

        candidates = _search_card(r, print_fn, needle)
        if not candidates:
            print_fn(f"No in_collection instances found matching {needle!r}.")
            return 2

        instance = _choose_instance(input_fn, print_fn, candidates)

        try:
            deck = _choose_deck(r, input_fn, print_fn)
        except Cancelled:
            decks = registry.list_decks(r)
            print_fn("No decks available." if not decks else "Cancelled.")
            return 2 if not decks else 1

        if not _confirm(input_fn, print_fn, instance, deck):
            print_fn("Cancelled.")
            return 1

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
            print_fn(f"Error: {e}")
            return 3

        registry.add_card_to_decklist(r, registry_id, updated["card_name"])

        print_fn(
            f"Assigned {updated['card_name']!r} (instance {updated['id']}) "
            f"-> deck {deck_name!r} ({registry_id})"
        )
        return 0

    except Cancelled:
        print_fn("Cancelled.")
        return 1


if __name__ == "__main__":
    import os
    import redis

    REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    sys.exit(run(redis.from_url(REDIS_URL, decode_responses=True)))
