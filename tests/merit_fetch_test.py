import gzip
import os
import sys
import urllib.error

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.server.infra.merit import fetchers as f  # noqa: E402


def make(tmp_path, responses):
    calls = []

    def opener(req):
        calls.append(req.full_url)
        r = responses.get(req.full_url)
        if r is None:
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
        return r

    return f.Fetcher(tmp_path, opener=opener, sleep=lambda s: None), calls


def test_cache_dir_required(tmp_path):
    with pytest.raises(f.CalibrationCacheError):
        f.cache_dir({})
    assert f.cache_dir({"CALIBRATION_CACHE_DIR": str(tmp_path / "x")}).is_dir()


def test_slugify():
    assert f.slugify("Krenko, Mob Boss") == "krenko-mob-boss"
    assert f.slugify("Fire // Ice") == "fire"


def test_edhrec_commander_writes_cache_only_and_resumes(tmp_path):
    base = f"{f.EDHREC_BASE}/commanders/krenko-mob-boss"
    resp = {base + ".json": b'{"a":1}', base + "/core.json": b'{"b":2}'}
    fx, calls = make(tmp_path, resp)
    out = fx.edhrec_commander("Krenko, Mob Boss")
    assert out["base"].read_bytes() == b'{"a":1}'
    assert out["core"].parent == tmp_path / "edhrec" / "krenko-mob-boss"
    assert out["cedh"] is None  # 404
    n = len(calls)
    fx.edhrec_commander("Krenko, Mob Boss")
    assert len(calls) == n + 4  # only the 404s retried; hits are cached
    assert not list(tmp_path.rglob("*.part"))


def test_spellbook_bulk(tmp_path):
    body = gzip.compress(b"[]")
    fx, _ = make(tmp_path, {f.SPELLBOOK_VARIANTS_URL: body})
    assert fx.spellbook_variants().read_bytes() == body


def test_commandersalt_refused(tmp_path):
    fx, calls = make(tmp_path, {})
    with pytest.raises(f.CalibrationCacheError):
        fx._get("https://api.commandersalt.com/x")
    assert calls == []


def test_retry_on_429(tmp_path):
    n = {"i": 0}

    def opener(req):
        n["i"] += 1
        if n["i"] == 1:
            raise urllib.error.HTTPError(req.full_url, 429, "x", {}, None)
        return b"ok"

    fx = f.Fetcher(tmp_path, opener=opener, sleep=lambda s: None)
    assert fx.edhrec_year().read_bytes() == b"ok"


@pytest.mark.parametrize("url", ["https://API.COMMANDERSALT.COM/x", "https://api.commandersalt.com:443/x", "https://commandersalt.com/x"])
def test_commandersalt_variants_refused(tmp_path, url):
    fx, calls = make(tmp_path, {})
    with pytest.raises(f.CalibrationCacheError):
        fx._get(url)
    assert calls == []


@pytest.mark.parametrize("slug", ["../../escaped", "a/b", "", "A b"])
def test_bad_slug_rejected(tmp_path, slug):
    fx, calls = make(tmp_path, {})
    with pytest.raises(f.CalibrationCacheError):
        fx.edhrec_commander(slug, slug=True)
    assert calls == []
