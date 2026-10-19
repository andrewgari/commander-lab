"""Guided Primer Builder: reconciled writeup backend (spirit, table facts, sections, sanitizer) and the
wizard / persistence endpoints. Scripted fake model and an in-memory Redis; no network."""
import json
import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import app as app_module
import registry
from analytics.deck_writeup import WriteupError, generate_description, generate_primer, sanitize_primer_html
from analytics.deck_writeup import prompts, service, store
from analytics.deck_writeup.service import build_user_prompt, validate_description, validate_primer

DECK = {"name": "Goblin Party", "commanders": ["Krenko, Mob Boss"], "color": "R", "source": "archidekt",
        "source_id": 7, "cards": [{"name": "Goblin Chieftain", "types": ["Creature"]}]}
RID = registry.registry_id_of(DECK)
META = [
    {"name": "Krenko, Mob Boss", "type_line": "Legendary Creature — Goblin Warrior", "tags": ["tokens"],
     "oracle_text": "Whenever Krenko attacks, put a +1/+1 counter on it, then create X 1/1 Goblins.", "mana_value": 4.0,
     "is_commander": True},
    {"name": "Goblin Chieftain", "type_line": "Creature — Goblin", "tags": ["tokens", "anthem"], "mana_value": 3.0},
    {"name": "Blasphemous Act", "type_line": "Sorcery", "tags": ["wipe"], "mana_value": 9.0},
    {"name": "Mountain", "type_line": "Basic Land — Mountain", "tags": []},
]
SPIRIT = {"confirmed": True, "thesis": "Goblins go wide.", "vision": "Chaos at the table.", "pillars": [
    {"name": "Go wide", "description": "Make more bodies than they have removal."}],
    "pets": ["Goblin Chieftain"], "novelties": "", "wincons": ["combat"], "genre": {}}
OLD_PRIMER = "## Overview\nGoblins.\n\n## Game Plan\nGo wide.\n\n## Weaknesses\nWipes."


class Fake:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    def __call__(self, system, user):
        self.calls.append((system, user))
        return self.reply


class FakeRedis:
    def __init__(self, data=None):
        self.d = dict(data or {})

    def get(self, k):
        return self.d.get(k)

    def mget(self, ks):
        return [self.d.get(k) for k in ks]

    def set(self, k, v):
        self.d[k] = v

    def delete(self, *ks):
        for k in ks:
            self.d.pop(k, None)

    def exists(self, k):
        return int(k in self.d)


# ---- prompts / service --------------------------------------------------------------------------------

def test_spirit_block_pets_and_never_cut_rule():
    ask = Fake(OLD_PRIMER)
    generate_primer(DECK, META, ask, {"theme": "Goblins", "game_plan": "Go wide"}, spirit=SPIRIT, bracket="3",
                    identity="R")
    system, user = ask.calls[0]
    assert "DECK SPIRIT (confirmed):" in user and "PETS: Goblin Chieftain" in user and "THESIS: Goblins go wide." in user
    assert user.index("DECK SPIRIT") < user.index("OWNER'S NOTES:")
    assert "Never suggest cutting a card listed under PETS" in system
    assert "owner's form answers > confirmed DECK SPIRIT > card data" in system
    assert "BRACKET TARGET: 3" in user and "COLOR IDENTITY: R" in user


def test_unconfirmed_spirit_is_labelled_draft():
    assert "DECK SPIRIT (draft spirit, may be wrong):" in build_user_prompt(DECK, META, {}, spirit=dict(SPIRIT, confirmed=False))
    assert "DECK SPIRIT (draft spirit, may be wrong):" in build_user_prompt(DECK, META, {})


@pytest.mark.parametrize("heads", [
    ["Overview", "Game Plan", "Key Cards", "Combos and Win Lines", "Mulligans and Opening Hands", "Piloting Tips",
     "Weaknesses", "Upgrades and Swaps", "Table Talk"],
    ["Overview", "Game plan", "Key cards", "Mulligan & piloting", "Table talk"],
    ["Mulligan and Piloting", "Table Talk"]])
def test_both_section_sets_validate(heads):
    validate_primer("\n\n".join(f"## {h}\nText." for h in heads))


def test_unknown_section_still_rejected():
    with pytest.raises(WriteupError):
        validate_primer("## Overview\nx\n\n## Table Chat\ny")


def test_table_talk_rule_and_empty_table_facts():
    ask = Fake("## Overview\nx\n\n## Table Talk\nNo stax, MLD, extra turns, or infinites detected.")
    out = generate_primer(DECK, META, ask, {"theme": "a", "game_plan": "b"}, table_facts={"facts": [], "combos": [],
                                                                                         "combos_checked": True})
    system, user = ask.calls[0]
    assert "TABLE FACTS:\n(none)" in user
    assert "No stax, MLD, extra turns, or infinites detected." in system
    assert "Never infer a combo from card names" in system
    assert out["text"].endswith("infinites detected.")


def test_table_facts_and_combos_in_packet():
    facts = {"facts": [{"id": "gc", "title": "1 Game Changer", "detail": "Rhystic Study", "on": True}],
             "combos": ["Thassa's Oracle + Demonic Consultation -> win"], "combos_checked": True}
    user = build_user_prompt(DECK, META, {}, table_facts=facts)
    assert "- 1 Game Changer: Rhystic Study" in user and "COMBOS:\n- Thassa's Oracle + Demonic Consultation" in user
    assert "(none selected by the owner" in build_user_prompt(DECK, META, {}, table_facts=facts, fact_ids=[])


def test_oracle_text_and_mana_value_in_packet():
    user = build_user_prompt(DECK, META, {"key_cards": ["Goblin Chieftain"]}, spirit=SPIRIT)
    assert "ORACLE TEXT:\n- Krenko, Mob Boss [MV 4]: Whenever Krenko attacks" in user
    assert "AVG MANA VALUE (non-land): 5.33" in user
    assert "Describe what a card does only from ORACLE TEXT" in prompts.PRIMER_SYSTEM


def test_voice_presets_and_anti_hype_in_both_systems():
    for kind in ("plain", "playful", "tactical"):
        ask = Fake("A swarm.")
        generate_description(DECK, META, ask, voice=kind)
        assert prompts.VOICE_PRESETS[kind] in ask.calls[0][0]
        assert "No hype words (powerhouse, dominates, unstoppable, cEDH-level)" in ask.calls[0][0]
    ask = Fake(OLD_PRIMER)
    generate_primer(DECK, META, ask, {"theme": "a", "game_plan": "b"}, voice="tactical")
    assert prompts.VOICE_PRESETS["tactical"] in ask.calls[0][0] and "not a sales pitch" in ask.calls[0][0]


def test_description_trimmed_to_280_chars_at_sentence_boundary():
    long = " ".join(["This deck makes goblins and attacks."] * 9)  # ~340 chars, 54 words
    out = validate_description(long)
    assert len(out) <= 280 and out.endswith(".")
    assert len(validate_description("word " * 80)) <= 280          # no sentence end: word boundary
    with pytest.raises(WriteupError) as e:
        validate_description("word " * 200)
    assert e.value.kind == "too_long"


def test_warns_on_cards_not_in_deck():
    reply = ("## Overview\n**Goblin Chieftain** and **Black Lotus** and [[Sol Ring]].\n**Early:** go.\n"
             '<span data-card="Krenko, Mob Boss">k</span>')
    out = generate_primer(DECK, META, Fake(reply), {"theme": "a", "game_plan": "b"})
    assert "Card mentioned in writeup not found in this deck: Black Lotus" in out["warnings"]
    assert "Card mentioned in writeup not found in this deck: Sol Ring" in out["warnings"]
    assert not any("Early" in w or "Chieftain" in w or "Krenko" in w for w in out["warnings"])


# ---- wizard generation -------------------------------------------------------------------------------------

def test_wizard_answers_blank_ok_and_keep_is_preserved():
    reply = "## Overview\nGoblins!\n\n## Game plan\nGo wide.\n\n## Table talk\nNone."
    ask = Fake(reply)
    form, whys, facts, bracket = service.answers_to_form({"pitch": "", "cards": [
        {"name": "Goblin Chieftain", "on": True, "why": "Anthem"}], "facts": ["gc"], "bracket": 3})
    out = generate_primer(DECK, META, ask, form, wizard=True, key_whys=whys, fact_ids=facts,
                          sections=["overview", "plan", "cards", "table"], keep={"cards": "<p>Mine</p>"})
    system, user = ask.calls[0]
    assert "## Key cards" not in system and "## Overview" in system and "## Mulligan" not in system
    assert "- Goblin Chieftain: Anthem" in user and bracket == "3"
    ids = [s["id"] for s in out["sections"]]
    assert ids == ["overview", "plan", "cards", "table"]
    kept = next(s for s in out["sections"] if s["id"] == "cards")
    assert kept["html"] == "<p>Mine</p>" and kept.get("kept")


def test_wizard_ignores_sections_the_model_wrote_that_are_locked_or_off():
    reply = "## Overview\nNew.\n\n## Key cards\nRewritten.\n\n## Mulligan & piloting\nOff."
    out = generate_primer(DECK, META, Fake(reply), {}, wizard=True, sections=["overview", "cards"],
                          keep={"cards": "<p>Mine</p>"})
    assert [(s["id"], s.get("kept", False)) for s in out["sections"]] == [("overview", False), ("cards", True)]
    assert "Rewritten" not in out["text"]


def test_sanitize_strips_scripts_handlers_and_wraps_known_cards():
    dirty = ('<p onclick="x()">Cast Goblin Chieftain <img src=x onerror=alert(1)> now</p>'
             '<script>alert(1)</script><a href="javascript:alert(1)">bad</a><h2>Head</h2>'
             '<span class="evil" style="x">plain</span><iframe src=x>frame</iframe>')
    out = sanitize_primer_html(dirty, ["Goblin Chieftain", "Krenko, Mob Boss"])
    for bad in ("<script", "onerror", "onclick", "javascript:", "<img", "<iframe", "alert(1)", "style=", "<a "):
        assert bad not in out
    assert 'data-card="Goblin Chieftain"' in out and "<h3>Head</h3>" in out and "plain" in out
    # idempotent: re-sanitizing saved HTML does not double-wrap
    assert sanitize_primer_html(out, ["Goblin Chieftain"]).count("pf-cardname") == 1


def test_sanitize_converts_markdown():
    out = sanitize_primer_html("## Game plan\n- **Early:** Krenko, Mob Boss\n- Mid\n\nHello *there*", ["Krenko, Mob Boss"])
    assert out.startswith("<h3>Game plan</h3><ul><li><strong>Early:</strong> <span") and "<em>there</em>" in out


# ---- endpoints --------------------------------------------------------------------------------------------------

class FakeOracle:
    def __init__(self, reply):
        self.reply, self.timeout, self.prompts = reply, None, []

    def ask(self, prompt, *, system=None, **kw):
        self.prompts.append((system, prompt))
        return self.reply(system) if callable(self.reply) else self.reply


@pytest.fixture
def env():
    journal = {"pet_cards": ["Goblin Chieftain"], "target_bracket": 3,
               "deck_vision": {"intent": "tune_up", "description": "Chaos.", "target_turn_win": 8}}
    fake = FakeRedis({f"journal:deck:{RID}": json.dumps(journal), "decks": json.dumps([dict(DECK)])})
    with patch.object(app_module, "r", fake), \
            patch.object(app_module, "_find_deck_any", return_value=dict(DECK)), \
            patch("analytics.deck_writeup.service.card_meta_for_deck", return_value=META), \
            patch.object(app_module, "_oracle_preflight", return_value=None):
        yield fake, TestClient(app_module.app)


def _gen(client, path, body, reply):
    oracle = FakeOracle(reply)
    with patch.object(app_module, "_oracle_client", return_value=oracle):
        return client.post(path, json=body), oracle


def test_identity_bracket_and_spirit_wired_through_handler(env):
    _, client = env
    res, oracle = _gen(client, "/api/decks/d1/primer/generate", {"theme": "a", "game_plan": "b"}, OLD_PRIMER)
    assert res.status_code == 200
    system, user = oracle.prompts[0]
    assert "COLOR IDENTITY: R" in user and "BRACKET TARGET: 3" in user      # identity from deck, bracket from Journal
    assert "PETS: Goblin Chieftain" in user and "DECK SPIRIT (confirmed)" in user
    assert "TABLE FACTS:" in user and "COMBOS:" in user
    res, oracle = _gen(client, "/api/decks/d1/description/generate", {"bracket": 2}, "A swarm of goblins.")
    assert "BRACKET TARGET: 2" in oracle.prompts[0][1]                      # an explicit bracket wins


def test_wizard_generate_endpoint_sections_description_and_staging(env):
    fake, client = env
    reply = "## Overview\nGoblins at **Goblin Chieftain** speed.\n\n## Game plan\nGo wide."
    body = {"answers": {"pitch": "", "early": "Cast **Black Lotus**", "cards": []},
            "options": {"length": "brief", "voice": "playful", "sections": ["overview", "plan"], "description": True},
            "keep": {"mull": "<p onclick=x>Kept</p>"}}
    oracle = FakeOracle(lambda system: "A goblin swarm." if "short deck descriptions" in system else reply)
    with patch.object(app_module, "_oracle_client", return_value=oracle):
        res = client.post("/api/decks/d1/primer/generate", json=body)
    j = res.json()
    assert res.status_code == 200 and j["success"]
    assert [s["id"] for s in j["sections"]] == ["overview", "plan"]
    assert j["sections"][0]["inferred"] == ["pitch"] and 'data-card="Goblin Chieftain"' in j["sections"][0]["html"]
    assert len(j["description"]) <= 280 and len(oracle.prompts) == 2
    assert json.loads(fake.d[f"primer:staged_desc:{RID}"])["description"] == j["description"]
    assert prompts.VOICE_PRESETS["playful"] in oracle.prompts[0][0]
    # a locked section outside the requested set is not smuggled back in
    assert "onclick" not in json.dumps(j)


def test_draft_roundtrip(env):
    fake, client = env
    assert client.get("/api/decks/d1/primer/draft").status_code == 404
    res = client.put("/api/decks/d1/primer/draft", json={"answers": {"pitch": "Hi"}})
    assert res.status_code == 204
    got = client.get("/api/decks/d1/primer/draft").json()
    assert got["answers"] == {"pitch": "Hi"} and got["updated_at"]
    assert client.put("/api/decks/d1/primer/draft", json={"answers": "nope"}).status_code == 422


def test_save_primer_sanitizes_versions_and_undoes(env):
    fake, client = env
    r1 = client.post("/api/decks/d1/primer", json={"html": "<p>One Goblin Chieftain</p><script>x</script>",
                                                   "answers": {"pitch": "p"}}).json()
    assert r1["version"] == 1 and not r1["has_undo"] and "<script" not in r1["html"] and "pf-cardname" in r1["html"]
    r2 = client.post("/api/decks/d1/primer", json={"html": "<p>Two</p>"}).json()
    assert r2["version"] == 2 and r2["has_undo"]
    got = client.get("/api/decks/d1/primer").json()
    assert got["primer"]["html"] == "<p>Two</p>" and got["primer"]["answers"] == {"pitch": "p"} and got["has_undo"]
    undone = client.post("/api/decks/d1/primer/undo").json()
    assert undone["version"] == 1 and "One" in undone["html"]
    assert client.post("/api/decks/d1/primer/undo").status_code == 404
    assert client.post("/api/decks/d1/primer", json={"html": "  "}).status_code == 422


def test_description_put_validates_persists_and_undoes(env):
    fake, client = env
    store.stage_description(fake, RID, "staged")
    ok = client.put("/api/decks/d1/description", json={"description": "A goblin swarm.", "src": "draft"})
    assert ok.status_code == 200 and ok.json()["src"] == "draft"
    deck = json.loads(fake.d["decks"])[0]
    assert deck["description"] == "A goblin swarm." and deck["description_src"] == "draft"
    assert f"primer:staged_desc:{RID}" not in fake.d                         # accepting clears the staged suggestion
    assert client.put("/api/decks/d1/description", json={"description": "x" * 281, "src": "yours"}).status_code == 422
    assert client.put("/api/decks/d1/description", json={"description": "ok", "src": "robot"}).status_code == 422
    assert client.put("/api/decks/d1/description", json={"description": "", "src": "yours"}).status_code == 422
    assert client.post("/api/decks/d1/description/undo").json()["description"] == ""
    assert client.post("/api/decks/d1/description/undo").status_code == 404


def test_staged_description_is_returned_with_the_primer(env):
    fake, client = env
    store.stage_description(fake, RID, "Staged text.")
    assert client.get("/api/decks/d1/primer").json()["staged_description"] == "Staged text."


def test_suggest_endpoint_is_grounded_in_deck_cards(env):
    _, client = env
    res = client.post("/api/decks/d1/primer/suggest", json={"answers": {"cards": [
        {"name": "Goblin Chieftain", "on": True, "why": ""}]}})
    j = res.json()
    assert res.status_code == 200 and set(j["suggestions"]) == {"pitch", "early", "mid", "late", "mull", "mist", "weak"}
    assert "Krenko, Mob Boss" in j["suggestions"]["pitch"][0] + j["suggestions"]["mid"][0]
    assert j["why"]["Goblin Chieftain"] and "Krenko, Mob Boss" in j["why"]
    assert set(client.post("/api/decks/d1/primer/suggest", json={"field": "mull"}).json()["suggestions"]) == {"mull"}


def test_suggest_never_names_cards_outside_the_deck(env):
    _, client = env
    j = client.post("/api/decks/d1/primer/suggest", json={}).json()
    text = json.dumps(j["suggestions"])
    for name in ("Sol Ring", "Teferi", "Edgar"):
        assert name not in text


def test_build_page_renders_with_bootstrap_and_prefilled_answers(env):
    fake, client = env
    store.save_primer(fake, RID, "<p>Old</p>", [], answers={"pitch": "Saved pitch"})
    with patch.object(app_module.registry, "list_decks", return_value=[dict(DECK)]):
        res = client.get("/deck/Goblin%20Party/primer/build")
    assert res.status_code == 200
    assert "Primer builder" in res.text and "/static/js/primer_builder.js" in res.text
    assert "Saved pitch" in res.text                                         # Rebuild with guide: pre-filled
    assert "Krenko, Mob Boss" in res.text and '"has_primer": true' in res.text


def test_deck_page_wires_description_and_primer_panels(env):
    _, client = env
    with patch.object(app_module.registry, "list_decks", return_value=[dict(DECK)]):
        html = client.get("/deck/Goblin%20Party").text
    assert "synergistic interactions" not in html                    # the hard-coded dummy primer is gone
    for needle in ('id="descDraftBtn"', 'id="descEditBtn"', 'id="primerBuildBtn"', "/primer/build", "/description/generate",
                   "savePrimer", 'id="deckDescriptionPanel"'):
        assert needle in html
    assert 'id="deckDescriptionPanel" hidden' not in html and "<section class=\"hud-panel\" id=\"deckDescriptionPanel\" hidden" not in html


def test_journal_page_has_vision_draft(env):
    _, client = env
    with patch.object(app_module.registry, "list_decks", return_value=[dict(DECK)]):
        html = client.get("/deck/Goblin%20Party/journal").text
    assert 'id="visionDraftBtn"' in html and "description/suggest" in html
