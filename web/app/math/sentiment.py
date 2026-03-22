"""Sentiment analytics — tomatometer calculations on review lists."""


def sentiment_counts(reviews: list[dict]) -> dict[str, int]:
    """Count reviews by tomatometer_sentiment value.

    Returns {"positive": N, "negative": N, "unknown": N}.
    """
    counts: dict[str, int] = {"positive": 0, "negative": 0, "unknown": 0}
    for r in reviews:
        s = r.get("tomatometer_sentiment")
        if s in ("positive", "negative"):
            counts[s] += 1
        else:
            counts["unknown"] += 1
    return counts


def current_tomatometer(reviews: list[dict]) -> float | None:
    """Return current tomatometer as a percentage (0–100), or None if no scored reviews."""
    counts = sentiment_counts(reviews)
    scored = counts["positive"] + counts["negative"]
    if scored == 0:
        return None
    return round(counts["positive"] / scored * 100, 1)


def tomatometer_over_time(reviews: list[dict]) -> list[dict]:
    """Cumulative tomatometer at each review, oldest-first.

    Expects reviews sorted by timestamp ascending.
    Returns list of {"timestamp", "score", "positive", "negative", "total_scored"}.
    """
    positive = 0
    negative = 0
    points: list[dict] = []
    for r in reviews:
        s = r.get("tomatometer_sentiment")
        if s == "positive":
            positive += 1
        elif s == "negative":
            negative += 1
        else:
            continue  # skip unscored for tomatometer line
        total = positive + negative
        points.append({
            "timestamp": r["timestamp"],
            "score": round(positive / total * 100, 1),
            "positive": positive,
            "negative": negative,
            "total_scored": total,
        })
    return points
