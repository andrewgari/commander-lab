import json
import pathlib

from src.server.infra.db.orm_models import Base

M = json.loads((pathlib.Path(__file__).parent.parent / "docs/redis_pg/mapping.json").read_text())
EXPECTED = {"comp:presets", "github_changelog_stale", "github_latest_version_stale",
            "interaction_graph:collection", "interactions:runs", "lab_tags", "lab_tags_source"}


def test_all_observed_patterns_mapped_once():
    pats = [e["pattern"] for e in M["observed"]]
    assert set(pats) == EXPECTED and len(pats) == len(EXPECTED)


def test_mapped_tables_exist():
    tables = set(Base.metadata.tables)
    for e in M["observed"] + M["documented_not_observed"]:
        if e.get("pg_table"):
            assert e["pg_table"] in tables
