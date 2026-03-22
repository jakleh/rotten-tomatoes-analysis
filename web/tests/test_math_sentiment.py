"""Tests for math.sentiment — tomatometer calculations."""

from app.math.sentiment import current_tomatometer, sentiment_counts, tomatometer_over_time


class TestSentimentCounts:
    def test_empty(self):
        assert sentiment_counts([]) == {"positive": 0, "negative": 0, "unknown": 0}

    def test_all_positive(self):
        reviews = [{"tomatometer_sentiment": "positive"} for _ in range(5)]
        assert sentiment_counts(reviews) == {"positive": 5, "negative": 0, "unknown": 0}

    def test_mixed(self):
        reviews = [
            {"tomatometer_sentiment": "positive"},
            {"tomatometer_sentiment": "negative"},
            {"tomatometer_sentiment": "positive"},
            {"tomatometer_sentiment": None},
            {},
        ]
        assert sentiment_counts(reviews) == {"positive": 2, "negative": 1, "unknown": 2}

    def test_unknown_values(self):
        reviews = [
            {"tomatometer_sentiment": ""},
            {"tomatometer_sentiment": "rotten"},
        ]
        assert sentiment_counts(reviews) == {"positive": 0, "negative": 0, "unknown": 2}


class TestCurrentTomatometer:
    def test_empty(self):
        assert current_tomatometer([]) is None

    def test_all_unknown(self):
        assert current_tomatometer([{"tomatometer_sentiment": None}]) is None

    def test_all_positive(self):
        reviews = [{"tomatometer_sentiment": "positive"} for _ in range(3)]
        assert current_tomatometer(reviews) == 100.0

    def test_all_negative(self):
        reviews = [{"tomatometer_sentiment": "negative"} for _ in range(3)]
        assert current_tomatometer(reviews) == 0.0

    def test_fifty_fifty(self):
        reviews = [
            {"tomatometer_sentiment": "positive"},
            {"tomatometer_sentiment": "negative"},
        ]
        assert current_tomatometer(reviews) == 50.0

    def test_ignores_unknown(self):
        reviews = [
            {"tomatometer_sentiment": "positive"},
            {"tomatometer_sentiment": "negative"},
            {"tomatometer_sentiment": None},
        ]
        assert current_tomatometer(reviews) == 50.0


class TestTomatometerOverTime:
    def test_empty(self):
        assert tomatometer_over_time([]) == []

    def test_only_unknown(self):
        reviews = [{"timestamp": "2026-01-01 12:00:00", "tomatometer_sentiment": None}]
        assert tomatometer_over_time(reviews) == []

    def test_single_positive(self):
        reviews = [{"timestamp": "2026-01-01 12:00:00", "tomatometer_sentiment": "positive"}]
        result = tomatometer_over_time(reviews)
        assert len(result) == 1
        assert result[0]["score"] == 100.0
        assert result[0]["positive"] == 1
        assert result[0]["negative"] == 0

    def test_cumulative(self):
        reviews = [
            {"timestamp": "2026-01-01 01:00:00", "tomatometer_sentiment": "positive"},
            {"timestamp": "2026-01-01 02:00:00", "tomatometer_sentiment": "negative"},
            {"timestamp": "2026-01-01 03:00:00", "tomatometer_sentiment": "positive"},
        ]
        result = tomatometer_over_time(reviews)
        assert len(result) == 3
        assert result[0]["score"] == 100.0
        assert result[1]["score"] == 50.0
        assert result[2]["score"] == 66.7

    def test_skips_unknown_in_sequence(self):
        reviews = [
            {"timestamp": "2026-01-01 01:00:00", "tomatometer_sentiment": "positive"},
            {"timestamp": "2026-01-01 02:00:00", "tomatometer_sentiment": None},
            {"timestamp": "2026-01-01 03:00:00", "tomatometer_sentiment": "negative"},
        ]
        result = tomatometer_over_time(reviews)
        assert len(result) == 2
        assert result[0]["score"] == 100.0
        assert result[1]["score"] == 50.0
