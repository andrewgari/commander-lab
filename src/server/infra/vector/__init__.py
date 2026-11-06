"""pgvector access layer (ARCHITECTURE.md §4.3, §7.1).

The vector store is a REBUILDABLE INDEX — a cache with a build step, never
truth. All reads and writes go through this package; the ONLY writer is
``src/server/workers/index_rebuild.py``. Nothing here connects at import
time; callers pass a SQLAlchemy Connection/Session.
"""
