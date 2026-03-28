# Dockerfile

---

## The Dockerfile

Create this file at the project root as `Dockerfile`:

```dockerfile
FROM python:3.14-slim-bookworm

# Install Chromium and ChromeDriver.
# chromium-driver provides chromedriver, matched to the chromium version by apt.
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# Install uv (Astral's fast Python package manager).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install Python dependencies first (layer-cached unless pyproject.toml/uv.lock changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy application code.
COPY rotten_tomatoes.py movies.json ./

# Tell Selenium where to find the Chromium binary.
ENV CHROME_BIN=/usr/bin/chromium

# Add venv to PATH so `python` resolves to the uv-managed interpreter.
ENV PATH="/app/.venv/bin:$PATH"

CMD ["python", "rotten_tomatoes.py"]
```

---

## Layer Caching Note

Dependencies (`uv sync`) are copied and installed before application code. This means:
- If only `rotten_tomatoes.py` or `movies.json` changes, Docker reuses the cached
  dependency layer — builds are fast (~10s instead of ~2min).
- If `pyproject.toml` or `uv.lock` changes, the dependency layer rebuilds.

---

## Local Build and Test

Build the image:
```bash
docker build -t rt-scraper .
```

Test it locally (requires DATABASE_URL to be set):
```bash
docker run \
  --env DATABASE_URL="postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require" \
  rt-scraper \
  python rotten_tomatoes.py --movie project_hail_mary
```

Expected: same output as running locally without Docker — scrapes, inserts, logs.

If Chrome fails to launch inside the container, check:
```bash
docker run --rm rt-scraper chromium --version
docker run --rm rt-scraper chromedriver --version
```

Both versions should match (e.g., both `131.x.x.x`). If they don't match,
see questions.md — this is a known potential issue with Debian Bookworm's package versions.

---

## Failure Scenarios

**`chromium-driver` package not found:**
Debian Bookworm may name the package differently. Try:
```bash
docker run --rm python:3.14-slim-bookworm apt-cache search chromium
```
This lists available chromium-related packages. Use whatever provides `chromedriver`.

**Chrome crashes with "No usable sandbox":**
The `--no-sandbox` flag in `_build_driver()` handles this for containerized environments.
This flag is already in the existing code and must stay.

**`uv sync` fails in Docker:**
If the lock file is stale (local dev added packages without `uv lock`), the build will fail
with a lock mismatch. Fix: run `uv lock` locally, commit the updated `uv.lock`, rebuild.

**Image too large:**
Expected final image size: ~1.2-1.8GB (Chromium is large). This is normal for
Selenium-based scrapers. Cloud Run Jobs pull the image on each execution from Artifact
Registry — in the same region, pull time is ~5-10 seconds.

---

## `.dockerignore`

Create `.dockerignore` at the project root to prevent large files from being sent to
the Docker build context:

```
reviews.db
reviews.db-shm
reviews.db-wal
reviews.db.bak
scraper.log
*.csv
.git
__pycache__
tests/
scripts/
plan/
deploy/
.github/
*.md
```

Without this, `docker build` would send the large SQLite file and all CSVs to the build
daemon unnecessarily (slow, no functional impact, but wasteful).
