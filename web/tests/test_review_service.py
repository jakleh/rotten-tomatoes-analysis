"""Tests for review_service — paginated queries against the reviews table."""

import sqlite3

from app.services.review_service import ReviewPage, get_reviews_page


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
    reviewer_name: str = "Reviewer",
    publication_name: str = "Pub",
    subjective_score: str = "3/5",
    tomatometer_sentiment: str = "positive",
    reconciled_timestamp: int = 0,
    top_critic: int = 0,
) -> None:
    conn.execute(
        """INSERT INTO reviews
           (movie_slug, timestamp, unique_review_id, subjective_score,
            tomatometer_sentiment, reconciled_timestamp, reviewer_name,
            publication_name, top_critic)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            movie_slug,
            timestamp,
            unique_review_id,
            subjective_score,
            tomatometer_sentiment,
            reconciled_timestamp,
            reviewer_name,
            publication_name,
            top_critic,
        ),
    )
    conn.commit()


class TestReviewPage:
    def test_total_pages_zero(self):
        page = ReviewPage(reviews=[], page=1, per_page=25, total=0)
        assert page.total_pages == 1

    def test_total_pages_exact(self):
        page = ReviewPage(reviews=[], page=1, per_page=10, total=30)
        assert page.total_pages == 3

    def test_total_pages_remainder(self):
        page = ReviewPage(reviews=[], page=1, per_page=10, total=31)
        assert page.total_pages == 4

    def test_has_prev_first_page(self):
        page = ReviewPage(reviews=[], page=1, per_page=10, total=50)
        assert page.has_prev is False

    def test_has_prev_second_page(self):
        page = ReviewPage(reviews=[], page=2, per_page=10, total=50)
        assert page.has_prev is True

    def test_has_next_last_page(self):
        page = ReviewPage(reviews=[], page=5, per_page=10, total=50)
        assert page.has_next is False

    def test_has_next_not_last(self):
        page = ReviewPage(reviews=[], page=4, per_page=10, total=50)
        assert page.has_next is True


class TestGetReviewsPage:
    def test_empty_db(self):
        conn = _make_conn()
        result = get_reviews_page(conn)
        assert result.total == 0
        assert result.reviews == []
        assert result.page == 1

    def test_single_review(self):
        conn = _make_conn()
        _insert_review(conn)
        result = get_reviews_page(conn)
        assert result.total == 1
        assert len(result.reviews) == 1
        assert result.reviews[0]["reviewer_name"] == "Reviewer"

    def test_pagination(self):
        conn = _make_conn()
        for i in range(15):
            _insert_review(
                conn,
                unique_review_id=f"id_{i}",
                timestamp=f"2026-01-01 {i:02d}:00:00",
            )
        # First page of 10
        result = get_reviews_page(conn, page=1, per_page=10)
        assert len(result.reviews) == 10
        assert result.total == 15
        assert result.has_next is True
        assert result.has_prev is False
        # Second page
        result2 = get_reviews_page(conn, page=2, per_page=10)
        assert len(result2.reviews) == 5
        assert result2.has_next is False
        assert result2.has_prev is True

    def test_newest_first(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="old", timestamp="2026-01-01 01:00:00")
        _insert_review(conn, unique_review_id="new", timestamp="2026-01-01 23:00:00")
        result = get_reviews_page(conn)
        assert result.reviews[0]["timestamp"] == "2026-01-01 23:00:00"
        assert result.reviews[1]["timestamp"] == "2026-01-01 01:00:00"

    def test_movie_filter(self):
        conn = _make_conn()
        _insert_review(conn, movie_slug="movie_a", unique_review_id="a1")
        _insert_review(conn, movie_slug="movie_b", unique_review_id="b1")
        _insert_review(conn, movie_slug="movie_a", unique_review_id="a2")
        result = get_reviews_page(conn, movie="movie_a")
        assert result.total == 2
        assert all(r["movie_slug"] == "movie_a" for r in result.reviews)

    def test_movie_filter_all(self):
        conn = _make_conn()
        _insert_review(conn, movie_slug="movie_a", unique_review_id="a1")
        _insert_review(conn, movie_slug="movie_b", unique_review_id="b1")
        result = get_reviews_page(conn, movie="all")
        assert result.total == 2

    def test_page_beyond_range(self):
        conn = _make_conn()
        _insert_review(conn)
        result = get_reviews_page(conn, page=999)
        assert result.reviews == []
        assert result.total == 1
        assert result.page == 999

    def test_clamps_per_page(self):
        conn = _make_conn()
        for i in range(200):
            _insert_review(conn, unique_review_id=f"id_{i}")
        result = get_reviews_page(conn, per_page=200)
        # Should be clamped to 100
        assert len(result.reviews) == 100

    def test_clamps_negative_page(self):
        conn = _make_conn()
        _insert_review(conn)
        result = get_reviews_page(conn, page=-5)
        assert result.page == 1
        assert len(result.reviews) == 1
