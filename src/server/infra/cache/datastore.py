"""
Flag-controlled read/write guard for the Redis data access layer.

Env flag DATA_ACCESS_MODE:
    readwrite (default) - all operations allowed (current behaviour)
    readonly            - reads pass through; any mutating command raises
                          WritesDisabledError before touching Redis.

The flag is read on every call, so it can be flipped without code changes
(restart needed only if the process env itself is fixed at launch).
Invalid values raise ValueError (fail loudly, never silently fall back).
"""
import logging
import os

import redis

logger = logging.getLogger("commander_lab.datastore")

MODE_ENV = "DATA_ACCESS_MODE"
READWRITE = "readwrite"
READONLY = "readonly"
_VALID = {READWRITE, READONLY}

WRITE_COMMANDS = frozenset({
    "set", "setex", "setnx", "psetex", "mset", "msetnx", "append", "getset", "getdel",
    "incr", "incrby", "incrbyfloat", "decr", "decrby", "setrange", "setbit",
    "delete", "unlink", "expire", "pexpire", "expireat", "persist", "rename", "renamenx",
    "hset", "hmset", "hsetnx", "hdel", "hincrby", "hincrbyfloat",
    "sadd", "srem", "spop", "smove", "sinterstore", "sunionstore", "sdiffstore",
    "lpush", "rpush", "lpushx", "rpushx", "lpop", "rpop", "lrem", "lset", "ltrim",
    "linsert", "rpoplpush", "lmove", "blpop", "brpop",
    "zadd", "zrem", "zincrby", "zremrangebyscore", "zremrangebyrank", "zpopmin", "zpopmax",
    "flushdb", "flushall", "eval", "evalsha", "restore", "copy", "move", "xadd", "xdel",
})


class WritesDisabledError(RuntimeError):
    """Raised when a write is attempted while DATA_ACCESS_MODE=readonly."""

    def __init__(self, operation: str):
        self.operation = operation
        super().__init__(f"Write operation '{operation}' blocked: {MODE_ENV}={READONLY}")


def get_mode() -> str:
    mode = os.getenv(MODE_ENV, READWRITE).strip().lower() or READWRITE
    if mode not in _VALID:
        raise ValueError(f"Invalid {MODE_ENV}={mode!r}; expected one of {sorted(_VALID)}")
    return mode


def writes_enabled() -> bool:
    return get_mode() == READWRITE


def assert_writes_enabled(operation: str) -> None:
    if not writes_enabled():
        logger.warning("Blocked write operation %s (%s=%s)", operation, MODE_ENV, READONLY)
        raise WritesDisabledError(operation)


def is_readonly() -> bool:
    """True when DATA_ACCESS_MODE=readonly."""
    return not writes_enabled()


def assert_readwrite(operation: str = "write") -> None:
    """Raise WritesDisabledError in readonly mode (alias of assert_writes_enabled)."""
    assert_writes_enabled(operation)


class GuardedPipeline:
    def __init__(self, pipe):
        self._pipe = pipe

    def __getattr__(self, name):
        attr = getattr(self._pipe, name)
        if name.lower() in WRITE_COMMANDS and callable(attr):
            def guarded(*a, **kw):
                assert_writes_enabled(name)
                return attr(*a, **kw)
            return guarded
        return attr

    def __enter__(self):
        self._pipe.__enter__()
        return self

    def __exit__(self, *exc):
        return self._pipe.__exit__(*exc)


class GuardedRedis:
    """Drop-in wrapper around a redis client that enforces DATA_ACCESS_MODE."""

    def __init__(self, client):
        self._client = client

    def pipeline(self, *a, **kw):
        return GuardedPipeline(self._client.pipeline(*a, **kw))

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if name.lower() in WRITE_COMMANDS and callable(attr):
            def guarded(*a, **kw):
                assert_writes_enabled(name)
                try:
                    return attr(*a, **kw)
                except redis.RedisError:
                    logger.exception("Redis write %s failed", name)
                    raise
            return guarded
        return attr


def from_url(url: str) -> GuardedRedis:
    client = redis.from_url(url, decode_responses=True)
    logger.info("Data store initialised; %s=%s", MODE_ENV, get_mode())
    return GuardedRedis(client)
