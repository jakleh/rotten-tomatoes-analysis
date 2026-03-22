"""Dashboard configuration. Reads from environment variables with sensible defaults."""

import os
from pathlib import Path

# The web/ directory
WEB_DIR = Path(__file__).resolve().parent.parent

# The repo root (one level above web/)
REPO_DIR = WEB_DIR.parent

# Database path — defaults to reviews.db in the repo root
DB_PATH = os.environ.get("RT_DB_PATH", str(REPO_DIR / "reviews.db"))

# Movie config — defaults to movies.json in the repo root
MOVIES_JSON_PATH = os.environ.get("RT_MOVIES_JSON", str(REPO_DIR / "movies.json"))

# Server binding
HOST = os.environ.get("RT_HOST", "127.0.0.1")
PORT = int(os.environ.get("RT_PORT", "8000"))
