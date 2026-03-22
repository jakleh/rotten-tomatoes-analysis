"""Tests for math.critics — top critic vs regular, publication breakdown."""

from app.math.critics import publication_counts, top_critic_split


class TestTopCriticSplit:
    def test_empty(self):
        result = top_critic_split([])
        assert result["top"]["total"] == 0
        assert result["top"]["pct"] is None
        assert result["regular"]["total"] == 0
        assert result["regular"]["pct"] is None

    def test_all_top_critics(self):
        reviews = [
            {"top_critic": 1, "tomatometer_sentiment": "positive"},
            {"top_critic": 1, "tomatometer_sentiment": "negative"},
        ]
        result = top_critic_split(reviews)
        assert result["top"]["positive"] == 1
        assert result["top"]["negative"] == 1
        assert result["top"]["pct"] == 50.0
        assert result["regular"]["total"] == 0

    def test_mixed(self):
        reviews = [
            {"top_critic": 1, "tomatometer_sentiment": "positive"},
            {"top_critic": 1, "tomatometer_sentiment": "positive"},
            {"top_critic": 0, "tomatometer_sentiment": "negative"},
            {"top_critic": 0, "tomatometer_sentiment": "positive"},
        ]
        result = top_critic_split(reviews)
        assert result["top"]["pct"] == 100.0
        assert result["regular"]["pct"] == 50.0

    def test_skips_unknown_sentiment(self):
        reviews = [
            {"top_critic": 1, "tomatometer_sentiment": "positive"},
            {"top_critic": 1, "tomatometer_sentiment": None},
        ]
        result = top_critic_split(reviews)
        assert result["top"]["total"] == 1
        assert result["top"]["pct"] == 100.0


class TestPublicationCounts:
    def test_empty(self):
        assert publication_counts([]) == []

    def test_counts_sorted(self):
        reviews = [
            {"publication_name": "NYT"},
            {"publication_name": "NYT"},
            {"publication_name": "Variety"},
        ]
        result = publication_counts(reviews)
        assert result[0] == {"publication": "NYT", "count": 2}
        assert result[1] == {"publication": "Variety", "count": 1}

    def test_top_n(self):
        reviews = [
            {"publication_name": f"Pub{i}"}
            for i in range(20)
        ]
        result = publication_counts(reviews, top_n=5)
        assert len(result) == 5

    def test_skips_none(self):
        reviews = [
            {"publication_name": None},
            {"publication_name": "Variety"},
        ]
        result = publication_counts(reviews)
        assert len(result) == 1
        assert result[0]["publication"] == "Variety"
