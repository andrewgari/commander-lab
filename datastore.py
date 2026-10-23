"""Read/write switch for the Redis datastore.

``DATA_ACCESS_MODE=readonly`` disables every Redis write; anything else
(default ``readwrite``) leaves behaviour unchanged. The env var is read on
each call so it can be toggled in tests without reloading modules.
"""
from __future__ import annotations

import os

__all__ = ["WritesDisabledError", "is_readonly", "assert_readwrite", "guard_redis"]

ERROR_CODE = "writes_disabled"

# Redis commands that mutate state.
WRITE_COMMANDS = frozenset({
    "set", "setex", "psetex", "setnx", "mset", "msetnx", "append", "getset", "getdel",
    "setrange", "incr", "incrby", "incrbyfloat", "decr", "decrby",
    "delete", "unlink", "expire", "pexpire", "expireat", "pexpireat", "persist",
    "rename", "renamenx", "copy", "move", "flushdb", "flushall", "restore",
    "hset", "hmset", "hsetnx", "hdel", "hincrby", "hincrbyfloat",
    "lpush", "rpush", "lpushx", "rpushx", "lpop", "rpop", "lset", "lrem", "ltrim",
    "linsert", "lmove", "blpop", "brpop",
    "sadd", "srem", "spop", "smove", "sinterstore", "sunionstore", "sdiffstore",
    "zadd", "zrem", "zincrby", "zpopmin", "zpopmax", "zremrangebyrank",
    "zremrangebyscore", "zremrangebylex", "zunionstore", "zinterstore",
    "xadd", "xdel", "xtrim", "eval", "evalsha", "script_load",
})


class WritesDisabledError(RuntimeError):
    """Raised when a datastore write is attempted in readonly mode."""

    code = ERROR_CODE

    def __init__(self, message: str = "Datastore writes are disabled (DATA_ACCESS_MODE=readonly)"):
        super().__init__(message)


def is_readonly() -> bool:
    return os.getenv("DATA_ACCESS_MODE", "readwrite").strip().lower() == "readonly"


def assert_readwrite() -> None:
    if is_readonly():
        raise WritesDisabledError()


class _GuardedPipeline:
    def __init__(self, pipe):
        self._pipe = pipe

    def __getattr__(self, name):
        attr = getattr(self._pipe, name)
        if name in WRITE_COMMANDS and callable(attr):
            def guarded(*a, **kw):
                assert_readwrite()
                return attr(*a, **kw)
            return guarded
        return attr

    def __enter__(self):
        self._pipe.__enter__()
        return self

    def __exit__(self, *exc):
        return self._pipe.__exit__(*exc)


class _GuardedRedis:
    """Proxy that raises WritesDisabledError before any write command is sent."""

    def __init__(self, client):
        object.__setattr__(self, "_client", client)

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            return attr
        if name in WRITE_COMMANDS:
            def guarded(*a, **kw):
                assert_readwrite()
                return attr(*a, **kw)
            return guarded
        if name in ("pipeline", "transaction"):
            return lambda *a, **kw: _GuardedPipeline(attr(*a, **kw))
        return attr


def guard_redis(client):
    """Wrap a redis client so writes raise in readonly mode (idempotent)."""
    return client if isinstance(client, _GuardedRedis) else _GuardedRedis(client)
