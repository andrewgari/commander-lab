"""Top-level re-export for git repository status detection service."""

from src.services.repo_status import (
    DEFAULT_CACHE_TTL,
    RepoStatus,
    clear_repo_status_cache,
    enrich_decks_with_repo_status,
    get_batch_repo_status,
    get_cache_size,
    get_cache_ttl,
    get_repo_status,
    resolve_deck_repo_path,
    set_cache_ttl,
)

__all__ = [
    "DEFAULT_CACHE_TTL",
    "RepoStatus",
    "clear_repo_status_cache",
    "enrich_decks_with_repo_status",
    "get_batch_repo_status",
    "get_cache_size",
    "get_cache_ttl",
    "get_repo_status",
    "resolve_deck_repo_path",
    "set_cache_ttl",
]
