"""Deck description / primer generation with a scripted fake model; no network."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.deck_writeup import (FormError, WriteupError, clean_form, generate_description, generate_primer,
                                    missing_required, summarize_cards)
from analytics.deck_writeup.service import build_user_prompt, unknown_mentions, validate_primer

DECK = {"name": "Goblin Party", "commanders": ["Krenko, Mob Boss"]}
META = [
    {"name": "Krenko, Mob Boss", "type_line": "Legendary Creature — Goblin Warrior", "tags": ["tokens"]},
    {"name": "Goblin Chieftain", "type_line": "Creature — Goblin", "tags": ["tokens", "anthem"]},
    {"name": "Blasphemous Act", "type_line": "Sorcery", "tags": ["wipe"]},
    {"name": "Mountain", "type_line": "Basic Land — Mountain", "tags": []},
]
GOOD_PRIMER = ("## Overview\n"
               "Goblin Party is a mono-red goblin tribal deck led by Krenko, Mob Boss. It floods the board with cheap "
               "goblins and token makers, then turns the whole horde sideways. Goblin Chieftain pumps the team, and "
               "Blasphemous Act resets the table when things get out of hand.\n\n"
               "## Game Plan\n"
               "Go wide early with one- and two-mana goblins, land Krenko, Mob Boss on turn four, and attack to double "
               "the board. Goblin Chieftain gives haste and an anthem, so the same turn Krenko lands the deck can swing "
               "for real damage. In the late game, keep Krenko protected and activate him again for a lethal swarm.\n\n"
               "## Weaknesses\n"
               "Board wipes are the big problem; Blasphemous Act is symmetrical, so hold back a haste enabler before "
               "casting it. Repeated spot removal on Krenko, Mob Boss slows the deck to a crawl.")


class Fake:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return self.reply


def test_clean_form_trims_and_drops_unknown():
    f = clean_form({"theme": "  Goblins   go wide ", "bogus": "x", "key_cards": "A, B\nC", "tone": "  "})
    assert f == {"theme": "Goblins go wide", "key_cards": ["A", "B", "C"]}


def test_clean_form_type_errors():
    with pytest.raises(FormError):
        clean_form({"theme": 5})
    with pytest.raises(FormError):
        clean_form("nope")


def test_missing_required():
    assert missing_required({"theme": "x"}) == ["game_plan"]


def test_summarize_excludes_lands_from_list_but_counts_them():
    s = summarize_cards(META)
    assert "Mountain" not in s["cards"] and "1 Land" in s["types"] and s["count"] == 4
    assert "tokens (2)" in s["tags"]


def test_description_happy_path_and_prompt_contents():
    ask = Fake("A goblin swarm deck led by Krenko, Mob Boss.")
    out = generate_description(DECK, META, ask, {"tone": "funny"}, bracket="3")
    assert out["text"].startswith("A goblin swarm") and out["warnings"] == []
    system, user = ask.calls[0]
    assert "Krenko, Mob Boss" in user and "Goblin Chieftain" in user and "BRACKET TARGET: 3" in user and "funny" in user
    assert "Never invent cards" in system


def test_description_rejects_empty_headings_and_runaway():
    for bad in ("", "  ", "# Title\ntext", "word " * 200):
        with pytest.raises(WriteupError):
            generate_description(DECK, META, Fake(bad))


def test_description_strips_fences_and_label():
    out = generate_description(DECK, META, Fake("```\nDescription: Swarm.\n```"))
    assert out["text"] == "Swarm."


def test_description_no_cards():
    with pytest.raises(WriteupError) as e:
        generate_description({"name": "x"}, [], Fake("hi"))
    assert e.value.kind == "no_cards"


def test_primer_requires_fields_before_calling_model():
    ask = Fake(GOOD_PRIMER)
    with pytest.raises(WriteupError) as e:
        generate_primer(DECK, META, ask, {"theme": "Goblins"})
    assert e.value.kind == "missing_fields" and ask.calls == []


def test_primer_happy_path_warns_on_unknown_key_card():
    form = {"theme": "Goblins", "game_plan": "Go wide", "key_cards": ["Goblin Chieftain", "Black Lotus"]}
    out = generate_primer(DECK, META, Fake(GOOD_PRIMER), form)
    assert out["text"].startswith("## Overview")
    assert out["warnings"] == ["Key card not found in this deck: Black Lotus"]


@pytest.mark.parametrize("bad", ["", "no sections here", "## Overview\nx\n\n## Secret Sauce\ny"])
def test_primer_rejects_bad_replies(bad):
    with pytest.raises(WriteupError):
        generate_primer(DECK, META, Fake(bad), {"theme": "a", "game_plan": "b"})


def test_validate_primer_min_chars_floor_is_opt_in():
    with pytest.raises(WriteupError) as e:
        validate_primer("## Overview\nShort.", min_chars=500)
    assert e.value.kind == "too_short"
    assert validate_primer("## Overview\nShort.") == "## Overview\nShort."  # no floor by default
    assert validate_primer(GOOD_PRIMER, min_chars=500) == GOOD_PRIMER


def test_primer_prompt_states_500_char_floor():
    ask = Fake(GOOD_PRIMER)
    generate_primer(DECK, META, ask, {"theme": "a", "game_plan": "b"})
    assert "at least 500 characters" in ask.calls[0][0]


def test_primer_too_short_retries_with_nudge_then_raises():
    ask = Fake("## Overview\nGoblins.")
    with pytest.raises(WriteupError) as e:
        generate_primer(DECK, META, ask, {"theme": "a", "game_plan": "b"})
    assert e.value.kind == "too_short" and len(ask.calls) == 2  # one retry, no more
    assert "at least 500 characters" in ask.calls[1][1] and "padding" in ask.calls[1][1]


def test_primer_retry_recovers_when_second_draft_meets_floor():
    class SeqFake:
        def __init__(self, replies):
            self.replies, self.calls = list(replies), []

        def __call__(self, system, user):
            self.calls.append((system, user))
            return self.replies.pop(0)

    ask = SeqFake(["## Overview\nGoblins.", GOOD_PRIMER])
    out = generate_primer(DECK, META, ask, {"theme": "a", "game_plan": "b"})
    assert out["text"] == GOOD_PRIMER and len(ask.calls) == 2


def test_card_list_bounded():
    big = [{"name": f"Card {i}", "type_line": "Creature"} for i in range(500)]
    assert build_user_prompt(DECK, big, {}).count("\n- Card") <= 100


# ---- endpoints ------------------------------------------------------------------------------------
from unittest.mock import patch

from fastapi.testclient import TestClient

import app as app_module


class FakeOracle:
    def __init__(self, reply):
        self.reply, self.timeout, self.prompts = reply, None, []

    def ask(self, prompt, *, system=None, **kw):
        self.prompts.append((system, prompt))
        return self.reply


def _post(path, body, reply="A goblin swarm led by Krenko.", preflight=None):
    client = FakeOracle(reply)
    with patch.object(app_module, "_find_deck_any", return_value=dict(DECK, cards=[{"name": "Goblin Chieftain", "types": ["Creature"]}])), \
            patch("analytics.deck_writeup.service.card_meta_for_deck", return_value=META), \
            patch.object(app_module, "_oracle_preflight", return_value=preflight), \
            patch.object(app_module, "_oracle_client", return_value=client):
        return TestClient(app_module.app).post(path, json=body), client


def test_form_endpoint_lists_fields():
    res = TestClient(app_module.app).get("/api/deck-writeup/primer-form")
    assert res.status_code == 200 and res.json()["fields"][0]["key"] == "theme"


def test_description_endpoint():
    res, client = _post("/api/decks/d1/description/generate", {})
    assert res.status_code == 200 and res.json()["description"].startswith("A goblin")
    assert "Goblin Chieftain" in client.prompts[0][1]


def test_primer_endpoint_ok_and_missing_fields():
    res, _ = _post("/api/decks/d1/primer/generate", {"theme": "Goblins", "game_plan": "Go wide"}, reply=GOOD_PRIMER)
    assert res.status_code == 200 and res.json()["primer"].startswith("## Overview")
    res, client = _post("/api/decks/d1/primer/generate", {"theme": "Goblins"}, reply=GOOD_PRIMER)
    assert res.status_code == 422 and client.prompts == []


def test_primer_bad_model_reply_is_422_and_offline_is_503():
    res, _ = _post("/api/decks/d1/primer/generate", {"theme": "a", "game_plan": "b"}, reply="nothing useful")
    assert (res.status_code, res.json()["error_kind"]) == (422, "format")
    res, _ = _post("/api/decks/d1/description/generate", {}, preflight=app_module._oracle_offline("down"))
    assert res.status_code == 503


def test_unknown_mentions_strips_trailing_sentence_punctuation():
    known = {"kaalia of the vast"}
    text = "**Kaalia of the Vast.** swings in. **Protection** keeps her alive, **Shivan Dragon!**"
    assert unknown_mentions(text, known) == ["Protection", "Shivan Dragon"]
