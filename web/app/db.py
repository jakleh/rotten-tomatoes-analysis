"""Read-only SQLite connection, provided as a FastAPI dependency."""

import json
import sqlite3
from collections.abc import Generator
from pathlib import Path

from app.config import DB_PATH, MOVIES_JSON_PATH


def get_connection() -> Generator[sqlite3.Connection]:
    """Yield a read-only SQLite connection scoped to a single request."""
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def load_movie_slugs() -> list[str]:
    """Read enabled movie slugs from movies.json."""
    path = Path(MOVIES_JSON_PATH)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []
    return [
        entry["slug"]
        for entry in data
        if isinstance(entry, dict)
        and entry.get("slug")
        and entry.get("enabled", True)
    ]
