"""Category registry (CT): core rows, custom CRUD, aliases, rules, targets, members, suppression, describe, routes."""
import fnmatch
import json
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

import src.server.app.main as app_module
import src.server.domain.tags.deck_tags as tag_store
from src.server.domain.analytics import categories as C
from src.server.domain.analytics import category_describe as D
from src.server.domain.analytics import composition as comp


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, k):
        v = self.store.get(k)
        return v if isinstance(v, (str, type(None))) else None

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.store:
            return None
        self.store[k] = v
        return True

    def incr(self, k):
        self.store[k] = str(int(self.store.get(k, 0)) + 1)
        return int(self.store[k])

    def delete(self, *ks):
        for k in ks:
            self.store.pop(k, None)

    def sadd(self, k, *m):
        self.store.setdefault(k, set()).update(m)

    def smembers(self, k):
        return set(self.store.get(k, set()))

    def srem(self, k, *m):
        self.store.get(k, set()).difference_update(m)

    def scard(self, k):
        return len(self.store.get(k, set()))

    def scan_iter(self, match="*"):
        return [k for k in list(self.store) if fnmatch.fnmatch(k, match)]


RID = "archidekt:1"
COMMANDER = "Big Boss"


def make_deck(n_nonland=89, lands=("Forest",)):
    cards = [{"name": f"Spell {i}", "quantity": 1} for i in range(n_nonland)]
    cards += [{"name": n, "quantity": 1} for n in lands]
    return {"id": "1", "registry_id": RID, "name": "Test Deck", "commanders": [COMMANDER], "cards": cards}


def tagged(**by_card):
    """{card: [{tag, source}]} from name=[tags]."""
    return {n.replace("_", " "): [{"tag": t, "source": "auto", "version": 1} for t in ts] for n, ts in by_card.items()}


TEXTS = {"Forest": {"type_line": "Basic Land — Forest"},
         "Spell 0": {"type_line": "Artifact", "oracle_text": "", "game_changer": True},
         "Spell 1": {"type_line": "Creature — Vampire Knight"},
         "Spell 2": {"type_line": "Creature — Elf"},
         "Spell 3": {"type_line": "Instant", "oracle_text": "Take an extra turn after this one."},
         COMMANDER: {"type_line": "Legendary Creature — Dragon", "cmc": 6}}


class TestAliasAndRules(unittest.TestCase):
    def test_aliases_normalise(self):
        for name, core in (("Draw", "card_advantage"), ("  card   DRAWS ", "card_advantage"), ("Wincons", "finisher"),
                           ("Sweeper", "board_wipe"), ("Rocks", "ramp"), ("Fixing", "mana_fixing")):
            self.assertEqual(C.alias_hit(name), {"core_id": core}, name)
        self.assertEqual(C.alias_hit("Interaction"), {"group": "interaction"})
        self.assertEqual(C.alias_hit("Disruption"), {"group": "interaction"})

    def test_allowed_names(self):
        for name in ("Counters", "Reanimation", "Artifact Removal", "Vampire Lords"):
            self.assertIsNone(C.alias_hit(name), name)

    def test_check_name_errors(self):
        with self.assertRaises(C.CategoryError) as cm:
            C.check_name("Draw")
        self.assertEqual((cm.exception.status, cm.exception.kind, cm.exception.extra["core_id"]), (422, "alias", "card_advantage"))
        with self.assertRaises(C.CategoryError):
            C.check_name("x" * 33)
        with self.assertRaises(C.CategoryError):
            C.check_name("   ")
        self.assertEqual(C.check_name("  Vampire   Lords "), "Vampire Lords")

    def test_rules(self):
        self.assertEqual(C.validate_rule(""), "")
        self.assertEqual(C.validate_rule("autotag:Sacrifice"), "autotag:sacrifice")
        self.assertEqual(C.validate_rule("kindred: Vampire"), "kindred:Vampire")
        for bad in ("autotag:landfall", "speed:sorcery", "nonsense", "type:" + "x" * 31, 5):
            with self.assertRaises(C.CategoryError, msg=str(bad)):
                C.validate_rule(bad)


class TestCoreRows(unittest.TestCase):
    def rows(self, deck, card_tags, deck_tags=(), template=None):
        rows, meta = C.core_rows(deck, TEXTS, card_tags, deck_tags, template or comp.DEFAULT_TEMPLATE)
        return {r["id"]: r for r in rows}, meta

    def test_global_rows_have_template_numbers_and_no_counts(self):
        rows, meta = self.rows(None, {})
        self.assertEqual(len(rows), 11)
        self.assertEqual((rows["ramp"]["target"], rows["ramp"]["floor"]), (10, 10))
        self.assertEqual((rows["removal"]["target"], rows["board_wipe"]["target"]), (12, 6))
        self.assertIsNone(rows["tutor"]["target"])
        self.assertIsNone(rows["ramp"]["count"])
        self.assertFalse(rows["ramp"]["editable_target"])
        self.assertIsNone(meta["interaction_distinct"])

    def test_deck_counts_exclude_commander_and_lands_use_front_face(self):
        ct = tagged(Spell_1=["ramp"], Spell_2=["ramp", "removal"], Spell_3=["counterspell"])
        ct[COMMANDER] = [{"tag": "ramp"}, {"tag": "finisher"}]
        rows, meta = self.rows(make_deck(), ct)
        self.assertEqual(rows["ramp"]["count"], 2)             # commander not counted
        self.assertTrue(rows["ramp"]["commander_does_this"])
        self.assertEqual(rows["lands"]["count"], 1)
        self.assertEqual(rows["counterspell"]["count"], 1)
        self.assertEqual(meta["interaction_distinct"], 2)      # Spell 2 (removal) + Spell 3 (counterspell)
        self.assertTrue(meta["built"])

    def test_floor_modifiers_and_status_scale(self):
        deck = make_deck()
        ct = tagged(Spell_1=["ramp"])
        ct[COMMANDER] = [{"tag": "finisher"}]
        rows, _ = self.rows(deck, ct)
        self.assertEqual(rows["ramp"]["floor"], 12)           # commander MV 6 -> +2
        self.assertEqual(rows["ramp"]["status"], "low")
        self.assertEqual(rows["finisher"]["floor"], 0)        # 2 - 3, clamped
        self.assertEqual(rows["finisher"]["status"], "")      # no target: count only, never judged
        self.assertEqual(rows["tutor"]["status"], "")

    def test_ok_short_over(self):
        ct = {f"Spell {i}": [{"tag": "card_advantage"}] for i in range(12)}
        rows, _ = self.rows(make_deck(), ct)
        self.assertEqual(rows["card_advantage"]["status"], "ok")
        rows, _ = self.rows(make_deck(), ct, template={**comp.DEFAULT_TEMPLATE, "card_advantage": 8})
        self.assertEqual((rows["card_advantage"]["status"], rows["card_advantage"]["overridden"]), ("over", True))
        rows, _ = self.rows(make_deck(), ct, template={**comp.DEFAULT_TEMPLATE, "card_advantage": 20})
        self.assertEqual(rows["card_advantage"]["status"], "short")

    def test_removal_status_covers_counterspells_and_counterspell_is_shared(self):
        ct = {f"Spell {i}": [{"tag": "removal" if i < 6 else "counterspell"}] for i in range(12)}
        rows, _ = self.rows(make_deck(), ct)
        self.assertEqual(rows["removal"]["count"], 6)
        self.assertEqual(rows["removal"]["status"], "ok")     # 12 distinct vs target 12
        self.assertTrue(rows["counterspell"]["shared"])
        self.assertEqual((rows["counterspell"]["status"], rows["counterspell"]["target"], rows["counterspell"]["floor"]), ("", None, None))

    def test_no_statuses_below_90_cards(self):
        rows, meta = self.rows(make_deck(n_nonland=50), tagged(Spell_1=["ramp"]))
        self.assertFalse(meta["built"])
        self.assertTrue(all(r["status"] == "" for r in rows.values()))
        self.assertEqual(rows["ramp"]["count"], 1)

    def test_tokens_deck_drops_wipe_floor(self):
        rows, _ = self.rows(make_deck(), {}, deck_tags=["tokens"])
        self.assertEqual(rows["board_wipe"]["floor"], 0)
        rows, _ = self.rows(make_deck(), {})
        self.assertEqual(rows["board_wipe"]["floor"], 2)

    def test_game_changer_count_on_tutors(self):
        ct = tagged(Spell_0=["tutor"], Spell_1=["tutor"])
        rows, _ = self.rows(make_deck(), ct)
        self.assertEqual((rows["tutor"]["count"], rows["tutor"]["gc"]), (2, 1))


class TestCustomStore(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()

    def test_create_global_and_deck_and_names(self):
        g = C.create(self.r, {"scope": "global", "name": "Artifact Removal", "description": "Cards that kill artifacts."})
        self.assertTrue(g["id"].startswith("g-"))
        d = C.create(self.r, {"scope": "deck", "name": "Vampire Lords", "rule": "kindred:Vampire"}, RID)
        self.assertTrue(d["id"].startswith("d-"))
        self.assertEqual(d["deck_id"], RID)
        with self.assertRaises(C.CategoryError) as cm:
            C.create(self.r, {"scope": "deck", "name": "artifact removal"}, RID)   # collides with a global in this deck
        self.assertEqual(cm.exception.status, 409)
        with self.assertRaises(C.CategoryError) as cm:
            C.create(self.r, {"scope": "global", "name": "Wincon"})
        self.assertEqual((cm.exception.status, cm.exception.kind), (422, "alias"))
        with self.assertRaises(C.CategoryError):
            C.create(self.r, {"scope": "deck", "name": "Needs deck"})
        # another deck may reuse a deck-category name
        C.create(self.r, {"scope": "deck", "name": "Vampire Lords"}, "archidekt:2")
        self.assertEqual(C.get_version(self.r), 3)

    def test_deck_cap(self):
        for i in range(C.DECK_CAP):
            C.create(self.r, {"scope": "deck", "name": f"Role {i}"}, RID)
        with self.assertRaises(C.CategoryError) as cm:
            C.create(self.r, {"scope": "deck", "name": "One too many"}, RID)
        self.assertEqual((cm.exception.status, cm.exception.kind), (409, "cap"))

    def test_update_delete_core_locked(self):
        g = C.create(self.r, {"scope": "global", "name": "Equipment"})
        u = C.update(self.r, g["id"], {"description": "Cards that attach.", "rule": "type:Equipment"})
        self.assertEqual((u["description"], u["rule"], u["name"]), ("Cards that attach.", "type:Equipment", "Equipment"))
        with self.assertRaises(C.CategoryError):
            C.update(self.r, g["id"], {"scope": "deck"})
        with self.assertRaises(C.CategoryError) as cm:
            C.update(self.r, "ramp", {"name": "Zoom"})
        self.assertEqual(cm.exception.status, 403)
        with self.assertRaises(C.CategoryError) as cm:
            C.delete(self.r, "ramp")
        self.assertEqual(cm.exception.status, 403)
        C.delete(self.r, g["id"])
        with self.assertRaises(C.CategoryNotFound):
            C.update(self.r, g["id"], {"name": "x"})

    def test_description_cap_and_group(self):
        with self.assertRaises(C.CategoryError):
            C.create(self.r, {"scope": "global", "name": "Long", "description": "x" * 401})
        with self.assertRaises(C.CategoryError):
            C.create(self.r, {"scope": "global", "name": "Grp", "group": "nope"})

    def test_promote_moves_to_global(self):
        d = C.create(self.r, {"scope": "deck", "name": "Haste Enablers", "pillar_id": "p1"}, RID)
        new, old = C.promote(self.r, d["id"], RID)
        self.assertEqual(old, d["id"])
        self.assertEqual((new["scope"], new["pillar_id"], "deck_id" in new), ("global", "", False))
        self.assertEqual(C.list_deck(self.r, RID), [])
        self.assertEqual([c["name"] for c in C.list_global(self.r)], ["Haste Enablers"])
        d2 = C.create(self.r, {"scope": "deck", "name": "Zed"}, RID)
        C.create(self.r, {"scope": "global", "name": "Zed"})      # a global may be created after the deck one
        with self.assertRaises(C.CategoryError):
            C.promote(self.r, d2["id"], RID)


class TestMembersAndSuggestions(unittest.TestCase):
    def test_member_names_and_rule_suggestions(self):
        deck = make_deck()
        ct = tagged(Spell_1=["cat:g-x"], Spell_2=["sacrifice"], Spell_3=["sacrifice", "cat:g-x"])
        ct[COMMANDER] = [{"tag": "cat:g-x"}]
        self.assertEqual(C.member_names(ct, "g-x", exclude=[COMMANDER]), ["Spell 1", "Spell 3"])
        cat = {"id": "g-x", "rule": "autotag:sacrifice"}
        self.assertEqual(C.suggest_members(deck, TEXTS, ct, cat), ["Spell 2"])              # Spell 3 is already a member
        self.assertEqual(C.suggest_members(deck, TEXTS, ct, {"id": "g-x", "rule": "kindred:Vampire"}), ["Spell 1"] if False else [])
        self.assertEqual(C.suggest_members(deck, TEXTS, {}, {"id": "g-y", "rule": "kindred:Vampire"}), ["Spell 1"])
        self.assertEqual(C.suggest_members(deck, TEXTS, {}, {"id": "g-y", "rule": "type:Instant"}), ["Spell 3"])
        self.assertEqual(C.suggest_members(deck, TEXTS, {}, {"id": "g-y", "rule": ""}), [])

    def test_flags(self):
        self.assertEqual(C.flags_for(TEXTS["Spell 0"]), ["game_changer"])
        self.assertEqual(C.flags_for(TEXTS["Spell 3"]), ["extra_turns"])
        self.assertEqual(C.flags_for(None), [])

    def test_theme_suggestions_skip_used_rules(self):
        used = [{"rule": "autotag:tokens"}]
        out = C.theme_suggestions(["tokens", "sacrifice", "kindred:vampire", "ramp", "sacrifice"], used)
        self.assertEqual([s["rule"] for s in out], ["autotag:sacrifice", "kindred:Vampire"])


class TestSuppression(unittest.TestCase):
    def test_removed_auto_tag_survives_rerun_until_user_adds_it(self):
        r = FakeRedis()
        result = {"cards": [{"name": "Sol Ring", "tags": [{"tag": "ramp"}, {"tag": "artifacts"}]}], "deck_tags": []}
        tag_store.apply_auto_tags(r, RID, result)
        self.assertTrue(tag_store.remove_card_tag_from_deck(r, RID, "Sol Ring", "ramp"))
        tag_store.apply_auto_tags(r, RID, result)
        self.assertEqual([t["tag"] for t in tag_store.get_deck_card_tags(r, RID)["Sol Ring"]], ["artifacts"])
        tag_store.add_card_tag_to_deck(r, RID, "Sol Ring", "ramp", source="user")
        tag_store.apply_auto_tags(r, RID, result)
        tags = {(t["tag"], t["source"]) for t in tag_store.get_deck_card_tags(r, RID)["Sol Ring"]}
        self.assertEqual(tags, {("ramp", "user"), ("artifacts", "auto")})

    def test_removing_user_tag_sets_no_marker(self):
        r = FakeRedis()
        tag_store.add_card_tag_to_deck(r, RID, "Sol Ring", "cat:g-x", source="user")
        tag_store.remove_card_tag_from_deck(r, RID, "Sol Ring", "cat:g-x")
        self.assertEqual(tag_store.get_suppressed(r, RID), {})

    def test_clear_deck_tags_clears_markers(self):
        r = FakeRedis()
        tag_store.apply_auto_tags(r, RID, {"cards": [{"name": "A", "tags": [{"tag": "ramp"}]}], "deck_tags": []})
        tag_store.remove_card_tag_from_deck(r, RID, "A", "ramp")
        tag_store.clear_deck_tags(r, RID)
        self.assertEqual(tag_store.get_suppressed(r, RID), {})


class FakeChain:
    def __init__(self, reply, available=True):
        self.reply, self._av, self.calls = reply, available, []

    def available(self):
        return self._av

    def generate_json(self, system, prompt, schema=None, purpose=None, on_call=None):
        self.calls.append(prompt)
        return self.reply, "fake", "m1"


class TestDescribe(unittest.TestCase):
    GOOD = {"description": "Cards that sacrifice other creatures cheaply. Not: cards that only sacrifice themselves.",
            "rule": "autotag:sacrifice", "duplicate_of": None}

    def test_alias_blocks_before_model(self):
        chain = FakeChain(self.GOOD)
        with self.assertRaises(C.CategoryError) as cm:
            D.describe_category(chain, {"name": "Draw"})
        self.assertEqual((cm.exception.status, cm.exception.extra["core_id"]), (422, "card_advantage"))
        self.assertEqual(chain.calls, [])

    def test_ok_and_prompt_has_context(self):
        chain = FakeChain(self.GOOD)
        deck = make_deck()
        out = D.describe_category(chain, {"name": "Sac outlets"}, deck, TEXTS, ["sacrifice"], [{"name": "Aristocrats", "description": "d"}])
        self.assertEqual((out["rule"], out["duplicate_of"], out["mode"]), ("autotag:sacrifice", None, "draft"))
        self.assertIn("Aristocrats", chain.calls[0])
        self.assertIn("COMMANDER Big Boss", chain.calls[0])
        self.assertIn("ramp:", chain.calls[0])

    def test_improve_mode_and_trim(self):
        reply = {"description": "Cards that " + "do things. " * 60, "rule": None}
        out = D.describe_category(FakeChain(reply), {"name": "X", "description": "old text"})
        self.assertEqual(out["mode"], "improve")
        self.assertLessEqual(len(out["description"]), C.DESC_MAX)

    def test_bad_rule_is_dropped_and_flagged(self):
        out = D.describe_category(FakeChain({**self.GOOD, "rule": "speed:sorcery"}), {"name": "Fast"})
        self.assertIsNone(out["rule"])
        self.assertEqual(out["flagged"][0]["rule"], "speed:sorcery")

    def test_duplicate_of_core(self):
        out = D.describe_category(FakeChain({"description": "Cards that make mana.", "duplicate_of": "ramp"}), {"name": "Mana makers"})
        self.assertEqual(out["duplicate_of"], "ramp")
        out = D.describe_category(FakeChain({"description": "Cards that x.", "duplicate_of": "bogus"}), {"name": "Zed"})
        self.assertIsNone(out["duplicate_of"])

    def test_rejects_card_names_and_power_language(self):
        with self.assertRaises(D.CategoryDescribeUnavailable) as cm:
            D.describe_category(FakeChain({"description": "Cards like Spell 12 that help."}), {"name": "Z"}, make_deck(), TEXTS)
        self.assertEqual(cm.exception.kind, "bad_response")
        with self.assertRaises(D.CategoryDescribeUnavailable):
            D.describe_category(FakeChain({"description": "Cards that are the best staples."}), {"name": "Z"})

    def test_unavailable_and_failed(self):
        with self.assertRaises(D.CategoryDescribeUnavailable) as cm:
            D.describe_category(None, {"name": "Z"})
        self.assertEqual(cm.exception.kind, "no_llm")
        with self.assertRaises(D.CategoryDescribeUnavailable) as cm:
            D.describe_category(FakeChain(None), {"name": "Z"})
        self.assertEqual(cm.exception.kind, "llm_failed")
        with self.assertRaises(D.CategoryDescribeUnavailable) as cm:
            D.describe_category(FakeChain({"description": "  "}), {"name": "Z"})
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_empty_request(self):
        with self.assertRaises(C.CategoryError):
            D.describe_category(FakeChain(self.GOOD), {})


class TestRoutes(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck = make_deck()
        ps = [patch.object(app_module, "r", self.r),
              patch.object(app_module, "_find_deck_any", side_effect=lambda d: self.deck if d in (RID, "1", "Test Deck") else None),
              patch.object(app_module.insights, "card_texts", return_value=TEXTS)]
        for p in ps:
            p.start()
            self.addCleanup(p.stop)
        self.c = TestClient(app_module.app)

    def test_page_renders(self):
        res = self.c.get("/categories")
        self.assertEqual(res.status_code, 200)
        self.assertIn("Categories", res.text)
        self.assertIn('href="/categories"', res.text)   # nav entry

    def test_global_list(self):
        self.c.post("/api/categories", json={"scope": "global", "name": "Equipment", "rule": "type:Equipment"})
        data = self.c.get("/api/categories").json()
        self.assertEqual(len([x for x in data["categories"] if x["scope"] == "core"]), 11)
        eq = next(x for x in data["categories"] if x["name"] == "Equipment")
        self.assertEqual((eq["scope"], eq["decks"]), ("global", 0))
        self.assertNotIn("deck", data)
        self.assertEqual(data["caps"]["deck"], 10)

    def test_deck_list_counts_suggestions_and_cap(self):
        tag_store.apply_auto_tags(self.r, RID, {"cards": [{"name": "Spell 1", "tags": [{"tag": "ramp"}]}],
                                                "deck_tags": [{"tag": "sacrifice"}, {"tag": "tokens"}]})
        res = self.c.post("/api/categories", json={"scope": "deck", "deck_id": RID, "name": "Vampire Lords", "rule": "kindred:Vampire"})
        self.assertEqual(res.status_code, 201, res.text)
        data = self.c.get("/api/categories", params={"deck_id": RID}).json()
        by = {x["name"]: x for x in data["categories"]}
        self.assertEqual(by["Ramp"]["count"], 1)
        self.assertEqual(by["Vampire Lords"]["count"], 0)
        self.assertEqual((data["deck"]["categories_used"], data["deck"]["cap"], data["deck"]["built"]), (1, 10, True))
        self.assertEqual({s["rule"] for s in data["suggestions"]}, {"autotag:sacrifice", "autotag:tokens"})
        self.assertEqual(self.c.get("/api/categories", params={"deck_id": "nope"}).status_code, 404)

    def test_create_errors(self):
        self.assertEqual(self.c.post("/api/categories", json={"scope": "global", "name": "Draw"}).json()["error_kind"], "alias")
        res = self.c.post("/api/categories", json={"scope": "global", "name": "Draw"})
        self.assertEqual((res.status_code, res.json()["core_id"]), (422, "card_advantage"))
        self.c.post("/api/categories", json={"scope": "global", "name": "Dup"})
        self.assertEqual(self.c.post("/api/categories", json={"scope": "global", "name": "dup"}).status_code, 409)
        self.assertEqual(self.c.post("/api/categories", json={"scope": "deck", "name": "No deck"}).status_code, 400)
        self.assertEqual(self.c.post("/api/categories", json={"scope": "global", "name": "R", "rule": "bad"}).status_code, 400)

    def test_update_delete_and_core_403(self):
        cid = self.c.post("/api/categories", json={"scope": "global", "name": "Equipment"}).json()["category"]["id"]
        res = self.c.put(f"/api/categories/{cid}", json={"description": "Cards that attach."})
        self.assertEqual(res.json()["category"]["description"], "Cards that attach.")
        self.assertEqual(self.c.put("/api/categories/ramp", json={"name": "Zoom"}).status_code, 403)
        self.assertEqual(self.c.delete("/api/categories/ramp").status_code, 403)
        self.assertEqual(self.c.put("/api/categories/g-nope", json={"name": "x"}).status_code, 404)
        self.assertEqual(self.c.put("/api/categories/d-nope-1", json={"name": "x"}).status_code, 400)   # needs deck_id
        self.assertEqual(self.c.delete(f"/api/categories/{cid}").status_code, 200)
        self.assertEqual(self.c.delete(f"/api/categories/{cid}").status_code, 404)

    def test_members_add_remove_suggest_and_delete_strips_tags(self):
        tag_store.apply_auto_tags(self.r, RID, {"cards": [{"name": "Spell 2", "tags": [{"tag": "sacrifice"}]}], "deck_tags": []})
        cid = self.c.post("/api/categories", json={"scope": "deck", "deck_id": RID, "name": "Sac Outlets",
                                                   "rule": "autotag:sacrifice"}).json()["category"]["id"]
        m = self.c.get(f"/api/categories/{cid}/members", params={"deck_id": RID}).json()
        self.assertEqual(([x["name"] for x in m["members"]], [x["name"] for x in m["suggested"]]), ([], ["Spell 2"]))
        self.assertEqual(self.c.post(f"/api/categories/{cid}/members", json={"deck_id": RID, "card": "Spell 2"}).status_code, 200)
        self.assertEqual(self.c.post(f"/api/categories/{cid}/members", json={"deck_id": RID, "card": COMMANDER}).status_code, 422)
        self.assertEqual(self.c.post(f"/api/categories/{cid}/members", json={"deck_id": RID, "card": "Not there"}).status_code, 422)
        m = self.c.get(f"/api/categories/{cid}/members", params={"deck_id": RID}).json()
        self.assertEqual(([x["name"] for x in m["members"]], m["suggested"]), (["Spell 2"], []))
        self.assertEqual(self.c.delete(f"/api/categories/{cid}/members", params={"deck_id": RID, "card": "Spell 2"}).json()["removed"], True)
        self.c.post(f"/api/categories/{cid}/members", json={"deck_id": RID, "card": "Spell 2"})
        self.assertEqual(self.c.delete(f"/api/categories/{cid}", params={"deck_id": RID}).status_code, 200)
        tags = tag_store.get_deck_card_tags(self.r, RID)["Spell 2"]
        self.assertEqual([t["tag"] for t in tags], ["sacrifice"])        # the auto tag stays

    def test_core_members_listing(self):
        tag_store.apply_auto_tags(self.r, RID, {"cards": [{"name": "Spell 0", "tags": [{"tag": "tutor"}]}], "deck_tags": []})
        m = self.c.get("/api/categories/tutor/members", params={"deck_id": RID}).json()
        self.assertEqual(m["members"], [{"name": "Spell 0", "flags": ["game_changer"]}])
        lands = self.c.get("/api/categories/lands/members", params={"deck_id": RID}).json()
        self.assertEqual([x["name"] for x in lands["members"]], ["Forest"])
        self.assertEqual(self.c.post("/api/categories/tutor/members", json={"deck_id": RID, "card": "Spell 0"}).status_code, 403)

    def test_promote_moves_members_and_global_delete_strips_everywhere(self):
        cid = self.c.post("/api/categories", json={"scope": "deck", "deck_id": RID, "name": "Haste"}).json()["category"]["id"]
        self.c.post(f"/api/categories/{cid}/members", json={"deck_id": RID, "card": "Spell 1"})
        res = self.c.post(f"/api/categories/{cid}/promote", params={"deck_id": RID})
        self.assertEqual(res.status_code, 200, res.text)
        gid = res.json()["category"]["id"]
        tags = [t["tag"] for t in tag_store.get_deck_card_tags(self.r, RID)["Spell 1"]]
        self.assertEqual(tags, [f"cat:{gid}"])
        self.assertEqual(self.c.get("/api/categories").json()["categories"][-1]["decks"], 1)
        self.c.delete(f"/api/categories/{gid}")
        self.assertNotIn("Spell 1", tag_store.get_deck_card_tags(self.r, RID))

    def test_targets_set_reset_and_validation(self):
        res = self.c.put(f"/api/decks/{RID}/category-targets/ramp", json={"target": 12})
        self.assertEqual((res.status_code, res.json()["target"], res.json()["default"]), (200, 12, 10))
        data = self.c.get("/api/categories", params={"deck_id": RID}).json()
        ramp = next(x for x in data["categories"] if x["id"] == "ramp")
        self.assertEqual((ramp["target"], ramp["overridden"], ramp["editable_target"]), (12, True, True))
        self.assertEqual(self.c.put(f"/api/decks/{RID}/category-targets/removal", json={"target": 9}).json()["target"], 9)
        res = self.c.delete(f"/api/decks/{RID}/category-targets/ramp")
        self.assertEqual(res.json()["target"], 10)
        data = self.c.get("/api/categories", params={"deck_id": RID}).json()
        self.assertEqual(next(x for x in data["categories"] if x["id"] == "removal")["target"], 9)   # other row kept
        for bad in ({"target": -1}, {"target": 100}, {"target": "x"}, {"target": True}, {}):
            self.assertEqual(self.c.put(f"/api/decks/{RID}/category-targets/ramp", json=bad).status_code, 422, bad)
        self.assertEqual(self.c.put(f"/api/decks/{RID}/category-targets/tutor", json={"target": 3}).status_code, 422)
        self.assertEqual(self.c.put(f"/api/decks/{RID}/category-targets/counterspell", json={"target": 3}).status_code, 422)
        self.assertEqual(self.c.put(f"/api/decks/{RID}/category-targets/zzz", json={"target": 3}).status_code, 404)
        self.assertEqual(self.c.put("/api/decks/nope/category-targets/ramp", json={"target": 3}).status_code, 404)

    def test_describe_route(self):
        chain = FakeChain({"description": "Cards that sacrifice creatures.", "rule": "autotag:sacrifice"})
        with patch("src.server.domain.analytics.interactions.llm.default_chain", return_value=chain):
            ok = self.c.post("/api/categories/describe", json={"name": "Sac outlets", "deck_id": RID})
            self.assertEqual(ok.status_code, 200, ok.text)
            self.assertEqual(ok.json()["rule"], "autotag:sacrifice")
            alias = self.c.post("/api/categories/describe", json={"name": "Wincon"})
            self.assertEqual((alias.status_code, alias.json()["error_kind"], alias.json()["core_id"]), (422, "alias", "finisher"))
            self.assertEqual(len(chain.calls), 1)                              # alias never reached the model
            self.assertEqual(self.c.post("/api/categories/describe", json={}).status_code, 422)
        with patch("src.server.domain.analytics.interactions.llm.default_chain", return_value=FakeChain(None, available=False)):
            res = self.c.post("/api/categories/describe", json={"name": "Zed"})
            self.assertEqual((res.status_code, res.json()["error_kind"]), (503, "no_llm"))
        with patch("src.server.domain.analytics.interactions.llm.default_chain", return_value=FakeChain(None)):
            self.assertEqual(self.c.post("/api/categories/describe", json={"name": "Zed"}).status_code, 502)
        self.assertNotIn("categories:global", self.r.store)   # describe never saves


if __name__ == "__main__":
    unittest.main()
