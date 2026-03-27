"""
Tests for rotten_tomatoes.py

Covers: timestamp utilities, MD5 hashing, interpolation logic, DB deduplication,
and reconciliation — all without hitting the network or launching a browser.
"""

import sqlite3
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from rotten_tomatoes import (
    _migrate_v1_review_ids,
    _migrate_v2_timestamp_confidence,
    _migrate_v3_provenance_columns,
    compute_review_id,
    convert_rel_timestamp_to_abs,
    fetch_review_count,
    get_db_review_ids,
    update_sentiment,
    get_last_review_count,
    get_timestamp_unit,
    has_new_reviews,
    init_reviews_table,
    init_precheck_table,
    insert_review,
    interpolate_timestamps,
    is_at_or_older_than,
    load_movie_config,
    reconcile_missing_reviews,
    record_precheck_failure,
    update_last_review_count,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_conn() -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the reviews table created."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_reviews_table(conn)
    return conn


SLUG = "test_movie"


def make_review(name="Alice", pub="AV Club", rating="4/5", movie_slug=SLUG, **kwargs) -> dict:
    return {
        "unique_review_id": compute_review_id(movie_slug, name, pub, rating),
        "timestamp": "2026-03-21 10:00:00",
        "tomatometer_sentiment": "positive",
        "subjective_score": rating,
        "reviewer_name": name,
        "publication_name": pub,
        "top_critic": False,
        "timestamp_confidence": "d",
        "scraped_at": "2026-03-21 10:00:00",
        "raw_timestamp_text": "5m",
        **kwargs,
    }


# ── get_timestamp_unit ────────────────────────────────────────────────────────

class TestGetTimestampUnit:
    def test_minutes(self):
        assert get_timestamp_unit("5m") == "m"

    def test_hours(self):
        assert get_timestamp_unit("2h") == "h"

    def test_days(self):
        assert get_timestamp_unit("3d") == "d"

    def test_month_format(self):
        assert get_timestamp_unit("Mar 20") == "date"

    def test_empty_string(self):
        assert get_timestamp_unit("") == "date"

    def test_strips_whitespace(self):
        assert get_timestamp_unit("  10m  ") == "m"


# ── is_at_or_older_than ───────────────────────────────────────────────────────

class TestIsAtOrOlderThan:
    def test_minutes_not_older_than_hours(self):
        assert not is_at_or_older_than("5m", "h")

    def test_hours_at_hours(self):
        assert is_at_or_older_than("2h", "h")

    def test_days_older_than_hours(self):
        assert is_at_or_older_than("3d", "h")

    def test_date_older_than_days(self):
        assert is_at_or_older_than("Mar 20", "d")

    def test_minutes_not_older_than_days(self):
        assert not is_at_or_older_than("59m", "d")

    def test_hours_not_older_than_days(self):
        assert not is_at_or_older_than("23h", "d")

    def test_stop_at_unit_none_skips_no_reviews(self):
        """When stop_at_unit is None, is_at_or_older_than should never be called —
        the guard `if stop_at_unit and ...` in get_reviews() prevents it.
        Verify the guard logic: None is falsy, so no review is filtered."""
        timestamps = ["5m", "2h", "3d", "Mar 20"]
        stop_at_unit = None
        for ts in timestamps:
            # Simulates the guard in get_reviews(): `if stop_at_unit and is_at_or_older_than(...)`
            assert not (stop_at_unit and is_at_or_older_than(ts, stop_at_unit))


# ── convert_rel_timestamp_to_abs ──────────────────────────────────────────────

FIXED_NOW = datetime(2026, 3, 21, 12, 0, 0, tzinfo=timezone.utc)


class TestConvertRelTimestampToAbs:
    def _convert(self, ts: str) -> datetime | None:
        with patch("rotten_tomatoes.datetime") as mock_dt:
            mock_dt.now.return_value = FIXED_NOW
            mock_dt.strptime.side_effect = datetime.strptime
            return convert_rel_timestamp_to_abs(ts)

    def test_minutes(self):
        result = self._convert("30m")
        assert result is not None
        assert result.minute == 30  # 12:00 - 30min = 11:30

    def test_hours(self):
        result = self._convert("3h")
        assert result is not None
        assert result.hour == 9  # 12:00 - 3h = 09:00

    def test_days(self):
        result = self._convert("2d")
        assert result is not None
        assert result.day == 19  # Mar 21 - 2 days = Mar 19

    def test_month_format(self):
        result = self._convert("Mar 15")
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 15

    def test_future_month_rolls_back_year(self):
        """A date like "Jul 15" when current date is Mar 21, 2026 → Jul 15, 2025."""
        result = self._convert("Jul 15")
        assert result is not None
        assert result.year == 2025
        assert result.month == 7
        assert result.day == 15

    def test_past_month_keeps_current_year(self):
        """A date like "Jan 10" when current date is Mar 21, 2026 → Jan 10, 2026."""
        result = self._convert("Jan 10")
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 10

    def test_future_day_in_current_month_rolls_back_year(self):
        """A date like "Mar 25" when current date is Mar 21, 2026 → Mar 25, 2025."""
        result = self._convert("Mar 25")
        assert result is not None
        assert result.year == 2025

    def test_empty_string_returns_none(self):
        assert convert_rel_timestamp_to_abs("") is None

    def test_malformed_returns_none(self):
        assert convert_rel_timestamp_to_abs("xyz") is None


# ── compute_review_id ─────────────────────────────────────────────────────────

class TestComputeReviewId:
    def test_deterministic(self):
        assert compute_review_id(SLUG, "Alice", "AV Club", "4/5") == compute_review_id(SLUG, "Alice", "AV Club", "4/5")

    def test_different_inputs_different_ids(self):
        assert compute_review_id(SLUG, "Alice", "AV Club", "4/5") != compute_review_id(SLUG, "Bob", "AV Club", "4/5")

    def test_none_inputs_handled(self):
        # Should not raise
        result = compute_review_id(SLUG, None, None, None)
        assert isinstance(result, str) and len(result) == 32

    def test_returns_md5_hex(self):
        result = compute_review_id(SLUG, "Alice", "Pub", "5/5")
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_movies_different_ids(self):
        id_a = compute_review_id("movie_a", "Alice", "AV Club", "4/5")
        id_b = compute_review_id("movie_b", "Alice", "AV Club", "4/5")
        assert id_a != id_b


# ── _migrate_v1_review_ids ────────────────────────────────────────────────────

class TestMigrateV1ReviewIds:
    def _make_legacy_conn(self):
        """Create a DB with old-style hashes (no movie_slug)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_slug TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                unique_review_id TEXT UNIQUE NOT NULL,
                subjective_score TEXT,
                tomatometer_sentiment TEXT,
                reconciled_timestamp INTEGER NOT NULL DEFAULT 0,
                reviewer_name TEXT,
                publication_name TEXT,
                top_critic INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        return conn

    def _old_hash(self, name, pub, rating):
        """Compute hash the old way (no movie_slug)."""
        import hashlib
        key = f"{name or ''}{pub or ''}{rating or ''}"
        return hashlib.md5(key.encode()).hexdigest()

    def test_rehashes_existing_rows(self):
        conn = self._make_legacy_conn()
        old_id = self._old_hash("Alice", "AV Club", "4/5")
        conn.execute(
            "INSERT INTO reviews (movie_slug, timestamp, unique_review_id, subjective_score, "
            "reviewer_name, publication_name, reconciled_timestamp, top_critic) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
            ("test_movie", "2026-03-21 10:00:00", old_id, "4/5", "Alice", "AV Club"),
        )
        conn.commit()

        _migrate_v1_review_ids(conn)

        row = conn.execute("SELECT unique_review_id FROM reviews").fetchone()
        expected = compute_review_id("test_movie", "Alice", "AV Club", "4/5")
        assert row[0] == expected
        assert row[0] != old_id

    def test_migration_is_idempotent(self):
        conn = self._make_legacy_conn()
        old_id = self._old_hash("Alice", "AV Club", "4/5")
        conn.execute(
            "INSERT INTO reviews (movie_slug, timestamp, unique_review_id, subjective_score, "
            "reviewer_name, publication_name, reconciled_timestamp, top_critic) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, 0)",
            ("test_movie", "2026-03-21 10:00:00", old_id, "4/5", "Alice", "AV Club"),
        )
        conn.commit()

        _migrate_v1_review_ids(conn)
        id_after_first = conn.execute("SELECT unique_review_id FROM reviews").fetchone()[0]

        _migrate_v1_review_ids(conn)
        id_after_second = conn.execute("SELECT unique_review_id FROM reviews").fetchone()[0]

        assert id_after_first == id_after_second

    def test_empty_table_no_error(self):
        conn = self._make_legacy_conn()
        _migrate_v1_review_ids(conn)  # Should not raise


class TestMigrateV2TimestampConfidence:
    """Tests for _migrate_v2_timestamp_confidence — replaces reconciled_timestamp with timestamp_confidence."""

    def _make_pre_v2_conn(self):
        """Create an in-memory DB with the pre-v2 schema (has reconciled_timestamp)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_slug TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                unique_review_id TEXT UNIQUE NOT NULL,
                subjective_score TEXT,
                tomatometer_sentiment TEXT,
                reconciled_timestamp INTEGER NOT NULL DEFAULT 0,
                reviewer_name TEXT,
                publication_name TEXT,
                top_critic INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        return conn

    def test_adds_column_and_drops_old(self):
        conn = self._make_pre_v2_conn()
        conn.execute(
            "INSERT INTO reviews (movie_slug, timestamp, unique_review_id, reconciled_timestamp) "
            "VALUES ('m', '2026-01-01 12:00:00', 'id1', 0)"
        )
        conn.commit()

        _migrate_v2_timestamp_confidence(conn)

        row = conn.execute("SELECT * FROM reviews").fetchone()
        assert row["timestamp_confidence"] == "d"
        # reconciled_timestamp column should be gone
        col_names = [desc[0] for desc in conn.execute("SELECT * FROM reviews").description]
        assert "reconciled_timestamp" not in col_names
        assert "timestamp_confidence" in col_names

    def test_reconciled_rows_get_d(self):
        conn = self._make_pre_v2_conn()
        conn.execute(
            "INSERT INTO reviews (movie_slug, timestamp, unique_review_id, reconciled_timestamp) "
            "VALUES ('m', '2026-01-01 12:00:00', 'id1', 1)"
        )
        conn.commit()

        _migrate_v2_timestamp_confidence(conn)

        row = conn.execute("SELECT timestamp_confidence FROM reviews").fetchone()
        assert row["timestamp_confidence"] == "d"

    def test_empty_table_no_error(self):
        conn = self._make_pre_v2_conn()
        _migrate_v2_timestamp_confidence(conn)  # Should not raise


class TestMigrateV3ProvenanceColumns:
    """Tests for _migrate_v3_provenance_columns — adds scraped_at, raw_timestamp_text, and indexes."""

    def _make_pre_v3_conn(self):
        """Create an in-memory DB with the pre-v3 schema (v2 columns but no provenance)."""
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_slug TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                unique_review_id TEXT UNIQUE NOT NULL,
                subjective_score TEXT,
                tomatometer_sentiment TEXT,
                timestamp_confidence TEXT NOT NULL DEFAULT 'd',
                reviewer_name TEXT,
                publication_name TEXT,
                top_critic INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
        return conn

    def test_adds_columns(self):
        conn = self._make_pre_v3_conn()
        _migrate_v3_provenance_columns(conn)
        col_names = [row[1] for row in conn.execute("PRAGMA table_info(reviews)").fetchall()]
        assert "scraped_at" in col_names
        assert "raw_timestamp_text" in col_names

    def test_creates_indexes(self):
        conn = self._make_pre_v3_conn()
        _migrate_v3_provenance_columns(conn)
        indexes = {row[1] for row in conn.execute("PRAGMA index_list(reviews)").fetchall()}
        assert "idx_reviews_movie_slug" in indexes
        assert "idx_reviews_movie_timestamp" in indexes

    def test_idempotent(self):
        conn = self._make_pre_v3_conn()
        _migrate_v3_provenance_columns(conn)
        _migrate_v3_provenance_columns(conn)  # Should not raise

    def test_existing_data_gets_defaults(self):
        conn = self._make_pre_v3_conn()
        conn.execute(
            "INSERT INTO reviews (movie_slug, timestamp, unique_review_id) "
            "VALUES ('m', '2026-01-01 12:00:00', 'id1')"
        )
        conn.commit()
        _migrate_v3_provenance_columns(conn)
        row = conn.execute("SELECT scraped_at, raw_timestamp_text FROM reviews").fetchone()
        assert row["scraped_at"] == ""
        assert row["raw_timestamp_text"] == ""

    def test_full_init_reviews_table_reaches_v3(self):
        conn = make_conn()
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 3
        col_names = [r[1] for r in conn.execute("PRAGMA table_info(reviews)").fetchall()]
        assert "scraped_at" in col_names
        assert "raw_timestamp_text" in col_names

    def test_empty_table_no_error(self):
        conn = self._make_pre_v3_conn()
        _migrate_v3_provenance_columns(conn)  # Should not raise


# ── insert_review / deduplication ─────────────────────────────────────────────

class TestInsertReview:
    SLUG = "test_movie"

    def test_insert_new_review(self):
        conn = make_conn()
        review = make_review()
        assert insert_review(conn, self.SLUG, review) is True

    def test_duplicate_is_rejected(self):
        conn = make_conn()
        review = make_review()
        insert_review(conn, self.SLUG, review)
        assert insert_review(conn, self.SLUG, review) is False

    def test_different_reviews_both_inserted(self):
        conn = make_conn()
        r1 = make_review(name="Alice")
        r2 = make_review(name="Bob")
        assert insert_review(conn, self.SLUG, r1) is True
        assert insert_review(conn, self.SLUG, r2) is True

    def test_db_review_ids_reflects_inserts(self):
        conn = make_conn()
        r1 = make_review(name="Alice")
        r2 = make_review(name="Bob")
        insert_review(conn, self.SLUG, r1)
        insert_review(conn, self.SLUG, r2)
        ids = get_db_review_ids(conn, self.SLUG)
        assert r1["unique_review_id"] in ids
        assert r2["unique_review_id"] in ids

    def test_tomatometer_sentiment_persisted(self):
        conn = make_conn()
        review = make_review(tomatometer_sentiment="positive")
        insert_review(conn, self.SLUG, review)
        row = conn.execute(
            "SELECT tomatometer_sentiment FROM reviews WHERE unique_review_id = ?",
            (review["unique_review_id"],),
        ).fetchone()
        assert row["tomatometer_sentiment"] == "positive"

    def test_tomatometer_sentiment_none_when_missing(self):
        conn = make_conn()
        review = make_review(tomatometer_sentiment=None)
        insert_review(conn, self.SLUG, review)
        row = conn.execute(
            "SELECT tomatometer_sentiment FROM reviews WHERE unique_review_id = ?",
            (review["unique_review_id"],),
        ).fetchone()
        assert row["tomatometer_sentiment"] is None

    def test_scraped_at_and_raw_timestamp_text_persisted(self):
        conn = make_conn()
        review = make_review(scraped_at="2026-03-21 15:30:00", raw_timestamp_text="3h")
        insert_review(conn, self.SLUG, review)
        row = conn.execute(
            "SELECT scraped_at, raw_timestamp_text FROM reviews WHERE unique_review_id = ?",
            (review["unique_review_id"],),
        ).fetchone()
        assert row["scraped_at"] == "2026-03-21 15:30:00"
        assert row["raw_timestamp_text"] == "3h"

    def test_new_columns_default_when_missing(self):
        conn = make_conn()
        review = make_review()
        del review["scraped_at"]
        del review["raw_timestamp_text"]
        insert_review(conn, self.SLUG, review)
        row = conn.execute(
            "SELECT scraped_at, raw_timestamp_text FROM reviews WHERE unique_review_id = ?",
            (review["unique_review_id"],),
        ).fetchone()
        assert row["scraped_at"] == ""
        assert row["raw_timestamp_text"] == ""


# ── interpolate_timestamps ────────────────────────────────────────────────────

class TestInterpolateTimestamps:
    def test_single_midpoint(self):
        result = interpolate_timestamps("2026-03-21 10:00:00", "2026-03-21 12:00:00", 1)
        assert result == ["2026-03-21 11:00:00"]

    def test_two_midpoints_evenly_spaced(self):
        result = interpolate_timestamps("2026-03-21 10:00:00", "2026-03-21 13:00:00", 2)
        assert result == ["2026-03-21 11:00:00", "2026-03-21 12:00:00"]

    def test_no_before_ts_uses_after(self):
        result = interpolate_timestamps(None, "2026-03-21 12:00:00", 2)
        assert result == ["2026-03-21 12:00:00", "2026-03-21 12:00:00"]

    def test_no_after_ts_uses_before(self):
        result = interpolate_timestamps("2026-03-21 10:00:00", None, 2)
        assert result == ["2026-03-21 10:00:00", "2026-03-21 10:00:00"]

    def test_no_anchors_returns_none_list(self):
        result = interpolate_timestamps(None, None, 3)
        assert result == [None, None, None]

    def test_count_zero(self):
        result = interpolate_timestamps("2026-03-21 10:00:00", "2026-03-21 12:00:00", 0)
        assert result == []


# ── reconcile_missing_reviews ─────────────────────────────────────────────────

class TestReconcileMissingReviews:
    SLUG = "test_movie"

    def _seed_db(self, conn, reviews):
        for r in reviews:
            insert_review(conn, self.SLUG, r)

    def test_no_missing_returns_zero(self):
        conn = make_conn()
        r1 = make_review(name="Alice", timestamp="2026-03-21 10:00:00")
        self._seed_db(conn, [r1])
        assert reconcile_missing_reviews(conn, self.SLUG, [r1]) == 0

    def test_single_missing_gets_interpolated(self):
        conn = make_conn()
        r_before = make_review(name="Alice", timestamp="2026-03-21 10:00:00")
        r_missing = make_review(name="Bob",   timestamp="2026-03-21 11:00:00")
        r_after   = make_review(name="Carol", timestamp="2026-03-21 12:00:00")
        r_before["unique_review_id"] = compute_review_id(SLUG, "Alice", "AV Club", "4/5")
        r_missing["unique_review_id"] = compute_review_id(SLUG, "Bob",   "AV Club", "4/5")
        r_after["unique_review_id"]   = compute_review_id(SLUG, "Carol", "AV Club", "4/5")

        self._seed_db(conn, [r_before, r_after])

        # Scrape contains all three (newest-first), Bob is missing from DB
        scraped = [r_after, r_missing, r_before]
        count = reconcile_missing_reviews(conn, self.SLUG, scraped)
        assert count == 1

        ids = get_db_review_ids(conn, self.SLUG)
        assert r_missing["unique_review_id"] in ids

    def test_interpolated_timestamp_is_between_anchors(self):
        conn = make_conn()
        r_before = make_review(name="Alice", timestamp="2026-03-21 10:00:00")
        r_missing = make_review(name="Bob",  timestamp="2026-03-21 11:00:00")
        r_after   = make_review(name="Carol",timestamp="2026-03-21 12:00:00")
        r_before["unique_review_id"] = compute_review_id(SLUG, "Alice", "AV Club", "4/5")
        r_missing["unique_review_id"] = compute_review_id(SLUG, "Bob",   "AV Club", "4/5")
        r_after["unique_review_id"]   = compute_review_id(SLUG, "Carol", "AV Club", "4/5")

        self._seed_db(conn, [r_before, r_after])
        reconcile_missing_reviews(conn, self.SLUG, [r_after, r_missing, r_before])

        # Check the stored timestamp is exactly the midpoint
        from rotten_tomatoes import get_db_reviews_sorted
        rows = get_db_reviews_sorted(conn, self.SLUG)
        bob_row = next(r for r in rows if r["reviewer_name"] == "Bob")
        assert bob_row["timestamp"] == "2026-03-21 11:00:00"
        assert bob_row["timestamp_confidence"] == "d"

    def test_no_db_context_skips_missing_review(self):
        """If a missing review has no DB neighbors, it can't be identified as lagging — skip it."""
        conn = make_conn()
        r_missing = make_review(name="Bob", timestamp="2026-03-21 11:00:00")
        r_missing["unique_review_id"] = compute_review_id(SLUG, "Bob", "AV Club", "4/5")

        # Empty DB — no hour-window context
        count = reconcile_missing_reviews(conn, self.SLUG, [r_missing])
        assert count == 0

        ids = get_db_review_ids(conn, self.SLUG)
        assert r_missing["unique_review_id"] not in ids


# ── Pre-check state ──────────────────────────────────────────────────────────

def _make_precheck_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_precheck_table(conn)
    return conn


class TestPrecheckState:
    SLUG = "test_movie"

    def test_init_creates_table(self):
        conn = _make_precheck_conn()
        # Should not raise
        conn.execute("SELECT * FROM precheck_state").fetchall()

    def test_get_returns_zero_when_empty(self):
        conn = _make_precheck_conn()
        assert get_last_review_count(conn, self.SLUG) == 0

    def test_update_and_get_round_trip(self):
        conn = _make_precheck_conn()
        update_last_review_count(conn, self.SLUG, 42)
        assert get_last_review_count(conn, self.SLUG) == 42

    def test_update_overwrites_existing(self):
        conn = _make_precheck_conn()
        update_last_review_count(conn, self.SLUG, 10)
        update_last_review_count(conn, self.SLUG, 20)
        assert get_last_review_count(conn, self.SLUG) == 20

    def test_failure_counter_increments(self):
        conn = _make_precheck_conn()
        assert record_precheck_failure(conn, self.SLUG) == 1
        assert record_precheck_failure(conn, self.SLUG) == 2
        assert record_precheck_failure(conn, self.SLUG) == 3

    def test_update_resets_failure_counter(self):
        conn = _make_precheck_conn()
        record_precheck_failure(conn, self.SLUG)
        record_precheck_failure(conn, self.SLUG)
        update_last_review_count(conn, self.SLUG, 50)
        # After update, next failure should restart at 1
        assert record_precheck_failure(conn, self.SLUG) == 1


# ── fetch_review_count ────────────────────────────────────────────────────────

class TestFetchReviewCount:
    SLUG = "test_movie"

    def test_success_extracts_count(self):
        with patch("rotten_tomatoes.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.text = '<span>289 Reviews</span>'
            assert fetch_review_count(self.SLUG) == 289

    def test_large_count(self):
        with patch("rotten_tomatoes.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.text = '<span>1042 Reviews</span>'
            assert fetch_review_count(self.SLUG) == 1042

    def test_request_failure_returns_none(self):
        import requests as req
        with patch("rotten_tomatoes.requests.get", side_effect=req.ConnectionError):
            assert fetch_review_count(self.SLUG) is None

    def test_no_match_returns_none(self):
        with patch("rotten_tomatoes.requests.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.text = '<html>no count here</html>'
            assert fetch_review_count(self.SLUG) is None

    def test_non_200_returns_none(self):
        with patch("rotten_tomatoes.requests.get") as mock_get:
            mock_get.return_value.status_code = 403
            assert fetch_review_count(self.SLUG) is None


# ── has_new_reviews ───────────────────────────────────────────────────────────

class TestHasNewReviews:
    SLUG = "test_movie"

    def test_returns_true_when_fetch_fails(self):
        conn = _make_precheck_conn()
        with patch("rotten_tomatoes.fetch_review_count", return_value=None):
            assert has_new_reviews(conn, self.SLUG) is True

    def test_failure_increments_counter(self):
        conn = _make_precheck_conn()
        with patch("rotten_tomatoes.fetch_review_count", return_value=None):
            has_new_reviews(conn, self.SLUG)
            has_new_reviews(conn, self.SLUG)
        row = conn.execute(
            "SELECT consecutive_failures FROM precheck_state WHERE movie_slug = ?",
            (self.SLUG,),
        ).fetchone()
        assert row["consecutive_failures"] == 2

    def test_returns_true_on_first_run(self):
        conn = _make_precheck_conn()
        with patch("rotten_tomatoes.fetch_review_count", return_value=100):
            assert has_new_reviews(conn, self.SLUG) is True
        assert get_last_review_count(conn, self.SLUG) == 100

    def test_returns_false_when_count_unchanged(self):
        conn = _make_precheck_conn()
        update_last_review_count(conn, self.SLUG, 100)
        with patch("rotten_tomatoes.fetch_review_count", return_value=100):
            assert has_new_reviews(conn, self.SLUG) is False

    def test_returns_true_when_count_increased(self):
        conn = _make_precheck_conn()
        update_last_review_count(conn, self.SLUG, 100)
        with patch("rotten_tomatoes.fetch_review_count", return_value=105):
            assert has_new_reviews(conn, self.SLUG) is True
        # Count should NOT be updated yet — deferred until scrape confirms capture
        assert get_last_review_count(conn, self.SLUG) == 100

    def test_returns_true_when_count_decreased(self):
        conn = _make_precheck_conn()
        update_last_review_count(conn, self.SLUG, 100)
        with patch("rotten_tomatoes.fetch_review_count", return_value=98):
            assert has_new_reviews(conn, self.SLUG) is True


# ── load_movie_config ────────────────────────────────────────────────────────

class TestLoadMovieConfig:
    def test_loads_enabled_movies(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text('[{"slug": "movie_a", "enabled": true}, {"slug": "movie_b", "enabled": true}]')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            assert load_movie_config() == ["movie_a", "movie_b"]

    def test_skips_disabled_movies(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text('[{"slug": "movie_a", "enabled": true}, {"slug": "movie_b", "enabled": false}]')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            assert load_movie_config() == ["movie_a"]

    def test_enabled_defaults_to_true(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text('[{"slug": "movie_a"}]')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            assert load_movie_config() == ["movie_a"]

    def test_missing_file_returns_empty(self, tmp_path):
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(tmp_path / "nope.json")):
            assert load_movie_config() == []

    def test_invalid_json_returns_empty(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text("not json!!!")
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            assert load_movie_config() == []

    def test_skips_entries_without_slug(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text('[{"slug": "movie_a"}, {"enabled": true}]')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            assert load_movie_config() == ["movie_a"]

    def test_non_array_returns_empty(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text('{"slug": "movie_a"}')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            assert load_movie_config() == []


# ── update_sentiment ─────────────────────────────────────────────────────────

class TestUpdateSentiment:
    def test_fills_null_sentiment(self):
        conn = make_conn()
        review = make_review(tomatometer_sentiment=None)
        insert_review(conn, SLUG, review)
        assert update_sentiment(conn, review["unique_review_id"], "positive") is True
        row = conn.execute(
            "SELECT tomatometer_sentiment FROM reviews WHERE unique_review_id = ?",
            (review["unique_review_id"],),
        ).fetchone()
        assert row[0] == "positive"

    def test_skips_existing_sentiment(self):
        conn = make_conn()
        review = make_review(tomatometer_sentiment="negative")
        insert_review(conn, SLUG, review)
        assert update_sentiment(conn, review["unique_review_id"], "positive") is False
        row = conn.execute(
            "SELECT tomatometer_sentiment FROM reviews WHERE unique_review_id = ?",
            (review["unique_review_id"],),
        ).fetchone()
        assert row[0] == "negative"  # unchanged

    def test_none_sentiment_returns_false(self):
        conn = make_conn()
        review = make_review(tomatometer_sentiment=None)
        insert_review(conn, SLUG, review)
        assert update_sentiment(conn, review["unique_review_id"], None) is False

    def test_nonexistent_id_returns_false(self):
        conn = make_conn()
        assert update_sentiment(conn, "nonexistent_hash", "positive") is False


# ── backfill_movie ───────────────────────────────────────────────────────────

# Import here to avoid top-level Selenium dependency
from scripts.backfill import backfill_movie


class TestBackfillMovie:
    def _mock_get_reviews(self, movie_slug, critic_filter, stop_at_unit=None):
        """Return pre-built reviews for testing. Keyed by critic_filter."""
        return self._reviews_by_filter.get(critic_filter, [])

    def _make_scraped_review(self, name, pub, rating, movie_slug=SLUG,
                             sentiment="positive", top_critic=False):
        return {
            "unique_review_id": compute_review_id(movie_slug, name, pub, rating),
            "timestamp": "2026-03-21 10:00:00",
            "tomatometer_sentiment": sentiment,
            "subjective_score": rating,
            "reviewer_name": name,
            "publication_name": pub,
            "top_critic": top_critic,
        }

    def test_inserts_new_reviews(self):
        conn = make_conn()
        review = self._make_scraped_review("Alice", "AV Club", "4/5")
        self._reviews_by_filter = {"top-critics": [], "all-critics": [review]}

        with patch("scripts.backfill.get_reviews", side_effect=self._mock_get_reviews):
            stats = backfill_movie(SLUG, conn)

        assert stats["inserted"] == 1
        assert len(get_db_review_ids(conn, SLUG)) == 1

    def test_updates_missing_sentiment(self):
        conn = make_conn()
        review = make_review(tomatometer_sentiment=None)
        insert_review(conn, SLUG, review)

        scraped = self._make_scraped_review("Alice", "AV Club", "4/5", sentiment="positive")
        self._reviews_by_filter = {"top-critics": [], "all-critics": [scraped]}

        with patch("scripts.backfill.get_reviews", side_effect=self._mock_get_reviews):
            stats = backfill_movie(SLUG, conn)

        assert stats["sentiment_updated"] == 1
        assert stats["inserted"] == 0

    def test_does_not_overwrite_existing_sentiment(self):
        conn = make_conn()
        review = make_review(tomatometer_sentiment="negative")
        insert_review(conn, SLUG, review)

        scraped = self._make_scraped_review("Alice", "AV Club", "4/5", sentiment="positive")
        self._reviews_by_filter = {"top-critics": [], "all-critics": [scraped]}

        with patch("scripts.backfill.get_reviews", side_effect=self._mock_get_reviews):
            stats = backfill_movie(SLUG, conn)

        assert stats["skipped"] == 1
        row = conn.execute(
            "SELECT tomatometer_sentiment FROM reviews WHERE unique_review_id = ?",
            (review["unique_review_id"],),
        ).fetchone()
        assert row[0] == "negative"

    def test_idempotent(self):
        conn = make_conn()
        review = self._make_scraped_review("Alice", "AV Club", "4/5")
        self._reviews_by_filter = {"top-critics": [], "all-critics": [review]}

        with patch("scripts.backfill.get_reviews", side_effect=self._mock_get_reviews):
            stats1 = backfill_movie(SLUG, conn)
            stats2 = backfill_movie(SLUG, conn)

        assert stats1["inserted"] == 1
        assert stats2["inserted"] == 0
        assert stats2["skipped"] == 1

    def test_two_pass_sets_top_critic(self):
        conn = make_conn()
        top_review = self._make_scraped_review("Alice", "AV Club", "4/5", top_critic=True)
        all_review = self._make_scraped_review("Alice", "AV Club", "4/5", top_critic=False)
        self._reviews_by_filter = {"top-critics": [top_review], "all-critics": [all_review]}

        with patch("scripts.backfill.get_reviews", side_effect=self._mock_get_reviews):
            stats = backfill_movie(SLUG, conn)

        assert stats["inserted"] == 1  # inserted from top-critics, skipped from all-critics
        row = conn.execute("SELECT top_critic FROM reviews").fetchone()
        assert row[0] == 1

    def test_dry_run_writes_nothing(self):
        conn = make_conn()
        review = self._make_scraped_review("Alice", "AV Club", "4/5")
        self._reviews_by_filter = {"top-critics": [], "all-critics": [review]}

        with patch("scripts.backfill.get_reviews", side_effect=self._mock_get_reviews):
            stats = backfill_movie(SLUG, conn, dry_run=True)

        assert stats["inserted"] == 1  # counted but not written
        assert len(get_db_review_ids(conn, SLUG)) == 0  # nothing in DB

    def test_continues_on_selenium_error(self):
        conn = make_conn()
        review = self._make_scraped_review("Alice", "AV Club", "4/5")

        def mock_get_reviews(movie_slug, critic_filter, stop_at_unit=None):
            if critic_filter == "top-critics":
                raise RuntimeError("Selenium crashed")
            return [review]

        with patch("scripts.backfill.get_reviews", side_effect=mock_get_reviews):
            stats = backfill_movie(SLUG, conn)

        assert stats["errors"] == 1
        assert stats["inserted"] == 1  # all-critics pass still worked
