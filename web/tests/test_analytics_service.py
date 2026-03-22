"""Tests for analytics_service — orchestration of math + DB + Plotly specs."""

import json
import sqlite3

from app.cache import cache_clear
from app.services.analytics_service import CHART_TYPES, get_chart, get_stats


def _make_conn() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with the reviews table schema."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE reviews (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_slug            TEXT NOT NULL,
            timestamp             TEXT NOT NULL,
            unique_review_id      TEXT UNIQUE NOT NULL,
            subjective_score      TEXT,
            tomatometer_sentiment TEXT,
            reconciled_timestamp  INTEGER NOT NULL DEFAULT 0,
            reviewer_name         TEXT,
            publication_name      TEXT,
            top_critic            INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    return conn


def _insert_review(
    conn: sqlite3.Connection,
    *,
    movie_slug: str = "test_movie",
    timestamp: str = "2026-01-01 12:00:00",
    unique_review_id: str = "abc123",
    subjective_score: str = "3/5",
    tomatometer_sentiment: str = "positive",
    reviewer_name: str = "Reviewer",
    publication_name: str = "Pub",
    top_critic: int = 0,
) -> None:
    conn.execute(
        """INSERT INTO reviews
           (movie_slug, timestamp, unique_review_id, subjective_score,
            tomatometer_sentiment, reconciled_timestamp, reviewer_name,
            publication_name, top_critic)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)""",
        (movie_slug, timestamp, unique_review_id, subjective_score,
         tomatometer_sentiment, reviewer_name, publication_name, top_critic),
    )
    conn.commit()


class TestGetChart:
    def setup_method(self):
        cache_clear()

    def test_empty_db(self):
        conn = _make_conn()
        result = get_chart(conn, "all", "tomatometer_over_time")
        spec = json.loads(result)
        assert "data" in spec
        assert "layout" in spec

    def test_all_chart_types_return_valid_json(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="r1", tomatometer_sentiment="positive")
        _insert_review(conn, unique_review_id="r2", tomatometer_sentiment="negative",
                       timestamp="2026-01-02 12:00:00")
        for chart_type, _ in CHART_TYPES:
            result = get_chart(conn, "all", chart_type)
            spec = json.loads(result)
            assert "data" in spec, f"{chart_type} missing 'data'"
            assert "layout" in spec, f"{chart_type} missing 'layout'"

    def test_movie_filter(self):
        conn = _make_conn()
        _insert_review(conn, movie_slug="movie_a", unique_review_id="a1",
                       tomatometer_sentiment="positive")
        _insert_review(conn, movie_slug="movie_b", unique_review_id="b1",
                       tomatometer_sentiment="negative")
        result = get_chart(conn, "movie_a", "tomatometer_over_time")
        spec = json.loads(result)
        # Should only have data from movie_a (positive), so score should be 100%
        if spec["data"][0]["y"]:
            assert spec["data"][0]["y"][-1] == 100.0

    def test_caching(self):
        conn = _make_conn()
        _insert_review(conn)
        r1 = get_chart(conn, "all", "tomatometer_over_time")
        r2 = get_chart(conn, "all", "tomatometer_over_time")
        assert r1 == r2

    def test_unknown_chart_falls_back(self):
        conn = _make_conn()
        result = get_chart(conn, "all", "nonexistent_chart")
        spec = json.loads(result)
        assert "data" in spec


class TestGetStats:
    def setup_method(self):
        cache_clear()

    def test_empty_db(self):
        conn = _make_conn()
        stats = get_stats(conn, "all")
        assert stats["total_reviews"] == 0
        assert stats["tomatometer"] is None
        assert stats["positive"] == 0
        assert stats["negative"] == 0

    def test_with_reviews(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="r1", tomatometer_sentiment="positive",
                       top_critic=1)
        _insert_review(conn, unique_review_id="r2", tomatometer_sentiment="negative",
                       timestamp="2026-01-02 12:00:00")
        _insert_review(conn, unique_review_id="r3", tomatometer_sentiment="positive",
                       timestamp="2026-01-02 14:00:00")
        stats = get_stats(conn, "all")
        assert stats["total_reviews"] == 3
        assert stats["tomatometer"] == 66.7
        assert stats["positive"] == 2
        assert stats["negative"] == 1
        assert stats["top_critic_pct"] == 100.0
        assert stats["top_critic_total"] == 1

    def test_movie_filter(self):
        conn = _make_conn()
        _insert_review(conn, movie_slug="movie_a", unique_review_id="a1",
                       tomatometer_sentiment="positive")
        _insert_review(conn, movie_slug="movie_b", unique_review_id="b1",
                       tomatometer_sentiment="negative")
        stats = get_stats(conn, "movie_a")
        assert stats["total_reviews"] == 1
        assert stats["tomatometer"] == 100.0

    def test_caching(self):
        conn = _make_conn()
        _insert_review(conn)
        s1 = get_stats(conn, "all")
        s2 = get_stats(conn, "all")
        assert s1 == s2
