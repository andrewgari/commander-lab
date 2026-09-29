"""Comprehensive unit tests for UnifiedCardService and related integrations.

Tests cover:
- Standardized domain models (CardMetadata, CardSynergy, CardSalt, ProviderStatus, UnifiedCardData)
- EDHRecClient (commander page, card page, synergy extraction, salt rating, error handling)
- CommanderSaltClient (card salt, deck cards salt, error handling)
- UnifiedCardService full success scenarios (Scryfall + EDHRec + Commander Salt aggregation)
- UnifiedCardService partial failure scenarios (EDHRec failure, Commander Salt failure, fallback to EDHRec salt)
- UnifiedCardService network error and timeout scenarios
- Batch lookup and Scryfall syntax search with analytics enrichment
- Client lifecycle and sync wrapper methods
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from typing import Any, Dict, List
from unittest import mock

import httpx

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.integrations.card_service import (
    CardNotFoundError,
    CardServiceError,
    UnifiedCardService,
)
from src.integrations.commandersalt import (
    CommanderSaltClient,
    CommanderSaltError,
    CommanderSaltNotFoundError,
    CommanderSaltRateLimitError,
    CommanderSaltRequestError,
    CommanderSaltResponseError,
    slugify_card_name as cs_slugify,
)
from src.integrations.edhrec import (
    EDHRecClient,
    EDHRecError,
    EDHRecNotFoundError,
    EDHRecRateLimitError,
    EDHRecRequestError,
    EDHRecResponseError,
    commander_slug,
    slugify_card_name as edh_slugify,
)
from src.integrations.models import (
    CardMetadata,
    CardSalt,
    CardSynergy,
    ProviderStatus,
    UnifiedCardData,
)
from src.integrations.scryfall import ScryfallCard, ScryfallClient
from src.shared.http import SharedHttpClient


# ---------------------------------------------------------------------------
# Fixture Generators
# ---------------------------------------------------------------------------


def _make_scryfall_payload(
    name: str = "Deepglow Skate",
    card_id: str = "4a58b98b-e666-41f2-850f-e23a54b321a6",
    oracle_id: str = "d7d91e60-f472-4e94-81eb-66fa6ce23554",
    **overrides: Any,
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "object": "card",
        "id": card_id,
        "oracle_id": oracle_id,
        "name": name,
        "mana_cost": "{4}{U}",
        "cmc": 5.0,
        "type_line": "Creature — Fish",
        "oracle_text": "When Deepglow Skate enters the battlefield, double the number of each kind of counter on any number of target permanents.",
        "colors": ["U"],
        "color_identity": ["U"],
        "keywords": [],
        "set": "c16",
        "set_name": "Commander 2016",
        "collector_number": "7",
        "rarity": "rare",
        "layout": "normal",
        "power": "3",
        "toughness": "3",
        "image_uris": {
            "small": f"https://cards.scryfall.io/small/{card_id}.jpg",
            "normal": f"https://cards.scryfall.io/normal/{card_id}.jpg",
            "art_crop": f"https://cards.scryfall.io/art_crop/{card_id}.jpg",
        },
        "legalities": {"commander": "legal"},
        "prices": {"usd": "2.50", "eur": "2.10"},
        "scryfall_uri": f"https://scryfall.com/card/c16/7/{name.lower().replace(' ', '-')}",
    }
    base.update(overrides)
    return base


def _make_edhrec_commander_payload(commander_name: str = "Atraxa, Praetors' Voice") -> Dict[str, Any]:
    return {
        "container": {
            "json_dict": {
                "card": {
                    "name": commander_name,
                    "sanitized": edh_slugify(commander_name),
                    "salt": 1.72,
                    "rank": 4,
                    "num_decks": 43941,
                    "is_commander": True,
                },
                "cardlists": [
                    {
                        "tag": "highsynergycards",
                        "header": "High Synergy Cards",
                        "cardviews": [
                            {
                                "name": "Deepglow Skate",
                                "sanitized": "deepglow-skate",
                                "synergy": 0.58,
                                "num_decks": 4200,
                                "potential_decks": 43941,
                            },
                            {
                                "name": "Inexorable Tide",
                                "sanitized": "inexorable-tide",
                                "synergy": 0.49,
                                "num_decks": 12000,
                                "potential_decks": 43941,
                            },
                        ],
                    },
                    {
                        "tag": "topcards",
                        "header": "Top Cards",
                        "cardviews": [
                            {
                                "name": "Sol Ring",
                                "sanitized": "sol-ring",
                                "synergy": 0.02,
                                "num_decks": 43000,
                                "potential_decks": 43941,
                            }
                        ],
                    },
                ],
            }
        }
    }


def _make_edhrec_card_payload(card_name: str = "Deepglow Skate") -> Dict[str, Any]:
    return {
        "container": {
            "json_dict": {
                "card": {
                    "name": card_name,
                    "sanitized": edh_slugify(card_name),
                    "salt": 0.45,
                    "rank": 142,
                    "num_decks": 18500,
                    "potential_decks": 120000,
                },
                "cardlists": [],
            }
        }
    }


def _make_commandersalt_card_payload(
    card_name: str = "Deepglow Skate",
    salt: float = 0.85,
    rank: int = 42,
) -> Dict[str, Any]:
    return {
        "name": card_name,
        "salt": salt,
        "rank": rank,
        "salt_sum": salt,
        "description": "Commander Salt card salt rating",
    }


def _make_commandersalt_deck_payload() -> Dict[str, Any]:
    return {
        "id": "deck-12345",
        "saltRating": 45.2,
        "cards": {
            "deepglow_skate": {"name": "Deepglow Skate", "salt": 0.85},
            "cyclonic_rift": {"name": "Cyclonic Rift", "salt": 2.45},
            "sol_ring": {"name": "Sol Ring", "salt": 0.0},
        },
    }


# ===========================================================================
# 1. Domain Models Tests
# ===========================================================================


class TestDomainModels(unittest.TestCase):
    """Test behavior and serialization of unified card domain models."""

    def test_card_metadata_from_dict_and_to_dict(self):
        payload = _make_scryfall_payload("Sol Ring")
        meta = CardMetadata.from_dict(payload)
        self.assertEqual(meta.name, "Sol Ring")
        self.assertEqual(meta.cmc, 5.0)
        self.assertTrue(meta.is_commander_legal)
        self.assertEqual(meta.get_image_uri("normal"), f"https://cards.scryfall.io/normal/{meta.scryfall_id}.jpg")
        self.assertIsNone(meta.get_image_uri("nonexistent"))

        d = meta.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["name"], "Sol Ring")
        self.assertEqual(d["oracle_id"], meta.oracle_id)
        self.assertTrue(d["is_commander_legal"])

    def test_card_metadata_from_scryfall_card(self):
        payload = _make_scryfall_payload("Rhystic Study")
        scry_card = ScryfallCard.from_dict(payload)
        meta = CardMetadata.from_scryfall_card(scry_card)
        self.assertEqual(meta.name, "Rhystic Study")
        self.assertEqual(meta.oracle_id, scry_card.oracle_id)
        self.assertEqual(meta.scryfall_id, scry_card.id)

    def test_card_metadata_invalid_raises(self):
        with self.assertRaises(TypeError):
            CardMetadata.from_dict(["not", "a", "dict"])  # type: ignore
        with self.assertRaises(TypeError):
            CardMetadata.from_scryfall_card(12345)  # type: ignore

    def test_card_synergy_model(self):
        synergy = CardSynergy(
            card_name="Deepglow Skate",
            synergy_score=0.58,
            inclusion_rate=0.0956,
            num_decks=4200,
            potential_decks=43941,
            commander_name="Atraxa, Praetors' Voice",
            categories=["High Synergy Cards"],
        )
        self.assertEqual(synergy.card_name, "Deepglow Skate")
        self.assertEqual(synergy.synergy_score, 0.58)
        d = synergy.to_dict()
        self.assertEqual(d["card_name"], "Deepglow Skate")
        self.assertEqual(d["synergy_score"], 0.58)
        self.assertEqual(d["commander_name"], "Atraxa, Praetors' Voice")

    def test_card_salt_model(self):
        salt = CardSalt(
            card_name="Cyclonic Rift",
            score=2.45,
            rank=1,
            source="commandersalt",
            is_fallback=False,
        )
        self.assertEqual(salt.score, 2.45)
        self.assertFalse(salt.is_fallback)
        d = salt.to_dict()
        self.assertEqual(d["score"], 2.45)
        self.assertEqual(d["source"], "commandersalt")

    def test_provider_status_model(self):
        status = ProviderStatus(provider="edhrec", status="success", latency_ms=45.2)
        d = status.to_dict()
        self.assertEqual(d["provider"], "edhrec")
        self.assertEqual(d["status"], "success")
        self.assertEqual(d["latency_ms"], 45.2)

    def test_unified_card_data_properties_and_dict_access(self):
        meta = CardMetadata.from_dict(_make_scryfall_payload("Demonic Tutor"))
        synergy = CardSynergy(card_name="Demonic Tutor", synergy_score=0.25)
        salt = CardSalt(card_name="Demonic Tutor", score=1.8)

        card_data = UnifiedCardData(
            name="Demonic Tutor",
            oracle_id="1234-5678",
            scryfall_id="abcd-ef01",
            metadata=meta,
            synergy=synergy,
            salt=salt,
            provider_statuses={
                "scryfall": ProviderStatus(provider="scryfall", status="success"),
                "edhrec": ProviderStatus(provider="edhrec", status="success"),
                "commandersalt": ProviderStatus(provider="commandersalt", status="success"),
            },
            successful_providers=["scryfall", "edhrec", "commandersalt"],
        )

        self.assertTrue(card_data.has_metadata)
        self.assertTrue(card_data.has_synergy)
        self.assertTrue(card_data.has_salt)
        self.assertTrue(card_data.is_complete)

        # Dict access and get()
        self.assertEqual(card_data["name"], "Demonic Tutor")
        self.assertEqual(card_data.get("name"), "Demonic Tutor")
        self.assertEqual(card_data.get("nonexistent", "fallback"), "fallback")

        d = card_data.to_dict()
        self.assertEqual(d["name"], "Demonic Tutor")
        self.assertIn("metadata", d)
        self.assertIn("synergy", d)
        self.assertIn("salt", d)
        self.assertTrue(d["is_complete"])


# ===========================================================================
# 2. EDHRecClient Tests
# ===========================================================================


class TestEDHRecClient(unittest.IsolatedAsyncioTestCase):
    """Test EDHRec client methods and error handling."""

    def test_slug_helpers(self):
        self.assertEqual(edh_slugify("Deepglow Skate"), "deepglow-skate")
        self.assertEqual(edh_slugify("Fire // Ice"), "fire")
        self.assertEqual(edh_slugify("Atraxa, Praetors' Voice"), "atraxa-praetors-voice")
        self.assertEqual(commander_slug("Atraxa, Praetors' Voice"), "atraxa-praetors-voice")
        self.assertEqual(
            commander_slug(["Tymna the Weaver", "Thrasios, Triton Hero"]),
            "tymna-the-weaver-thrasios-triton-hero",
        )
        self.assertEqual(edh_slugify(""), "")

    async def test_get_commander_page_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/pages/commanders/atraxa-praetors-voice.json")
            return httpx.Response(200, json=_make_edhrec_commander_payload())

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://json.edhrec.com", transport=transport)
        client = EDHRecClient(http_client=shared_http)

        data = await client.get_commander_page("Atraxa, Praetors' Voice")
        self.assertIn("container", data)

    async def test_get_card_page_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/pages/cards/deepglow-skate.json")
            return httpx.Response(200, json=_make_edhrec_card_payload("Deepglow Skate"))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://json.edhrec.com", transport=transport)
        client = EDHRecClient(http_client=shared_http)

        data = await client.get_card_page("Deepglow Skate")
        self.assertIn("container", data)

    async def test_get_card_synergy_with_commander_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_commander_payload())

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://json.edhrec.com", transport=transport)
        client = EDHRecClient(http_client=shared_http)

        synergy = await client.get_card_synergy("Deepglow Skate", commander_name="Atraxa, Praetors' Voice")
        self.assertIsNotNone(synergy)
        assert synergy is not None
        self.assertEqual(synergy.card_name, "Deepglow Skate")
        self.assertEqual(synergy.synergy_score, 0.58)
        self.assertEqual(synergy.num_decks, 4200)
        self.assertEqual(synergy.potential_decks, 43941)
        self.assertAlmostEqual(synergy.inclusion_rate or 0.0, 4200 / 43941, places=4)
        self.assertIn("High Synergy Cards", synergy.categories)

    async def test_get_card_synergy_commander_card_itself(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_commander_payload())

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://json.edhrec.com", transport=transport)
        client = EDHRecClient(http_client=shared_http)

        synergy = await client.get_card_synergy("Atraxa, Praetors' Voice", commander_name="Atraxa, Praetors' Voice")
        self.assertIsNotNone(synergy)
        assert synergy is not None
        self.assertEqual(synergy.card_name, "Atraxa, Praetors' Voice")
        self.assertEqual(synergy.synergy_score, 1.0)
        self.assertEqual(synergy.rank, 4)
        self.assertEqual(synergy.num_decks, 43941)

    async def test_get_card_synergy_without_commander(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_card_payload("Deepglow Skate"))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://json.edhrec.com", transport=transport)
        client = EDHRecClient(http_client=shared_http)

        synergy = await client.get_card_synergy("Deepglow Skate")
        self.assertIsNotNone(synergy)
        assert synergy is not None
        self.assertEqual(synergy.card_name, "Deepglow Skate")
        self.assertEqual(synergy.rank, 142)
        self.assertEqual(synergy.num_decks, 18500)

    async def test_get_card_synergy_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://json.edhrec.com", transport=transport)
        client = EDHRecClient(http_client=shared_http)

        synergy = await client.get_card_synergy("Unknown Card")
        self.assertIsNone(synergy)

    async def test_get_card_salt_from_commander_page(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_commander_payload("Atraxa, Praetors' Voice"))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://json.edhrec.com", transport=transport)
        client = EDHRecClient(http_client=shared_http)

        salt = await client.get_card_salt("Atraxa, Praetors' Voice", commander_name="Atraxa, Praetors' Voice")
        self.assertIsNotNone(salt)
        assert salt is not None
        self.assertEqual(salt.score, 1.72)
        self.assertEqual(salt.source, "edhrec")

    async def test_edhrec_errors(self):
        # 404
        def h404(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        client = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(h404)))
        with self.assertRaises(EDHRecNotFoundError):
            await client.get_card_page("Ghost Card")

        # 429
        def h429(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text="Rate Limited")

        client = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(h429), retry_config={"max_retries": 1}))
        with self.assertRaises(EDHRecRateLimitError):
            await client.get_card_page("Throttled Card")

        # 500
        def h500(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Server Error")

        client = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(h500), retry_config={"max_retries": 1}))
        with self.assertRaises(EDHRecRequestError):
            await client.get_card_page("Error Card")

        # Invalid JSON
        def h_bad_json(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="NOT JSON")

        client = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(h_bad_json)))
        with self.assertRaises(EDHRecResponseError):
            await client.get_card_page("Bad JSON")


# ===========================================================================
# 3. CommanderSaltClient Tests
# ===========================================================================


class TestCommanderSaltClient(unittest.IsolatedAsyncioTestCase):
    """Test CommanderSalt client methods and error handling."""

    def test_slug_helper(self):
        self.assertEqual(cs_slugify("Deepglow Skate"), "deepglow-skate")
        self.assertEqual(cs_slugify("Fire // Ice"), "fire")

    async def test_get_card_salt_success(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/cards/deepglow-skate")
            return httpx.Response(200, json=_make_commandersalt_card_payload("Deepglow Skate", salt=0.85, rank=42))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.commandersalt.com", transport=transport)
        client = CommanderSaltClient(http_client=shared_http)

        salt = await client.get_card_salt("Deepglow Skate")
        self.assertIsNotNone(salt)
        assert salt is not None
        self.assertEqual(salt.card_name, "Deepglow Skate")
        self.assertEqual(salt.score, 0.85)
        self.assertEqual(salt.rank, 42)
        self.assertEqual(salt.source, "commandersalt")

    async def test_get_card_salt_query_param_fallback(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/cards/deepglow-skate":
                return httpx.Response(404, text="Not Found")
            if request.url.path == "/cards" and "name=Deepglow+Skate" in str(request.url):
                return httpx.Response(200, json=_make_commandersalt_card_payload("Deepglow Skate", salt=0.85))
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.commandersalt.com", transport=transport)
        client = CommanderSaltClient(http_client=shared_http)

        salt = await client.get_card_salt("Deepglow Skate")
        self.assertIsNotNone(salt)
        assert salt is not None
        self.assertEqual(salt.score, 0.85)

    async def test_get_card_salt_not_found(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.commandersalt.com", transport=transport)
        client = CommanderSaltClient(http_client=shared_http)

        salt = await client.get_card_salt("Nonexistent Card")
        self.assertIsNone(salt)

    async def test_get_deck_and_deck_cards_salt(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/decks")
            return httpx.Response(200, json=_make_commandersalt_deck_payload())

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.commandersalt.com", transport=transport)
        client = CommanderSaltClient(http_client=shared_http)

        deck_cards = await client.get_deck_cards_salt("https://archidekt.com/decks/12345")
        self.assertIn("deepglow skate", deck_cards)
        self.assertEqual(deck_cards["deepglow skate"].score, 0.85)
        self.assertIn("cyclonic rift", deck_cards)
        self.assertEqual(deck_cards["cyclonic rift"].score, 2.45)

    async def test_commandersalt_errors(self):
        def h500(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="Internal Server Error")

        client = CommanderSaltClient(http_client=SharedHttpClient(base_url="https://api.commandersalt.com", transport=httpx.MockTransport(h500), retry_config={"max_retries": 1}))
        with self.assertRaises(CommanderSaltRequestError):
            await client.get_deck("https://archidekt.com/decks/5555")


# ===========================================================================
# 4. UnifiedCardService Success Scenarios
# ===========================================================================


class TestUnifiedCardServiceSuccess(unittest.IsolatedAsyncioTestCase):
    """Test full successful aggregation across Scryfall, EDHRec, and Commander Salt."""

    async def test_full_success_aggregation(self):
        # Scryfall mock
        def scry_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_scryfall_payload("Deepglow Skate"))

        scryfall = ScryfallClient(http_client=SharedHttpClient(base_url="https://api.scryfall.com", transport=httpx.MockTransport(scry_h)))

        # EDHRec mock
        def edh_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_commander_payload("Atraxa, Praetors' Voice"))

        edhrec = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(edh_h)))

        # CommanderSalt mock
        def cs_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_commandersalt_card_payload("Deepglow Skate", salt=0.85, rank=42))

        cs = CommanderSaltClient(http_client=SharedHttpClient(base_url="https://api.commandersalt.com", transport=httpx.MockTransport(cs_h)))

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        result = await service.get_card_data("Deepglow Skate", commander_name="Atraxa, Praetors' Voice")

        self.assertEqual(result.name, "Deepglow Skate")
        self.assertIsNotNone(result.metadata)
        self.assertIsNotNone(result.synergy)
        self.assertIsNotNone(result.salt)

        # Verify metadata
        assert result.metadata is not None
        self.assertEqual(result.metadata.name, "Deepglow Skate")
        self.assertEqual(result.metadata.cmc, 5.0)

        # Verify synergy
        assert result.synergy is not None
        self.assertEqual(result.synergy.synergy_score, 0.58)
        self.assertEqual(result.synergy.commander_name, "Atraxa, Praetors' Voice")

        # Verify salt
        assert result.salt is not None
        self.assertEqual(result.salt.score, 0.85)
        self.assertEqual(result.salt.rank, 42)
        self.assertFalse(result.salt.is_fallback)

        # Verify provider status tracking
        self.assertEqual(result.provider_statuses["scryfall"].status, "success")
        self.assertEqual(result.provider_statuses["edhrec"].status, "success")
        self.assertEqual(result.provider_statuses["commandersalt"].status, "success")
        self.assertEqual(set(result.successful_providers), {"scryfall", "edhrec", "commandersalt"})
        self.assertEqual(result.failed_providers, [])
        self.assertTrue(result.is_complete)

    async def test_get_card_by_id(self):
        card_id = "4a58b98b-e666-41f2-850f-e23a54b321a6"

        def scry_h(req: httpx.Request) -> httpx.Response:
            if req.url.path == f"/cards/{card_id}":
                return httpx.Response(200, json=_make_scryfall_payload("Deepglow Skate", card_id=card_id))
            return httpx.Response(200, json=_make_scryfall_payload("Deepglow Skate", card_id=card_id))

        scryfall = ScryfallClient(http_client=SharedHttpClient(base_url="https://api.scryfall.com", transport=httpx.MockTransport(scry_h)))

        def edh_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_card_payload("Deepglow Skate"))

        edhrec = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(edh_h)))

        def cs_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_commandersalt_card_payload("Deepglow Skate", salt=0.85))

        cs = CommanderSaltClient(http_client=SharedHttpClient(base_url="https://api.commandersalt.com", transport=httpx.MockTransport(cs_h)))

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        result = await service.get_card_by_id(card_id)
        self.assertEqual(result.name, "Deepglow Skate")
        self.assertEqual(result.scryfall_id, card_id)

    async def test_selective_providers(self):
        def scry_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_scryfall_payload("Sol Ring"))

        scryfall = ScryfallClient(http_client=SharedHttpClient(base_url="https://api.scryfall.com", transport=httpx.MockTransport(scry_h)))

        def edh_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_card_payload("Sol Ring"))

        edhrec = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(edh_h)))

        cs = CommanderSaltClient(http_client=SharedHttpClient(base_url="https://api.commandersalt.com"))

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        # Only query scryfall and edhrec
        result = await service.get_card_data("Sol Ring", providers=["scryfall", "edhrec"])
        self.assertIsNotNone(result.metadata)
        self.assertIsNotNone(result.synergy)
        self.assertIsNone(result.salt)
        self.assertEqual(result.provider_statuses["commandersalt"].status, "skipped")
        self.assertEqual(set(result.successful_providers), {"scryfall", "edhrec"})


# ===========================================================================
# 5. UnifiedCardService Partial Failure Scenarios
# ===========================================================================


class TestUnifiedCardServicePartialFailure(unittest.IsolatedAsyncioTestCase):
    """Test resilience when one or more external services fail."""

    async def test_edhrec_failure_scryfall_and_salt_succeed(self):
        # Scryfall succeeds
        scryfall = ScryfallClient(
            http_client=SharedHttpClient(
                base_url="https://api.scryfall.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json=_make_scryfall_payload("Cyclonic Rift"))),
            )
        )

        # EDHRec returns 500
        edhrec = EDHRecClient(
            http_client=SharedHttpClient(
                base_url="https://json.edhrec.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(500, text="EDHRec Down")),
                retry_config={"max_retries": 1},
            )
        )

        # CommanderSalt succeeds
        cs = CommanderSaltClient(
            http_client=SharedHttpClient(
                base_url="https://api.commandersalt.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json=_make_commandersalt_card_payload("Cyclonic Rift", salt=2.45))),
            )
        )

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        result = await service.get_card_data("Cyclonic Rift")

        # Result should still return successfully with metadata and salt
        self.assertEqual(result.name, "Cyclonic Rift")
        self.assertIsNotNone(result.metadata)
        self.assertIsNone(result.synergy)
        self.assertIsNotNone(result.salt)
        assert result.salt is not None
        self.assertEqual(result.salt.score, 2.45)

        # Status checking
        self.assertEqual(result.provider_statuses["edhrec"].status, "error")
        self.assertIn("edhrec", result.failed_providers)
        self.assertIn("scryfall", result.successful_providers)
        self.assertIn("commandersalt", result.successful_providers)
        self.assertFalse(result.is_complete)
        self.assertTrue(len(result.warnings) > 0)

    async def test_commandersalt_failure_with_edhrec_salt_fallback(self):
        """When Commander Salt fails, fallback to EDHRec community salt score."""
        # Scryfall succeeds
        scryfall = ScryfallClient(
            http_client=SharedHttpClient(
                base_url="https://api.scryfall.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json=_make_scryfall_payload("Atraxa, Praetors' Voice"))),
            )
        )

        # EDHRec returns commander payload with salt: 1.72
        edhrec = EDHRecClient(
            http_client=SharedHttpClient(
                base_url="https://json.edhrec.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json=_make_edhrec_commander_payload("Atraxa, Praetors' Voice"))),
            )
        )

        # CommanderSalt returns 500 error
        cs = CommanderSaltClient(
            http_client=SharedHttpClient(
                base_url="https://api.commandersalt.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(500, text="Commander Salt Server Error")),
                retry_config={"max_retries": 1},
            )
        )

        service = UnifiedCardService(
            scryfall_client=scryfall,
            edhrec_client=edhrec,
            commandersalt_client=cs,
            enable_salt_fallback=True,
        )

        result = await service.get_card_data("Atraxa, Praetors' Voice", commander_name="Atraxa, Praetors' Voice")

        self.assertEqual(result.name, "Atraxa, Praetors' Voice")
        self.assertIsNotNone(result.metadata)
        self.assertIsNotNone(result.synergy)
        self.assertIsNotNone(result.salt)

        # Fallback salt verified
        assert result.salt is not None
        self.assertEqual(result.salt.score, 1.72)
        self.assertEqual(result.salt.source, "edhrec")
        self.assertTrue(result.salt.is_fallback)
        self.assertEqual(result.provider_statuses["commandersalt"].status, "fallback")

    async def test_commandersalt_failure_without_fallback(self):
        """When Commander Salt fails and no fallback is enabled or available."""
        scryfall = ScryfallClient(
            http_client=SharedHttpClient(
                base_url="https://api.scryfall.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json=_make_scryfall_payload("Arcane Signet"))),
            )
        )

        # EDHRec returns payload with NO salt field
        no_salt_edhrec = {
            "container": {
                "json_dict": {
                    "card": {"name": "Arcane Signet", "rank": 2},
                    "cardlists": [],
                }
            }
        }
        edhrec = EDHRecClient(
            http_client=SharedHttpClient(
                base_url="https://json.edhrec.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(200, json=no_salt_edhrec)),
            )
        )

        # CommanderSalt 404
        cs = CommanderSaltClient(
            http_client=SharedHttpClient(
                base_url="https://api.commandersalt.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(404, text="Not Found")),
            )
        )

        service = UnifiedCardService(
            scryfall_client=scryfall,
            edhrec_client=edhrec,
            commandersalt_client=cs,
            enable_salt_fallback=True,
        )

        result = await service.get_card_data("Arcane Signet")
        self.assertIsNotNone(result.metadata)
        self.assertIsNone(result.salt)
        self.assertEqual(result.provider_statuses["commandersalt"].status, "not_found")

    async def test_scryfall_not_found_raises_card_not_found_error(self):
        scryfall = ScryfallClient(
            http_client=SharedHttpClient(
                base_url="https://api.scryfall.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(404, text="Card Not Found")),
            )
        )

        service = UnifiedCardService(scryfall_client=scryfall)
        with self.assertRaises(CardNotFoundError) as cm:
            await service.get_card_data("Totally Invented Card", raise_on_not_found=True)
        self.assertEqual(cm.exception.card_name, "Totally Invented Card")

    async def test_scryfall_not_found_without_raising(self):
        scryfall = ScryfallClient(
            http_client=SharedHttpClient(
                base_url="https://api.scryfall.com",
                transport=httpx.MockTransport(lambda req: httpx.Response(404, text="Card Not Found")),
            )
        )

        service = UnifiedCardService(scryfall_client=scryfall)
        result = await service.get_card_data("Totally Invented Card", raise_on_not_found=False)
        self.assertIsNone(result.metadata)
        self.assertEqual(result.provider_statuses["scryfall"].status, "not_found")
        self.assertIn("scryfall", result.failed_providers)


# ===========================================================================
# 6. UnifiedCardService Network Error Scenarios
# ===========================================================================


class TestUnifiedCardServiceNetworkErrors(unittest.IsolatedAsyncioTestCase):
    """Test network timeouts, connection refused, and total failure recovery."""

    async def test_network_connection_error_on_all_services_with_raise(self):
        def conn_err_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        scryfall = ScryfallClient(
            http_client=SharedHttpClient(
                base_url="https://api.scryfall.com",
                transport=httpx.MockTransport(conn_err_handler),
                retry_config={"max_retries": 1},
            )
        )
        edhrec = EDHRecClient(
            http_client=SharedHttpClient(
                base_url="https://json.edhrec.com",
                transport=httpx.MockTransport(conn_err_handler),
                retry_config={"max_retries": 1},
            )
        )
        cs = CommanderSaltClient(
            http_client=SharedHttpClient(
                base_url="https://api.commandersalt.com",
                transport=httpx.MockTransport(conn_err_handler),
                retry_config={"max_retries": 1},
            )
        )

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        with self.assertRaises(CardServiceError):
            await service.get_card_data("Lightning Bolt", raise_on_error=True)

    async def test_network_connection_error_without_raise(self):
        def conn_err_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused")

        scryfall = ScryfallClient(
            http_client=SharedHttpClient(
                base_url="https://api.scryfall.com",
                transport=httpx.MockTransport(conn_err_handler),
                retry_config={"max_retries": 1},
            )
        )
        edhrec = EDHRecClient(
            http_client=SharedHttpClient(
                base_url="https://json.edhrec.com",
                transport=httpx.MockTransport(conn_err_handler),
                retry_config={"max_retries": 1},
            )
        )
        cs = CommanderSaltClient(
            http_client=SharedHttpClient(
                base_url="https://api.commandersalt.com",
                transport=httpx.MockTransport(conn_err_handler),
                retry_config={"max_retries": 1},
            )
        )

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        result = await service.get_card_data("Lightning Bolt", raise_on_error=False, raise_on_not_found=False)
        self.assertEqual(result.name, "Lightning Bolt")
        self.assertIsNone(result.metadata)
        self.assertIsNone(result.synergy)
        self.assertIsNone(result.salt)
        self.assertEqual(set(result.failed_providers), {"scryfall", "edhrec", "commandersalt"})
        self.assertFalse(result.is_complete)


# ===========================================================================
# 7. Batch and Search Operations
# ===========================================================================


class TestBatchAndSearchOperations(unittest.IsolatedAsyncioTestCase):
    """Test batch querying and syntax search operations."""

    async def test_get_cards_batch(self):
        def scry_h(req: httpx.Request) -> httpx.Response:
            query = str(req.url)
            if "Sol+Ring" in query:
                return httpx.Response(200, json=_make_scryfall_payload("Sol Ring"))
            if "Arcane+Signet" in query:
                return httpx.Response(200, json=_make_scryfall_payload("Arcane Signet"))
            return httpx.Response(404)

        scryfall = ScryfallClient(http_client=SharedHttpClient(base_url="https://api.scryfall.com", transport=httpx.MockTransport(scry_h)))

        def edh_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_card_payload("Generic Card"))

        edhrec = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(edh_h)))

        def cs_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_commandersalt_card_payload("Generic Card", salt=0.0))

        cs = CommanderSaltClient(http_client=SharedHttpClient(base_url="https://api.commandersalt.com", transport=httpx.MockTransport(cs_h)))

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        cards = await service.get_cards_batch(["Sol Ring", "Arcane Signet", "Unknown Card"])
        self.assertEqual(len(cards), 3)
        self.assertEqual(cards[0].name, "Sol Ring")
        self.assertEqual(cards[1].name, "Arcane Signet")
        self.assertIn("scryfall", cards[2].failed_providers)

    async def test_search_cards_with_analytics(self):
        search_payload = {
            "object": "list",
            "total_cards": 2,
            "has_more": False,
            "data": [
                _make_scryfall_payload("Counterspell"),
                _make_scryfall_payload("Mana Drain"),
            ],
        }

        def scry_h(req: httpx.Request) -> httpx.Response:
            if "/cards/search" in req.url.path:
                return httpx.Response(200, json=search_payload)
            if "Counterspell" in str(req.url):
                return httpx.Response(200, json=_make_scryfall_payload("Counterspell"))
            if "Mana+Drain" in str(req.url):
                return httpx.Response(200, json=_make_scryfall_payload("Mana Drain"))
            return httpx.Response(404)

        scryfall = ScryfallClient(http_client=SharedHttpClient(base_url="https://api.scryfall.com", transport=httpx.MockTransport(scry_h)))

        def edh_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_card_payload("Counterspell"))

        edhrec = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(edh_h)))

        def cs_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_commandersalt_card_payload("Counterspell", salt=1.2))

        cs = CommanderSaltClient(http_client=SharedHttpClient(base_url="https://api.commandersalt.com", transport=httpx.MockTransport(cs_h)))

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        results = await service.search_cards("type:instant c:u", limit=2, include_analytics=True)
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0].name, "Counterspell")
        self.assertEqual(results[1].name, "Mana Drain")
        self.assertIsNotNone(results[0].metadata)
        self.assertIsNotNone(results[0].synergy)
        self.assertIsNotNone(results[0].salt)

    async def test_search_cards_without_analytics(self):
        search_payload = {
            "object": "list",
            "total_cards": 1,
            "has_more": False,
            "data": [_make_scryfall_payload("Swords to Plowshares")],
        }

        def scry_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=search_payload)

        scryfall = ScryfallClient(http_client=SharedHttpClient(base_url="https://api.scryfall.com", transport=httpx.MockTransport(scry_h)))
        service = UnifiedCardService(scryfall_client=scryfall)

        results = await service.search_cards("Swords to Plowshares", include_analytics=False)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].name, "Swords to Plowshares")
        self.assertIsNotNone(results[0].metadata)
        self.assertIsNone(results[0].synergy)
        self.assertIsNone(results[0].salt)


# ===========================================================================
# 8. Lifecycle & Sync Wrapper
# ===========================================================================


class TestLifecycleAndSync(unittest.TestCase):
    """Test client lifecycle management and synchronous wrappers."""

    def test_sync_wrapper(self):
        def scry_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_scryfall_payload("Birds of Paradise"))

        scryfall = ScryfallClient(http_client=SharedHttpClient(base_url="https://api.scryfall.com", transport=httpx.MockTransport(scry_h)))

        def edh_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_edhrec_card_payload("Birds of Paradise"))

        edhrec = EDHRecClient(http_client=SharedHttpClient(base_url="https://json.edhrec.com", transport=httpx.MockTransport(edh_h)))

        def cs_h(req: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_commandersalt_card_payload("Birds of Paradise", salt=0.1))

        cs = CommanderSaltClient(http_client=SharedHttpClient(base_url="https://api.commandersalt.com", transport=httpx.MockTransport(cs_h)))

        service = UnifiedCardService(scryfall_client=scryfall, edhrec_client=edhrec, commandersalt_client=cs)

        res = service.get_card_data_sync("Birds of Paradise")
        self.assertEqual(res.name, "Birds of Paradise")
        self.assertIsNotNone(res.metadata)
        self.assertIsNotNone(res.synergy)
        self.assertIsNotNone(res.salt)

    def test_async_context_manager(self):
        async def _test():
            async with UnifiedCardService() as service:
                self.assertIsNotNone(service)

        asyncio.run(_test())


if __name__ == "__main__":
    unittest.main()
