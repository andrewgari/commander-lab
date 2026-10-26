"""Thin FastAPI routers over domain/infra (docs/ARCHITECTURE.md §2 rule 2).

Slice 1 routers are mounted under /api/v2 so the Postgres-backed read models
coexist with the legacy Redis-backed /api/* routes during the transition.
"""
