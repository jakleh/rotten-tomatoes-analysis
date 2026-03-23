"""Tests for report_service — report data collection and PDF generation."""

import sqlite3

from app.cache import cache_clear
from app.services.report_service import generate_pdf, get_report_data


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


class TestGetReportData:
    def setup_method(self):
        cache_clear()

    def test_empty_db(self):
        conn = _make_conn()
        data = get_report_data(conn, "all")
        assert data["total_reviews"] == 0
        assert data["tomatometer"] is None
        assert data["movie_display"] == "All Movies"
        assert data["positive"] == 0
        assert data["negative"] == 0
        assert data["publications"] == []
        assert data["score_distribution"] == []

    def test_with_reviews(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="r1", tomatometer_sentiment="positive",
                       top_critic=1, publication_name="NYT")
        _insert_review(conn, unique_review_id="r2", tomatometer_sentiment="negative",
                       timestamp="2026-01-02 12:00:00", publication_name="LAT")
        _insert_review(conn, unique_review_id="r3", tomatometer_sentiment="positive",
                       timestamp="2026-01-02 14:00:00", publication_name="NYT")
        data = get_report_data(conn, "all")
        assert data["total_reviews"] == 3
        assert data["positive"] == 2
        assert data["negative"] == 1
        assert data["tomatometer"] == 66.7
        assert data["top_critic_pct"] == 100.0
        assert data["top_critic_total"] == 1
        assert len(data["publications"]) == 2

    def test_movie_filter(self):
        conn = _make_conn()
        _insert_review(conn, movie_slug="movie_a", unique_review_id="a1",
                       tomatometer_sentiment="positive")
        _insert_review(conn, movie_slug="movie_b", unique_review_id="b1",
                       tomatometer_sentiment="negative")
        data = get_report_data(conn, "movie_a")
        assert data["total_reviews"] == 1
        assert data["tomatometer"] == 100.0
        assert data["movie_display"] == "Movie A"

    def test_movie_display_all(self):
        conn = _make_conn()
        data = get_report_data(conn, "all")
        assert data["movie_display"] == "All Movies"

    def test_generated_at_present(self):
        conn = _make_conn()
        data = get_report_data(conn, "all")
        assert "UTC" in data["generated_at"]

    def test_caching(self):
        conn = _make_conn()
        _insert_review(conn)
        d1 = get_report_data(conn, "all")
        d2 = get_report_data(conn, "all")
        assert d1 is d2

    def test_tomatometer_points_present(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="r1", tomatometer_sentiment="positive")
        _insert_review(conn, unique_review_id="r2", tomatometer_sentiment="negative",
                       timestamp="2026-01-02 12:00:00")
        data = get_report_data(conn, "all")
        assert len(data["tomatometer_points"]) == 2

    def test_volume_buckets_present(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="r1", timestamp="2026-01-01 12:00:00")
        _insert_review(conn, unique_review_id="r2", timestamp="2026-01-02 12:00:00")
        data = get_report_data(conn, "all")
        assert len(data["volume"]) == 2

    def test_cumulative_present(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="r1")
        _insert_review(conn, unique_review_id="r2", timestamp="2026-01-02 12:00:00")
        data = get_report_data(conn, "all")
        assert len(data["cumulative"]) == 2
        assert data["cumulative"][-1]["cumulative"] == 2


class TestGeneratePdf:
    def setup_method(self):
        cache_clear()

    def test_empty_db_produces_valid_pdf(self):
        conn = _make_conn()
        result = generate_pdf(conn, "all")
        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"

    def test_with_reviews_produces_pdf(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="r1", tomatometer_sentiment="positive",
                       publication_name="NYT", subjective_score="4/5")
        _insert_review(conn, unique_review_id="r2", tomatometer_sentiment="negative",
                       timestamp="2026-01-02 12:00:00", publication_name="LAT",
                       subjective_score="2/5")
        result = generate_pdf(conn, "all")
        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"
        assert len(result) > 5000

    def test_movie_filter(self):
        conn = _make_conn()
        _insert_review(conn, movie_slug="movie_a", unique_review_id="a1",
                       tomatometer_sentiment="positive")
        _insert_review(conn, movie_slug="movie_b", unique_review_id="b1",
                       tomatometer_sentiment="negative")
        result = generate_pdf(conn, "movie_a")
        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"

    def test_single_review(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="solo", tomatometer_sentiment="positive")
        result = generate_pdf(conn, "all")
        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"

    def test_all_negative_reviews(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="n1", tomatometer_sentiment="negative")
        _insert_review(conn, unique_review_id="n2", tomatometer_sentiment="negative",
                       timestamp="2026-01-02 12:00:00")
        result = generate_pdf(conn, "all")
        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"

    def test_no_scores(self):
        conn = _make_conn()
        _insert_review(conn, unique_review_id="ns1", subjective_score="",
                       tomatometer_sentiment="positive")
        result = generate_pdf(conn, "all")
        assert isinstance(result, bytes)
        assert result[:5] == b"%PDF-"
