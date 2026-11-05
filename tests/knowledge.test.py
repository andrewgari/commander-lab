"""Knowledge pack: render + build_messages (pytest)."""
import json
import re
from pathlib import Path

import pytest

from src.server.domain import knowledge as k
from src.server.domain.knowledge import render
from src.server.domain.analytics import brackets as br

ROOT = Path(k.__file__).parent
A1_FIRST = "## Rules for this answer\nYou are Commander Lab's deck reviewer"


def test_version():
    assert k.PACK_VERSION == "kp-1"


def test_static_files_verbatim_headings():
    assert render.read_static("core").startswith(A1_FIRST)
    assert render.read_static("core").count("\n") == 9
    assert render.read_static("salt_and_table").startswith("## Salt and table health")
    assert "none come from Commander Salt." in render.read_static("commandersalt_structure")


def test_render_brackets_matches_code():
    t = render.render_brackets()
    assert br.RULES_AS_OF in t
    for b in br.BRACKETS:
        assert f"### Bracket {b.number}: {b.name}" in t


def test_render_mana_math():
    t = render.render_mana_math()
    assert "Lands: 38" in t and "generic 0, 4 colored pips: 39 sources" in t


def test_game_changers_dynamic():
    assert "unavailable" in render.render_game_changers(None)
    out = render.render_game_changers({"cards": ["Sol Ring"], "source": "scryfall", "fetched_at": "x"})
    assert "- Sol Ring" in out and "count 1" in out


@pytest.mark.parametrize("task", sorted(k.TASKS))
def test_build_messages_shape(task):
    msgs, pid = k.build_messages(task, game_changers={"cards": ["Sol Ring"], "source": "s", "fetched_at": "t"},
                                 facts_json='{"a":1}', user_text="hello")
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[1]["content"] == '{"a":1}\n\nhello'
    assert pid.startswith("kp-1:") and len(pid) == len("kp-1:") + 12
    parts = msgs[0]["content"]
    assert parts[0]["cache_control"] == {"type": "ephemeral"}
    assert "## Rules for this answer" in parts[0]["text"]
    assert len(parts[0]["text"]) <= k.MAX_STATIC_CHARS
    has_br = "### Bracket 1:" in parts[0]["text"]
    assert has_br == (len(parts) == 2)
    if has_br:
        assert "- Sol Ring" in parts[1]["text"] and "cache_control" not in parts[1]


def test_pack_id_stable_and_dynamic_excluded():
    a = k.build_messages("theory", game_changers=None)[1]
    b = k.build_messages("theory", game_changers={"cards": ["X"], "source": "s", "fetched_at": "t"})[1]
    assert a == b
    assert k.build_messages("deck_review")[1] != a
    assert k.pack_hash("theory") == k.pack_hash("theory") and len(k.pack_hash()) == 64


def test_unknown_task():
    with pytest.raises(ValueError):
        k.build_messages("nope")


@pytest.mark.parametrize("f", sorted((ROOT / "tasks").glob("*.md")))
def test_task_texts_clean(f):
    t = f.read_text(encoding="utf-8")
    assert not t.startswith("---")
    for bad in ("skill_view", "web_search", "Hermes", "related_skills", "Oracle", "browse", "http"):
        assert bad not in t, (f.name, bad)
    # output-contract field names oracle_summary / oracle_score are kept as-is
    assert not re.search(r"(?<![a-z_])oracle(?![_a-z])", t, re.I)


def test_schemas_parse_and_no_provenance():
    files = sorted((ROOT / "schemas").glob("*.json"))
    assert len(files) == 4
    for f in files:
        s = f.read_text(encoding="utf-8")
        json.loads(s)
        assert "hermes" not in s.lower() and "Oracle" not in s


def test_no_external_profile_reads():
    for f in ROOT.rglob("*.py"):
        assert ".hermes" not in f.read_text(encoding="utf-8")
