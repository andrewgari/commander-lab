import copy
import json
from pathlib import Path

import pytest

jsonschema = pytest.importorskip("jsonschema")

from tests.review_document_refs import check_references, load_schema  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "review_document" / "example.json"


@pytest.fixture()
def doc():
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="module")
def validator():
    schema = load_schema()
    jsonschema.Draft202012Validator.check_schema(schema)
    return jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())


def test_fixture_validates(doc, validator):
    assert list(validator.iter_errors(doc)) == []


def test_fixture_references_resolve(doc):
    assert check_references(doc) == []


def test_fixture_coverage(doc):
    assert len(doc["swaps"]) >= 8
    names = [s[k]["name"] for s in doc["swaps"] for k in ("cut", "add")]
    assert max(map(len, names)) >= 25  # long-name rendering case
    for key in ("strengths", "problems", "scorecard", "cuts_to_consider", "questions_for_owner", "omitted", "forge"):
        assert doc[key], key
    assert all(doc["charts"][k] for k in ("pips_vs_sources", "roles", "synergy_clusters"))


def _broken(doc, mutate):
    d = copy.deepcopy(doc)
    mutate(d)
    return d


SCHEMA_BREAKS = {
    "bad_grade": lambda d: d["verdict"].update(grade="E"),
    "score_zero": lambda d: d["scorecard"][0].update(score=0),
    "confidence_over_1": lambda d: d["verdict"].update(confidence=1.5),
    "bad_severity": lambda d: d["problems"][0].update(severity="severe"),
    "empty_top_changes": lambda d: d["top_changes"].update(swap_ids=[]),
    "four_top_changes": lambda d: d["top_changes"].update(swap_ids=d["top_changes"]["swap_ids"] + ["x"]),
    "missing_charts": lambda d: d.pop("charts"),
    "chart_bucket_count": lambda d: d["charts"]["curve"]["buckets"].pop(),
    "unknown_field": lambda d: d.update(extra=1),
    "forge_unavailable_no_reason": lambda d: d.update(forge={"status": "unavailable"}),
}


@pytest.mark.parametrize("name", SCHEMA_BREAKS)
def test_broken_fixture_fails_schema(doc, validator, name):
    assert list(validator.iter_errors(_broken(doc, SCHEMA_BREAKS[name]))), name


REF_BREAKS = {
    "dangling_top_change": lambda d: d["top_changes"]["swap_ids"].__setitem__(0, "nope"),
    "dangling_scorecard_finding": lambda d: d["scorecard"][0]["finding_ids"].append("nope"),
    "dangling_swap_finding": lambda d: d["swaps"][0].update(resolves_finding_id="nope"),
    "duplicate_swap_id": lambda d: d["swaps"][1].update(id=d["swaps"][0]["id"]),
    "problems_unordered": lambda d: d["problems"].reverse(),
}


@pytest.mark.parametrize("name", REF_BREAKS)
def test_broken_references_detected(doc, validator, name):
    broken = _broken(doc, REF_BREAKS[name])
    # Reference breaks are schema-valid by design; the helper must catch them.
    assert check_references(broken), name
