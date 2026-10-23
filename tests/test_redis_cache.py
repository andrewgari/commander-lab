"""Tests for redis_cache.RedisCache using an in-repo fake Redis (no network)."""
import fnmatch
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from redis_cache import RedisCache


class FakeRedis:
    def __init__(self):
        self.d, self.exp, self.lock = {}, {}, threading.RLock()
        self.fail = False

    def _chk(self):
        if self.fail:
            raise redis.exceptions.ConnectionError("down")

    def _alive(self, k):
        if k in self.exp and self.exp[k] <= time.time():
            self.d.pop(k, None); self.exp.pop(k, None)
        return k in self.d

    def get(self, k):
        with self.lock:
            self._chk(); return self.d[k] if self._alive(k) else None

    def mget(self, ks):
        return [self.get(k) for k in ks]

    def set(self, k, v, px=None):
        with self.lock:
            self._chk(); self.d[k] = v
            self.exp.pop(k, None)
            if px: self.exp[k] = time.time() + px / 1000
            return True

    def delete(self, *ks):
        with self.lock:
            self._chk(); n = 0
            for k in ks:
                if self._alive(k): n += 1
                self.d.pop(k, None); self.exp.pop(k, None)
            return n
    unlink = delete

    def exists(self, k):
        with self.lock:
            self._chk(); return int(self._alive(k))

    def pexpire(self, k, ms):
        with self.lock:
            self._chk()
            if not self._alive(k): return 0
            self.exp[k] = time.time() + ms / 1000; return 1

    def persist(self, k):
        with self.lock:
            self._chk(); return int(self._alive(k) and self.exp.pop(k, None) is not None)

    def ttl(self, k):
        with self.lock:
            self._chk()
            if not self._alive(k): return -2
            if k not in self.exp: return -1
            return max(0, round(self.exp[k] - time.time()))

    def incr(self, k):
        with self.lock:
            self._chk(); v = int(self.d.get(k, 0)) + 1; self.d[k] = str(v); return v

    def scan_iter(self, match, count=None):
        with self.lock:
            self._chk()
            ks = [k for k in list(self.d) if self._alive(k)]
        # fnmatch honours the same [ ] * ? syntax; backslash escapes unsupported,
        # so strip them for the (glob-free) keys used in tests.
        pat = match.replace("\\", "")
        return iter([k for k in ks if fnmatch.fnmatchcase(k, pat)])

    def pipeline(self):
        outer = self
        class P:
            def __init__(s): s.ops = []
            def set(s, *a, **kw): s.ops.append((a, kw)); return s
            def execute(s):
                for a, kw in s.ops: outer.set(*a, **kw)
        return P()


class RedisCacheTest(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.c = RedisCache(self.r, namespace="t")

    def test_roundtrip_and_none(self):
        self.c.set("a", {"x": [1, 2, {"y": None}]})
        self.assertEqual(self.c.get("a"), {"x": [1, 2, {"y": None}]})
        self.c.set("n", None)
        self.assertTrue(self.c.exists("n"))
        self.assertEqual(self.c.get("n", "dflt"), None)
        self.assertEqual(self.c.get("missing", "dflt"), "dflt")

    def test_ttl_and_expiry(self):
        self.c.set("a", 1, ttl=100)
        self.assertGreater(self.c.ttl("a"), 90)
        self.c.set("b", 1)
        self.assertEqual(self.c.ttl("b"), -1)
        self.c.expire("b", 50); self.assertGreater(self.c.ttl("b"), 40)
        self.c.persist("b"); self.assertEqual(self.c.ttl("b"), -1)
        self.c.set("c", 1, ttl=0.01); time.sleep(0.03)
        self.assertIsNone(self.c.get("c"))
        self.assertEqual(self.c.ttl("zz"), -2)
        with self.assertRaises(ValueError): self.c.set("d", 1, ttl=0)

    def test_default_ttl_and_jitter(self):
        c = RedisCache(self.r, namespace="j", default_ttl=100, jitter=0.5)
        c.set("a", 1); self.assertGreaterEqual(c.ttl("a"), 99)

    def test_many_and_delete(self):
        self.c.set_many({"a": 1, "b": 2})
        self.assertEqual(self.c.get_many(["a", "b", "z"]), {"a": 1, "b": 2})
        self.assertEqual(self.c.delete("a", "z"), 1)
        self.assertFalse(self.c.exists("a"))

    def test_prefix_invalidation_scoped(self):
        other = RedisCache(self.r, namespace="o")
        self.r.set("deck:1", "durable")
        for k in ("deck:1", "deck:2", "card:1"): self.c.set(k, 1)
        other.set("deck:1", 1)
        self.assertEqual(self.c.invalidate_prefix("deck:"), 2)
        self.assertTrue(self.c.exists("card:1"))
        self.assertTrue(other.exists("deck:1"))
        self.assertEqual(self.r.get("deck:1"), "durable")

    def test_clear_bumps_generation(self):
        self.c.set("a", 1); other = RedisCache(self.r, namespace="o"); other.set("a", 1)
        self.c.clear()
        self.assertIsNone(self.c.get("a")); self.assertEqual(other.get("a"), 1)

    def test_corrupt_value_is_miss(self):
        self.c.set("a", 1)
        k = next(k for k in self.r.d if k.endswith(":a"))
        self.r.d[k] = "not json"
        self.assertEqual(self.c.get("a", "m"), "m")

    def test_fail_open_and_closed(self):
        self.r.fail = True
        self.assertIsNone(self.c.get("a")); self.assertFalse(self.c.set("a", 1))
        self.assertEqual(self.c.delete("a"), 0)
        with self.assertRaises(redis.exceptions.ConnectionError):
            RedisCache(self.r, namespace="t", fail_open=False).get("a")

    def test_bad_namespace(self):
        with self.assertRaises(ValueError): RedisCache(self.r, namespace="a:b")

    def test_get_or_set_single_load_under_threads(self):
        calls = []
        def loader():
            calls.append(1); time.sleep(0.05); return 42
        with ThreadPoolExecutor(16) as ex:
            res = list(ex.map(lambda _: self.c.get_or_set("k", loader), range(32)))
        self.assertEqual(set(res), {42}); self.assertEqual(len(calls), 1)
        self.assertEqual(self.c._locks, {})

    def test_concurrent_mixed_ops(self):
        errs = []
        def work(i):
            try:
                for j in range(50):
                    self.c.set(f"k{i}", j); self.assertEqual(self.c.get(f"k{i}"), j)
                    self.c.get_many([f"k{n}" for n in range(8)])
            except Exception as e: errs.append(e)
        ts = [threading.Thread(target=work, args=(i,)) for i in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
