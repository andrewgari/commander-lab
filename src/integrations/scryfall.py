"""Scryfall API client wrapper using SharedHttpClient.

Provides:
- `ScryfallClient`: Async client for querying the Scryfall REST API.
  - `get_card_by_name`: Exact or fuzzy name lookup (/cards/named).
  - `get_card_by_exact_name`: Convenience method for exact lookup.
  - `get_card_by_fuzzy_name`: Convenience method for fuzzy lookup.
  - `get_card_by_id`: Card lookup by Scryfall UUID (/cards/{id}).
  - `search_cards`: Full Scryfall syntax query search (/cards/search).
  - `search_all_cards`: Async generator across all pages of search results.
- `ScryfallCard`: Deserialized Scryfall card domain model.
- `ScryfallSearchResult`: Deserialized Scryfall search results collection.
- Standardized exceptions: `ScryfallError`, `ScryfallNotFoundError`,
  `ScryfallRateLimitError`, `ScryfallRequestError`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, List, Mapping, Optional, Union

from src.shared.http import SharedHttpClient
from src.shared.http.exceptions import (
    ApiError,
    HttpClientError,
    RateLimitError,
    TimeoutError as HttpTimeoutError,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScryfallError(HttpClientError):
    """Base exception for all Scryfall client errors."""


class ScryfallNotFoundError(ScryfallError):
    """Raised when a card or query is not found on Scryfall (HTTP 404)."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 404,
        body: Any = None,
        url: Optional[str] = None,
    ) -> None:
        super().__init__(message, url=url)
        self.status_code = status_code
        self.body = body


class ScryfallRateLimitError(ScryfallError):
    """Raised when Scryfall rate limits the client and retries are exhausted (HTTP 429)."""

    def __init__(
        self,
        message: str,
        *,
        url: Optional[str] = None,
        retry_after: Optional[float] = None,
    ) -> None:
        super().__init__(message, url=url)
        self.retry_after = retry_after


class ScryfallRequestError(ScryfallError):
    """Raised when an API request fails with an unrecoverable non-2xx status code or network error."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        body: Any = None,
        url: Optional[str] = None,
    ) -> None:
        super().__init__(message, url=url)
        self.status_code = status_code
        self.body = body


# ---------------------------------------------------------------------------
# Domain Models
# ---------------------------------------------------------------------------


@dataclass
class ScryfallCard:
    """Deserialized Scryfall card object."""

    id: str
    name: str
    oracle_id: Optional[str] = None
    mana_cost: Optional[str] = None
    cmc: Optional[float] = None
    type_line: Optional[str] = None
    oracle_text: Optional[str] = None
    colors: List[str] = field(default_factory=list)
    color_identity: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    set: Optional[str] = None
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
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScryfallCard:
        """Construct a ScryfallCard from raw API JSON dictionary."""
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

        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            oracle_id=oracle_id,
            mana_cost=data.get("mana_cost"),
            cmc=cmc,
            type_line=data.get("type_line"),
            oracle_text=data.get("oracle_text"),
            colors=list(data.get("colors") or []),
            color_identity=list(data.get("color_identity") or []),
            keywords=list(data.get("keywords") or []),
            set=data.get("set"),
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
            legalities=data.get("legalities"),
            scryfall_uri=data.get("scryfall_uri"),
            raw=data,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Return raw Scryfall dict payload, or reconstructed dictionary."""
        if self.raw:
            return dict(self.raw)
        return {
            "id": self.id,
            "oracle_id": self.oracle_id,
            "name": self.name,
            "mana_cost": self.mana_cost,
            "cmc": self.cmc,
            "type_line": self.type_line,
            "oracle_text": self.oracle_text,
            "colors": self.colors,
            "color_identity": self.color_identity,
            "keywords": self.keywords,
            "set": self.set,
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
        }

    def __getitem__(self, key: str) -> Any:
        if key in self.raw:
            return self.raw[key]
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None:
                return val
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.raw:
            return self.raw[key]
        if hasattr(self, key):
            val = getattr(self, key)
            return val if val is not None else default
        return default

    def __contains__(self, key: str) -> bool:
        return key in self.raw or hasattr(self, key)

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

    @property
    def is_commander_legal(self) -> bool:
        """Check whether the card is legal in Commander format."""
        if not self.legalities:
            return False
        return self.legalities.get("commander") == "legal"

    @property
    def is_multi_faced(self) -> bool:
        """Check whether the card has multiple faces (transform, flip, MDFC, etc.)."""
        return bool(self.card_faces and len(self.card_faces) > 1)


@dataclass
class ScryfallSearchResult:
    """Deserialized Scryfall search results collection."""

    data: List[ScryfallCard] = field(default_factory=list)
    total_cards: int = 0
    has_more: bool = False
    next_page: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def __iter__(self):
        return iter(self.data)

    def __len__(self) -> int:
        return len(self.data)

    def __getitem__(self, index):
        return self.data[index]

    @property
    def cards(self) -> List[ScryfallCard]:
        """Alias for data list."""
        return self.data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ScryfallSearchResult:
        """Construct ScryfallSearchResult from raw API JSON dictionary."""
        if not isinstance(data, dict):
            raise TypeError(f"Expected dict, got {type(data).__name__}")
        raw_cards = data.get("data", [])
        cards = [ScryfallCard.from_dict(c) for c in raw_cards if isinstance(c, dict)]
        return cls(
            data=cards,
            total_cards=data.get("total_cards", len(cards)),
            has_more=data.get("has_more", False),
            next_page=data.get("next_page"),
            warnings=list(data.get("warnings") or []),
            raw=data,
        )


# ---------------------------------------------------------------------------
# ScryfallClient
# ---------------------------------------------------------------------------


class ScryfallClient:
    """Async API client for Scryfall using SharedHttpClient."""

    def __init__(
        self,
        http_client: Optional[SharedHttpClient] = None,
        *,
        base_url: Optional[str] = None,
        **client_kwargs: Any,
    ) -> None:
        """Initialize ScryfallClient.

        Parameters
        ----------
        http_client : Optional[SharedHttpClient]
            Existing SharedHttpClient instance. If None, one is created from the
            'scryfall' configuration profile.
        base_url : Optional[str]
            Optional base URL override (default: https://api.scryfall.com).
        **client_kwargs : Any
            Additional keyword arguments passed to SharedHttpClient.from_config.
        """
        if http_client is not None:
            self._http_client = http_client
            self._owns_http_client = False
        else:
            kwargs = dict(client_kwargs)
            if base_url is not None:
                kwargs["base_url"] = base_url
            self._http_client = SharedHttpClient.from_config("scryfall", **kwargs)
            self._owns_http_client = True

    @property
    def http_client(self) -> SharedHttpClient:
        """Return the underlying SharedHttpClient."""
        return self._http_client

    async def __aenter__(self) -> ScryfallClient:
        return self

    async def __aexit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[Any],
    ) -> None:
        await self.close()

    async def aclose(self) -> None:
        """Close the underlying client if owned."""
        if self._owns_http_client:
            await self._http_client.aclose()

    async def close(self) -> None:
        """Alias for aclose."""
        await self.aclose()

    @staticmethod
    def _parse_retry_after(headers: Optional[Mapping[str, str]]) -> Optional[float]:
        """Extract Retry-After header in seconds."""
        if not headers:
            return None
        val = headers.get("Retry-After") or headers.get("retry-after")
        if val is None:
            return None
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    async def get_card_by_name(
        self,
        exact: Optional[str] = None,
        *,
        fuzzy: Optional[str] = None,
        set: Optional[str] = None,
        raise_on_not_found: bool = False,
        raise_for_not_found: bool = False,
    ) -> Optional[ScryfallCard]:
        """Look up a card by exact or fuzzy name (/cards/named).

        Parameters
        ----------
        exact : Optional[str]
            The exact card name to search for (case-insensitive).
        fuzzy : Optional[str]
            A fuzzy card name to search for.
        set : Optional[str]
            Optional set code to limit the search.
        raise_on_not_found : bool
            If True, raise ScryfallNotFoundError on 404. Default False (returns None).
        raise_for_not_found : bool
            Alias for raise_on_not_found.

        Returns
        -------
        Optional[ScryfallCard]
            Deserialized card object, or None if not found and raise_on_not_found=False.
        """
        if exact is None and fuzzy is None:
            raise ValueError("Either 'exact' or 'fuzzy' must be specified.")
        if exact is not None and fuzzy is not None:
            raise ValueError("Only one of 'exact' or 'fuzzy' may be specified.")

        params: Dict[str, Any] = {}
        lookup_term = exact if exact is not None else fuzzy
        if exact is not None:
            params["exact"] = exact
        else:
            params["fuzzy"] = fuzzy

        if set is not None:
            params["set"] = set

        should_raise_not_found = raise_on_not_found or raise_for_not_found

        try:
            resp = await self._http_client.get("/cards/named", params=params)
            if resp.status_code == 404:
                if should_raise_not_found:
                    raise ScryfallNotFoundError(
                        f"Scryfall card not found: {lookup_term!r}",
                        status_code=404,
                        body=resp.text,
                        url=str(resp.url),
                    )
                return None
            if resp.status_code == 429:
                retry_after = self._parse_retry_after(resp.headers)
                raise ScryfallRateLimitError(
                    f"Scryfall rate limited (429) for card {lookup_term!r}",
                    url=str(resp.url),
                    retry_after=retry_after,
                )
            if resp.status_code >= 400:
                raise ScryfallRequestError(
                    f"Scryfall API returned status {resp.status_code}",
                    status_code=resp.status_code,
                    body=resp.text,
                    url=str(resp.url),
                )
            return ScryfallCard.from_dict(resp.json())
        except ApiError as exc:
            if exc.status_code == 404:
                if should_raise_not_found:
                    raise ScryfallNotFoundError(
                        f"Scryfall card not found: {lookup_term!r}",
                        status_code=404,
                        body=exc.body,
                        url=exc.url,
                    ) from exc
                return None
            raise ScryfallRequestError(
                exc.message,
                status_code=exc.status_code,
                body=exc.body,
                url=exc.url,
            ) from exc
        except RateLimitError as exc:
            raise ScryfallRateLimitError(
                exc.message,
                url=exc.url,
                retry_after=exc.retry_after,
            ) from exc
        except HttpTimeoutError as exc:
            raise ScryfallRequestError(
                str(exc),
                status_code=408,
                url=exc.url,
            ) from exc
        except HttpClientError as exc:
            raise ScryfallRequestError(
                str(exc),
                status_code=500,
                url=exc.url,
            ) from exc

    async def get_card_by_exact_name(
        self,
        name: str,
        *,
        set: Optional[str] = None,
        raise_on_not_found: bool = False,
        raise_for_not_found: bool = False,
    ) -> Optional[ScryfallCard]:
        """Convenience method for exact card name lookup."""
        return await self.get_card_by_name(
            exact=name,
            set=set,
            raise_on_not_found=raise_on_not_found,
            raise_for_not_found=raise_for_not_found,
        )

    async def get_card_by_fuzzy_name(
        self,
        name: str,
        *,
        set: Optional[str] = None,
        raise_on_not_found: bool = False,
        raise_for_not_found: bool = False,
    ) -> Optional[ScryfallCard]:
        """Convenience method for fuzzy card name lookup."""
        return await self.get_card_by_name(
            fuzzy=name,
            set=set,
            raise_on_not_found=raise_on_not_found,
            raise_for_not_found=raise_for_not_found,
        )

    async def get_card_by_id(
        self,
        id: str,
        *,
        raise_on_not_found: bool = False,
        raise_for_not_found: bool = False,
    ) -> Optional[ScryfallCard]:
        """Look up a card by its Scryfall UUID (/cards/{id})."""
        if not id:
            raise ValueError("Card id must be specified.")
        card_id = id.strip()
        should_raise_not_found = raise_on_not_found or raise_for_not_found

        try:
            resp = await self._http_client.get(f"/cards/{card_id}")
            if resp.status_code == 404:
                if should_raise_not_found:
                    raise ScryfallNotFoundError(
                        f"Scryfall card not found by id: {card_id!r}",
                        status_code=404,
                        body=resp.text,
                        url=str(resp.url),
                    )
                return None
            if resp.status_code == 429:
                retry_after = self._parse_retry_after(resp.headers)
                raise ScryfallRateLimitError(
                    f"Scryfall rate limited (429) for id {card_id!r}",
                    url=str(resp.url),
                    retry_after=retry_after,
                )
            if resp.status_code >= 400:
                raise ScryfallRequestError(
                    f"Scryfall API returned status {resp.status_code}",
                    status_code=resp.status_code,
                    body=resp.text,
                    url=str(resp.url),
                )
            return ScryfallCard.from_dict(resp.json())
        except ApiError as exc:
            if exc.status_code == 404:
                if should_raise_not_found:
                    raise ScryfallNotFoundError(
                        f"Scryfall card not found by id: {card_id!r}",
                        status_code=404,
                        body=exc.body,
                        url=exc.url,
                    ) from exc
                return None
            raise ScryfallRequestError(
                exc.message,
                status_code=exc.status_code,
                body=exc.body,
                url=exc.url,
            ) from exc
        except RateLimitError as exc:
            raise ScryfallRateLimitError(
                exc.message,
                url=exc.url,
                retry_after=exc.retry_after,
            ) from exc
        except HttpTimeoutError as exc:
            raise ScryfallRequestError(
                str(exc),
                status_code=408,
                url=exc.url,
            ) from exc
        except HttpClientError as exc:
            raise ScryfallRequestError(
                str(exc),
                status_code=500,
                url=exc.url,
            ) from exc

    async def search_cards(
        self,
        query: Optional[str] = None,
        *,
        q: Optional[str] = None,
        page: int = 1,
        unique: Optional[str] = None,
        order: Optional[str] = None,
        dir: Optional[str] = None,
        include_extras: Optional[bool] = None,
        include_multilingual: Optional[bool] = None,
        include_variations: Optional[bool] = None,
        raise_on_not_found: bool = False,
        raise_for_not_found: bool = False,
    ) -> ScryfallSearchResult:
        """Search cards using full Scryfall syntax query (/cards/search).

        Parameters
        ----------
        query : Optional[str]
            Scryfall query string (positional or keyword).
        q : Optional[str]
            Alternative query parameter name.
        page : int
            Result page number (1-indexed).
        unique : Optional[str]
            De-duplication strategy ('cards', 'art', 'prints').
        order : Optional[str]
            Order field ('name', 'set', 'released', 'cmc', etc.).
        dir : Optional[str]
            Sort direction ('auto', 'asc', 'desc').
        include_extras : Optional[bool]
            Include tokens, emblems, etc.
        include_multilingual : Optional[bool]
            Include all printed languages.
        include_variations : Optional[bool]
            Include rare variations.
        raise_on_not_found : bool
            If True, raise ScryfallNotFoundError when query matches 0 cards.
            Default is False (returns empty ScryfallSearchResult).
        raise_for_not_found : bool
            Alias for raise_on_not_found.

        Returns
        -------
        ScryfallSearchResult
            Collection containing cards, total_cards, has_more, and warnings.
        """
        search_query = query if query is not None else q
        if not search_query:
            raise ValueError("Search query must be provided.")

        params: Dict[str, Any] = {"q": search_query}
        if page > 1:
            params["page"] = page
        if unique is not None:
            params["unique"] = unique
        if order is not None:
            params["order"] = order
        if dir is not None:
            params["dir"] = dir
        if include_extras is not None:
            params["include_extras"] = "true" if include_extras else "false"
        if include_multilingual is not None:
            params["include_multilingual"] = "true" if include_multilingual else "false"
        if include_variations is not None:
            params["include_variations"] = "true" if include_variations else "false"

        should_raise_not_found = raise_on_not_found or raise_for_not_found

        try:
            resp = await self._http_client.get("/cards/search", params=params)
            if resp.status_code == 404:
                if should_raise_not_found:
                    raise ScryfallNotFoundError(
                        f"No cards found matching query: {search_query!r}",
                        status_code=404,
                        body=resp.text,
                        url=str(resp.url),
                    )
                return ScryfallSearchResult(
                    data=[],
                    total_cards=0,
                    has_more=False,
                    raw={"object": "list", "total_cards": 0, "has_more": False, "data": []},
                )
            if resp.status_code == 429:
                retry_after = self._parse_retry_after(resp.headers)
                raise ScryfallRateLimitError(
                    f"Scryfall rate limited (429) for search {search_query!r}",
                    url=str(resp.url),
                    retry_after=retry_after,
                )
            if resp.status_code >= 400:
                raise ScryfallRequestError(
                    f"Scryfall API returned status {resp.status_code}",
                    status_code=resp.status_code,
                    body=resp.text,
                    url=str(resp.url),
                )
            return ScryfallSearchResult.from_dict(resp.json())
        except ApiError as exc:
            if exc.status_code == 404:
                if should_raise_not_found:
                    raise ScryfallNotFoundError(
                        f"No cards found matching query: {search_query!r}",
                        status_code=404,
                        body=exc.body,
                        url=exc.url,
                    ) from exc
                return ScryfallSearchResult(
                    data=[],
                    total_cards=0,
                    has_more=False,
                    raw=exc.body if isinstance(exc.body, dict) else {},
                )
            raise ScryfallRequestError(
                exc.message,
                status_code=exc.status_code,
                body=exc.body,
                url=exc.url,
            ) from exc
        except RateLimitError as exc:
            raise ScryfallRateLimitError(
                exc.message,
                url=exc.url,
                retry_after=exc.retry_after,
            ) from exc
        except HttpTimeoutError as exc:
            raise ScryfallRequestError(
                str(exc),
                status_code=408,
                url=exc.url,
            ) from exc
        except HttpClientError as exc:
            raise ScryfallRequestError(
                str(exc),
                status_code=500,
                url=exc.url,
            ) from exc

    async def search_all_cards(
        self,
        query: Optional[str] = None,
        *,
        q: Optional[str] = None,
        unique: Optional[str] = None,
        order: Optional[str] = None,
        dir: Optional[str] = None,
        include_extras: Optional[bool] = None,
        include_multilingual: Optional[bool] = None,
        include_variations: Optional[bool] = None,
    ) -> AsyncIterator[ScryfallCard]:
        """Iterate over all cards matching the query across all result pages."""
        page = 1
        while True:
            result = await self.search_cards(
                query=query,
                q=q,
                page=page,
                unique=unique,
                order=order,
                dir=dir,
                include_extras=include_extras,
                include_multilingual=include_multilingual,
                include_variations=include_variations,
                raise_on_not_found=False,
            )
            for card in result.data:
                yield card
            if not result.has_more or not result.data:
                break
            page += 1
