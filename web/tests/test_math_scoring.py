"""Tests for math.scoring — score distribution."""

from app.math.scoring import score_distribution


class TestScoreDistribution:
    def test_empty(self):
        assert score_distribution([]) == []

    def test_counts(self):
        reviews = [
            {"subjective_score": "3/5"},
            {"subjective_score": "3/5"},
            {"subjective_score": "4/5"},
        ]
        result = score_distribution(reviews)
        assert result[0] == {"score": "3/5", "count": 2}
        assert result[1] == {"score": "4/5", "count": 1}

    def test_skips_none(self):
        reviews = [
            {"subjective_score": None},
            {"subjective_score": ""},
            {"subjective_score": "A-"},
        ]
        result = score_distribution(reviews)
        assert len(result) == 1
        assert result[0]["score"] == "A-"

    def test_sorted_by_count_desc(self):
        reviews = [
            {"subjective_score": "B+"},
            {"subjective_score": "A"},
            {"subjective_score": "A"},
            {"subjective_score": "A"},
            {"subjective_score": "B+"},
        ]
        result = score_distribution(reviews)
        assert result[0]["score"] == "A"
        assert result[0]["count"] == 3
        assert result[1]["score"] == "B+"
        assert result[1]["count"] == 2
