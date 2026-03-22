"""In-memory TTL cache keyed by (function_name, movie, params)."""

import time
from typing import Any

_DEFAULT_TTL = 60  # seconds

_store: dict[tuple, tuple[float, Any]] = {}


def cache_get(key: tuple) -> Any | None:
    """Return cached value if present and not expired, else None."""
    entry = _store.get(key)
    if entry is None:
        return None
    expires_at, value = entry
    if time.monotonic() > expires_at:
        del _store[key]
        return None
    return value


def cache_set(key: tuple, value: Any, ttl: int = _DEFAULT_TTL) -> None:
    """Store a value with a TTL (seconds)."""
    _store[key] = (time.monotonic() + ttl, value)


def cache_clear() -> None:
    """Clear the entire cache (useful for testing)."""
    _store.clear()


def make_key(func_name: str, movie: str, **params: Any) -> tuple:
    """Build a cache key from function name, movie slug, and extra params."""
    frozen = tuple(sorted(params.items()))
    return (func_name, movie, frozen)
