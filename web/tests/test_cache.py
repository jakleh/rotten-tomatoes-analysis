"""Tests for the in-memory TTL cache."""

import time
from unittest.mock import patch

from app.cache import cache_clear, cache_get, cache_set, make_key


class TestMakeKey:
    def test_basic(self):
        key = make_key("chart", "movie_a", chart="tomatometer")
        assert key == ("chart", "movie_a", (("chart", "tomatometer"),))

    def test_no_params(self):
        key = make_key("stats", "all")
        assert key == ("stats", "all", ())

    def test_multiple_params_sorted(self):
        key = make_key("chart", "m", z="last", a="first")
        assert key == ("chart", "m", (("a", "first"), ("z", "last")))


class TestCacheGetSet:
    def setup_method(self):
        cache_clear()

    def test_miss_returns_none(self):
        assert cache_get(("missing",)) is None

    def test_set_then_get(self):
        cache_set(("k",), "value")
        assert cache_get(("k",)) == "value"

    def test_expired_returns_none(self):
        cache_set(("k",), "value", ttl=0)
        # monotonic has advanced past ttl=0
        assert cache_get(("k",)) is None

    def test_not_yet_expired(self):
        cache_set(("k",), "value", ttl=9999)
        assert cache_get(("k",)) == "value"

    def test_clear(self):
        cache_set(("a",), 1)
        cache_set(("b",), 2)
        cache_clear()
        assert cache_get(("a",)) is None
        assert cache_get(("b",)) is None

    def test_different_keys_independent(self):
        cache_set(("a",), "alpha")
        cache_set(("b",), "beta")
        assert cache_get(("a",)) == "alpha"
        assert cache_get(("b",)) == "beta"
