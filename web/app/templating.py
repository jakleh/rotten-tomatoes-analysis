"""Shared Jinja2 templates instance — separate module to avoid circular imports."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

APP_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=str(APP_DIR / "templates"))
