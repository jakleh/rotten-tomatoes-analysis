"""Analytics dashboard page and HTMX partials."""

import sqlite3

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse

from app.db import get_connection, load_movie_slugs
from app.templating import templates
from app.services.analytics_service import CHART_TYPES, get_chart, get_stats

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/", response_class=HTMLResponse)
async def analytics_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_connection),
    movie: str = Query(None),
    chart: str = Query("tomatometer_over_time"),
):
    """Full analytics page (initial load)."""
    movies = load_movie_slugs()
    if not movie or movie == "all":
        movie = movies[0] if movies else "all"
    chart_json = get_chart(conn, movie, chart)
    stats = get_stats(conn, movie)
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "movies": movies,
            "selected_movie": movie,
            "selected_chart": chart,
            "chart_types": CHART_TYPES,
            "chart_json": chart_json,
            "stats": stats,
        },
    )


@router.get("/chart", response_class=HTMLResponse)
async def analytics_chart(
    request: Request,
    conn: sqlite3.Connection = Depends(get_connection),
    movie: str = Query(None),
    chart: str = Query("tomatometer_over_time"),
):
    """HTMX partial: chart container."""
    chart_json = get_chart(conn, movie, chart)
    return templates.TemplateResponse(
        request,
        "partials/chart.html",
        {
            "chart_json": chart_json,
        },
    )


@router.get("/calc", response_class=HTMLResponse)
async def analytics_calc(
    request: Request,
    conn: sqlite3.Connection = Depends(get_connection),
    movie: str = Query(None),
):
    """HTMX partial: stats calculations panel."""
    stats = get_stats(conn, movie)
    return templates.TemplateResponse(
        request,
        "partials/calculation.html",
        {
            "stats": stats,
        },
    )
