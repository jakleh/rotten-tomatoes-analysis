# Rotten Tomatoes Review Scraper

A web scraper that builds a time-series database of Rotten Tomatoes movie reviews. The scraper runs on a GCP VM via cron, and Datasette provides a zero-code web UI for browsing and querying the data.

## How It Works

The scraper uses two complementary sliding windows to maintain a complete, ordered timeline of reviews:

### Hour Window (every 5 minutes)

1. **Pre-check** — lightweight HTTP request to the movie page, extracts the review count via regex
2. If the count hasn't changed since last check, skip (no Selenium overhead)
3. If count changed (or pre-check failed) — launch headless Chrome via Selenium
4. Scrape only recent reviews (those with minute-level timestamps)
5. Insert new reviews into the database, skip duplicates

### Day Window (every 6 hours)

1. Full Selenium scrape of reviews from the past day
2. **Reconcile** — detect reviews the hour window missed ("lagging" reviews)
3. Interpolate timestamps for missed reviews between known anchor points
4. Export a reference CSV snapshot
5. Recalibrate the pre-check state with an authoritative count

### Reconciliation

Reviews that the hour window missed get their timestamps interpolated — evenly distributed between the nearest known reviews in the database. Each review's `timestamp_confidence` records the granularity of its timestamp: `m` (minute), `h` (hour), or `d` (day/date). Interpolated reviews get `d`. Reviews are never deleted; the system is insert-only.

### Deduplication

Each review gets a unique ID via MD5 hash of `(movie_slug + reviewer_name + publication_name + subjective_score)`, enforced by a SQLite UNIQUE constraint.

## Database Schema

### `reviews`

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER | Auto-increment primary key |
| `movie_slug` | TEXT | Movie identifier (e.g., `project_hail_mary`) |
| `timestamp` | TEXT | UTC datetime |
| `unique_review_id` | TEXT (UNIQUE) | MD5 hash for deduplication |
| `subjective_score` | TEXT | e.g., `3/5`, `A-` |
| `tomatometer_sentiment` | TEXT | e.g., `positive`, `negative` |
| `timestamp_confidence` | TEXT | Timestamp granularity: `m` (minute), `h` (hour), `d` (day/date) |
| `reviewer_name` | TEXT | |
| `publication_name` | TEXT | |
| `top_critic` | INTEGER | `1` if from top-critics filter |
| `scraped_at` | TEXT | UTC datetime when the review was first captured |
| `raw_timestamp_text` | TEXT | Original RT relative timestamp (e.g., `5m`, `3h`, `Mar 20`) |

### `precheck_state`

| Column | Type | Description |
|---|---|---|
| `movie_slug` | TEXT (PK) | Movie identifier |
| `last_review_count` | INTEGER | Last known review count |
| `consecutive_failures` | INTEGER | Consecutive pre-check failures |
| `last_checked` | TEXT | UTC datetime of last check |

## Datasette

Datasette provides a browser-based UI for exploring the review database — filterable tables, faceted search, a SQL editor, JSON/CSV export, and inline charts via the datasette-vega plugin.

**Access** (via SSH tunnel — Datasette binds to `127.0.0.1:8001`):
```bash
ssh -L 8001:127.0.0.1:8001 jakelehner@<vm-ip>
# Then open http://localhost:8001
```

**Local development:**
```bash
uv run datasette --immutable reviews.db --metadata metadata.yml
```

## Setup

**Requirements:** Python 3.14+, [uv](https://docs.astral.sh/uv/), Chromium/Chrome

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
# Run both windows for all enabled movies
uv run python rotten_tomatoes.py

# Run a specific window
uv run python rotten_tomatoes.py --window hour
uv run python rotten_tomatoes.py --window day

# Override config to scrape a single movie
uv run python rotten_tomatoes.py --window hour --movie project_hail_mary
```

## Testing

```bash
uv run --group dev pytest tests/ -v
```

All tests use in-memory SQLite and mocks — no network or browser required.

## Deployment

The scraper and Datasette run on a GCP e2-micro VM (free tier, Debian 12). The scraper runs via cron, and Datasette runs as a systemd service (`rt-datasette`). Both are auto-deployed via GitHub Actions on push to `main`.

### CI/CD

Pushing to `main` triggers `.github/workflows/deploy.yml`, which:
1. Authenticates with GCP via Workload Identity Federation (no stored keys)
2. SCPs scraper files, metadata, and deploy scripts to the VM
3. Runs `uv sync` for dependencies
4. Restarts the `rt-datasette` systemd service

The workflow triggers when these files change: `rotten_tomatoes.py`, `movies.json`, `pyproject.toml`, `uv.lock`, `metadata.yml`, `deploy/**`, `.github/workflows/**`. It can also be triggered manually from the Actions tab.

### Cron Schedule

| Schedule | Job |
|---|---|
| `*/5 * * * *` | Hour sliding window |
| `0 */6 * * *` | Day sliding window |
| `0 3 * * *` | Daily DB backup to GCS |
| `0 4 * * *` | Daily cleanup of reference CSVs older than 30 days |

### Manual VM Access

```bash
# SSH into the VM
gcloud compute ssh rt-scraper --zone=us-east1-b

# Manual deploy (if CI/CD is unavailable)
gcloud compute scp rotten_tomatoes.py movies.json metadata.yml pyproject.toml uv.lock \
  jakelehner@rt-scraper:~/rotten-tomatoes-analysis/ --zone=us-east1-b

# Full VM setup (installs deps, Chrome, cron, Ops Agent, Datasette service)
# Run deploy/setup_vm.sh on the VM
```

### Monitoring

- **Logs**: `scraper.log` (Python logging), `cron.log` (cron stdout/stderr), and `datasette.log` (Datasette output) are shipped to Cloud Logging via the Google Cloud Ops Agent
- **Alerts**: Cloud Monitoring emails on ERROR-level log entries (pre-check failures, Selenium errors, backup failures)
- **Backups**: Daily SQLite snapshots to `gs://rotten-tomatoes-scraper-backups/`

## Project Structure

```
├── rotten_tomatoes.py           # Main scraper
├── movies.json                  # Movie config: [{slug, enabled}]
├── metadata.yml                 # Datasette config (facets, queries, descriptions)
├── tests/
│   └── test_rotten_tomatoes.py  # Scraper tests (pure logic, no network/browser)
├── scripts/
│   └── backfill.py              # One-time historical review backfill
├── deploy/
│   ├── setup_vm.sh              # GCP VM provisioning script
│   ├── rt-datasette.service     # systemd unit for Datasette
│   ├── backup_db.sh             # Daily GCS backup
│   ├── cleanup_csv.sh           # Daily reference CSV cleanup
│   └── ops-agent-config.yaml    # Cloud Logging agent config
├── .github/
│   └── workflows/
│       └── deploy.yml           # GitHub Actions: auto-deploy on push to main
├── pyproject.toml               # Dependencies (uv managed)
└── uv.lock                      # Lockfile
```
