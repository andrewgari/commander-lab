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

# Fail closed: Redis regularly gains commands, and several ostensibly read-like
# commands (GETEX, SORT ... STORE, BITFIELD, scripts) can mutate data. Do not
# expose unknown methods, client factories, or connection pools in readonly.
_READ_METHODS = frozenset({
    "get", "mget", "exists", "type", "ttl", "pttl", "expiretime", "pexpiretime",
    "strlen", "getrange", "getbit", "bitcount", "bitpos",
    "hget", "hmget", "hgetall", "hexists", "hlen", "hkeys", "hvals", "hstrlen",
    "hscan", "hscan_iter", "scard", "sismember", "smismember", "smembers",
    "srandmember", "sdiff", "sinter", "sunion", "sscan", "sscan_iter",
    "lindex", "llen", "lpos", "lrange", "zcard", "zcount", "zlexcount",
    "zrange", "zrangebyscore", "zrangebylex", "zrevrange", "zrevrangebyscore",
    "zrevrangebylex", "zrank", "zrevrank", "zscore", "zmscore", "zscan", "zscan_iter",
    "xlen", "xrange", "xrevrange", "xread", "keys", "scan", "scan_iter",
    "randomkey", "dbsize", "ping", "echo", "info", "time", "lastsave",
    "close",  # client lifecycle, not a Redis mutation
})
# Raw Redis command names do not include redis-py's iterator/helper methods.
_READ_COMMANDS = _READ_METHODS - {"close", "scan_iter", "hscan_iter", "sscan_iter", "zscan_iter"}
_PIPELINE_CONTROL = frozenset({"execute", "reset", "watch", "unwatch", "multi"})


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


def _raw_read_command(args) -> bool:
    if not args:
        return False
    command = args[0]
    if isinstance(command, bytes):
        try:
            command = command.decode("ascii")
        except UnicodeDecodeError:
            return False
    return isinstance(command, str) and command.lower() in _READ_COMMANDS


def _check_method(name, args, *, pipeline=False):
    """Check on invocation (not attribute lookup), so mode changes take effect."""
    safe = (name in _READ_METHODS or (pipeline and name in _PIPELINE_CONTROL)
            or (name == "execute_command" and _raw_read_command(args)))
    if not safe:
        assert_writes_enabled(name)
    else:
        get_mode()  # invalid mode must never silently allow a read
    return safe


class GuardedPipeline:
    def __init__(self, pipe):
        self._pipe = pipe
        self._unsafe_queued = False

    def __getattr__(self, name):
        attr = getattr(self._pipe, name)
        if not callable(attr):
            _check_method(name, (), pipeline=True)
            return attr

        def guarded(*args, **kwargs):
            safe = _check_method(name, args, pipeline=True)
            # Commands queued in readwrite mode must not escape after a flip.
            if name == "execute" and self._unsafe_queued:
                assert_writes_enabled("pipeline.execute")
            if not safe:
                self._unsafe_queued = True
            try:
                result = attr(*args, **kwargs)
            finally:
                if name in ("execute", "reset"):
                    self._unsafe_queued = False
            # redis-py pipelines return themselves to support chaining. Never
            # return the unguarded pipeline from a chained command.
            return self if result is self._pipe else result
        return guarded

    def __enter__(self):
        self._pipe.__enter__()
        return self

    def __exit__(self, *exc):
        return self._pipe.__exit__(*exc)


class GuardedRedis:
    """Wrapper around a Redis client; readonly only exposes known-safe reads."""

    def __init__(self, client):
        self._client = client

    def pipeline(self, *a, **kw):
        get_mode()
        return GuardedPipeline(self._client.pipeline(*a, **kw))

    def __getattr__(self, name):
        attr = getattr(self._client, name)
        if not callable(attr):
            _check_method(name, ())
            return attr

        def guarded(*args, **kwargs):
            safe = _check_method(name, args)
            try:
                return attr(*args, **kwargs)
            except redis.RedisError:
                if not safe:
                    logger.exception("Redis write %s failed", name)
                raise
        return guarded


def from_url(url: str) -> GuardedRedis:
    client = redis.from_url(url, decode_responses=True)
    logger.info("Data store initialised; %s=%s", MODE_ENV, get_mode())
    return GuardedRedis(client)
