"""Critic analytics — top critic vs all, publication breakdown."""

from collections import Counter


def top_critic_split(reviews: list[dict]) -> dict:
    """Compare sentiment between top critics and regular critics.

    Returns {
        "top": {"positive": N, "negative": N, "total": N, "pct": float|None},
        "regular": {"positive": N, "negative": N, "total": N, "pct": float|None},
    }
    """
    groups: dict[str, dict[str, int]] = {
        "top": {"positive": 0, "negative": 0},
        "regular": {"positive": 0, "negative": 0},
    }
    for r in reviews:
        key = "top" if r.get("top_critic") else "regular"
        s = r.get("tomatometer_sentiment")
        if s in ("positive", "negative"):
            groups[key][s] += 1

    result = {}
    for key, counts in groups.items():
        total = counts["positive"] + counts["negative"]
        pct = round(counts["positive"] / total * 100, 1) if total > 0 else None
        result[key] = {
            "positive": counts["positive"],
            "negative": counts["negative"],
            "total": total,
            "pct": pct,
        }
    return result


def publication_counts(reviews: list[dict], top_n: int = 10) -> list[dict]:
    """Top N publications by review count.

    Returns list of {"publication": name, "count": N} sorted descending.
    """
    counter: Counter[str] = Counter()
    for r in reviews:
        pub = r.get("publication_name")
        if pub:
            counter[pub] += 1
    return [
        {"publication": pub, "count": count}
        for pub, count in counter.most_common(top_n)
    ]
