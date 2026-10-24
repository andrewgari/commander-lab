"""RedisCacheAdapter satisfies the storage-neutral Cache interface."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.repository_interfaces import Cache
from redis_cache import RedisCache, RedisCacheAdapter
from tests.test_redis_cache import FakeRedis


def _adapter():
    return RedisCacheAdapter(RedisCache(client=FakeRedis(), namespace="t"))


def test_is_cache():
    assert isinstance(_adapter(), Cache)


def test_put_get_delete_roundtrip():
    c = _adapter()
    assert c.get("k") is None
    c.put("k", {"a": 1})
    assert c.get("k") == {"a": 1}
    assert c.delete("k") is True
    assert c.delete("k") is False
    assert c.get("k") is None


def test_put_with_ttl():
    c = _adapter()
    c.put("k", 1, ttl_seconds=60)
    assert c._c.ttl("k") > 0
