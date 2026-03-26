"""Report page — document preview and PDF download."""

import asyncio
import sqlite3

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response

from app.db import get_connection, load_movie_slugs
from app.services.analytics_service import get_chart
from app.services.report_service import generate_pdf, get_report_data
from app.templating import templates

router = APIRouter(prefix="/reports", tags=["reports"])

# Serialize PDF renders to protect memory on the e2-micro VM.
_render_semaphore = asyncio.Semaphore(1)

_PREVIEW_CHARTS = [
    ("tomatometer_over_time", "Tomatometer Over Time"),
    ("review_volume", "Review Volume"),
    ("cumulative_reviews", "Cumulative Reviews"),
]


@router.get("/", response_class=HTMLResponse)
async def reports_page(
    request: Request,
    conn: sqlite3.Connection = Depends(get_connection),
    movie: str = Query(None),
):
    """Full reports page (initial load)."""
    movies = load_movie_slugs()
    if not movie or movie == "all":
        movie = movies[0] if movies else "all"
    data = get_report_data(conn, movie)
    charts = {key: get_chart(conn, movie, key) for key, _ in _PREVIEW_CHARTS}
    return templates.TemplateResponse(
        request,
        "reports.html",
        {
            "movies": movies,
            "selected_movie": movie,
            "data": data,
            "charts": charts,
            "chart_labels": _PREVIEW_CHARTS,
        },
    )


@router.get("/preview", response_class=HTMLResponse)
async def reports_preview(
    request: Request,
    conn: sqlite3.Connection = Depends(get_connection),
    movie: str = Query(None),
):
    """HTMX partial: report document preview."""
    data = get_report_data(conn, movie)
    charts = {key: get_chart(conn, movie, key) for key, _ in _PREVIEW_CHARTS}
    return templates.TemplateResponse(
        request,
        "partials/report_preview.html",
        {
            "data": data,
            "charts": charts,
            "chart_labels": _PREVIEW_CHARTS,
        },
    )


@router.get("/download")
async def download_report(
    conn: sqlite3.Connection = Depends(get_connection),
    movie: str = Query(None),
):
    """Generate and return a PDF report."""
    async with _render_semaphore:
        pdf_bytes = await asyncio.to_thread(generate_pdf, conn, movie)
    filename = f"rt_report_{movie}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
