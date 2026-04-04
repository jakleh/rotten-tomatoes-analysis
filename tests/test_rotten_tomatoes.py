"""
Tests for rotten_tomatoes.py

Covers: timestamp utilities (including robust regex), MD5 hashing, movie config
loading, selector helper, and JSON log formatter -- all without hitting the
network or launching a browser. DB tests deferred until migration is complete.
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

from rotten_tomatoes import (
    SELECTORS,
    _CloudRunFormatter,
    _find_selector,
    compute_review_id,
    convert_rel_timestamp_to_abs,
    get_timestamp_unit,
    load_movie_config,
)

# Add scripts/ to path so we can import backfill module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from backfill import filter_reviews_by_cutoff, load_backfill_config, _parse_time_end, BACKFILL_CSV_PATH, main as backfill_main


# -- Helpers -------------------------------------------------------------------

SLUG = "test_movie"
FIXED_NOW = datetime(2026, 3, 21, 12, 0, 0, tzinfo=timezone.utc)


# -- get_timestamp_unit --------------------------------------------------------

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

    # Robust regex: alternate forms
    def test_minutes_min(self):
        assert get_timestamp_unit("5min") == "m"

    def test_hours_hr(self):
        assert get_timestamp_unit("2hr") == "h"

    def test_hours_hrs(self):
        assert get_timestamp_unit("2hrs") == "h"

    def test_days_day(self):
        assert get_timestamp_unit("3day") == "d"

    def test_days_days(self):
        assert get_timestamp_unit("3days") == "d"

    def test_minutes_mins(self):
        assert get_timestamp_unit("10mins") == "m"

    def test_case_insensitive(self):
        assert get_timestamp_unit("5M") == "m"
        assert get_timestamp_unit("2H") == "h"
        assert get_timestamp_unit("3D") == "d"
        assert get_timestamp_unit("5Min") == "m"


# -- convert_rel_timestamp_to_abs ----------------------------------------------

class TestConvertRelTimestampToAbs:
    def test_minutes(self):
        result = convert_rel_timestamp_to_abs("30m", FIXED_NOW)
        assert result is not None
        assert result.minute == 30  # 12:00 - 30min = 11:30

    def test_hours(self):
        result = convert_rel_timestamp_to_abs("3h", FIXED_NOW)
        assert result is not None
        assert result.hour == 9  # 12:00 - 3h = 09:00

    def test_days(self):
        result = convert_rel_timestamp_to_abs("2d", FIXED_NOW)
        assert result is not None
        assert result.day == 19  # Mar 21 - 2 days = Mar 19

    def test_month_format(self):
        result = convert_rel_timestamp_to_abs("Mar 15", FIXED_NOW)
        assert result is not None
        assert result.year == 2026
        assert result.month == 3
        assert result.day == 15

    def test_future_month_rolls_back_year(self):
        """A date like "Jul 15" when current date is Mar 21, 2026 -> Jul 15, 2025."""
        result = convert_rel_timestamp_to_abs("Jul 15", FIXED_NOW)
        assert result is not None
        assert result.year == 2025
        assert result.month == 7
        assert result.day == 15

    def test_past_month_keeps_current_year(self):
        """A date like "Jan 10" when current date is Mar 21, 2026 -> Jan 10, 2026."""
        result = convert_rel_timestamp_to_abs("Jan 10", FIXED_NOW)
        assert result is not None
        assert result.year == 2026
        assert result.month == 1
        assert result.day == 10

    def test_future_day_in_current_month_rolls_back_year(self):
        """A date like "Mar 25" when current date is Mar 21, 2026 -> Mar 25, 2025."""
        result = convert_rel_timestamp_to_abs("Mar 25", FIXED_NOW)
        assert result is not None
        assert result.year == 2025

    def test_empty_string_returns_none(self):
        assert convert_rel_timestamp_to_abs("", FIXED_NOW) is None

    def test_malformed_returns_none(self):
        assert convert_rel_timestamp_to_abs("xyz", FIXED_NOW) is None

    # Robust regex: alternate forms
    def test_minutes_min_form(self):
        result = convert_rel_timestamp_to_abs("5min", FIXED_NOW)
        assert result is not None
        assert result == FIXED_NOW - timedelta(minutes=5)

    def test_hours_hr_form(self):
        result = convert_rel_timestamp_to_abs("2hr", FIXED_NOW)
        assert result is not None
        assert result == FIXED_NOW - timedelta(hours=2)

    def test_hours_hrs_form(self):
        result = convert_rel_timestamp_to_abs("2hrs", FIXED_NOW)
        assert result is not None
        assert result == FIXED_NOW - timedelta(hours=2)

    def test_days_day_form(self):
        result = convert_rel_timestamp_to_abs("3days", FIXED_NOW)
        assert result is not None
        assert result == FIXED_NOW - timedelta(days=3)

    def test_scrape_time_is_reference(self):
        """All reviews in a scrape should use the same reference time."""
        earlier = datetime(2026, 1, 1, 6, 0, 0, tzinfo=timezone.utc)
        result = convert_rel_timestamp_to_abs("2h", earlier)
        assert result == datetime(2026, 1, 1, 4, 0, 0, tzinfo=timezone.utc)


# -- compute_review_id --------------------------------------------------------

class TestComputeReviewId:
    def test_deterministic(self):
        assert compute_review_id(SLUG, "Alice", "AV Club", "4/5") == compute_review_id(SLUG, "Alice", "AV Club", "4/5")

    def test_different_inputs_different_ids(self):
        assert compute_review_id(SLUG, "Alice", "AV Club", "4/5") != compute_review_id(SLUG, "Bob", "AV Club", "4/5")

    def test_none_inputs_handled(self):
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


# -- _find_selector ------------------------------------------------------------

class TestFindSelector:
    CARD_HTML = """
    <review-card>
        <span slot="timestamp">5m</span>
        <rt-link slot="name">Alice</rt-link>
        <rt-link slot="publication">AV Club</rt-link>
        <span slot="rating"><span style="font-weight:bold">4/5</span></span>
        <score-icon-critics sentiment="positive"></score-icon-critics>
        <div slot="review">Great movie!</div>
    </review-card>
    """

    def _card(self):
        soup = BeautifulSoup(self.CARD_HTML, "html.parser")
        return soup.find("review-card")

    def test_finds_timestamp(self):
        tag = _find_selector(self._card(), "timestamp")
        assert tag is not None
        assert tag.get_text().strip() == "5m"

    def test_finds_reviewer_name(self):
        tag = _find_selector(self._card(), "reviewer_name")
        assert tag is not None
        assert tag.get_text().strip() == "Alice"

    def test_finds_publication(self):
        tag = _find_selector(self._card(), "publication")
        assert tag is not None
        assert tag.get_text().strip() == "AV Club"

    def test_finds_sentiment(self):
        tag = _find_selector(self._card(), "sentiment")
        assert tag is not None
        assert tag.get("sentiment") == "positive"

    def test_finds_written_review(self):
        tag = _find_selector(self._card(), "written_review")
        assert tag is not None
        assert tag.get_text().strip() == "Great movie!"

    def test_missing_selector_returns_none(self):
        html = "<review-card></review-card>"
        soup = BeautifulSoup(html, "html.parser")
        card = soup.find("review-card")
        assert _find_selector(card, "timestamp") is None
        assert _find_selector(card, "reviewer_name") is None
        assert _find_selector(card, "written_review") is None


# -- load_movie_config ---------------------------------------------------------

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


# -- _CloudRunFormatter --------------------------------------------------------

class TestCloudRunFormatter:
    def _make_record(self, level, msg, exc_info=None):
        record = logging.LogRecord(
            name="test", level=level, pathname="", lineno=0,
            msg=msg, args=(), exc_info=exc_info,
        )
        return record

    def test_outputs_valid_json(self):
        fmt = _CloudRunFormatter()
        record = self._make_record(logging.INFO, "hello")
        line = fmt.format(record)
        parsed = json.loads(line)
        assert "severity" in parsed
        assert "message" in parsed
        assert "time" in parsed

    def test_severity_matches_level_name(self):
        fmt = _CloudRunFormatter()
        for level, name in [(logging.INFO, "INFO"), (logging.WARNING, "WARNING"), (logging.ERROR, "ERROR")]:
            record = self._make_record(level, "test")
            parsed = json.loads(fmt.format(record))
            assert parsed["severity"] == name

    def test_message_content(self):
        fmt = _CloudRunFormatter()
        record = self._make_record(logging.INFO, "scrape started")
        parsed = json.loads(fmt.format(record))
        assert parsed["message"] == "scrape started"

    def test_traceback_included(self):
        fmt = _CloudRunFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            record = self._make_record(logging.ERROR, "failed", exc_info=sys.exc_info())
        line = fmt.format(record)
        parsed = json.loads(line)
        assert "ValueError: boom" in parsed["message"]
        assert "Traceback" in parsed["message"]

    def test_non_ascii_message(self):
        fmt = _CloudRunFormatter()
        record = self._make_record(logging.INFO, "review by Jos\u00e9 \u2014 5\u2605")
        line = fmt.format(record)
        parsed = json.loads(line)
        assert "Jos\u00e9" in parsed["message"]


# -- filter_reviews_by_cutoff --------------------------------------------------

CUTOFF = datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)  # midnight UTC Mar 1


def _review(ts):
    """Helper: minimal review dict with given estimated_timestamp."""
    return {"estimated_timestamp": ts, "unique_review_id": "x"}


class TestFilterReviewsByCutoff:
    def test_includes_reviews_before_cutoff(self):
        reviews = [_review(datetime(2026, 2, 15, tzinfo=timezone.utc))]
        assert len(filter_reviews_by_cutoff(reviews, CUTOFF)) == 1

    def test_excludes_reviews_after_cutoff(self):
        reviews = [_review(datetime(2026, 3, 15, tzinfo=timezone.utc))]
        assert len(filter_reviews_by_cutoff(reviews, CUTOFF)) == 0

    def test_includes_reviews_on_end_date(self):
        # 23:59:59 on Feb 28 (day before cutoff) should be included
        reviews = [_review(datetime(2026, 2, 28, 23, 59, 59, tzinfo=timezone.utc))]
        assert len(filter_reviews_by_cutoff(reviews, CUTOFF)) == 1

    def test_excludes_reviews_at_exact_cutoff(self):
        reviews = [_review(CUTOFF)]
        assert len(filter_reviews_by_cutoff(reviews, CUTOFF)) == 0

    def test_excludes_none_timestamps(self):
        reviews = [_review(None)]
        assert len(filter_reviews_by_cutoff(reviews, CUTOFF)) == 0

    def test_empty_input(self):
        assert filter_reviews_by_cutoff([], CUTOFF) == []

    def test_all_excluded(self):
        reviews = [
            _review(datetime(2026, 4, 1, tzinfo=timezone.utc)),
            _review(datetime(2026, 5, 1, tzinfo=timezone.utc)),
        ]
        assert len(filter_reviews_by_cutoff(reviews, CUTOFF)) == 0

    def test_mixed_reviews(self):
        reviews = [
            _review(datetime(2026, 2, 1, tzinfo=timezone.utc)),   # before -> kept
            _review(datetime(2026, 3, 15, tzinfo=timezone.utc)),  # after -> excluded
            _review(None),                                         # None -> excluded
            _review(datetime(2026, 2, 28, tzinfo=timezone.utc)),  # before -> kept
        ]
        result = filter_reviews_by_cutoff(reviews, CUTOFF)
        assert len(result) == 2


# -- Backfill argparse validation ----------------------------------------------

class TestLoadBackfillConfig:
    def test_loads_slugs_from_csv(self, tmp_path):
        csv_file = tmp_path / "backfill_movies.csv"
        csv_file.write_text("slug,time_end\nproject_hail_mary,2026-03-23\nthunderbolts,2025-05-05\n")
        with patch("backfill.BACKFILL_CSV_PATH", str(csv_file)):
            result = load_backfill_config()
            assert result == [
                {"slug": "project_hail_mary", "time_end": "2026-03-23"},
                {"slug": "thunderbolts", "time_end": "2025-05-05"},
            ]

    def test_skips_blank_lines(self, tmp_path):
        csv_file = tmp_path / "backfill_movies.csv"
        csv_file.write_text("slug,time_end\nproject_hail_mary,2026-03-23\n\nthunderbolts,2025-05-05\n")
        with patch("backfill.BACKFILL_CSV_PATH", str(csv_file)):
            result = load_backfill_config()
            assert len(result) == 2
            assert result[0]["slug"] == "project_hail_mary"
            assert result[1]["slug"] == "thunderbolts"

    def test_strips_whitespace(self, tmp_path):
        csv_file = tmp_path / "backfill_movies.csv"
        csv_file.write_text("slug,time_end\n  project_hail_mary  ,  2026-03-23  \n")
        with patch("backfill.BACKFILL_CSV_PATH", str(csv_file)):
            result = load_backfill_config()
            assert result == [{"slug": "project_hail_mary", "time_end": "2026-03-23"}]

    def test_missing_file_returns_empty(self, tmp_path):
        with patch("backfill.BACKFILL_CSV_PATH", str(tmp_path / "nope.csv")):
            assert load_backfill_config() == []

    def test_empty_csv_returns_empty(self, tmp_path):
        csv_file = tmp_path / "backfill_movies.csv"
        csv_file.write_text("slug,time_end\n")
        with patch("backfill.BACKFILL_CSV_PATH", str(csv_file)):
            assert load_backfill_config() == []

    def test_slug_only_csv_no_time_end(self, tmp_path):
        """CSV with only slug column (no time_end) still works -- backward compat."""
        csv_file = tmp_path / "backfill_movies.csv"
        csv_file.write_text("slug\nproject_hail_mary\nthunderbolts\n")
        with patch("backfill.BACKFILL_CSV_PATH", str(csv_file)):
            result = load_backfill_config()
            assert result == [
                {"slug": "project_hail_mary", "time_end": None},
                {"slug": "thunderbolts", "time_end": None},
            ]

    def test_blank_time_end_is_none(self, tmp_path):
        """Rows with blank time_end get None (no cutoff)."""
        csv_file = tmp_path / "backfill_movies.csv"
        csv_file.write_text("slug,time_end\nproject_hail_mary,\nthunderbolts,2025-05-05\n")
        with patch("backfill.BACKFILL_CSV_PATH", str(csv_file)):
            result = load_backfill_config()
            assert result[0] == {"slug": "project_hail_mary", "time_end": None}
            assert result[1] == {"slug": "thunderbolts", "time_end": "2025-05-05"}


class TestParseTimeEnd:
    def test_returns_next_day_midnight_utc(self):
        result = _parse_time_end("2026-03-23")
        assert result == datetime(2026, 3, 24, 0, 0, 0, tzinfo=timezone.utc)

    def test_includes_reviews_on_given_date(self):
        """A review at 23:59:59 on the given date should be < cutoff."""
        cutoff = _parse_time_end("2026-03-23")
        review_ts = datetime(2026, 3, 23, 23, 59, 59, tzinfo=timezone.utc)
        assert review_ts < cutoff

    def test_excludes_reviews_after_given_date(self):
        """A review at 00:00:00 the next day should be >= cutoff."""
        cutoff = _parse_time_end("2026-03-23")
        review_ts = datetime(2026, 3, 24, 0, 0, 0, tzinfo=timezone.utc)
        assert review_ts >= cutoff

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _parse_time_end("not-a-date")

    def test_wrong_format_raises(self):
        with pytest.raises(ValueError):
            _parse_time_end("03/23/2026")


class TestBackfillArgparse:
    def test_neither_movie_nor_all_exits(self):
        with patch("sys.argv", ["backfill.py"]):
            with pytest.raises(SystemExit):
                backfill_main()

    def test_movie_and_all_mutually_exclusive(self):
        with patch("sys.argv", ["backfill.py", "--movie", "test", "--all"]):
            with pytest.raises(SystemExit):
                backfill_main()

    def test_time_end_requires_movie(self):
        with patch("sys.argv", ["backfill.py", "--all", "--time-end", "2026-02-21"]):
            with pytest.raises(SystemExit):
                backfill_main()

    def test_time_end_invalid_format(self):
        with patch("sys.argv", ["backfill.py", "--movie", "test", "--time-end", "bad"]):
            with pytest.raises(SystemExit):
                backfill_main()
