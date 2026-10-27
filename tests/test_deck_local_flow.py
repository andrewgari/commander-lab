"""STA-11 work package B: thin end-to-end deck-local workflow in an isolated
test database.

Open a deck -> edit only that deck's vision/goals/non-negotiables -> save and
reload -> stage a list change (canonical untouched) -> view copy demand vs
known inventory (printing explicitly unknown) -> export locally -> prove one
bound physical instance can never be shared by two decks -> prove a source
refresh erases neither local edits nor stated intent.

Covers the STA-9 acceptance checks relevant to this slice: AT-04 (save/
reload/export), AT-05 (staging safety), AT-06 (personal intent), AT-07
(owned/missing math), SRC-05 (refresh = diff, never overwrite), SRC-06
(unknown stays unknown), SRC-07 (deck-local intent stays deck-local).

Written as unittest.TestCase so ``python -m unittest discover`` collects it.
Runs on in-memory SQLite; the Archidekt fetcher is stubbed (no network).
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.server.domain.decks import deck_intent as di
from src.server.domain.decks import decklist_export
from src.server.domain.decks import reconciliation_preview as rp
from src.server.domain.draft import draft_mode
from src.server.domain.intake import decklist_parser
from src.server.domain.inventory import copy_demand as cd
from src.server.infra.db import repositories as repo
from src.server.infra.db.orm_models import (
    Base,
    CardInstanceRow,
    DraftCardRow,
    DraftInstanceMoveRow,
    DraftRow,
)
from src.server.infra.services import import_service

FIXTURES = Path(__file__).parent / "fixtures" / "archidekt_reconciliation"


def make_factory():
    eng = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    return sessionmaker(bind=eng, expire_on_commit=False)


def _card(oracle_id, name, cmc=3.0, colors=None, color_identity=None):
    return {
        "oracle_id": oracle_id,
        "name": name,
        "type_line": "Creature" if cmc else "Land",
        "cmc": cmc,
        "colors": colors or ["Green"],
        "color_identity": color_identity or ["Green"],
    }


# Deck A: commander + 10 Forest + 1 Sol Ring + 1 maybeboard card.
DECK_A = {
    "id": "archidekt:5054882",
    "name": "The Return of the King",
    "archidekt_id": "5054882",
    "status": "have",
    "source": "archidekt",
    "commanders": ["Aragorn, King Elessar"],
    "color_identity": ["G", "R", "W"],
    "fingerprint": "fp-a",
}
DECK_A_CARDS = [
    {"oracle_id": "o-cmd", "qty": 1, "board": "commander", "category": None},
    {"oracle_id": "o-forest", "qty": 10, "board": "main", "category": "Land"},
    {"oracle_id": "o-ring", "qty": 1, "board": "main", "category": "Ramp"},
    {"oracle_id": "o-maybe", "qty": 1, "board": "maybe", "category": "Maybeboard"},
]

# Deck B shares Sol Ring with deck A (the cross-deck duplicate case, SRC-06).
DECK_B = {
    "id": "archidekt:417778",
    "name": "Beta Deck",
    "archidekt_id": "417778",
    "status": "have",
    "source": "archidekt",
    "commanders": ["Beta Commander"],
    "color_identity": ["G"],
    "fingerprint": "fp-b",
}
DECK_B_CARDS = [
    {"oracle_id": "o-beta-cmd", "qty": 1, "board": "commander", "category": None},
    {"oracle_id": "o-ring", "qty": 1, "board": "main", "category": "Ramp"},
]

CARDS = [
    _card("o-cmd", "Aragorn, King Elessar", 7.0, ["Red", "Green", "White"], ["R", "G", "W"]),
    _card("o-forest", "Forest", 0.0, [], ["G"]),
    _card("o-ring", "Sol Ring", 1.0, [], []),
    _card("o-maybe", "Maybe Card", 2.0),
    _card("o-beta-cmd", "Beta Commander", 4.0),
]


def seed(factory):
    with factory() as s:
        for c in CARDS:
            repo.upsert_card(s, c)
        repo.upsert_deck(s, DECK_A)
        repo.replace_deck_cards(s, DECK_A["id"], DECK_A_CARDS)
        repo.upsert_deck(s, DECK_B)
        repo.replace_deck_cards(s, DECK_B["id"], DECK_B_CARDS)
        s.commit()


def _instance(iid, oracle_id, status, deck_id=None):
    return {
        "id": iid,
        "oracle_id": oracle_id,
        "status": status,
        "deck_id": deck_id,
        "finish": "nonfoil",
        "condition": "NM",
        "set_code": "",
        "collector_number": "",
    }


# A plausible owner statement for deck A: vision + goals + non-negotiables.
INTENT_A = {
    "deck_vision": {
        "intent": "tune_up",
        "description": "Big splashy legends; win with commander damage.",
        "target_turn_win": 9,
    },
    "preferred_win_conditions": {"primary": "commander_damage"},
    "pet_cards": ["Sol Ring"],
    "freeform_notes": "Non-negotiable: keep every legends-matter card.",
}


class DeckLocalWorkflowTests(unittest.TestCase):
    """The thin end-to-end slice, in delivery order."""

    def setUp(self):
        self.factory = make_factory()
        seed(self.factory)

    # -- open + edit intent + save + reload (AT-04/AT-06) --------------------

    def test_intent_edit_save_reload(self):
        with self.factory() as s:
            deck = repo.get_deck(s, DECK_A["id"])  # open the deck
            self.assertEqual(deck["name"], DECK_A["name"])
            payload = di.validate_stated(INTENT_A)
            repo.upsert_deck_intent(s, deck["id"], payload)
            s.commit()
        # Reload in a brand-new session (application-restart equivalent).
        with self.factory() as s:
            row = repo.get_deck_intent(s, DECK_A["id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["provenance"], "stated")
        self.assertEqual(
            row["payload"]["deck_vision"]["description"],
            INTENT_A["deck_vision"]["description"],
        )
        self.assertEqual(row["payload"]["pet_cards"], ["Sol Ring"])
        self.assertEqual(
            row["payload"]["freeform_notes"], INTENT_A["freeform_notes"]
        )

    def test_intent_is_stated_only_no_default_backfill(self):
        with self.factory() as s:
            payload = di.validate_stated(INTENT_A)
            repo.upsert_deck_intent(s, DECK_A["id"], payload)
            s.commit()
            row = repo.get_deck_intent(s, DECK_A["id"])
        # Sections the owner never mentioned stay absent, not defaulted.
        self.assertNotIn("budget_constraints", row["payload"])
        self.assertNotIn("target_power_level", row["payload"])
        self.assertNotIn("archetype_preferences", row["payload"])

    def test_unstated_deck_stays_unstated(self):
        with self.factory() as s:
            self.assertIsNone(repo.get_deck_intent(s, DECK_B["id"]))
        self.assertTrue(di.is_unstated(None))
        self.assertTrue(di.is_unstated({}))
        self.assertFalse(di.is_unstated(INTENT_A))

    def test_two_decks_intents_stay_different(self):
        """SRC-07: stated goals in two decks stay different; no inheritance."""
        intent_b = {
            "deck_vision": {"intent": "overhaul", "description": "Combo off."},
            "preferred_win_conditions": {"primary": "combo"},
        }
        with self.factory() as s:
            repo.upsert_deck_intent(s, DECK_A["id"], di.validate_stated(INTENT_A))
            repo.upsert_deck_intent(s, DECK_B["id"], di.validate_stated(intent_b))
            s.commit()
        with self.factory() as s:
            a = repo.get_deck_intent(s, DECK_A["id"])["payload"]
            b = repo.get_deck_intent(s, DECK_B["id"])["payload"]
        self.assertEqual(a["preferred_win_conditions"]["primary"], "commander_damage")
        self.assertEqual(b["preferred_win_conditions"]["primary"], "combo")
        self.assertNotEqual(a["deck_vision"], b["deck_vision"])
        # B never inherited A's pet cards or notes.
        self.assertNotIn("pet_cards", b)
        self.assertNotIn("freeform_notes", b)

    def test_intent_validation_rejects_garbage(self):
        with self.assertRaises(di.DeckIntentError):
            di.validate_stated({"target_power_level": {"scale": 99}})
        with self.assertRaises(di.DeckIntentError):
            di.validate_stated({"deck_vision": {"intent": "bogus"}})
        with self.assertRaises(di.DeckIntentError):
            di.validate_stated(["not-a-dict"])

    def test_intent_merge_sections_and_unknown_keys_kept(self):
        merged = di.merge_sections(
            INTENT_A, {"budget_constraints": {"total_budget": 100}}
        )
        self.assertEqual(merged["budget_constraints"]["total_budget"], 100)
        self.assertEqual(merged["pet_cards"], ["Sol Ring"])  # untouched
        # None un-states a section.
        merged = di.merge_sections(merged, {"budget_constraints": None})
        self.assertNotIn("budget_constraints", merged)
        # Unknown keys are preserved in metadata, never dropped.
        merged = di.merge_sections(INTENT_A, {"custom_field": "x"})
        self.assertEqual(merged["metadata"]["custom_field"], "x")

    # -- stage a list change: canonical untouched (AT-05) ---------------------

    def _open_draft(self, s, draft_id="draft-test01"):
        return repo.create_draft(s, {
            "id": draft_id,
            "kind": draft_mode.KIND_DISMANTLE,
            "name": "Rework Alpha",
            "source_decks": [{"id": DECK_A["id"], "name": DECK_A["name"]}],
            "target_name": "Alpha 2.0",
        })

    def _canonical_snapshot(self, s):
        return {
            "deck_a_cards": repo.get_deck_cards(s, DECK_A["id"]),
            "deck_b_cards": repo.get_deck_cards(s, DECK_B["id"]),
            "instances": repo.list_instances(s),
            "decks": repo.list_decks(s),
        }

    def test_staged_list_change_leaves_canonical_unchanged(self):
        with self.factory() as s:
            before = self._canonical_snapshot(s)
            draft = self._open_draft(s)
            repo.upsert_draft_card(s, draft["id"], {
                "oracle_id": "o-cmd", "qty": 1, "board": "commander",
            })
            repo.upsert_draft_card(s, draft["id"], {
                "oracle_id": "o-ring", "qty": 1, "board": "main",
            })
            s.commit()
        with self.factory() as s:
            after = self._canonical_snapshot(s)
        self.assertEqual(before, after)  # nothing canonical moved
        # Abandon: zero staging residue, canonical still untouched.
        with self.factory() as s:
            repo.abandon_draft(s, "draft-test01")
            s.commit()
        with self.factory() as s:
            self.assertEqual(self._canonical_snapshot(s), before)
            self.assertIsNone(repo.get_draft(s, "draft-test01"))
            self.assertEqual(
                s.execute(select(DraftRow).where(DraftRow.id == "draft-test01")).all(), []
            )
            self.assertEqual(
                s.execute(select(DraftCardRow).where(
                    DraftCardRow.draft_id == "draft-test01")).all(), []
            )
            self.assertEqual(
                s.execute(select(DraftInstanceMoveRow).where(
                    DraftInstanceMoveRow.draft_id == "draft-test01")).all(), []
            )

    def test_rejected_commit_changes_nothing(self):
        with self.factory() as s:
            before = self._canonical_snapshot(s)
            draft = self._open_draft(s)  # no staged cards -> commit must fail
            with self.assertRaises(draft_mode.DraftError):
                repo.commit_draft(s, draft["id"])
            s.rollback()
        with self.factory() as s:
            self.assertEqual(self._canonical_snapshot(s), before)

    # -- copy demand vs known inventory (AT-07, SRC-06) ------------------------

    def _seed_instances(self, s):
        # 1 Sol Ring bound to deck A; 1 Sol Ring free; 1 Forest in the mail.
        repo.insert_instance(s, _instance("inst-ring-1", "o-ring", "in_deck", DECK_A["id"]))
        repo.insert_instance(s, _instance("inst-ring-2", "o-ring", "in_collection"))
        repo.insert_instance(s, _instance("inst-forest-1", "o-forest", "in_mail"))

    def test_copy_demand_matches_hand_calc(self):
        """AT-07 hand calculation for deck A (commander+main scope):
        Aragorn need 1, no copies -> have 0, pending 0, buy 1.
        Forest  need 10, 1 in mail -> have 0, pending 1, buy 9.
        Sol Ring need 1, 1 bound + 1 free -> have 1, pending 0, buy 0."""
        with self.factory() as s:
            self._seed_instances(s)
            s.commit()
        with self.factory() as s:
            cards = repo.get_deck_cards(s, DECK_A["id"])
            instances = repo.list_instances(s)
            report = cd.copy_demand(cards, instances, DECK_A["id"])
        lines = {l["oracle_id"]: l for l in report["lines"]}
        self.assertNotIn("o-maybe", lines)  # maybeboard out of scope
        self.assertEqual(
            (lines["o-cmd"]["need"], lines["o-cmd"]["have"], lines["o-cmd"]["buy"]),
            (1, 0, 1),
        )
        self.assertEqual(
            (lines["o-forest"]["need"], lines["o-forest"]["pending"], lines["o-forest"]["buy"]),
            (10, 1, 9),
        )
        self.assertEqual(
            (lines["o-ring"]["need"], lines["o-ring"]["have"], lines["o-ring"]["buy"]),
            (1, 1, 0),
        )
        self.assertEqual(
            report["totals"],
            {"need": 12, "have": 1, "pending": 1, "buy": 10,
             "complete": False, "have_pct": 8},
        )

    def test_copy_demand_printing_explicitly_unknown(self):
        """SRC-06: no invented printing/finish/quantity."""
        with self.factory() as s:
            self._seed_instances(s)
            s.commit()
        with self.factory() as s:
            cards = repo.get_deck_cards(s, DECK_A["id"])
            instances = repo.list_instances(s)
            report = cd.copy_demand(cards, instances, DECK_A["id"])
        for line in report["lines"]:
            self.assertIsNone(line["printing"])
            self.assertEqual(line["printing_status"], "unknown")
            self.assertTrue(line["printing_reason"])
        self.assertEqual(report["unknowns"]["printing"]["status"], "unavailable")

    # -- one bound instance can never be shared by two decks (SRC-06) ----------

    def test_bound_instance_cannot_be_shared(self):
        with self.factory() as s:
            self._seed_instances(s)
            s.commit()
        with self.factory() as s:
            draft = self._open_draft(s)
            repo.upsert_draft_card(s, draft["id"], {
                "oracle_id": "o-cmd", "qty": 1, "board": "commander",
            })
            # Donor pull: the Sol Ring bound to deck A is staged for the
            # draft's result deck. Canonical binding unchanged before commit.
            repo.stage_move(s, draft["id"], "inst-ring-1", draft_mode.ACTION_ASSIGN)
            s.commit()
            inst = s.get(CardInstanceRow, "inst-ring-1")
            self.assertEqual((inst.status, inst.deck_id), ("in_deck", DECK_A["id"]))
            result = repo.commit_draft(s, draft["id"])
            s.commit()
        with self.factory() as s:
            inst = s.get(CardInstanceRow, "inst-ring-1")
            # Exactly one row, exactly one location: the new deck. Deck A no
            # longer holds it — the copy was never in two decks at once.
            self.assertEqual(inst.status, "in_deck")
            self.assertEqual(inst.deck_id, result["deck_id"])
            rows = s.execute(
                select(CardInstanceRow).where(CardInstanceRow.id == "inst-ring-1")
            ).all()
            self.assertEqual(len(rows), 1)
            bound_to_a = [
                i for i in repo.list_instances(s)
                if i["status"] == "in_deck" and i["deck_id"] == DECK_A["id"]
            ]
            self.assertEqual(bound_to_a, [])
            # Provenance: the move is an append-only event, from A to result.
            events = repo.event_counts_by_instance(s, ["inst-ring-1"])
            self.assertEqual(events.get("inst-ring-1"), 1)

    def test_illegal_move_rejected_at_staging(self):
        with self.factory() as s:
            # A wishlist placeholder (not_owned) can never be assigned.
            repo.insert_instance(s, _instance("inst-wish", "o-ring", "not_owned"))
            draft = self._open_draft(s)
            with self.assertRaises(draft_mode.DraftError):
                repo.stage_move(s, draft["id"], "inst-wish", draft_mode.ACTION_ASSIGN)
            with self.assertRaises(draft_mode.DraftError):
                repo.stage_move(s, draft["id"], "inst-missing", draft_mode.ACTION_ASSIGN)

    # -- source refresh: diff, never overwrite (SRC-05) -------------------------

    _REFRESH_PAYLOAD = {
        "id": 5054882,
        "name": "The Return of the King",
        "parentFolderId": 588380,
        "cards": [
            {"quantity": 1, "categories": ["Commander"], "card": {
                "uid": "sf-cmd", "collectorNumber": "1", "rarity": "mythic",
                "edition": {"editioncode": "tst"}, "prices": {"usd": 5.0},
                "oracleCard": {"name": "Aragorn, King Elessar", "uid": "o-cmd",
                               "types": ["Legendary", "Creature"], "superTypes": [],
                               "subTypes": [], "cmc": 7.0, "manaCost": "",
                               "colors": ["Red", "Green", "White"],
                               "colorIdentity": ["Red", "Green", "White"],
                               "text": "", "keywords": []}}},
            {"quantity": 20, "categories": ["Land"], "card": {
                "uid": "sf-forest", "collectorNumber": "1", "rarity": "common",
                "edition": {"editioncode": "tst"}, "prices": {"usd": 0.1},
                "oracleCard": {"name": "Forest", "uid": "o-forest",
                               "types": ["Land"], "superTypes": [], "subTypes": [],
                               "cmc": 0.0, "manaCost": "", "colors": [],
                               "colorIdentity": ["Green"], "text": "",
                               "keywords": []}}},
        ],
    }

    def test_source_refresh_preserves_local_edits_and_intent(self):
        with self.factory() as s:
            self._seed_instances(s)
            repo.upsert_deck_intent(s, DECK_A["id"], di.validate_stated(INTENT_A))
            # A local edit the source knows nothing about: Forest 10 -> 12.
            edited = [dict(c) for c in DECK_A_CARDS]
            edited[1]["qty"] = 12
            repo.replace_deck_cards(s, DECK_A["id"], edited)
            s.commit()
            before = {
                "cards": repo.get_deck_cards(s, DECK_A["id"]),
                "intent": repo.get_deck_intent(s, DECK_A["id"]),
                "instances": repo.list_instances(s),
            }
        # The refresh preview (STA-10 A) proposes a diff; it writes nothing.
        preview = import_service.preview_archidekt_deck(
            "5054882",
            session_factory=self.factory,
            fetcher=lambda _ident: self._REFRESH_PAYLOAD,
        )
        with self.factory() as s:
            after = {
                "cards": repo.get_deck_cards(s, DECK_A["id"]),
                "intent": repo.get_deck_intent(s, DECK_A["id"]),
                "instances": repo.list_instances(s),
            }
        self.assertEqual(before, after)  # local edits, intent, inventory intact
        # And the proposal is a real diff against work package A's exact
        # preview shape — never an overwrite: source wants 20 Forests where
        # the local list has 12.
        self.assertEqual(preview["writes"], "none")
        self.assertTrue(preview["identity"]["matched_locally"])
        self.assertGreater(preview["differences"]["card_change_count"], 0)
        changes = {c["oracle_id"]: c for c in preview["differences"]["cards"]}
        self.assertEqual(changes["o-forest"]["status"], "quantity_change")
        self.assertEqual(
            (changes["o-forest"]["source_qty"], changes["o-forest"]["local_qty"]),
            (20, 12),
        )
        for c in preview["differences"]["cards"]:
            self.assertEqual(c["action"], "proposed_only")
        self.assertIn("deck_intent", preview["protected"])

    def test_refresh_with_real_snapshot_fixture_proposes_only(self):
        """SRC-05/SRC-06 against work package A's real STA-9 snapshot fixture:
        the preview proposes a diff and writes nothing; the local printing
        side of the comparison stays explicitly unknown."""
        payload = json.loads((FIXTURES / "aragorn_5054882.json").read_text())
        with self.factory() as s:
            repo.upsert_deck_intent(s, DECK_A["id"], di.validate_stated(INTENT_A))
            s.commit()
            local_deck = repo.get_deck(s, DECK_A["id"])
            local_cards = repo.get_deck_cards(s, DECK_A["id"])
            before = (local_cards, repo.get_deck_intent(s, DECK_A["id"]))
        preview = rp.preview_deck_refresh(payload, local_deck, local_cards)
        self.assertEqual(preview["writes"], "none")
        self.assertTrue(preview["identity"]["matched_locally"])
        self.assertGreater(preview["differences"]["card_change_count"], 0)
        statuses = {c["status"] for c in preview["differences"]["cards"]}
        self.assertLessEqual(
            statuses, {"source_addition", "source_removal", "quantity_change"}
        )
        self.assertTrue(
            all(c["action"] == "proposed_only" for c in preview["differences"]["cards"])
        )
        self.assertIn(rp.EX_PRINTING_LOCAL_UNKNOWN, preview["exceptions"])
        self.assertIn("unavailable", preview["printing_metadata"]["local_comparison"])
        with self.factory() as s:
            # The local side is untouched by the preview.
            self.assertEqual(repo.get_deck_cards(s, DECK_A["id"]), before[0])
            self.assertEqual(repo.get_deck_intent(s, DECK_A["id"]), before[1])

    def test_roster_removal_is_proposed_not_deleted(self):
        """SRC-05: a deck that left the upstream folder is a proposed roster
        change; the local record stays."""
        roster = json.loads(
            (FIXTURES / "folder_588380_roster.json").read_text()
        )["decks"]
        with self.factory() as s:
            repo.upsert_deck(s, {
                "id": "archidekt:999999", "name": "Local Only", "status": "have",
                "source": "archidekt", "archidekt_id": "999999",
                "commanders": [], "color_identity": [], "fingerprint": "fp-x",
            })
            s.commit()
            out = rp.preview_folder_roster(
                roster, repo.list_decks(s, include_archived=True)
            )
        self.assertEqual(out["writes"], "none")
        gone = {e["local_deck_id"] for e in out["local_not_in_roster"]}
        self.assertIn("archidekt:999999", gone)
        with self.factory() as s:
            self.assertIsNotNone(repo.get_deck(s, "archidekt:999999"))

    # -- export the list locally (AT-04) ----------------------------------------

    def test_export_roundtrip_text(self):
        with self.factory() as s:
            cards = repo.get_deck_cards(s, DECK_A["id"])
        export_cards = [
            {"name": c["name"], "quantity": c["qty"],
             "board": "maybeboard" if c["board"] == "maybe" else c["board"]}
            for c in cards
        ]
        text = decklist_export.export_decklist_text(
            {"name": DECK_A["name"], "cards": export_cards},
            format="plain", include_maybeboard=True,
        )
        lines = text.splitlines()
        # Commander is always exported first.
        self.assertEqual(lines[0], "1 Aragorn, King Elessar")
        self.assertIn("10 Forest", lines)
        self.assertIn("1 Sol Ring", lines)
        self.assertIn("Maybeboard", lines)
        self.assertIn("1 Maybe Card", lines)
        # Every canonical card appears with its exact quantity.
        for c in cards:
            self.assertIn(f"{c['qty']} {c['name']}", lines)

    def test_export_parse_roundtrip(self):
        """AT-04: exporting to paste/import text and parsing it back
        reproduces the same boards and quantities (no remote writes)."""
        with self.factory() as s:
            cards = [
                c for c in repo.get_deck_cards(s, DECK_A["id"])
                if c["board"] in ("main", "commander")
            ]
        text = decklist_export.export_decklist_text(
            {
                "name": DECK_A["name"],
                "cards": [
                    {"name": c["name"], "quantity": c["qty"], "board": c["board"]}
                    for c in cards
                ],
            },
            format="moxfield",
        )
        parsed = decklist_parser.parse_decklist(text)
        got = {
            (c.name, c.quantity, c.section)
            for c in parsed.mainboard + parsed.commander
        }
        want = {
            (c["name"], c["qty"], "mainboard" if c["board"] == "main" else c["board"])
            for c in cards
        }
        self.assertEqual(got, want)

    def test_commit_revalidates_stale_moves_and_refuses(self):
        """SRC-06 safety net: a move staged while the copy was free is
        re-validated against the CURRENT state at commit; a copy that became
        unowned in the meantime is refused and nothing is applied."""
        with self.factory() as s:
            self._seed_instances(s)
            draft = self._open_draft(s, draft_id="draft-stale01")
            repo.upsert_draft_card(s, draft["id"], {
                "oracle_id": "o-cmd", "qty": 1, "board": "commander",
            })
            # Legal at staging: inst-ring-2 is a free in-collection copy.
            repo.stage_move(s, draft["id"], "inst-ring-2", draft_mode.ACTION_ASSIGN)
            s.commit()
        with self.factory() as s:
            inst = s.get(CardInstanceRow, "inst-ring-2")
            inst.status = "not_owned"  # the world changed: copy was sold
            inst.deck_id = None
            s.commit()
            before = self._canonical_snapshot(s)
            draft = repo.get_draft(s, "draft-stale01")
            assert draft is not None
            moves = repo.list_draft_moves(s, "draft-stale01")
            cards = repo.list_draft_cards(s, "draft-stale01")
            insts = {i["id"]: i for i in repo.list_instances(s)}
            with self.assertRaises(draft_mode.DraftError):
                draft_mode.plan_commit(draft, cards, moves, insts)
            self.assertEqual(self._canonical_snapshot(s), before)


class DeckLocalApiTests(unittest.TestCase):
    """The same slice through the /api/v2 app surface."""

    def setUp(self):
        from fastapi.testclient import TestClient

        from src.server.app.api import deps
        from src.server.app.main import app

        self.factory = make_factory()
        seed(self.factory)

        def _override():
            s = self.factory()
            try:
                yield s
                s.commit()
            finally:
                s.close()

        app.dependency_overrides[deps.db_session] = _override
        self.client = TestClient(app)
        self.addCleanup(app.dependency_overrides.clear)

    def test_intent_unstated_then_put_then_get(self):
        deck_id = DECK_A["id"]
        r = self.client.get(f"/api/v2/decks/{deck_id}/intent")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "unstated")
        self.assertIsNone(r.json()["intent"])

        r = self.client.put(
            f"/api/v2/decks/{deck_id}/intent", json={"intent": INTENT_A}
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "stated")
        self.assertEqual(body["provenance"], "stated")
        self.assertEqual(body["intent"]["pet_cards"], ["Sol Ring"])

        r = self.client.get(f"/api/v2/decks/{deck_id}/intent")
        self.assertEqual(r.json()["intent"]["deck_vision"]["intent"], "tune_up")

        # Other deck untouched (SRC-07).
        r = self.client.get(f"/api/v2/decks/{DECK_B['id']}/intent")
        self.assertEqual(r.json()["status"], "unstated")

        # Delete returns to unstated.
        r = self.client.delete(f"/api/v2/decks/{deck_id}/intent")
        self.assertEqual(r.json()["status"], "unstated")

    def test_intent_put_invalid_is_422(self):
        r = self.client.put(
            f"/api/v2/decks/{DECK_A['id']}/intent",
            json={"intent": {"target_power_level": {"scale": 99}}},
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["detail"]["error"], "invalid_intent")

    def test_intent_unknown_deck_is_404(self):
        r = self.client.get("/api/v2/decks/nope/intent")
        self.assertEqual(r.status_code, 404)
        r = self.client.put("/api/v2/decks/nope/intent", json={"intent": {}})
        self.assertEqual(r.status_code, 404)

    def test_copy_demand_endpoint(self):
        with self.factory() as s:
            repo.insert_instance(s, _instance("inst-ring-1", "o-ring", "in_deck", DECK_A["id"]))
            repo.insert_instance(s, _instance("inst-forest-1", "o-forest", "in_mail"))
            s.commit()
        r = self.client.get(f"/api/v2/decks/{DECK_A['id']}/copy-demand")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["totals"]["need"], 12)
        self.assertEqual(body["totals"]["have"], 1)
        self.assertEqual(body["totals"]["pending"], 1)
        self.assertEqual(body["totals"]["buy"], 10)
        for line in body["lines"]:
            self.assertEqual(line["printing_status"], "unknown")
            self.assertIsNone(line["printing"])
        self.assertEqual(body["unknowns"]["printing"]["status"], "unavailable")

    def test_export_endpoint(self):
        r = self.client.get(
            f"/api/v2/decks/{DECK_A['id']}/export",
            params={"format": "plain", "include_maybeboard": True},
        )
        self.assertEqual(r.status_code, 200, r.text)
        lines = r.text.splitlines()
        self.assertEqual(lines[0], "1 Aragorn, King Elessar")
        self.assertIn("10 Forest", lines)
        self.assertIn("1 Maybe Card", lines)
        # Unknown format -> 422, not a 500.
        r = self.client.get(
            f"/api/v2/decks/{DECK_A['id']}/export", params={"format": "bogus"}
        )
        self.assertEqual(r.status_code, 422)
        r = self.client.get("/api/v2/decks/nope/export")
        self.assertEqual(r.status_code, 404)


if __name__ == "__main__":
    unittest.main()
