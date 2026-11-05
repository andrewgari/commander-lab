#!/usr/bin/env python3
"""Offline merit calibration: fetch raw EDHREC + Commander Spellbook data.

Run on a workstation, never in prod. Raw payloads go to $CALIBRATION_CACHE_DIR
only (never committed). Resumable: cached files are skipped.

  CALIBRATION_CACHE_DIR=~/cal python scripts/calibrate_merit.py fetch \
      [--commander "Krenko, Mob Boss" ...] [--year-top N] [--skip-spellbook]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.infra.merit.fetchers import Fetcher, cache_dir, write_manifest  # noqa: E402


def _year_slugs(path, limit):
    data = json.loads(path.read_text())
    slugs = []

    def walk(o):
        if isinstance(o, dict):
            href = o.get("sanitized") or o.get("slug")
            if isinstance(href, str) and o.get("num_decks") is not None:
                slugs.append(href)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return list(dict.fromkeys(slugs))[:limit]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--commander", action="append", default=[])
    f.add_argument("--year-top", type=int, default=0, help="also fetch top N commanders from year.json")
    f.add_argument("--skip-spellbook", action="store_true")
    args = ap.parse_args(argv)

    cache = cache_dir()
    fx = Fetcher(cache)
    manifest = {"fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "commanders": [], "missing": []}
    slugs = []
    if args.year_top:
        year = fx.edhrec_year()
        if year:
            slugs += _year_slugs(year, args.year_top)
    for slug in slugs:
        res = fx.edhrec_commander(slug, slug=True)
        manifest["commanders"].append(slug)
        manifest["missing"] += [f"{slug}/{k}" for k, v in res.items() if v is None]
    for name in args.commander:
        res = fx.edhrec_commander(name)
        manifest["commanders"].append(name)
        manifest["missing"] += [f"{name}/{k}" for k, v in res.items() if v is None]
    if not args.skip_spellbook:
        manifest["spellbook"] = str(fx.spellbook_variants())
    manifest["requests"] = fx.requests
    write_manifest(cache, manifest)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in manifest.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
