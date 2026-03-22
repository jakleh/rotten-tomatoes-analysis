"""Paginated review queries against the reviews table."""

import sqlite3
from dataclasses import dataclass


@dataclass
class ReviewPage:
    """A page of reviews with pagination metadata."""

    reviews: list[dict]
    page: int
    per_page: int
    total: int

    @property
    def total_pages(self) -> int:
        if self.total == 0:
            return 1
        return (self.total + self.per_page - 1) // self.per_page

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.total_pages


def get_reviews_page(
    conn: sqlite3.Connection,
    page: int = 1,
    per_page: int = 25,
    movie: str | None = None,
) -> ReviewPage:
    """Return a paginated slice of reviews, newest first."""
    page = max(1, page)
    per_page = max(1, min(per_page, 100))

    if movie and movie != "all":
        count_row = conn.execute(
            "SELECT COUNT(*) FROM reviews WHERE movie_slug = ?", (movie,)
        ).fetchone()
        total = count_row[0]

        rows = conn.execute(
            "SELECT * FROM reviews WHERE movie_slug = ? ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (movie, per_page, (page - 1) * per_page),
        ).fetchall()
    else:
        count_row = conn.execute("SELECT COUNT(*) FROM reviews").fetchone()
        total = count_row[0]

        rows = conn.execute(
            "SELECT * FROM reviews ORDER BY timestamp DESC LIMIT ? OFFSET ?",
            (per_page, (page - 1) * per_page),
        ).fetchall()

    reviews = [dict(row) for row in rows]
    return ReviewPage(reviews=reviews, page=page, per_page=per_page, total=total)
