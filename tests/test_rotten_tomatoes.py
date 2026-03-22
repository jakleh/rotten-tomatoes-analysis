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
    compute_review_id,
    convert_rel_timestamp_to_abs,
    fetch_review_count,
    get_db_review_ids,
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


def make_review(name="Alice", pub="AV Club", rating="4/5", **kwargs) -> dict:
    return {
        "unique_review_id": compute_review_id(name, pub, rating),
        "timestamp": "2026-03-21 10:00:00",
        "tomatometer_sentiment": "positive",
        "subjective_score": rating,
        "reviewer_name": name,
        "publication_name": pub,
        "top_critic": False,
        "reconciled_timestamp": False,
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
        result = convert_rel_timestamp_to_abs("Mar 15")
        assert result is not None
        assert result.month == 3
        assert result.day == 15

    def test_empty_string_returns_none(self):
        assert convert_rel_timestamp_to_abs("") is None

    def test_malformed_returns_none(self):
        assert convert_rel_timestamp_to_abs("xyz") is None


# ── compute_review_id ─────────────────────────────────────────────────────────

class TestComputeReviewId:
    def test_deterministic(self):
        assert compute_review_id("Alice", "AV Club", "4/5") == compute_review_id("Alice", "AV Club", "4/5")

    def test_different_inputs_different_ids(self):
        assert compute_review_id("Alice", "AV Club", "4/5") != compute_review_id("Bob", "AV Club", "4/5")

    def test_none_inputs_handled(self):
        # Should not raise
        result = compute_review_id(None, None, None)
        assert isinstance(result, str) and len(result) == 32

    def test_returns_md5_hex(self):
        result = compute_review_id("Alice", "Pub", "5/5")
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)


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
        r_before["unique_review_id"] = compute_review_id("Alice", "AV Club", "4/5")
        r_missing["unique_review_id"] = compute_review_id("Bob",   "AV Club", "4/5")
        r_after["unique_review_id"]   = compute_review_id("Carol", "AV Club", "4/5")

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
        r_before["unique_review_id"] = compute_review_id("Alice", "AV Club", "4/5")
        r_missing["unique_review_id"] = compute_review_id("Bob",   "AV Club", "4/5")
        r_after["unique_review_id"]   = compute_review_id("Carol", "AV Club", "4/5")

        self._seed_db(conn, [r_before, r_after])
        reconcile_missing_reviews(conn, self.SLUG, [r_after, r_missing, r_before])

        # Check the stored timestamp is exactly the midpoint
        from rotten_tomatoes import get_db_reviews_sorted
        rows = get_db_reviews_sorted(conn, self.SLUG)
        bob_row = next(r for r in rows if r["reviewer_name"] == "Bob")
        assert bob_row["timestamp"] == "2026-03-21 11:00:00"
        assert bob_row["reconciled_timestamp"] == 1

    def test_no_db_context_skips_missing_review(self):
        """If a missing review has no DB neighbors, it can't be identified as lagging — skip it."""
        conn = make_conn()
        r_missing = make_review(name="Bob", timestamp="2026-03-21 11:00:00")
        r_missing["unique_review_id"] = compute_review_id("Bob", "AV Club", "4/5")

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
        assert get_last_review_count(conn, self.SLUG) == 105

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
