"""FastAPI application factory."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.routers import analytics, reviews

APP_DIR = Path(__file__).resolve().parent

app = FastAPI(title="RT Dashboard")

app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")

app.include_router(reviews.router)
app.include_router(analytics.router)


@app.get("/")
async def root():
    """Redirect root to reviews page."""
    return RedirectResponse(url="/reviews")
