"""
Backfill deck source links (t_a2966d3f).

Before this fix, account sync and /api/import never wrote deck["links"], and
the Moxfield account sync upserted its decks as separate moxfield:<id>
entries that a later full sync dropped. This script repairs existing data:

1. links.archidekt = {identifier: deck url, deck_id: source_id} on every
   Archidekt-sourced deck that doesn't have one yet (no network).
2. With --moxfield-user, pulls that user's public Moxfield decks and attaches
   each to the registry deck it matches (same commanders + normalized name,
   see registry.match_provider_deck): links.moxfield + moxfield_id only.
   Decks with no unique match are listed and left alone.

Only `links` and `moxfield_id` are written on deck records. Status, cards,
instances, journal, and identity are never touched.
Idempotent: a second run reports nothing left to change.

Usage:
    python scripts/backfill_deck_links.py [--moxfield-user CovaDax]           # dry run
    python scripts/backfill_deck_links.py [--moxfield-user CovaDax] --apply
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.server.domain.decks.registry as registry  # noqa: E402

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def backfill_archidekt(r, apply: bool) -> list:
    """Step 1. Returns the names of decks that get (or would get) a link."""
    decks = registry.list_decks(r)
    changed = []
    for d in decks:
        if (d.get("source") or "archidekt") != "archidekt":
            continue
        if (d.get("links") or {}).get("archidekt"):
            continue
        sid = str(d.get("source_id") or d.get("id") or "")
        if not sid.isdigit():
            continue
        entry = registry.provider_link({"source": "archidekt", "source_id": sid, "url": d.get("url")})
        d["links"] = registry.merge_link(d.get("links"), "archidekt", entry)
        changed.append(d.get("name"))
    if apply and changed:
        registry._save_decks(r, decks)
    return changed


def backfill_moxfield(r, username: str, apply: bool, provider=None) -> dict:
    """Step 2. Returns {"matched": [(mox name, registry name)],
    "already": [...], "unmatched": [{...}], "failures": [{...}]}."""
    if provider is None:
        from src.server.infra.providers import moxfield as provider
    report = {"matched": [], "already": [], "unmatched": [], "failures": []}
    for pid in provider.list_decks(username):
        try:
            normalized = provider.fetch_deck(pid)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            report["failures"].append({"deck_id": pid, "error": str(exc)})
            continue
        before = registry.list_decks(r)
        already = any(((d.get("links") or {}).get("moxfield") or {}).get("deck_id") == str(normalized["source_id"])
                      for d in before)
        res = registry.attach_provider_deck(r, normalized, apply=apply)
        if res["result"] == "unmatched":
            report["unmatched"].append({"deck_id": str(normalized["source_id"]), "name": normalized.get("name"),
                                        "commanders": normalized.get("commanders", []), "reason": res["reason"]})
        elif already:
            report["already"].append(normalized.get("name"))
        else:
            report["matched"].append((normalized.get("name"), (res["deck"] or {}).get("name")))
    return report


def main():
    import redis

    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--moxfield-user", default=None)
    args = parser.parse_args()
    r = redis.from_url(REDIS_URL, decode_responses=True)
    verb = "Linked" if args.apply else "Would link"

    arch = backfill_archidekt(r, args.apply)
    print(f"{verb} archidekt on {len(arch)} decks")

    if args.moxfield_user:
        rep = backfill_moxfield(r, args.moxfield_user, args.apply)
        print(f"{verb} moxfield on {len(rep['matched'])} decks; already linked: {len(rep['already'])}")
        for mox, reg in rep["matched"]:
            print(f"  + {mox!r} -> {reg!r}")
        print(f"Unmatched: {len(rep['unmatched'])}")
        for u in rep["unmatched"]:
            print(f"  - {u['name']!r} {u['commanders']} ({u['deck_id']}): {u['reason']}")
        for f in rep["failures"]:
            print(f"  ! fetch failed {f['deck_id']}: {f['error']}")

    decks = registry.list_decks(r)
    print(json.dumps({
        "decks": len(decks),
        "with_links_archidekt": sum(1 for d in decks if (d.get("links") or {}).get("archidekt")),
        "with_links_moxfield": sum(1 for d in decks if (d.get("links") or {}).get("moxfield")),
        "physical": sum(1 for d in decks if d.get("status") == "physical"),
    }))


if __name__ == "__main__":
    main()
