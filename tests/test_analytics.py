"""
Unit tests for deck analytics domain models, abstract provider interface, and registry.

Verifies:
1. Pydantic input models (validation of valid payloads, rejection of malformed data).
2. Pydantic output models (scores, recommendations, synergy, popularity).
3. Abstract Base Provider interface enforcement and concrete provider stub execution.
4. Pluggable provider registration and retrieval mechanism at runtime.
"""
import os
import sys
import unittest
from typing import List

# Ensure project root is in path for standalone execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import ValidationError

from analytics import (
    AnalyticsProviderRegistry,
    BaseAnalyticsProvider,
    CardCutRecommendation,
    CardRecommendation,
    CardRecommendations,
    CommanderIdentifier,
    DeckAnalyticsResult,
    DeckCardEntry,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PopularityMetric,
    PowerScore,
    ProviderNotFoundError,
    ProviderRegistrationError,
    SaltCardDetail,
    SaltScore,
    SynergyMetric,
    clear_registry,
    default_registry,
    get_provider,
    list_providers,
    register_provider,
    unregister_provider,
)


class TestAnalyticsInputModels(unittest.TestCase):
    """Test validation and behavior of input models."""

    def test_commander_identifier_valid(self):
        cmd = CommanderIdentifier(
            name="Atraxa, Praetors' Voice",
            oracle_id="402eb06a-ff55-4623-a1bf-4ad67f8fbcf4",
            scryfall_id="b06ae34e-0c4a-4c28-98e3-05ec86c07a3c",
        )
        self.assertEqual(cmd.name, "Atraxa, Praetors' Voice")
        self.assertEqual(cmd.oracle_id, "402eb06a-ff55-4623-a1bf-4ad67f8fbcf4")
        self.assertEqual(cmd.scryfall_id, "b06ae34e-0c4a-4c28-98e3-05ec86c07a3c")

    def test_commander_identifier_minimal(self):
        cmd = CommanderIdentifier(name="Krenko, Mob Boss")
        self.assertEqual(cmd.name, "Krenko, Mob Boss")
        self.assertIsNone(cmd.oracle_id)
        self.assertIsNone(cmd.scryfall_id)

    def test_commander_identifier_strips_whitespace(self):
        cmd = CommanderIdentifier(name="  Edgar Markov  ")
        self.assertEqual(cmd.name, "Edgar Markov")

    def test_commander_identifier_rejects_empty_name(self):
        with self.assertRaises(ValidationError):
            CommanderIdentifier(name="")
        with self.assertRaises(ValidationError):
            CommanderIdentifier(name="   ")

    def test_commander_identifier_rejects_invalid_uuid(self):
        with self.assertRaises(ValidationError):
            CommanderIdentifier(name="Atraxa", oracle_id="not-a-valid-uuid")
        with self.assertRaises(ValidationError):
            CommanderIdentifier(name="Atraxa", scryfall_id="12345")

    def test_deck_card_entry_valid(self):
        entry = DeckCardEntry(
            name="Sol Ring",
            quantity=1,
            oracle_id="4a58b98b-e666-41f2-850f-e23a54b321a6",
            category="Mainboard",
        )
        self.assertEqual(entry.name, "Sol Ring")
        self.assertEqual(entry.quantity, 1)
        self.assertEqual(entry.category, "Mainboard")

    def test_deck_card_entry_default_quantity(self):
        entry = DeckCardEntry(name="Command Tower")
        self.assertEqual(entry.quantity, 1)

    def test_deck_card_entry_rejects_zero_or_negative_quantity(self):
        with self.assertRaises(ValidationError):
            DeckCardEntry(name="Sol Ring", quantity=0)
        with self.assertRaises(ValidationError):
            DeckCardEntry(name="Sol Ring", quantity=-2)

    def test_deck_card_entry_rejects_empty_name(self):
        with self.assertRaises(ValidationError):
            DeckCardEntry(name="")

    def test_deck_card_entry_rejects_invalid_uuid(self):
        with self.assertRaises(ValidationError):
            DeckCardEntry(name="Sol Ring", oracle_id="invalid-uuid")

    def test_decklist_input_valid(self):
        deck = DecklistInput(
            deck_id="archidekt:12345",
            name="My Token Deck",
            commanders=[CommanderIdentifier(name="Rhys the Redeemed")],
            cards=[
                DeckCardEntry(name="Doubling Season", quantity=1),
                DeckCardEntry(name="Parallel Lives", quantity=1),
                DeckCardEntry(name="Plains", quantity=10),
            ],
            format="commander",
        )
        self.assertEqual(deck.deck_id, "archidekt:12345")
        self.assertEqual(deck.name, "My Token Deck")
        self.assertEqual(len(deck.commanders), 1)
        self.assertEqual(len(deck.cards), 3)
        self.assertEqual(deck.total_cards(include_commanders=True), 13)
        self.assertEqual(deck.total_cards(include_commanders=False), 12)
        names = deck.all_card_names(include_commanders=True)
        self.assertIn("Rhys the Redeemed", names)
        self.assertIn("Doubling Season", names)

    def test_decklist_input_rejects_empty_commanders(self):
        with self.assertRaises(ValidationError):
            DecklistInput(
                commanders=[],
                cards=[DeckCardEntry(name="Sol Ring", quantity=1)],
            )

    def test_decklist_input_rejects_empty_cards(self):
        with self.assertRaises(ValidationError):
            DecklistInput(
                commanders=[CommanderIdentifier(name="Rhys the Redeemed")],
                cards=[],
            )

    def test_decklist_input_rejects_blank_format(self):
        with self.assertRaises(ValidationError):
            DecklistInput(
                commanders=[CommanderIdentifier(name="Rhys the Redeemed")],
                cards=[DeckCardEntry(name="Sol Ring", quantity=1)],
                format="   ",
            )


class TestAnalyticsOutputModels(unittest.TestCase):
    """Test validation and behavior of output models."""

    def test_salt_card_detail_valid(self):
        detail = SaltCardDetail(
            card_name="Cyclonic Rift",
            salt_score=2.45,
            rank=1,
            oracle_id="f516a22f-d890-48e0-bb1b-01ec25ffac62",
        )
        self.assertEqual(detail.card_name, "Cyclonic Rift")
        self.assertEqual(detail.salt_score, 2.45)
        self.assertEqual(detail.rank, 1)

    def test_salt_card_detail_rejects_negative_score(self):
        with self.assertRaises(ValidationError):
            SaltCardDetail(card_name="Cyclonic Rift", salt_score=-0.5)

    def test_salt_card_detail_rejects_zero_or_negative_rank(self):
        with self.assertRaises(ValidationError):
            SaltCardDetail(card_name="Cyclonic Rift", salt_score=2.0, rank=0)

    def test_salt_score_valid(self):
        score = SaltScore(
            score=45.2,
            salt_sum=72.1,
            high_salt_cards=[
                SaltCardDetail(card_name="Cyclonic Rift", salt_score=2.45, rank=1)
            ],
            description="High salt deck",
        )
        self.assertEqual(score.score, 45.2)
        self.assertEqual(len(score.high_salt_cards), 1)

    def test_salt_score_rejects_negative_score(self):
        with self.assertRaises(ValidationError):
            SaltScore(score=-1.0)
        with self.assertRaises(ValidationError):
            SaltScore(score=10.0, salt_sum=-5.0)

    def test_power_score_valid(self):
        power = PowerScore(
            score=7.5,
            tier="Optimized",
            description="Fast ramp and strong wincons",
            breakdown={"speed": 8.0, "interaction": 7.0},
        )
        self.assertEqual(power.score, 7.5)
        self.assertEqual(power.tier, "Optimized")
        self.assertEqual(power.breakdown["speed"], 8.0)

    def test_power_score_bounds(self):
        PowerScore(score=0.0)
        PowerScore(score=10.0)
        with self.assertRaises(ValidationError):
            PowerScore(score=-0.1)
        with self.assertRaises(ValidationError):
            PowerScore(score=10.1)

    def test_meta_scores_valid(self):
        meta = MetaScores(
            salt=SaltScore(score=30.0),
            power=PowerScore(score=6.0),
            meta_rank=85.0,
            provider_metrics={"edhrec_rank": 42},
        )
        self.assertIsNotNone(meta.salt)
        self.assertIsNotNone(meta.power)
        self.assertEqual(meta.meta_rank, 85.0)
        self.assertEqual(meta.provider_metrics["edhrec_rank"], 42)

    def test_meta_scores_rejects_negative_meta_rank(self):
        with self.assertRaises(ValidationError):
            MetaScores(meta_rank=-1.0)

    def test_card_recommendation_valid(self):
        rec = CardRecommendation(
            card_name="Evolution Sage",
            oracle_id="0f63e007-88f2-491c-b25c-0c159239ba5c",
            score=0.88,
            synergy=0.65,
            inclusion_rate=0.72,
            reason="Proliferates counters on land drops.",
            categories=["Synergy", "Counters"],
        )
        self.assertEqual(rec.card_name, "Evolution Sage")
        self.assertEqual(rec.synergy, 0.65)
        self.assertEqual(rec.inclusion_rate, 0.72)

    def test_card_recommendation_synergy_bounds(self):
        CardRecommendation(card_name="Card A", synergy=-1.0)
        CardRecommendation(card_name="Card B", synergy=1.0)
        with self.assertRaises(ValidationError):
            CardRecommendation(card_name="Bad", synergy=-1.1)
        with self.assertRaises(ValidationError):
            CardRecommendation(card_name="Bad", synergy=1.1)

    def test_card_recommendation_inclusion_rate_bounds(self):
        CardRecommendation(card_name="Card A", inclusion_rate=0.0)
        CardRecommendation(card_name="Card B", inclusion_rate=1.0)
        with self.assertRaises(ValidationError):
            CardRecommendation(card_name="Bad", inclusion_rate=-0.01)
        with self.assertRaises(ValidationError):
            CardRecommendation(card_name="Bad", inclusion_rate=1.01)

    def test_card_cut_recommendation_valid(self):
        cut = CardCutRecommendation(
            card_name="Vryn Wingmare",
            synergy=-0.3,
            reason="Tax slows our spells",
        )
        self.assertEqual(cut.card_name, "Vryn Wingmare")
        self.assertEqual(cut.synergy, -0.3)

    def test_card_recommendations_collection(self):
        recs = CardRecommendations(
            items=[CardRecommendation(card_name="Good Card", score=0.9)],
            cuts=[CardCutRecommendation(card_name="Bad Card", synergy=-0.4)],
            total=1,
        )
        self.assertEqual(len(recs.items), 1)
        self.assertEqual(len(recs.cuts), 1)
        self.assertEqual(recs.total, 1)

    def test_card_recommendations_total_defaults_to_item_count(self):
        recs = CardRecommendations(
            items=[
                CardRecommendation(card_name="Good Card A"),
                CardRecommendation(card_name="Good Card B"),
            ]
        )
        self.assertEqual(recs.total, 2)

    def test_card_recommendations_empty_defaults_total_to_zero(self):
        recs = CardRecommendations()
        self.assertEqual(recs.total, 0)

    def test_synergy_metric_valid(self):
        syn = SynergyMetric(
            card_name="Deepglow Skate",
            synergy_score=0.55,
            commander_name="Atraxa, Praetors' Voice",
            context="+55% synergy",
        )
        self.assertEqual(syn.card_name, "Deepglow Skate")
        self.assertEqual(syn.synergy_score, 0.55)

    def test_synergy_metric_bounds(self):
        with self.assertRaises(ValidationError):
            SynergyMetric(card_name="Bad", synergy_score=1.5)
        with self.assertRaises(ValidationError):
            SynergyMetric(card_name="Bad", synergy_score=-1.5)

    def test_deck_synergy_valid(self):
        deck_syn = DeckSynergy(
            overall_synergy=0.42,
            card_synergies=[
                SynergyMetric(card_name="Deepglow Skate", synergy_score=0.55)
            ],
        )
        self.assertEqual(deck_syn.overall_synergy, 0.42)
        self.assertEqual(len(deck_syn.card_synergies), 1)

    def test_deck_synergy_bounds(self):
        with self.assertRaises(ValidationError):
            DeckSynergy(overall_synergy=1.5)

    def test_popularity_metric_valid(self):
        pop = PopularityMetric(
            card_name="Sol Ring",
            deck_count=850000,
            percentage=85.0,
            rank=1,
        )
        self.assertEqual(pop.card_name, "Sol Ring")
        self.assertEqual(pop.deck_count, 850000)
        self.assertEqual(pop.percentage, 85.0)

    def test_popularity_metric_bounds(self):
        with self.assertRaises(ValidationError):
            PopularityMetric(card_name="Sol Ring", percentage=-1.0)
        with self.assertRaises(ValidationError):
            PopularityMetric(card_name="Sol Ring", percentage=101.0)
        with self.assertRaises(ValidationError):
            PopularityMetric(card_name="Sol Ring", deck_count=-5)

    def test_deck_popularity_valid(self):
        deck_pop = DeckPopularity(
            rank=5,
            num_decks=15000,
            popularity_percentile=98.5,
            card_popularity=[
                PopularityMetric(card_name="Sol Ring", percentage=85.0)
            ],
        )
        self.assertEqual(deck_pop.rank, 5)
        self.assertEqual(deck_pop.num_decks, 15000)
        self.assertEqual(deck_pop.popularity_percentile, 98.5)

    def test_deck_analytics_result_valid(self):
        result = DeckAnalyticsResult(
            provider_name="edhrec",
            timestamp="2026-09-29T12:00:00Z",
            meta_scores=MetaScores(power=PowerScore(score=7.0)),
            recommendations=CardRecommendations(items=[]),
            synergy=DeckSynergy(overall_synergy=0.35),
            popularity=DeckPopularity(rank=10),
            raw_metadata={"version": "1.0"},
        )
        self.assertEqual(result.provider_name, "edhrec")
        self.assertIsNotNone(result.meta_scores)
        self.assertIsNotNone(result.recommendations)
        self.assertIsNotNone(result.synergy)
        self.assertIsNotNone(result.popularity)
        self.assertEqual(result.raw_metadata["version"], "1.0")

    def test_deck_analytics_result_serialization_round_trip(self):
        result = DeckAnalyticsResult(
            provider_name="commandersalt",
            meta_scores=MetaScores(
                salt=SaltScore(score=22.5, salt_sum=40.0),
                power=PowerScore(score=6.5),
            ),
        )
        dumped_dict = result.model_dump()
        self.assertEqual(dumped_dict["provider_name"], "commandersalt")
        self.assertEqual(dumped_dict["meta_scores"]["salt"]["score"], 22.5)

        reloaded = DeckAnalyticsResult.model_validate(dumped_dict)
        self.assertEqual(reloaded.provider_name, "commandersalt")
        self.assertEqual(reloaded.meta_scores.salt.score, 22.5)


class ConcreteProviderStub(BaseAnalyticsProvider):
    """Minimal concrete stub satisfying the abstract provider interface contract."""

    @property
    def name(self) -> str:
        return "stub_analytics"

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        return MetaScores(
            salt=SaltScore(score=15.0, salt_sum=25.0),
            power=PowerScore(score=6.0, tier="Mid-Power"),
        )

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        return CardRecommendations(
            items=[
                CardRecommendation(
                    card_name="Cultivate",
                    score=0.9,
                    synergy=0.4,
                    inclusion_rate=0.6,
                    reason="Ramp staple",
                )
            ],
            cuts=[
                CardCutRecommendation(card_name="Darksteel Ingot", synergy=-0.2)
            ],
            total=1,
        )

    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        return DeckSynergy(
            overall_synergy=0.35,
            card_synergies=[
                SynergyMetric(
                    card_name="Cultivate",
                    synergy_score=0.4,
                    commander_name=deck.commanders[0].name,
                )
            ],
        )

    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        return DeckPopularity(
            rank=42,
            num_decks=5200,
            popularity_percentile=80.0,
            card_popularity=[
                PopularityMetric(card_name="Sol Ring", deck_count=5000, percentage=96.1)
            ],
        )


class TestAnalyticsProviderInterface(unittest.TestCase):
    """Verify abstract interface enforcement and concrete provider contract."""

    def test_abstract_class_cannot_be_instantiated(self):
        with self.assertRaises(TypeError):
            BaseAnalyticsProvider()

    def test_incomplete_subclass_cannot_be_instantiated(self):
        class IncompleteProvider(BaseAnalyticsProvider):
            @property
            def name(self) -> str:
                return "incomplete"

        with self.assertRaises(TypeError):
            IncompleteProvider()

    def test_concrete_provider_stub_instantiation_and_contract(self):
        provider = ConcreteProviderStub()
        self.assertEqual(provider.name, "stub_analytics")

        deck = DecklistInput(
            deck_id="test:1",
            name="Test Deck",
            commanders=[CommanderIdentifier(name="Merieke Ri Berit")],
            cards=[DeckCardEntry(name="Sol Ring", quantity=1)],
        )

        # 1. get_meta_scores
        meta = provider.get_meta_scores(deck)
        self.assertIsInstance(meta, MetaScores)
        self.assertEqual(meta.salt.score, 15.0)
        self.assertEqual(meta.power.score, 6.0)

        # 2. get_recommendations
        recs = provider.get_recommendations(deck)
        self.assertIsInstance(recs, CardRecommendations)
        self.assertEqual(len(recs.items), 1)
        self.assertEqual(recs.items[0].card_name, "Cultivate")

        # 3. get_synergy
        synergy = provider.get_synergy(deck)
        self.assertIsInstance(synergy, DeckSynergy)
        self.assertEqual(synergy.overall_synergy, 0.35)

        # 4. get_popularity
        pop = provider.get_popularity(deck)
        self.assertIsInstance(pop, DeckPopularity)
        self.assertEqual(pop.rank, 42)

        # 5. analyze_deck (aggregated method)
        result = provider.analyze_deck(deck)
        self.assertIsInstance(result, DeckAnalyticsResult)
        self.assertEqual(result.provider_name, "stub_analytics")
        self.assertEqual(result.meta_scores.power.score, 6.0)
        self.assertEqual(len(result.recommendations.items), 1)
        self.assertEqual(result.synergy.overall_synergy, 0.35)
        self.assertEqual(result.popularity.rank, 42)

    def test_supports_query_check(self):
        provider = ConcreteProviderStub()
        self.assertTrue(provider.supports_query("meta_scores"))
        self.assertTrue(provider.supports_query("recommendations"))
        self.assertTrue(provider.supports_query("synergy"))
        self.assertTrue(provider.supports_query("popularity"))
        self.assertFalse(provider.supports_query("non_existent_metric"))


class TestAnalyticsProviderRegistry(unittest.TestCase):
    """Test the pluggable registration mechanism for analytics providers."""

    def setUp(self):
        self.registry = AnalyticsProviderRegistry()

    def test_register_and_get_by_class(self):
        self.registry.register(ConcreteProviderStub, name="my_stub")
        provider = self.registry.get("my_stub")
        self.assertIsInstance(provider, ConcreteProviderStub)
        self.assertEqual(provider.name, "stub_analytics")

    def test_register_and_get_by_instance(self):
        instance = ConcreteProviderStub()
        self.registry.register(instance, name="instance_stub")
        retrieved = self.registry.get("instance_stub")
        self.assertIs(retrieved, instance)

    def test_register_as_decorator_without_args(self):
        @self.registry.register
        class DecoratedProvider(ConcreteProviderStub):
            @property
            def name(self) -> str:
                return "decorated"

        retrieved = self.registry.get("decorated")
        self.assertIsInstance(retrieved, DecoratedProvider)

    def test_register_as_decorator_with_name(self):
        @self.registry.register(name="custom_decorated")
        class DecoratedWithName(ConcreteProviderStub):
            pass

        retrieved = self.registry.get("custom_decorated")
        self.assertIsInstance(retrieved, DecoratedWithName)

    def test_case_insensitive_lookup(self):
        self.registry.register(ConcreteProviderStub, name="StubProvider")
        self.assertIsInstance(self.registry.get("stubprovider"), ConcreteProviderStub)
        self.assertIsInstance(self.registry.get("STUBPROVIDER"), ConcreteProviderStub)
        self.assertIsInstance(self.registry.get("StubProvider"), ConcreteProviderStub)

    def test_list_providers(self):
        self.registry.register(ConcreteProviderStub, name="BetaProvider")
        self.registry.register(ConcreteProviderStub, name="AlphaProvider")
        names = self.registry.list_providers()
        self.assertEqual(names, ["AlphaProvider", "BetaProvider"])

    def test_unregister_provider(self):
        self.registry.register(ConcreteProviderStub, name="to_remove")
        self.assertIn("to_remove", self.registry.list_providers())
        self.assertTrue(self.registry.unregister("to_remove"))
        self.assertNotIn("to_remove", self.registry.list_providers())
        self.assertFalse(self.registry.unregister("to_remove"))

        with self.assertRaises(ProviderNotFoundError):
            self.registry.get("to_remove")

    def test_get_nonexistent_provider_raises_error(self):
        with self.assertRaises(ProviderNotFoundError) as ctx:
            self.registry.get("non_existent")
        self.assertIn("non_existent", str(ctx.exception))

    def test_duplicate_registration_without_overwrite_raises_error(self):
        self.registry.register(ConcreteProviderStub, name="duplicate")
        with self.assertRaises(ProviderRegistrationError):
            self.registry.register(ConcreteProviderStub, name="duplicate", overwrite=False)

    def test_duplicate_registration_with_overwrite_succeeds(self):
        self.registry.register(ConcreteProviderStub, name="duplicate")
        # Should not raise
        self.registry.register(ConcreteProviderStub, name="duplicate", overwrite=True)
        self.assertEqual(len(self.registry.list_providers()), 1)

    def test_register_invalid_class_raises_error(self):
        class NotAProvider:
            pass

        with self.assertRaises(ProviderRegistrationError):
            self.registry.register(NotAProvider, name="invalid")

    def test_register_invalid_instance_raises_error(self):
        with self.assertRaises(ProviderRegistrationError):
            self.registry.register("not an object instance", name="invalid")

    def test_global_registry_functions(self):
        clear_registry()
        try:
            register_provider(ConcreteProviderStub, name="global_stub")
            self.assertIn("global_stub", list_providers())
            retrieved = get_provider("global_stub")
            self.assertIsInstance(retrieved, ConcreteProviderStub)
            self.assertTrue(unregister_provider("global_stub"))
            self.assertNotIn("global_stub", list_providers())
        finally:
            clear_registry()


if __name__ == "__main__":
    unittest.main()
