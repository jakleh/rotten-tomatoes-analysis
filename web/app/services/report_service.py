"""Report service — collects report data and generates PDF with fpdf2 + matplotlib."""

import io
import sqlite3
from datetime import datetime, timezone

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from app.cache import cache_get, cache_set, make_key
from app.math.critics import publication_counts, top_critic_split
from app.math.sentiment import (
    current_tomatometer,
    sentiment_counts,
    tomatometer_over_time,
)
from app.math.timing import avg_reviews_per_day, cumulative_reviews, reviews_per_bucket


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


def get_report_data(conn: sqlite3.Connection, movie: str) -> dict:
    """Collect all data needed for a report preview or PDF."""
    key = make_key("report_data", movie)
    cached = cache_get(key)
    if cached is not None:
        return cached

    reviews = _fetch_reviews(conn, movie)
    counts = sentiment_counts(reviews)
    tomatometer = current_tomatometer(reviews)
    critic_split = top_critic_split(reviews)
    avg_per_day = avg_reviews_per_day(reviews)
    pubs = publication_counts(reviews, top_n=10)
    volume = reviews_per_bucket(reviews, bucket="day")
    tomato_points = tomatometer_over_time(reviews)
    cumul = cumulative_reviews(reviews)

    data = {
        "movie": movie,
        "movie_display": (
            movie.replace("_", " ").title() if movie != "all" else "All Movies"
        ),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total_reviews": len(reviews),
        "tomatometer": tomatometer,
        "positive": counts["positive"],
        "negative": counts["negative"],
        "unknown": counts["unknown"],
        "top_critic_pct": critic_split["top"]["pct"],
        "top_critic_total": critic_split["top"]["total"],
        "regular_critic_pct": critic_split["regular"]["pct"],
        "regular_critic_total": critic_split["regular"]["total"],
        "avg_per_day": avg_per_day,
        "publications": pubs,
        "volume": volume,
        "tomatometer_points": tomato_points,
        "cumulative": cumul,
    }
    cache_set(key, data)
    return data


# ---------------------------------------------------------------------------
# Matplotlib chart renderers — each returns a BytesIO PNG buffer
# ---------------------------------------------------------------------------

def _render_tomatometer_chart(points: list[dict]) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7.5, 3))
    if points:
        timestamps = [p["timestamp"] for p in points]
        scores = [p["score"] for p in points]
        ax.plot(range(len(timestamps)), scores, color="#e53935", linewidth=1.5)
        ax.set_ylim(0, 100)
        ax.set_ylabel("Score (%)", fontsize=8)
        if len(timestamps) > 6:
            step = max(1, len(timestamps) // 6)
            ticks = list(range(0, len(timestamps), step))
            ax.set_xticks(ticks)
            ax.set_xticklabels(
                [timestamps[i][:10] for i in ticks],
                rotation=45, ha="right", fontsize=7,
            )
        else:
            ax.set_xticks(range(len(timestamps)))
            ax.set_xticklabels(
                [t[:10] for t in timestamps], rotation=45, ha="right", fontsize=7,
            )
    ax.set_title("Tomatometer Over Time", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _render_volume_chart(volume: list[dict]) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7.5, 3))
    if volume:
        buckets = [v["bucket"] for v in volume]
        counts = [v["count"] for v in volume]
        ax.bar(range(len(buckets)), counts, color="#1a73e8")
        ax.set_ylabel("Count", fontsize=8)
        if len(buckets) > 8:
            step = max(1, len(buckets) // 8)
            ticks = list(range(0, len(buckets), step))
            ax.set_xticks(ticks)
            ax.set_xticklabels(
                [buckets[i] for i in ticks], rotation=45, ha="right", fontsize=7,
            )
        else:
            ax.set_xticks(range(len(buckets)))
            ax.set_xticklabels(buckets, rotation=45, ha="right", fontsize=7)
    ax.set_title("Reviews Per Day", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


def _render_cumulative_chart(cumul: list[dict]) -> io.BytesIO:
    fig, ax = plt.subplots(figsize=(7.5, 3))
    if cumul:
        timestamps = [c["timestamp"] for c in cumul]
        counts = [c["cumulative"] for c in cumul]
        ax.fill_between(range(len(timestamps)), counts, alpha=0.1, color="#1a73e8")
        ax.plot(range(len(timestamps)), counts, color="#1a73e8", linewidth=1.5)
        ax.set_ylabel("Total Reviews", fontsize=8)
        if len(timestamps) > 6:
            step = max(1, len(timestamps) // 6)
            ticks = list(range(0, len(timestamps), step))
            ax.set_xticks(ticks)
            ax.set_xticklabels(
                [timestamps[i][:10] for i in ticks],
                rotation=45, ha="right", fontsize=7,
            )
        else:
            ax.set_xticks(range(len(timestamps)))
            ax.set_xticklabels(
                [t[:10] for t in timestamps], rotation=45, ha="right", fontsize=7,
            )
    ax.set_title("Cumulative Reviews", fontsize=10)
    ax.grid(axis="y", alpha=0.3)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# PDF generation
# ---------------------------------------------------------------------------

class _ReportPDF(FPDF):
    """FPDF subclass with page numbering footer."""

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, f"Page {self.page_no()}" + "/{nb}", align="C")


def generate_pdf(conn: sqlite3.Connection, movie: str) -> bytes:
    """Generate a full PDF report. Caller should hold the render semaphore."""
    data = get_report_data(conn, movie)

    pdf = _ReportPDF()
    pdf.alias_nb_pages()
    pdf.set_auto_page_break(auto=True, margin=20)

    # --- Title page ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 28)
    pdf.cell(0, 60, "", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.cell(0, 15, "Rotten Tomatoes Analysis", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "", 18)
    pdf.cell(0, 12, data["movie_display"], new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.ln(8)
    pdf.cell(0, 8, f"Generated {data['generated_at']}", new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")

    # --- Summary ---
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Summary", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    tm_str = f"{data['tomatometer']}%" if data["tomatometer"] is not None else "N/A"
    tc_str = (
        f"{data['top_critic_pct']}% ({data['top_critic_total']} reviews)"
        if data["top_critic_pct"] is not None
        else "N/A"
    )
    stats_rows = [
        ("Tomatometer", tm_str),
        ("Total Reviews", str(data["total_reviews"])),
        ("Positive", str(data["positive"])),
        ("Negative", str(data["negative"])),
        ("Unknown", str(data["unknown"])),
        ("Top Critic Score", tc_str),
        ("Avg Reviews / Day", str(data["avg_per_day"])),
    ]

    pdf.set_fill_color(245, 245, 245)
    for i, (label, value) in enumerate(stats_rows):
        fill = i % 2 == 0
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(70, 8, label, fill=fill)
        pdf.set_font("Helvetica", "", 10)
        pdf.cell(0, 8, value, new_x=XPos.LMARGIN, new_y=YPos.NEXT, fill=fill)

    # --- Charts (render one at a time to minimize peak memory) ---
    pdf.ln(8)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Charts", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    buf = _render_tomatometer_chart(data["tomatometer_points"])
    pdf.image(buf, x=10, w=190)
    buf.close()
    pdf.ln(4)

    buf = _render_volume_chart(data["volume"])
    pdf.image(buf, x=10, w=190)
    buf.close()

    pdf.add_page()

    buf = _render_cumulative_chart(data["cumulative"])
    pdf.image(buf, x=10, w=190)
    buf.close()

    # --- Publications table ---
    if data["publications"]:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(0, 10, "Top Publications", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(4)

        pdf.set_font("Helvetica", "B", 10)
        pdf.set_fill_color(250, 250, 250)
        pdf.cell(130, 8, "Publication", border=1, fill=True)
        pdf.cell(40, 8, "Reviews", border=1, fill=True, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

        pdf.set_font("Helvetica", "", 10)
        for pub in data["publications"]:
            pdf.cell(130, 8, pub["publication"][:50], border=1)
            pdf.cell(40, 8, str(pub["count"]), border=1, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    return bytes(pdf.output())
