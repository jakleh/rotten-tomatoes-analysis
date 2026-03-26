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
    after: str | None = None,
) -> ReviewPage:
    """Return a paginated slice of reviews, newest first."""
    page = max(1, page)
    per_page = max(1, min(per_page, 100))

    where_clauses: list[str] = []
    params: list[str | int] = []

    if movie and movie != "all":
        where_clauses.append("movie_slug = ?")
        params.append(movie)

    if after:
        where_clauses.append("timestamp > ?")
        params.append(after)

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    count_row = conn.execute(
        f"SELECT COUNT(*) FROM reviews{where_sql}", params
    ).fetchone()
    total = count_row[0]

    rows = conn.execute(
        f"SELECT * FROM reviews{where_sql} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
        [*params, per_page, (page - 1) * per_page],
    ).fetchall()

    reviews = [dict(row) for row in rows]
    return ReviewPage(reviews=reviews, page=page, per_page=per_page, total=total)
