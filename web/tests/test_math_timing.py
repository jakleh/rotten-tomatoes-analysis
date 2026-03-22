"""Tests for math.timing — review velocity and cadence."""

from app.math.timing import avg_reviews_per_day, cumulative_reviews, reviews_per_bucket


class TestReviewsPerBucket:
    def test_empty(self):
        assert reviews_per_bucket([]) == []

    def test_day_bucket(self):
        reviews = [
            {"timestamp": "2026-01-01 10:00:00"},
            {"timestamp": "2026-01-01 14:00:00"},
            {"timestamp": "2026-01-02 09:00:00"},
        ]
        result = reviews_per_bucket(reviews, bucket="day")
        assert result == [
            {"bucket": "2026-01-01", "count": 2},
            {"bucket": "2026-01-02", "count": 1},
        ]

    def test_hour_bucket(self):
        reviews = [
            {"timestamp": "2026-01-01 10:05:00"},
            {"timestamp": "2026-01-01 10:30:00"},
            {"timestamp": "2026-01-01 11:00:00"},
        ]
        result = reviews_per_bucket(reviews, bucket="hour")
        assert result == [
            {"bucket": "2026-01-01 10", "count": 2},
            {"bucket": "2026-01-01 11", "count": 1},
        ]

    def test_sorted_chronologically(self):
        reviews = [
            {"timestamp": "2026-01-03 10:00:00"},
            {"timestamp": "2026-01-01 10:00:00"},
        ]
        result = reviews_per_bucket(reviews, bucket="day")
        assert result[0]["bucket"] == "2026-01-01"
        assert result[1]["bucket"] == "2026-01-03"


class TestCumulativeReviews:
    def test_empty(self):
        assert cumulative_reviews([]) == []

    def test_increments(self):
        reviews = [
            {"timestamp": "2026-01-01 01:00:00"},
            {"timestamp": "2026-01-01 02:00:00"},
            {"timestamp": "2026-01-01 03:00:00"},
        ]
        result = cumulative_reviews(reviews)
        assert [p["cumulative"] for p in result] == [1, 2, 3]

    def test_timestamps_preserved(self):
        reviews = [{"timestamp": "2026-01-01 12:00:00"}]
        result = cumulative_reviews(reviews)
        assert result[0]["timestamp"] == "2026-01-01 12:00:00"


class TestAvgReviewsPerDay:
    def test_empty(self):
        assert avg_reviews_per_day([]) == 0.0

    def test_single_day(self):
        reviews = [
            {"timestamp": "2026-01-01 10:00:00"},
            {"timestamp": "2026-01-01 14:00:00"},
        ]
        assert avg_reviews_per_day(reviews) == 2.0

    def test_multiple_days(self):
        reviews = [
            {"timestamp": "2026-01-01 10:00:00"},
            {"timestamp": "2026-01-01 14:00:00"},
            {"timestamp": "2026-01-02 09:00:00"},
        ]
        # 3 reviews / 2 days = 1.5
        assert avg_reviews_per_day(reviews) == 1.5

    def test_missing_timestamp(self):
        reviews = [{"timestamp": ""}, {"timestamp": "2026-01-01 10:00:00"}]
        # 2 reviews but only 1 valid day
        assert avg_reviews_per_day(reviews) == 2.0
