"""Reviews table page and HTMX partials."""

import sqlite3

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.db import get_connection, load_movie_slugs
from app.templating import templates
from app.services.review_service import get_reviews_page

router = APIRouter(prefix="/reviews", tags=["reviews"])


@router.get("/", response_class=HTMLResponse)
async def reviews_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_connection),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    movie: str = Query("all"),
    after: str = Query(""),
):
    """Full reviews page (initial load)."""
    after_val = after if after else None
    result = get_reviews_page(conn, page=page, per_page=per_page, movie=movie, after=after_val)
    movies = load_movie_slugs()
    return templates.TemplateResponse(
        request,
        "reviews.html",
        {
            "result": result,
            "movies": movies,
            "selected_movie": movie,
            "selected_after": after,
        },
    )


@router.get("/table", response_class=HTMLResponse)
async def reviews_table(
    request: Request,
    conn: sqlite3.Connection = Depends(get_connection),
    page: int = Query(1, ge=1),
    per_page: int = Query(25, ge=1, le=100),
    movie: str = Query("all"),
    after: str = Query(""),
):
    """HTMX partial: just the table body + pagination controls."""
    after_val = after if after else None
    result = get_reviews_page(conn, page=page, per_page=per_page, movie=movie, after=after_val)
    return templates.TemplateResponse(
        request,
        "partials/review_table.html",
        {
            "result": result,
            "selected_movie": movie,
            "selected_after": after,
        },
    )
