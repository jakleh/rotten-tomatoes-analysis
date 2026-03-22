"""Score analytics — distribution of subjective scores."""

from collections import Counter


def score_distribution(reviews: list[dict]) -> list[dict]:
    """Count occurrences of each subjective_score value.

    Returns list of {"score": value, "count": N} sorted by count descending.
    Skips reviews with no subjective_score.
    """
    counter: Counter[str] = Counter()
    for r in reviews:
        score = r.get("subjective_score")
        if score:
            counter[score] += 1
    return [
        {"score": s, "count": c}
        for s, c in counter.most_common()
    ]
