"""Timing analytics — review velocity and cadence."""

from collections import Counter


def reviews_per_bucket(reviews: list[dict], bucket: str = "day") -> list[dict]:
    """Group reviews into time buckets and count.

    bucket: "day" groups by YYYY-MM-DD, "hour" by YYYY-MM-DD HH.
    Returns list of {"bucket": label, "count": N} sorted chronologically.
    """
    counter: Counter[str] = Counter()
    for r in reviews:
        ts = r.get("timestamp", "")
        if bucket == "hour":
            label = ts[:13]  # "2026-01-01 12"
        else:
            label = ts[:10]  # "2026-01-01"
        counter[label] += 1
    return [{"bucket": k, "count": v} for k, v in sorted(counter.items())]


def cumulative_reviews(reviews: list[dict]) -> list[dict]:
    """Cumulative review count over time, oldest-first.

    Expects reviews sorted by timestamp ascending.
    Returns list of {"timestamp", "cumulative"}.
    """
    points: list[dict] = []
    for i, r in enumerate(reviews, 1):
        points.append({"timestamp": r["timestamp"], "cumulative": i})
    return points


def avg_reviews_per_day(reviews: list[dict]) -> float:
    """Average number of reviews per calendar day that has at least one review."""
    if not reviews:
        return 0.0
    days: set[str] = set()
    for r in reviews:
        days.add(r.get("timestamp", "")[:10])
    days.discard("")
    if not days:
        return 0.0
    return round(len(reviews) / len(days), 1)
