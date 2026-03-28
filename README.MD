# Rotten Tomatoes Review Scraper

A web scraper that builds a time-series database of Rotten Tomatoes movie reviews. Runs every 50 minutes via GCP Cloud Run Jobs + Cloud Scheduler, storing reviews in Neon (serverless PostgreSQL).

## How It Works

1. Cloud Scheduler triggers a Cloud Run Job every 50 minutes
2. The job scrapes reviews for each enabled movie using headless Chromium via Selenium
3. For each movie, scrapes both `top-critics` and `all-critics` review pages
4. Loads pages until the oldest visible review has an absolute date timestamp (e.g., "Mar 20"), capturing all reviews with relative timestamps (~last 7 days)
5. Inserts new reviews into Neon (PostgreSQL), skipping duplicates via MD5-based unique ID

### Deduplication

Each review gets a unique ID via MD5 hash of `(movie_slug + reviewer_name + publication_name + subjective_score)`, enforced by a PostgreSQL UNIQUE constraint. Re-scraping the same reviews is a no-op.

### Spike Guard

If a single batch inserts an abnormally high number of reviews (> 50) for a movie that already has 50+ reviews, the batch is rolled back instead of committed. This catches selector breakage that would create new hashes for every existing review.

## Database Schema

### `reviews` (Neon PostgreSQL)

| Column | Type | Description |
|---|---|---|
| `id` | SERIAL | Auto-increment primary key |
| `unique_review_id` | TEXT (UNIQUE) | MD5 hash for deduplication |
| `movie_slug` | TEXT | Movie identifier (e.g., `project_hail_mary`) |
| `reviewer_name` | TEXT | Critic's name |
| `publication_name` | TEXT | Publication name |
| `top_critic` | BOOLEAN | True if from top-critics filter |
| `tomatometer_sentiment` | TEXT | `positive` or `negative` |
| `subjective_score` | TEXT | e.g., `3/5`, `A-` |
| `written_review` | TEXT | Review snippet text |
| `site_timestamp_text` | TEXT | Raw RT timestamp (e.g., `5m`, `3h`, `Mar 20`) |
| `scrape_time` | TIMESTAMPTZ | When the scrape ran |
| `estimated_timestamp` | TIMESTAMPTZ | Computed absolute timestamp |
| `timestamp_confidence` | TEXT | Granularity: `m` (minute), `h` (hour), `d` (day) |
| `page_position` | INTEGER | Position in scrape result (0 = newest) |

## Setup

**Requirements:** Python 3.14+, [uv](https://docs.astral.sh/uv/)

```bash
# Install dependencies
uv sync

# Install dev dependencies (for testing)
uv sync --group dev
```

## Configuration

Movies to track are defined in `movies.json`:

```json
[
  {"slug": "project_hail_mary", "enabled": true}
]
```

The `slug` corresponds to the Rotten Tomatoes URL path (e.g., `rottentomatoes.com/m/project_hail_mary`).

## Usage

```bash
# Run scraper locally (requires DATABASE_URL)
DATABASE_URL="postgresql://..." uv run python rotten_tomatoes.py

# Scrape a single movie
DATABASE_URL="postgresql://..." uv run python rotten_tomatoes.py --movie project_hail_mary

# Backfill historical reviews
DATABASE_URL="postgresql://..." uv run python scripts/backfill.py
DATABASE_URL="postgresql://..." uv run python scripts/backfill.py --movie project_hail_mary --dry-run
```

## Testing

```bash
uv run --group dev pytest tests/ -v
```

All 45 tests use mocks -- no network, browser, or database required.

## Deployment

The scraper runs as a Cloud Run Job on GCP, triggered every 50 minutes by Cloud Scheduler. Docker images are built and deployed automatically via GitHub Actions on push to `main`.

### CI/CD

Pushing to `main` triggers `.github/workflows/deploy.yml`, which:
1. Authenticates with GCP via Workload Identity Federation (no stored keys)
2. Builds a Docker image (tagged with commit SHA + `latest`)
3. Verifies the image imports cleanly (sanity check)
4. Pushes both tags to Artifact Registry
5. Updates the Cloud Run Job to use the new image

The workflow triggers when these files change: `rotten_tomatoes.py`, `movies.json`, `pyproject.toml`, `uv.lock`, `Dockerfile`, `.github/workflows/**`. It can also be triggered manually from the Actions tab.

### Infrastructure

| Component | Details |
|---|---|
| Cloud Run Job | `rt-scraper` in `us-east1`, 2Gi memory, 900s timeout |
| Cloud Scheduler | `every 50 minutes`, America/New_York timezone |
| Artifact Registry | `us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper` |
| Secret Manager | `DATABASE_URL` (Neon connection string) |
| Database | Neon project `rotten-tomatoes`, database `neondb` |

### Docker

```bash
# Build locally
docker build -t rt-scraper:local .

# Run locally
docker run --rm -e DATABASE_URL="postgresql://..." rt-scraper:local
```

## Project Structure

```
├── rotten_tomatoes.py           # Main scraper
├── movies.json                  # Movie config: [{slug, enabled}]
├── Dockerfile                   # Python 3.14 + Chromium + ChromeDriver
├── .dockerignore                # Excludes non-runtime files from image
├── tests/
│   └── test_rotten_tomatoes.py  # 45 tests (pure logic, no network/browser)
├── scripts/
│   └── backfill.py              # One-time historical review backfill
├── .github/
│   └── workflows/
│       └── deploy.yml           # CI/CD: build + push image + update Cloud Run Job
├── pyproject.toml               # Dependencies (uv managed)
└── uv.lock                      # Lockfile
```
