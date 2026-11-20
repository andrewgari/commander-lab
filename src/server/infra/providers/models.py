"""Domain models for unified card data service and external integrations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


@dataclass
class CardMetadata:
    """Standardized MTG card metadata sourced from Scryfall or card database."""

    name: str
    oracle_id: Optional[str] = None
    scryfall_id: Optional[str] = None
    mana_cost: Optional[str] = None
    cmc: Optional[float] = None
    type_line: Optional[str] = None
    oracle_text: Optional[str] = None
    colors: List[str] = field(default_factory=list)
    color_identity: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    set_code: Optional[str] = None
    set_name: Optional[str] = None
    collector_number: Optional[str] = None
    rarity: Optional[str] = None
    layout: Optional[str] = None
    power: Optional[str] = None
    toughness: Optional[str] = None
    loyalty: Optional[str] = None
    image_uris: Optional[Dict[str, str]] = None
    card_faces: Optional[List[Dict[str, Any]]] = None
    prices: Optional[Dict[str, Optional[str]]] = None
    legalities: Optional[Dict[str, str]] = None
    scryfall_uri: Optional[str] = None
    is_commander_legal: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def get_image_uri(self, version: str = "normal") -> Optional[str]:
        """Retrieve image URI for specified version (e.g. 'normal', 'art_crop', 'small').

        Falls back to the first face for multi-faced cards if top-level image_uris is absent.
        """
        if self.image_uris and version in self.image_uris:
            return self.image_uris[version]
        if self.card_faces:
            for face in self.card_faces:
                if isinstance(face, dict):
                    face_uris = face.get("image_uris")
                    if isinstance(face_uris, dict) and version in face_uris:
                        return face_uris[version]
        return None

    @classmethod
    def from_scryfall_card(cls, card: Any) -> CardMetadata:
        """Construct CardMetadata from a ScryfallCard instance or mapping."""
        if hasattr(card, "raw") and hasattr(card, "name"):
            # ScryfallCard instance
            return cls(
                name=card.name,
                oracle_id=card.oracle_id,
                scryfall_id=card.id,
                mana_cost=card.mana_cost,
                cmc=card.cmc,
                type_line=card.type_line,
                oracle_text=card.oracle_text,
                colors=list(card.colors or []),
                color_identity=list(card.color_identity or []),
                keywords=list(card.keywords or []),
                set_code=card.set,
                set_name=card.set_name,
                collector_number=card.collector_number,
                rarity=card.rarity,
                layout=card.layout,
                power=card.power,
                toughness=card.toughness,
                loyalty=card.loyalty,
                image_uris=card.image_uris,
                card_faces=card.card_faces,
                prices=card.prices,
                legalities=card.legalities,
                scryfall_uri=card.scryfall_uri,
                is_commander_legal=card.is_commander_legal,
                raw=card.raw or {},
            )
        if isinstance(card, dict):
            return cls.from_dict(card)
        raise TypeError(f"Cannot construct CardMetadata from {type(card).__name__}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CardMetadata:
        """Construct CardMetadata from raw dictionary."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")

        oracle_id = data.get("oracle_id")
        card_faces = data.get("card_faces")
        if not oracle_id and card_faces and isinstance(card_faces, list):
            for face in card_faces:
                if isinstance(face, dict) and face.get("oracle_id"):
                    oracle_id = face["oracle_id"]
                    break

        cmc = data.get("cmc")
        if cmc is not None:
            try:
                cmc = float(cmc)
            except (ValueError, TypeError):
                cmc = None

        legalities = data.get("legalities") or {}
        is_commander_legal = legalities.get("commander") == "legal"

        return cls(
            name=data.get("name", ""),
            oracle_id=oracle_id,
            scryfall_id=data.get("id") or data.get("scryfall_id"),
            mana_cost=data.get("mana_cost"),
            cmc=cmc,
            type_line=data.get("type_line"),
            oracle_text=data.get("oracle_text"),
            colors=list(data.get("colors") or []),
            color_identity=list(data.get("color_identity") or []),
            keywords=list(data.get("keywords") or []),
            set_code=data.get("set") or data.get("set_code"),
            set_name=data.get("set_name"),
            collector_number=data.get("collector_number"),
            rarity=data.get("rarity"),
            layout=data.get("layout"),
            power=data.get("power"),
            toughness=data.get("toughness"),
            loyalty=data.get("loyalty"),
            image_uris=data.get("image_uris"),
            card_faces=card_faces,
            prices=data.get("prices"),
            legalities=legalities,
            scryfall_uri=data.get("scryfall_uri"),
            is_commander_legal=is_commander_legal,
            raw=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert CardMetadata to a plain dictionary."""
        return {
            "name": self.name,
            "oracle_id": self.oracle_id,
            "scryfall_id": self.scryfall_id,
            "mana_cost": self.mana_cost,
            "cmc": self.cmc,
            "type_line": self.type_line,
            "oracle_text": self.oracle_text,
            "colors": self.colors,
            "color_identity": self.color_identity,
            "keywords": self.keywords,
            "set_code": self.set_code,
            "set_name": self.set_name,
            "collector_number": self.collector_number,
            "rarity": self.rarity,
            "layout": self.layout,
            "power": self.power,
            "toughness": self.toughness,
            "loyalty": self.loyalty,
            "image_uris": self.image_uris,
            "card_faces": self.card_faces,
            "prices": self.prices,
            "legalities": self.legalities,
            "scryfall_uri": self.scryfall_uri,
            "is_commander_legal": self.is_commander_legal,
        }


@dataclass
class CardSynergy:
    """Synergy metrics for a card, sourced from EDHREC."""

    card_name: str
    synergy_score: Optional[float] = None  # Normalized synergy delta (-1.0 to 1.0)
    inclusion_rate: Optional[float] = None  # Inclusion fraction in archetype (0.0 to 1.0)
    num_decks: Optional[int] = None
    potential_decks: Optional[int] = None
    commander_name: Optional[str] = None
    rank: Optional[int] = None  # EDHRec overall rank or commander rank
    categories: List[str] = field(default_factory=list)  # e.g. ["High Synergy Cards"]
    context: Optional[str] = None
    source: str = "edhrec"
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert CardSynergy to a plain dictionary."""
        return {
            "card_name": self.card_name,
            "synergy_score": self.synergy_score,
            "inclusion_rate": self.inclusion_rate,
            "num_decks": self.num_decks,
            "potential_decks": self.potential_decks,
            "commander_name": self.commander_name,
            "rank": self.rank,
            "categories": self.categories,
            "context": self.context,
            "source": self.source,
        }


@dataclass
class CardSalt:
    """Salt rating and community metrics for a card."""

    card_name: str
    score: float = 0.0  # Salt rating value (typically 0.0 - 4.0)
    rank: Optional[int] = None
    salt_sum: Optional[float] = None
    description: Optional[str] = None
    source: str = "commandersalt"  # e.g. "commandersalt", "edhrec"
    is_fallback: bool = False
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert CardSalt to a plain dictionary."""
        return {
            "card_name": self.card_name,
            "score": self.score,
            "rank": self.rank,
            "salt_sum": self.salt_sum,
            "description": self.description,
            "source": self.source,
            "is_fallback": self.is_fallback,
        }


@dataclass
class ProviderStatus:
    """Status tracking for an external provider query."""

    provider: str
    status: str  # "success", "error", "not_found", "fallback", "skipped"
    latency_ms: Optional[float] = None
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert ProviderStatus to a plain dictionary."""
        return {
            "provider": self.provider,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


@dataclass
class UnifiedCardData:
    """Standardized aggregated card data combining Scryfall, EDHREC, and Commander Salt."""

    name: str
    oracle_id: Optional[str] = None
    scryfall_id: Optional[str] = None
    metadata: Optional[CardMetadata] = None
    synergy: Optional[CardSynergy] = None
    salt: Optional[CardSalt] = None
    provider_statuses: Dict[str, ProviderStatus] = field(default_factory=dict)
    successful_providers: List[str] = field(default_factory=list)
    failed_providers: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def has_metadata(self) -> bool:
        """True if card metadata is available."""
        return self.metadata is not None

    @property
    def has_synergy(self) -> bool:
        """True if synergy data is available."""
        return self.synergy is not None

    @property
    def has_salt(self) -> bool:
        """True if salt score is available."""
        return self.salt is not None

    @property
    def is_complete(self) -> bool:
        """True if all attempted providers succeeded without error."""
        return len(self.failed_providers) == 0 and len(self.successful_providers) > 0

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None:
                return val
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            val = getattr(self, key)
            return val if val is not None else default
        return default

    def to_dict(self) -> Dict[str, Any]:
        """Convert UnifiedCardData to a serializable dictionary."""
        return {
            "name": self.name,
            "oracle_id": self.oracle_id,
            "scryfall_id": self.scryfall_id,
            "metadata": self.metadata.to_dict() if self.metadata else None,
            "synergy": self.synergy.to_dict() if self.synergy else None,
            "salt": self.salt.to_dict() if self.salt else None,
            "provider_statuses": {k: v.to_dict() for k, v in self.provider_statuses.items()},
            "successful_providers": list(self.successful_providers),
            "failed_providers": list(self.failed_providers),
            "warnings": list(self.warnings),
            "is_complete": self.is_complete,
        }
