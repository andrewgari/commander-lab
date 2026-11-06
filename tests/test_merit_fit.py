import gzip
import importlib.util
import json
import os
import random
import urllib.error
from pathlib import Path

import pytest

from src.server.infra.merit import fetchers as f

_spec = importlib.util.spec_from_file_location(
    "calibrate_merit", Path(__file__).resolve().parent.parent / "scripts" / "calibrate_merit.py")
cm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cm)


def make(tmp_path, responses):
    calls = []

    def opener(req):
        calls.append(req.full_url)
        r = responses.get(req.full_url)
        if r is None:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return r

    return f.Fetcher(tmp_path, opener=opener, sleep=lambda s: None), calls


def test_scryfall_bulk_cached_under_cache_dir_and_resumes(tmp_path):
    idx = json.dumps({"data": [{"type": "oracle_cards", "jsonl_download_uri": "https://data.scryfall.io/x.jsonl.gz"}]}).encode()
    fx, calls = make(tmp_path, {f.SCRYFALL_BULK_INDEX: idx, "https://data.scryfall.io/x.jsonl.gz": b"gz"})
    p = fx.scryfall_bulk()
    assert p == (tmp_path / "scryfall" / "oracle_cards.jsonl.gz").resolve() or p.read_bytes() == b"gz"
    assert p.is_relative_to(tmp_path.resolve())
    n = len(calls)
    fx.scryfall_bulk()
    assert len(calls) == n


def test_scryfall_bulk_rejects_bad_kind(tmp_path):
    fx, _ = make(tmp_path, {})
    with pytest.raises(f.CalibrationCacheError):
        fx.scryfall_bulk("../etc")


def _listing(ids, bracket, size=100, nxt=None):
    return json.dumps({"next": nxt, "results": [{"id": i, "size": size, "edhBracket": bracket} for i in ids]}).encode()


def test_archidekt_heldout_filters_caches_and_rate_limits(tmp_path):
    url = f"{f.ARCHIDEKT_LIST}?formats=3&edhBracket=3&pageSize=50&page=1"
    resp = {url: json.dumps({"next": None, "results": [
        {"id": 1, "size": 100, "edhBracket": 3}, {"id": 2, "size": 60, "edhBracket": 3},
        {"id": 3, "size": 100, "edhBracket": 3}, {"id": 4, "size": 100, "edhBracket": 2}]}).encode()}
    for i in (1, 3):
        resp[f.ARCHIDEKT_DECK.format(id=i)] = b'{"id": %d}' % i
    intervals = []
    fx, calls = make(tmp_path, resp)
    orig = fx._get
    fx._get = lambda u, m=None: (intervals.append(m), orig(u, m))[1]
    got = fx.archidekt_heldout(3, 10, exclude_ids=[3])
    assert [p.name for p in got] == ["1.json"]
    assert got[0].is_relative_to(tmp_path.resolve())
    assert all(m >= 1.0 for m in intervals)
    assert not any("commandersalt" in c for c in calls)
    with pytest.raises(f.CalibrationCacheError):
        fx.archidekt_heldout(9)


def test_forbidden_host_still_refused(tmp_path):
    fx, _ = make(tmp_path, {})
    with pytest.raises(f.CalibrationCacheError):
        fx._get("https://api.commandersalt.com/x")


# -- fit -------------------------------------------------------------------
def _edhrec(total, cards):
    return json.dumps({"container": {"json_dict": {"card": {"num_decks": total}, "cardlists": [
        {"header": "Creatures", "cardviews": [{"name": n, "num_decks": d, "synergy": s} for n, d, s in cards]}]}}})


def _deck(i, bracket, commander, cards):
    cs = [{"categories": ["Commander"], "card": {"oracleCard": {"name": commander}}}]
    cs += [{"categories": [], "card": {"oracleCard": {"name": n}}} for n in cards]
    return {"id": i, "edhBracket": bracket, "cards": cs}


def _world(root: Path, ncmd=10):
    rng = random.Random(1)
    sc = [{"name": "Cmd%d" % c, "edhrec_rank": 100, "type_line": "Legendary Creature"} for c in range(ncmd)]
    (root / "scryfall").mkdir(parents=True)
    (root / "scryfall" / "oracle_cards.jsonl.gz").write_bytes(gzip.compress("\n".join(json.dumps(c) for c in sc).encode()))
    did = 0
    for c in range(ncmd):
        names = ["C%d_%d" % (c, k) for k in range(200)]
        # popularity ~ position; high-ranked cards are in decks
        view = [(n, 1000 - 4 * k, 0.1) for k, n in enumerate(names)]
        d = root / "edhrec" / ("cmd%d" % c)
        d.mkdir(parents=True)
        for page in ["base", "exhibition", "core", "upgraded", "optimized", "cedh"]:
            (d / (page + ".json")).write_text(_edhrec(1000, view))
        for _ in range(3):
            did += 1
            cards = names[:100] if rng.random() < 0.9 else rng.sample(names, 100)
            b = root / "archidekt" / "b3"
            b.mkdir(parents=True, exist_ok=True)
            (b / ("%d.json" % did)).write_text(json.dumps(_deck(did, 3, "Cmd%d" % c, cards)))


def test_load_decks_filters(tmp_path):
    _world(tmp_path, 2)
    assert len(cm.load_decks(tmp_path)) == 6
    assert len(cm.load_decks(tmp_path, {1, 2})) == 4
    (tmp_path / "archidekt" / "b3" / "99.json").write_text(json.dumps(_deck(99, None, "Cmd0", ["a"] * 1)))
    assert len(cm.load_decks(tmp_path)) == 6


def test_fit_emits_weights_and_heldout_evidence_without_network(tmp_path, monkeypatch):
    _world(tmp_path)
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **k: (_ for _ in ()).throw(AssertionError("network")))
    rep = cm.fit(tmp_path)
    assert rep["counts"]["decks"] == 30
    assert rep["counts"]["test_commanders"] == 3
    assert set(rep["weights"]) == set(cm.KEYS)
    assert abs(sum(rep["weights"].values()) - cm.CARD_SHARE) < 1e-2
    assert "auc_fit" in rep["heldout"] and "auc_current_weights" in rep["heldout"]
    assert rep["provenance"]["commander_salt"] == "not used"
    assert rep["provenance"]["raw_data_committed"] is False
    assert "caveat" in rep
    assert all(v >= 0 for v in rep["weights"].values())
    assert rep["adopted"] in (True, False)
    json.dumps(rep)


def test_fit_insufficient_data(tmp_path):
    _world(tmp_path, 1)
    rep = cm.fit(tmp_path)
    assert rep["adopted"] is False and "insufficient" in rep["reason"]


def test_split_is_commander_disjoint_and_seeded():
    cs = ["c%d" % i for i in range(10)]
    a, b = cm.split_commanders(cs)
    assert not a & b and a | b == set(cs) and len(b) == 3
    assert cm.split_commanders(cs) == (a, b)


def test_auc_and_logistic():
    assert cm.auc([1, 2, 3, 4], [0, 0, 1, 1]) == 1.0
    assert cm.auc([1, 1], [0, 1]) == 0.5
    X = [[-2], [-1], [1], [2]] * 5
    y = [0, 0, 1, 1] * 5
    assert cm.fit_logistic(X, y)[1] > 0


def test_cli_fit_writes_report_in_cache(tmp_path, monkeypatch, capsys):
    _world(tmp_path / "cache")
    monkeypatch.setenv("CALIBRATION_CACHE_DIR", str(tmp_path / "cache"))
    assert cm.main(["fit"]) == 0
    assert (tmp_path / "cache" / "fit_report.json").exists()
