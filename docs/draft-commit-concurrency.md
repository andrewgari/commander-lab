# Draft commit concurrency

On PostgreSQL, every supported staging mutation, commit and abandonment takes a
`FOR UPDATE` lock on its draft before checking whether it is open. The lock is
held until the caller commits or rolls back. Commit reads the staged rows only
after acquiring it, so concurrent edits cannot slip into or follow a closed
draft. The locking read refreshes cached ORM rows.

Commit also locks every staged physical instance in ID order with `FOR NO KEY
UPDATE` and refreshes its current state before validating and writing the plan.
This serializes competing draft commits, avoids lock-order cycles among their
instance sets, and makes event origins reflect the preceding committed move.
If the new state makes a move illegal, the entire commit fails and the draft
remains open. An assignment that is still legal may instead move a copy again:
the staged `from_deck` is informational, not a reservation or a stale-state
precondition. No partial deck, instance update or event is committed on failure.

These guarantees assume the normal PostgreSQL READ COMMITTED isolation level
and a transaction held through the session boundary. Other writers that make
read/modify/write decisions about instances must also lock them before reading;
plain updates wait for these locks but cannot correct their own stale decisions.
Direct staging-table writes that bypass the repository must follow the draft
lock protocol. At stricter isolation, PostgreSQL can abort a conflicting
transaction with a serialization error; automatic retry is not provided.
SQLite ignores row-lock clauses and is not a concurrency substitute.

Run `TEST_DATABASE_URL=postgresql://... pytest tests/test_draft_concurrency.py`
against a disposable PostgreSQL database. The tests create and drop isolated
schemas and require permission to create schemas.
