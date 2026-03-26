"""Analytics service — orchestrates DB queries, math, Plotly specs, and caching."""

import json
import sqlite3

from app.cache import cache_get, cache_set, make_key
from app.math.sentiment import (
    current_tomatometer,
    sentiment_counts,
    tomatometer_over_time,
)
from app.math.timing import avg_reviews_per_day, cumulative_reviews, reviews_per_bucket
from app.math.critics import publication_counts, top_critic_split

# Available chart types — order matches the dropdown
CHART_TYPES = [
    ("tomatometer_over_time", "Tomatometer Over Time"),
    ("review_volume", "Review Volume (per day)"),
    ("top_critic_comparison", "Top Critic vs Regular"),
    ("cumulative_reviews", "Cumulative Reviews"),
]


def _fetch_reviews(conn: sqlite3.Connection, movie: str) -> list[dict]:
    """Fetch all reviews for a movie (or all), oldest-first."""
    if movie and movie != "all":
        rows = conn.execute(
            "SELECT * FROM reviews WHERE movie_slug = ? ORDER BY timestamp ASC",
            (movie,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM reviews ORDER BY timestamp ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_chart(conn: sqlite3.Connection, movie: str, chart: str) -> str:
    """Return Plotly JSON string for the requested chart type.

    Returns a JSON object with "data" and "layout" keys ready for Plotly.newPlot().
    """
    key = make_key("chart", movie, chart=chart)
    cached = cache_get(key)
    if cached is not None:
        return cached

    reviews = _fetch_reviews(conn, movie)
    spec = _build_chart_spec(reviews, chart)
    result = json.dumps(spec)
    cache_set(key, result)
    return result


def get_stats(conn: sqlite3.Connection, movie: str) -> dict:
    """Return summary statistics for the stats panel."""
    key = make_key("stats", movie)
    cached = cache_get(key)
    if cached is not None:
        return cached

    reviews = _fetch_reviews(conn, movie)
    counts = sentiment_counts(reviews)
    tomatometer = current_tomatometer(reviews)
    critic_split = top_critic_split(reviews)
    avg_per_day = avg_reviews_per_day(reviews)

    stats = {
        "total_reviews": len(reviews),
        "tomatometer": tomatometer,
        "positive": counts["positive"],
        "negative": counts["negative"],
        "unknown": counts["unknown"],
        "top_critic_pct": critic_split["top"]["pct"],
        "top_critic_total": critic_split["top"]["total"],
        "regular_critic_pct": critic_split["regular"]["pct"],
        "avg_per_day": avg_per_day,
    }
    cache_set(key, stats)
    return stats


def _build_chart_spec(reviews: list[dict], chart: str) -> dict:
    """Build a Plotly figure spec (data + layout) for the given chart type."""
    builders = {
        "tomatometer_over_time": _chart_tomatometer_over_time,
        "review_volume": _chart_review_volume,
        "top_critic_comparison": _chart_top_critic_comparison,
        "cumulative_reviews": _chart_cumulative_reviews,
    }
    builder = builders.get(chart, _chart_tomatometer_over_time)
    return builder(reviews)


def _chart_tomatometer_over_time(reviews: list[dict]) -> dict:
    points = tomatometer_over_time(reviews)
    scores = [p["score"] for p in points]
    if scores:
        y_min = max(0, min(scores) - 5)
        y_max = min(100, max(scores) + 5)
    else:
        y_min, y_max = 0, 100
    return {
        "data": [{
            "x": [p["timestamp"] for p in points],
            "y": scores,
            "type": "scatter",
            "mode": "lines",
            "name": "Tomatometer %",
            "line": {"color": "#e53935", "width": 2},
        }],
        "layout": {
            "title": "Tomatometer Over Time",
            "xaxis": {"title": "Time"},
            "yaxis": {"title": "Score (%)", "range": [y_min, y_max]},
            "margin": {"t": 40, "r": 20, "b": 50, "l": 50},
        },
    }


def _chart_review_volume(reviews: list[dict]) -> dict:
    buckets = reviews_per_bucket(reviews, bucket="day")
    return {
        "data": [{
            "x": [b["bucket"] for b in buckets],
            "y": [b["count"] for b in buckets],
            "type": "bar",
            "name": "Reviews",
            "marker": {"color": "#1a73e8"},
        }],
        "layout": {
            "title": "Reviews Per Day",
            "xaxis": {"title": "Date"},
            "yaxis": {"title": "Count"},
            "margin": {"t": 40, "r": 20, "b": 50, "l": 50},
        },
    }


def _chart_top_critic_comparison(reviews: list[dict]) -> dict:
    split = top_critic_split(reviews)
    categories = ["Top Critics", "Regular Critics"]
    positive_pcts = [split["top"]["pct"] or 0, split["regular"]["pct"] or 0]
    negative_pcts = [
        round(100 - (split["top"]["pct"] or 0), 1) if split["top"]["total"] > 0 else 0,
        round(100 - (split["regular"]["pct"] or 0), 1) if split["regular"]["total"] > 0 else 0,
    ]
    return {
        "data": [
            {
                "x": categories,
                "y": positive_pcts,
                "type": "bar",
                "name": "Positive %",
                "marker": {"color": "#4caf50"},
            },
            {
                "x": categories,
                "y": negative_pcts,
                "type": "bar",
                "name": "Negative %",
                "marker": {"color": "#e53935"},
            },
        ],
        "layout": {
            "title": "Top Critics vs Regular Critics",
            "barmode": "group",
            "yaxis": {"title": "Percentage", "range": [0, 100]},
            "margin": {"t": 40, "r": 20, "b": 50, "l": 50},
        },
    }


def _chart_cumulative_reviews(reviews: list[dict]) -> dict:
    points = cumulative_reviews(reviews)
    return {
        "data": [{
            "x": [p["timestamp"] for p in points],
            "y": [p["cumulative"] for p in points],
            "type": "scatter",
            "mode": "lines",
            "name": "Total Reviews",
            "line": {"color": "#1a73e8", "width": 2},
            "fill": "tozeroy",
            "fillcolor": "rgba(26, 115, 232, 0.1)",
        }],
        "layout": {
            "title": "Cumulative Reviews Over Time",
            "xaxis": {"title": "Time"},
            "yaxis": {"title": "Total Reviews"},
            "margin": {"t": 40, "r": 20, "b": 50, "l": 50},
        },
    }


