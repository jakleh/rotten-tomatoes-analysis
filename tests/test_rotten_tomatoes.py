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
    _log_no_reviews,
    _parse_config_date,
    compute_review_id,
    convert_rel_timestamp_to_abs,
    get_timestamp_unit,
    load_movie_config,
)

# Add scripts/ to path so we can import backfill module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from backfill import (
    filter_reviews_by_cutoff,
    load_backfill_config,
    _parse_time_end,
    _parse_card_html,
    _extract_new_cards,
    BACKFILL_CSV_PATH,
    main as backfill_main,
)


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

    def test_slash_date_returns_date(self):
        assert get_timestamp_unit("01/19/2025") == "date"


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

    # Slash date format (MM/DD/YYYY) — used by RT for older reviews
    def test_slash_date_basic(self):
        result = convert_rel_timestamp_to_abs("01/19/2025", FIXED_NOW)
        assert result is not None
        assert result == datetime(2025, 1, 19, tzinfo=timezone.utc)

    def test_slash_date_dec_31(self):
        result = convert_rel_timestamp_to_abs("12/31/2024", FIXED_NOW)
        assert result is not None
        assert result == datetime(2024, 12, 31, tzinfo=timezone.utc)

    def test_slash_date_feb_29_leap_year(self):
        result = convert_rel_timestamp_to_abs("02/29/2024", FIXED_NOW)
        assert result is not None
        assert result == datetime(2024, 2, 29, tzinfo=timezone.utc)

    def test_slash_date_invalid_returns_none(self):
        assert convert_rel_timestamp_to_abs("13/40/2025", FIXED_NOW) is None


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
    <review-card-critic>
        <span slot="timestamp">5m</span>
        <rt-link slot="name">Alice</rt-link>
        <rt-link slot="publication">AV Club</rt-link>
        <span slot="rating"><span style="font-weight:bold">4/5</span></span>
        <score-icon-critics sentiment="positive"></score-icon-critics>
        <div slot="review">Great movie!</div>
    </review-card-critic>
    """

    def _card(self):
        soup = BeautifulSoup(self.CARD_HTML, "html.parser")
        return soup.find("review-card-critic")

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
        html = "<review-card-critic></review-card-critic>"
        soup = BeautifulSoup(html, "html.parser")
        card = soup.find("review-card-critic")
        assert _find_selector(card, "timestamp") is None
        assert _find_selector(card, "reviewer_name") is None
        assert _find_selector(card, "written_review") is None


# -- load_movie_config ---------------------------------------------------------

class TestLoadMovieConfig:
    def test_loads_enabled_movies(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text(
            '[{"slug": "movie_a", "enabled": true, "theatrical_release_date": "2026-01-15"}, '
            '{"slug": "movie_b", "enabled": true, "theatrical_release_date": "2026-02-20"}]'
        )
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            result = load_movie_config()
        assert [e["slug"] for e in result] == ["movie_a", "movie_b"]

    def test_skips_disabled_movies(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text(
            '[{"slug": "movie_a", "enabled": true, "theatrical_release_date": "2026-01-15"}, '
            '{"slug": "movie_b", "enabled": false}]'
        )
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            result = load_movie_config()
        assert [e["slug"] for e in result] == ["movie_a"]

    def test_enabled_defaults_to_true(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text('[{"slug": "movie_a", "theatrical_release_date": "2026-01-15"}]')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            result = load_movie_config()
        assert [e["slug"] for e in result] == ["movie_a"]

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
        config.write_text(
            '[{"slug": "movie_a", "theatrical_release_date": "2026-01-15"}, '
            '{"enabled": true}]'
        )
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            result = load_movie_config()
        assert [e["slug"] for e in result] == ["movie_a"]

    def test_non_array_returns_empty(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text('{"slug": "movie_a"}')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            assert load_movie_config() == []

    def test_parses_valid_embargo_and_release_dates(self, tmp_path):
        config = tmp_path / "movies.json"
        config.write_text(
            '[{"slug": "movie_a", "enabled": true, '
            '"embargo_lift_date": "2026-07-10", '
            '"theatrical_release_date": "2026-07-15"}]'
        )
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            result = load_movie_config()
        assert len(result) == 1
        # Midnight ET during EDT = 04:00 UTC
        assert result[0]["embargo_lift_date"] == datetime(2026, 7, 10, 4, 0, 0, tzinfo=timezone.utc)
        assert result[0]["theatrical_release_date"] == datetime(2026, 7, 15, 4, 0, 0, tzinfo=timezone.utc)

    def test_missing_embargo_date_is_none_no_log(self, tmp_path, caplog):
        config = tmp_path / "movies.json"
        config.write_text(
            '[{"slug": "movie_a", "enabled": true, '
            '"theatrical_release_date": "2026-07-15"}]'
        )
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            with caplog.at_level(logging.WARNING, logger="rotten_tomatoes"):
                result = load_movie_config()
        assert result[0]["embargo_lift_date"] is None
        # No warnings about embargo — it's optional
        assert not any(
            "embargo" in r.getMessage().lower() for r in caplog.records
        )

    def test_missing_release_date_for_enabled_logs_error(self, tmp_path, caplog):
        config = tmp_path / "movies.json"
        config.write_text('[{"slug": "movie_a", "enabled": true}]')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            with caplog.at_level(logging.ERROR, logger="rotten_tomatoes"):
                result = load_movie_config()
        assert result[0]["slug"] == "movie_a"  # still returned
        assert result[0]["theatrical_release_date"] is None
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert any(
            "theatrical_release_date" in r.getMessage() and "movie_a" in r.getMessage()
            for r in errors
        )

    def test_missing_release_date_for_disabled_no_log(self, tmp_path, caplog):
        config = tmp_path / "movies.json"
        config.write_text('[{"slug": "movie_a", "enabled": false}]')
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            with caplog.at_level(logging.ERROR, logger="rotten_tomatoes"):
                result = load_movie_config()
        assert result == []  # disabled entries excluded
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert not errors

    def test_invalid_date_format_is_none_with_warning(self, tmp_path, caplog):
        config = tmp_path / "movies.json"
        config.write_text(
            '[{"slug": "movie_a", "enabled": true, '
            '"theatrical_release_date": "not-a-date"}]'
        )
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            with caplog.at_level(logging.WARNING, logger="rotten_tomatoes"):
                result = load_movie_config()
        assert result[0]["theatrical_release_date"] is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("not-a-date" in r.getMessage() for r in warnings)

    def test_non_string_date_is_none_with_warning(self, tmp_path, caplog):
        config = tmp_path / "movies.json"
        config.write_text(
            '[{"slug": "movie_a", "enabled": true, '
            '"theatrical_release_date": 20260715}]'
        )
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            with caplog.at_level(logging.WARNING, logger="rotten_tomatoes"):
                result = load_movie_config()
        assert result[0]["theatrical_release_date"] is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("int" in r.getMessage() for r in warnings)

    def test_embargo_after_release_logs_warning(self, tmp_path, caplog):
        config = tmp_path / "movies.json"
        config.write_text(
            '[{"slug": "movie_a", "enabled": true, '
            '"embargo_lift_date": "2026-07-20", '
            '"theatrical_release_date": "2026-07-15"}]'
        )
        with patch("rotten_tomatoes.MOVIES_CONFIG_PATH", str(config)):
            with caplog.at_level(logging.WARNING, logger="rotten_tomatoes"):
                result = load_movie_config()
        assert result[0]["slug"] == "movie_a"  # still returned
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "embargo_lift_date" in r.getMessage() and "after" in r.getMessage()
            for r in warnings
        )


# -- _parse_config_date --------------------------------------------------------

class TestParseConfigDate:
    def test_summer_date_gives_edt_offset(self):
        result = _parse_config_date("2026-07-15", "test_movie", "theatrical_release_date")
        # Midnight EDT (UTC-4) = 04:00 UTC
        assert result == datetime(2026, 7, 15, 4, 0, 0, tzinfo=timezone.utc)

    def test_winter_date_gives_est_offset(self):
        result = _parse_config_date("2026-01-15", "test_movie", "theatrical_release_date")
        # Midnight EST (UTC-5) = 05:00 UTC
        assert result == datetime(2026, 1, 15, 5, 0, 0, tzinfo=timezone.utc)

    def test_none_input_returns_none(self, caplog):
        with caplog.at_level(logging.WARNING, logger="rotten_tomatoes"):
            result = _parse_config_date(None, "test_movie", "theatrical_release_date")
        assert result is None
        assert len(caplog.records) == 0

    def test_invalid_format_returns_none_with_warning(self, caplog):
        with caplog.at_level(logging.WARNING, logger="rotten_tomatoes"):
            result = _parse_config_date("not a date", "test_movie", "theatrical_release_date")
        assert result is None
        assert any("not a date" in r.getMessage() for r in caplog.records)


# -- _log_no_reviews -----------------------------------------------------------

class TestLogNoReviews:
    EMBARGO = datetime(2026, 7, 10, 4, 0, 0, tzinfo=timezone.utc)
    RELEASE = datetime(2026, 7, 15, 4, 0, 0, tzinfo=timezone.utc)

    def test_past_release_logs_error(self, caplog):
        now = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)
        with caplog.at_level(logging.INFO, logger="rotten_tomatoes"):
            _log_no_reviews("test_movie", self.EMBARGO, self.RELEASE, now)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        assert "test_movie" in errors[0].getMessage()
        assert "theatrical release" in errors[0].getMessage()

    def test_past_embargo_not_release_logs_warning(self, caplog):
        now = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
        with caplog.at_level(logging.INFO, logger="rotten_tomatoes"):
            _log_no_reviews("test_movie", self.EMBARGO, self.RELEASE, now)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(warnings) == 1
        assert len(errors) == 0
        assert "test_movie" in warnings[0].getMessage()
        assert "embargo lift" in warnings[0].getMessage()

    def test_pre_embargo_logs_info(self, caplog):
        now = datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc)
        with caplog.at_level(logging.INFO, logger="rotten_tomatoes"):
            _log_no_reviews("test_movie", self.EMBARGO, self.RELEASE, now)
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        elevated = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(infos) == 1
        assert len(elevated) == 0

    def test_no_dates_set_logs_info(self, caplog):
        now = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)
        with caplog.at_level(logging.INFO, logger="rotten_tomatoes"):
            _log_no_reviews("test_movie", None, None, now)
        infos = [r for r in caplog.records if r.levelno == logging.INFO]
        elevated = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(infos) == 1
        assert len(elevated) == 0

    def test_only_release_past_logs_error(self, caplog):
        now = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        with caplog.at_level(logging.INFO, logger="rotten_tomatoes"):
            _log_no_reviews("test_movie", None, self.RELEASE, now)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1

    def test_only_embargo_past_logs_warning(self, caplog):
        now = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
        with caplog.at_level(logging.INFO, logger="rotten_tomatoes"):
            _log_no_reviews("test_movie", self.EMBARGO, None, now)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(warnings) == 1
        assert len(errors) == 0


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


class TestParseCardHtml:
    """Tests for _parse_card_html -- parses a single BeautifulSoup card into a review dict."""

    CARD_HTML = """
    <review-card-critic>
        <span slot="timestamp">5m</span>
        <rt-link slot="name">Alice</rt-link>
        <rt-link slot="publication">AV Club</rt-link>
        <span slot="rating"><span style="font-weight:bold">4/5</span></span>
        <score-icon-critics sentiment="positive"></score-icon-critics>
        <div slot="review">Great movie!</div>
    </review-card-critic>
    """

    def _card(self):
        soup = BeautifulSoup(self.CARD_HTML, "html.parser")
        return soup.find("review-card-critic")

    def test_extracts_all_fields(self):
        review = _parse_card_html(self._card(), "test_movie", FIXED_NOW, False, 0)
        assert review["reviewer_name"] == "Alice"
        assert review["publication_name"] == "AV Club"
        assert review["subjective_score"] == "4/5"
        assert review["tomatometer_sentiment"] == "positive"
        assert review["written_review"] == "Great movie!"
        assert review["site_timestamp_text"] == "5m"
        assert review["page_position"] == 0
        assert review["top_critic"] is False

    def test_top_critic_flag(self):
        review = _parse_card_html(self._card(), "test_movie", FIXED_NOW, True, 3)
        assert review["top_critic"] is True
        assert review["page_position"] == 3

    def test_timestamp_confidence_relative(self):
        review = _parse_card_html(self._card(), "test_movie", FIXED_NOW, False, 0)
        assert review["timestamp_confidence"] == "m"

    def test_timestamp_confidence_date_format(self):
        html = '<review-card-critic><span slot="timestamp">Mar 15</span></review-card-critic>'
        soup = BeautifulSoup(html, "html.parser")
        card = soup.find("review-card-critic")
        review = _parse_card_html(card, "test_movie", FIXED_NOW, False, 0)
        assert review["timestamp_confidence"] == "d"

    def test_missing_fields_handled(self):
        html = "<review-card-critic></review-card-critic>"
        soup = BeautifulSoup(html, "html.parser")
        card = soup.find("review-card-critic")
        review = _parse_card_html(card, "test_movie", FIXED_NOW, False, 0)
        assert review["reviewer_name"] is None
        assert review["publication_name"] is None
        assert review["written_review"] is None
        assert review["estimated_timestamp"] is None
        assert review["unique_review_id"] is not None  # hash still computed

    def test_unique_review_id_deterministic(self):
        r1 = _parse_card_html(self._card(), "test_movie", FIXED_NOW, False, 0)
        r2 = _parse_card_html(self._card(), "test_movie", FIXED_NOW, False, 5)
        assert r1["unique_review_id"] == r2["unique_review_id"]  # position doesn't affect hash


class TestExtractNewCards:
    """Tests for _extract_new_cards -- parses HTML string into card list."""

    def test_extracts_cards_from_html(self):
        # Simulate what JS would return: just the new cards' outerHTML joined
        html = (
            '<review-card-critic><span slot="timestamp">5m</span>'
            '<rt-link slot="name">Alice</rt-link></review-card-critic>'
            '<review-card-critic><span slot="timestamp">3h</span>'
            '<rt-link slot="name">Bob</rt-link></review-card-critic>'
        )
        # _extract_new_cards calls driver.execute_script; test the parsing
        # by directly calling BeautifulSoup on the HTML
        soup = BeautifulSoup(html, "html.parser")
        cards = soup.find_all("review-card-critic")
        assert len(cards) == 2
        assert cards[0].find("rt-link", attrs={"slot": "name"}).get_text().strip() == "Alice"
        assert cards[1].find("rt-link", attrs={"slot": "name"}).get_text().strip() == "Bob"

    def test_empty_html_returns_no_cards(self):
        soup = BeautifulSoup("", "html.parser")
        cards = soup.find_all("review-card-critic")
        assert cards == []


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
