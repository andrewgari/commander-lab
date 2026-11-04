"""Unit tests for providers/archidekt_collection.py — CSV parsing, credential
handling and pagination. No network: requests is monkeypatched.

Run: python tests/test_archidekt_collection.py
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.infra.providers import archidekt_collection as ac


HEADER = (
    "quantity,card__oracleCard__name,modifier,condition,createdAt,language,"
    "purchasePrice,tags,card__edition__editionname,card__edition__editioncode,"
    "card__collectorNumber,card__uid,card__oracle__uid\n"
)


def csv_row(**kw):
    defaults = {
        "quantity": "1",
        "card__oracleCard__name": "Sol Ring",
        "modifier": "Normal",
        "condition": "NM",
        "createdAt": "2025-01-02",
        "language": "English",
        "purchasePrice": "1.50",
        "tags": "",
        "card__edition__editionname": "Commander 2021",
        "card__edition__editioncode": "c21",
        "card__collectorNumber": "263",
        "card__uid": "uid-1",
        "card__oracle__uid": "oracle-1",
    }
    defaults.update(kw)
    order = [
        "quantity", "card__oracleCard__name", "modifier", "condition", "createdAt",
        "language", "purchasePrice", "tags", "card__edition__editionname",
        "card__edition__editioncode", "card__collectorNumber", "card__uid",
        "card__oracle__uid",
    ]
    return ",".join(str(defaults[k]) for k in order) + "\n"


class TestCredentials(unittest.TestCase):
    def test_missing_credentials_raises_with_actionable_message(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ac.CollectionAuthError) as ctx:
                ac.credentials()
        self.assertIn("ARCHIDEKT_SESSION", str(ctx.exception))

    def test_partial_credentials_raise(self):
        with patch.dict(os.environ, {"ARCHIDEKT_SESSION": "s"}, clear=True):
            with self.assertRaises(ac.CollectionAuthError):
                ac.credentials()

    def test_both_present_returns_pair(self):
        with patch.dict(os.environ, {"ARCHIDEKT_SESSION": "s", "ARCHIDEKT_CSRF": "c"}, clear=True):
            self.assertEqual(ac.credentials(), ("s", "c"))


class TestParseCsvRows(unittest.TestCase):
    def test_normalizes_finish_condition_language_and_set(self):
        rows = ac.parse_csv_rows(HEADER + csv_row())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["card_name"], "Sol Ring")
        self.assertEqual(row["quantity"], 1)
        self.assertEqual(row["finish"], "nonfoil")
        self.assertEqual(row["condition"], "NM")
        self.assertEqual(row["language"], "EN")
        self.assertEqual(row["set"], "C21")
        self.assertEqual(row["collector_number"], "263")
        self.assertEqual(row["price_paid"], 1.5)

    def test_foil_and_etched_map_to_scryfall_finishes(self):
        rows = ac.parse_csv_rows(HEADER + csv_row(modifier="Foil") + csv_row(modifier="Etched"))
        self.assertEqual([r["finish"] for r in rows], ["foil", "etched"])

    def test_full_condition_names_map_to_codes(self):
        rows = ac.parse_csv_rows(
            HEADER
            + csv_row(condition="Lightly Played")
            + csv_row(condition="Damaged")
            + csv_row(condition="Heavily Played")
        )
        self.assertEqual([r["condition"] for r in rows], ["LP", "DMG", "HP"])

    def test_unknown_finish_and_condition_fall_back_instead_of_raising(self):
        rows = ac.parse_csv_rows(HEADER + csv_row(modifier="Gilded", condition="Sparkly"))
        self.assertEqual(rows[0]["finish"], "nonfoil")
        self.assertEqual(rows[0]["condition"], "NM")

    def test_skips_rows_without_name_or_with_zero_quantity(self):
        content = (
            HEADER
            + csv_row(card__oracleCard__name="")
            + csv_row(quantity="0")
            + csv_row(quantity="not-a-number")
            + csv_row(card__oracleCard__name="Arcane Signet")
        )
        rows = ac.parse_csv_rows(content)
        self.assertEqual([r["card_name"] for r in rows], ["Arcane Signet"])

    def test_card_names_with_commas_survive_csv_parsing(self):
        content = HEADER + '1,"Bruna, Light of Alabaster",Normal,NM,2025-01-02,English,0,,Avacyn Restored,avr,3,uid-2,oracle-2\n'
        rows = ac.parse_csv_rows(content)
        self.assertEqual(rows[0]["card_name"], "Bruna, Light of Alabaster")

    def test_tags_split_into_list(self):
        rows = ac.parse_csv_rows(HEADER + csv_row(tags="\"Trade,Binder\""))
        self.assertEqual(rows[0]["tags"], ["Trade", "Binder"])

    def test_empty_purchase_price_becomes_zero(self):
        rows = ac.parse_csv_rows(HEADER + csv_row(purchasePrice=""))
        self.assertEqual(rows[0]["price_paid"], 0.0)


class TestFetchCollection(unittest.TestCase):
    def _post_response(self, content, more=False, status=200):
        res = MagicMock()
        res.status_code = status
        res.text = content
        res.json.return_value = {"content": content, "moreContent": more, "totalRows": 1}
        res.raise_for_status.return_value = None
        return res

    def test_pages_until_more_content_false(self):
        page1 = self._post_response(HEADER + csv_row(card__oracleCard__name="Sol Ring"), more=True)
        page2 = self._post_response(HEADER + csv_row(card__oracleCard__name="Arcane Signet"), more=False)

        with patch.object(ac.requests, "post", side_effect=[page1, page2]) as mock_post:
            rows = ac.fetch_collection(user_id="35025", session_id="s", csrf_token="c")

        self.assertEqual([r["card_name"] for r in rows], ["Sol Ring", "Arcane Signet"])
        self.assertEqual(mock_post.call_count, 2)
        # page number must increment, not repeat
        self.assertEqual(mock_post.call_args_list[0].kwargs["json"]["page"], 1)
        self.assertEqual(mock_post.call_args_list[1].kwargs["json"]["page"], 2)

    def test_sends_session_cookies_and_csrf_header(self):
        page = self._post_response(HEADER + csv_row())
        with patch.object(ac.requests, "post", return_value=page) as mock_post:
            ac.fetch_collection(user_id="35025", session_id="sess", csrf_token="tok")

        headers = mock_post.call_args.kwargs["headers"]
        self.assertIn("sessionid=sess", headers["Cookie"])
        self.assertEqual(headers["X-CSRFToken"], "tok")

    def test_requests_paper_game_only(self):
        page = self._post_response(HEADER + csv_row())
        with patch.object(ac.requests, "post", return_value=page) as mock_post:
            ac.fetch_collection(user_id="35025", session_id="s", csrf_token="c")
        self.assertEqual(mock_post.call_args.kwargs["json"]["game"], ac.PAPER_GAME)

    def test_401_raises_auth_error(self):
        res = self._post_response("", status=401)
        with patch.object(ac.requests, "post", return_value=res):
            with self.assertRaises(ac.CollectionAuthError):
                ac.fetch_collection(user_id="35025", session_id="s", csrf_token="c")

    def test_400_no_collection_found_raises_auth_error(self):
        res = self._post_response('{"error":"No collection found."}', status=400)
        with patch.object(ac.requests, "post", return_value=res):
            with self.assertRaises(ac.CollectionAuthError) as ctx:
                ac.fetch_collection(user_id="35025", session_id="s", csrf_token="c")
        self.assertIn("No collection found", str(ctx.exception))

    def test_unknown_username_raises_fetch_error(self):
        user_res = MagicMock()
        user_res.json.return_value = {"results": []}
        user_res.raise_for_status.return_value = None
        with patch.object(ac.requests, "get", return_value=user_res):
            with self.assertRaises(ac.CollectionFetchError):
                ac.fetch_collection(username="nope", session_id="s", csrf_token="c")

    def test_resolves_username_to_user_id_then_posts_to_that_id(self):
        user_res = MagicMock()
        user_res.json.return_value = {"results": [{"id": 35025}]}
        user_res.raise_for_status.return_value = None
        page = self._post_response(HEADER + csv_row())

        with patch.object(ac.requests, "get", return_value=user_res), \
             patch.object(ac.requests, "post", return_value=page) as mock_post:
            ac.fetch_collection(username="CovaDax", session_id="s", csrf_token="c")

        self.assertIn("/35025/", mock_post.call_args.args[0])

    def test_pagination_stops_at_max_pages(self):
        forever = self._post_response(HEADER + csv_row(), more=True)
        with patch.object(ac.requests, "post", return_value=forever) as mock_post:
            rows = ac.fetch_collection(user_id="35025", session_id="s", csrf_token="c")
        self.assertEqual(mock_post.call_count, ac.MAX_PAGES)
        self.assertEqual(len(rows), ac.MAX_PAGES)


if __name__ == "__main__":
    unittest.main()
