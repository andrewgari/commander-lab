#!/usr/bin/env python3
"""Offline merit calibration: fetch raw EDHREC + Commander Spellbook data.

Run on a workstation, never in prod. Raw payloads go to $CALIBRATION_CACHE_DIR
only (never committed). Resumable: cached files are skipped.

  CALIBRATION_CACHE_DIR=~/cal python scripts/calibrate_merit.py fetch \
      [--commander "Krenko, Mob Boss" ...] [--year-top N] [--skip-spellbook]
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics.merit import combine as merit_combine  # noqa: E402
from src.server.domain.analytics.merit import signals as sig  # noqa: E402
from src.server.infra.merit.fetchers import (  # noqa: E402
    MIN_DECK_CARDS, Fetcher, cache_dir, slugify, write_manifest,
)


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


SEED = 20261105
LAMBDA = 1.0
ADOPT_MARGIN_AUC = 0.01
CARD_SHARE = 0.65
KEYS = ("I", "S", "L", "C", "R")
STRUCTURAL = {"sideboard", "maybeboard", "considering"}
HI_PAGES, LO_PAGES = ("optimized", "cedh"), ("exhibition", "core", "upgraded")
SKIP_HEADERS = ("land", "new cards")
CAVEAT = ("EDHREC aggregates Archidekt decks, so held-out Archidekt decks are not independent of the "
          "inclusion signals; AUCs are optimistic. Labelled decks are used for evaluation only and never republished.")


# -- cache readers ---------------------------------------------------------
def _load_json(path: Path):
    raw = path.read_bytes()
    if path.suffix == ".gz" or raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)


def load_scryfall(cache: Path) -> dict:
    """name -> {"rank": int|None, "land": bool} from the cached oracle_cards bulk."""
    out = {}
    for f in sorted((cache / "scryfall").glob("oracle_cards.*")):
        raw = f.read_bytes()
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8")
        if text.lstrip()[:1] == "[":
            cards = json.loads(text)
        else:
            cards = [json.loads(line) for line in text.splitlines() if line.strip()]
        for c in cards:
            full = c.get("name", "")
            for n in {full, full.split(" // ")[0]}:
                if n:
                    out[n] = {"rank": c.get("edhrec_rank"), "land": "Land" in (c.get("type_line") or "")}
        break
    return out


def load_spellbook(cache: Path):
    """-> (by_card {name: [(pieces, tag, popularity)]}, p99_popularity) or (None, 0)."""
    f = cache / "spellbook" / "variants.json.gz"
    if not f.exists():
        return None, 0.0
    by_card, pops = {}, []
    for v in _load_json(f).get("variants", []):
        names = frozenset(u["card"]["name"] for u in v.get("uses", []) if u.get("card"))
        if len(names) < 2:
            continue
        entry = (names, v.get("bracketTag"), v.get("popularity") or 0)
        pops.append(entry[2])
        for n in names:
            by_card.setdefault(n, []).append(entry)
    pops.sort()
    return by_card, float(pops[int(0.99 * (len(pops) - 1))]) if pops else 0.0


def load_decks(cache: Path, exclude_ids=frozenset()) -> list:
    """Cached Archidekt decks passing the filter: bracket labelled, >=95 cards, one commander."""
    decks = []
    for f in sorted((cache / "archidekt").glob("b*/*.json")):
        d = json.loads(f.read_text())
        if d.get("id") in exclude_ids or not isinstance(d.get("edhBracket"), int):
            continue
        cmd, cards = [], []
        for c in d.get("cards") or []:
            cats = {x.lower() for x in c.get("categories") or []}
            name = ((c.get("card") or {}).get("oracleCard") or {}).get("name")
            if not name or cats & STRUCTURAL:
                continue
            (cmd if "commander" in cats else cards).append(name)
        if len(cmd) != 1 or len(cards) + 1 < MIN_DECK_CARDS:
            continue
        decks.append({"id": d["id"], "bracket": d["edhBracket"], "commander": cmd[0], "cards": sorted(set(cards))})
    return decks


def _page(path: Path):
    """EDHREC page -> (total_decks, {name: (num_decks, synergy)}) or None."""
    if not path.exists():
        return None
    jd = _load_json(path)["container"]["json_dict"]
    total = float((jd.get("card") or {}).get("num_decks") or 0)
    views = {}
    for lst in jd.get("cardlists") or []:
        if any(h in (lst.get("header") or "").lower() for h in SKIP_HEADERS):
            continue
        for cv in lst.get("cardviews") or []:
            views.setdefault(cv["name"], (cv.get("num_decks"), cv.get("synergy")))
    return total, views


def commander_pages(cache: Path, commander: str):
    s = slugify(commander)
    base = _page(cache / "edhrec" / s / "base.json")
    if not base or base[0] <= 0:
        return None
    return base, {b: _page(cache / "edhrec" / s / f"{b}.json") for b in HI_PAGES + LO_PAGES}


def card_signals(name, base, brackets, rank, deck, variants, p99):
    total, views = base
    n, syn = views[name]
    p0 = n / total if n is not None else None

    def pages(keys):
        out = []
        for k in keys:
            pg = brackets.get(k)
            if not pg or pg[0] <= 0:
                continue
            t, v = pg
            mn = min((x[0] / t for x in v.values() if x[0] is not None), default=None)
            out.append(sig.BracketPage(v[name][0] if name in v else None, t, mn))
        return out

    return {
        "I": sig.inclusion(p0), "S": sig.synergy(syn),
        "L": sig.bracket_lift(pages(HI_PAGES), pages(LO_PAGES), p0).value,
        "C": sig.combo(name, deck, variants, p99) if variants is not None else None,
        "R": sig.global_rank(rank),
    }


# -- numerics (stdlib; 5 features) -----------------------------------------
def auc(scores, labels) -> float:
    pairs = sorted(zip(scores, labels))
    pos = sum(labels)
    neg = len(labels) - pos
    if not pos or not neg:
        return float("nan")
    rank_sum, i = 0.0, 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg = (i + j + 1) / 2.0
        rank_sum += avg * sum(l for _, l in pairs[i:j])
        i = j
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def fit_logistic(X, y, lam=LAMBDA, iters=200):
    """L2 logistic regression via Newton's method (intercept unpenalised)."""
    k = len(X[0]) + 1
    w = [0.0] * k
    for _ in range(iters):
        g = [0.0] * k
        H = [[0.0] * k for _ in range(k)]
        for xi, yi in zip(X, y):
            z = [1.0] + list(xi)
            p = 1.0 / (1.0 + math.exp(-max(-30, min(30, sum(a * b for a, b in zip(w, z))))))
            for a in range(k):
                g[a] += (p - yi) * z[a]
                for b in range(k):
                    H[a][b] += p * (1 - p) * z[a] * z[b]
        for a in range(1, k):
            g[a] += lam * w[a]
            H[a][a] += lam
        step = _solve(H, g)
        w = [a - b for a, b in zip(w, step)]
        if max(abs(s) for s in step) < 1e-8:
            break
    return w


def _solve(A, b):
    n = len(b)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for c in range(n):
        piv = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[piv] = M[piv], M[c]
        d = M[c][c] or 1e-12
        for r in range(c + 1, n):
            f = M[r][c] / d
            for cc in range(c, n + 1):
                M[r][cc] -= f * M[c][cc]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (M[r][n] - sum(M[r][c] * x[c] for c in range(r + 1, n))) / (M[r][r] or 1e-12)
    return x


def _prep(rows, keys, stats=None):
    if stats is None:
        stats = {}
        for k in keys:
            vals = [r["x"][k] for r in rows if r["x"][k] is not None]
            m = sum(vals) / len(vals) if vals else 0.0
            sd = math.sqrt(sum((v - m) ** 2 for v in vals) / len(vals)) if vals else 1.0
            stats[k] = (m, sd or 1.0)
    X = [[((r["x"][k] if r["x"][k] is not None else stats[k][0]) - stats[k][0]) / stats[k][1] for k in keys] for r in rows]
    return X, stats


def fit_weights(rows, keys=KEYS):
    """Fit; clamp negatives to 0 and refit without them. Returns (coefs, stats, keys_used)."""
    keys = list(keys)
    while keys:
        X, stats = _prep(rows, keys)
        w = fit_logistic(X, [r["y"] for r in rows])[1:]
        bad = [k for k, c in zip(keys, w) if c <= 0]
        if not bad:
            return dict(zip(keys, w)), stats, keys
        keys = [k for k in keys if k not in bad]
    return {}, {}, []


def split_commanders(commanders, seed=SEED, test_frac=0.30):
    cs = sorted(commanders)
    random.Random(seed).shuffle(cs)
    k = max(1, round(len(cs) * test_frac)) if len(cs) > 1 else 0
    return set(cs[k:]), set(cs[:k])


def build_rows(decks, cache, sc, spell, p99, seed):
    rng = random.Random(seed)
    rows, skipped = [], {}
    page_cache = {}
    for d in decks:
        c = d["commander"]
        if c not in sc:
            skipped["commander_not_in_scryfall"] = skipped.get("commander_not_in_scryfall", 0) + 1
            continue
        if c not in page_cache:
            page_cache[c] = commander_pages(cache, c)
        pages = page_cache[c]
        if pages is None:
            skipped["no_edhrec_pages"] = skipped.get("no_edhrec_pages", 0) + 1
            continue
        base, brackets = pages
        views = base[1]
        in_deck = set(d["cards"])
        pos = [n for n in d["cards"] if n in views and not sc.get(n, {}).get("land")]
        pool = sorted(n for n in views if n not in in_deck and n != c and not sc.get(n, {}).get("land"))
        neg = rng.sample(pool, min(len(pos), len(pool)))
        deck_all = in_deck | {c}
        for names, y in ((pos, 1), (neg, 0)):
            for n in names:
                x = card_signals(n, base, brackets, (sc.get(n) or {}).get("rank"), deck_all,
                                 spell.get(n, []) if spell is not None else None, p99)
                rows.append({"commander": c, "deck": d["id"], "bracket": d["bracket"], "y": y, "x": x})
    return rows, skipped


def fit(cache: Path, seed=SEED, exclude_ids=frozenset(), n_boot=5) -> dict:
    sc = load_scryfall(cache)
    spell, p99 = load_spellbook(cache)
    decks = load_decks(cache, exclude_ids)
    rows, skipped = build_rows(decks, cache, sc, spell, p99, seed)
    per_bracket = {}
    for d in decks:
        per_bracket[str(d["bracket"])] = per_bracket.get(str(d["bracket"]), 0) + 1
    rep = {
        "version": "mw-fit-1", "seed": seed, "caveat": CAVEAT,
        "provenance": {"sources": ["json.edhrec.com", "json.commanderspellbook.com", "api.scryfall.com", "archidekt.com"],
                       "commander_salt": "not used", "raw_data_committed": False},
        "counts": {"decks": len(decks), "decks_per_bracket": per_bracket, "rows": len(rows), "skipped": skipped},
        "adopted": False, "weights": {}, "current_weights": dict(merit_combine.DEFAULT_WEIGHTS),
    }
    train_c, test_c = split_commanders({r["commander"] for r in rows}, seed)
    train = [r for r in rows if r["commander"] in train_c]
    test = [r for r in rows if r["commander"] in test_c]
    rep["counts"].update(train_commanders=len(train_c), test_commanders=len(test_c), train_rows=len(train), test_rows=len(test))
    if len(train) < 50 or len({r["y"] for r in train}) < 2 or len({r["y"] for r in test}) < 2:
        rep["reason"] = "insufficient held-out data to fit"
        return rep
    coefs, stats, used = fit_weights(train)
    # sign stability over bootstrap resamples of train commanders
    rng = random.Random(seed)
    tc = sorted(train_c)
    signs = {k: 0 for k in used}
    for _ in range(n_boot):
        pick = [rng.choice(tc) for _ in tc]
        cnt = {}
        for c in pick:
            cnt[c] = cnt.get(c, 0) + 1
        boot = [r for r in train if r["commander"] in cnt for _ in range(cnt[r["commander"]])]
        if len({r["y"] for r in boot}) < 2:
            continue
        Xb, _ = _prep(boot, used)
        wb = fit_logistic(Xb, [r["y"] for r in boot])[1:]
        for k, c in zip(used, wb):
            signs[k] += c > 0
    stable = [k for k in used if signs[k] == n_boot]
    if stable != used:
        coefs, stats, used = fit_weights(train, stable)
    if not used:
        rep["reason"] = "no signal had a stable positive coefficient"
        return rep
    tot = sum(coefs.values())
    weights = {k: round(CARD_SHARE * coefs[k] / tot, 4) if k in coefs else 0.0 for k in KEYS}
    ys = [r["y"] for r in test]
    Xt, _ = _prep(test, used, stats)
    fit_auc = auc([sum(c * v for c, v in zip((coefs[k] for k in used), row)) for row in Xt], ys)
    cur = [merit_combine.merit_e(r["x"]) for r in test]
    cur_auc = auc([c if c is not None else 0.5 for c in cur], ys)
    single = {}
    for k in KEYS:
        vals = [r["x"][k] for r in test]
        known = [v for v in vals if v is not None]
        if known:
            m = sum(known) / len(known)
            single[k] = round(auc([v if v is not None else m for v in vals], ys), 4)
    rep.update(
        weights=weights, weights_normalised={k: round(v / CARD_SHARE, 4) for k, v in weights.items()},
        coefficients_standardised={k: round(v, 4) for k, v in coefs.items()},
        bootstrap_positive_sign={k: f"{signs[k]}/{n_boot}" for k in signs},
        heldout={"commanders": len(test_c), "rows": len(test), "auc_fit": round(fit_auc, 4),
                 "auc_current_weights": round(cur_auc, 4), "auc_single_signal": single,
                 "auc_gain": round(fit_auc - cur_auc, 4)},
        adopted=bool(fit_auc - cur_auc >= ADOPT_MARGIN_AUC),
        reason=("fit beats current weights on held-out AUC by >= %.2f" if fit_auc - cur_auc >= ADOPT_MARGIN_AUC
                else "not adopted: AUC gain below %.2f") % ADOPT_MARGIN_AUC,
        note="Report only. Weights are not applied anywhere (MERIT-4). Deck-power fit (T2) is not part of this mode.",
    )
    return rep


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("fetch")
    f.add_argument("--commander", action="append", default=[])
    f.add_argument("--year-top", type=int, default=0, help="also fetch top N commanders from year.json")
    f.add_argument("--skip-spellbook", action="store_true")
    f.add_argument("--scryfall-bulk", action="store_true", help="cache the Scryfall oracle_cards bulk file")
    f.add_argument("--archidekt-per-bracket", type=int, default=0, help="cache up to N labelled decks per bracket")
    f.add_argument("--exclude-deck-id", type=int, action="append", default=[], help="Archidekt deck ids to skip (your own)")
    f.add_argument("--edhrec-for-heldout", type=int, default=0, help="fetch EDHREC pages for the top N commanders of cached held-out decks")
    r = sub.add_parser("fit")
    r.add_argument("--out", default=None, help="report path (default: <cache>/fit_report.json)")
    r.add_argument("--exclude-deck-id", type=int, action="append", default=[])
    r.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)

    cache = cache_dir()
    if args.cmd == "fit":
        report = fit(cache, seed=args.seed, exclude_ids=set(args.exclude_deck_id))
        out = Path(args.out) if args.out else cache / "fit_report.json"
        out.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(json.dumps({"report": str(out), "adopt_card_weights": report.get("adopted"), "reason": report.get("reason")}))
        return 0
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
    if args.scryfall_bulk:
        manifest["scryfall_bulk"] = str(fx.scryfall_bulk())
    if args.archidekt_per_bracket:
        manifest["archidekt"] = {}
        for b in (1, 2, 3, 4, 5):
            got = fx.archidekt_heldout(b, args.archidekt_per_bracket, args.exclude_deck_id)
            manifest["archidekt"][str(b)] = len(got)
    if args.edhrec_for_heldout:
        counts = {}
        for d in load_decks(cache, set(args.exclude_deck_id)):
            counts[d["commander"]] = counts.get(d["commander"], 0) + 1
        top = sorted(counts, key=lambda c: (-counts[c], c))[: args.edhrec_for_heldout]
        for name in top:
            res = fx.edhrec_commander(name)
            manifest["commanders"].append(name)
            manifest["missing"] += [f"{name}/{k}" for k, v in res.items() if v is None]
    manifest["requests"] = fx.requests
    write_manifest(cache, manifest)
    print(json.dumps({k: (len(v) if isinstance(v, list) else v) for k, v in manifest.items()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
