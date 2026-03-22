# Rotten Tomatoes Review Scraper & Analytics Dashboard

A web scraper that builds a time-series database of Rotten Tomatoes movie reviews, plus an analytics dashboard for viewing and analyzing that data. The scraper runs on a GCP VM via cron, and the dashboard runs on the same VM as a systemd service.

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

Reviews that the hour window missed get their timestamps interpolated — evenly distributed between the nearest known reviews in the database. All interpolated reviews are flagged with `reconciled_timestamp = 1`. Reviews are never deleted; the system is insert-only.

### Deduplication

Each review gets a unique ID via MD5 hash of `(reviewer_name + publication_name + subjective_score)`, enforced by a SQLite UNIQUE constraint.

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
| `reconciled_timestamp` | INTEGER | `1` if timestamp was interpolated |
| `reviewer_name` | TEXT | |
| `publication_name` | TEXT | |
| `top_critic` | INTEGER | `1` if from top-critics filter |

### `precheck_state`

| Column | Type | Description |
|---|---|---|
| `movie_slug` | TEXT (PK) | Movie identifier |
| `last_review_count` | INTEGER | Last known review count |
| `consecutive_failures` | INTEGER | Consecutive pre-check failures |
| `last_checked` | TEXT | UTC datetime of last check |

## Analytics Dashboard

A web dashboard for browsing and analyzing scraped review data. Built with FastAPI, Jinja2, HTMX, and Plotly.js.

**Features:**
- Paginated review table with movie filtering
- Analytics charts: Tomatometer over time, review volume, sentiment breakdown, top critic comparison, cumulative reviews, score distribution
- Stats panel: current Tomatometer %, total reviews, positive/negative counts, top critic score, average reviews per day

**Access** (via SSH tunnel — dashboard binds to `127.0.0.1:8000`):
```bash
ssh -L 8000:127.0.0.1:8000 jakelehner@<vm-ip>
# Then open http://localhost:8000
```

**Local development:**
```bash
cd web && uv run uvicorn app.main:app --reload
```

## Setup

**Requirements:** Python 3.14+, [uv](https://docs.astral.sh/uv/), Chromium/Chrome

```bash
# Install scraper dependencies
uv sync

# Install dashboard dependencies
cd web && uv sync

# Install dev dependencies (for testing)
uv sync --group dev          # scraper
cd web && uv sync --group dev  # dashboard
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
# Scraper tests
uv run --group dev pytest tests/ -v

# Dashboard tests
cd web && uv run --group dev pytest tests/ -v
```

All tests use in-memory SQLite and mocks — no network or browser required.

## Deployment

The scraper and dashboard run on a GCP e2-micro VM (free tier, Debian 12). The scraper runs via cron, and the dashboard runs as a systemd service (`rt-dashboard`). Both are auto-deployed via GitHub Actions on push to `main`.

### CI/CD

Pushing to `main` triggers `.github/workflows/deploy.yml`, which:
1. Authenticates with GCP via Workload Identity Federation (no stored keys)
2. SCPs scraper files and the `web/` directory to the VM
3. Runs `uv sync` for both scraper and dashboard dependencies
4. Restarts the `rt-dashboard` systemd service

The workflow triggers when these files change: `rotten_tomatoes.py`, `movies.json`, `pyproject.toml`, `deploy/**`, `web/**`, `.github/workflows/**`. It can also be triggered manually from the Actions tab.

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
gcloud compute scp rotten_tomatoes.py movies.json \
  jakelehner@rt-scraper:~/rotten-tomatoes-analysis/ --zone=us-east1-b

# Full VM setup (installs deps, Chrome, cron, Ops Agent, dashboard service)
# Run deploy/setup_vm.sh on the VM
```

### Monitoring

- **Logs**: `scraper.log` (Python logging), `cron.log` (cron stdout/stderr), and `dashboard.log` (uvicorn output) are shipped to Cloud Logging via the Google Cloud Ops Agent
- **Alerts**: Cloud Monitoring emails on ERROR-level log entries (pre-check failures, Selenium errors, backup failures)
- **Backups**: Daily SQLite snapshots to `gs://rotten-tomatoes-scraper-backups/`

## Project Structure

```
├── rotten_tomatoes.py           # Main scraper
├── movies.json                  # Movie config: [{slug, enabled}]
├── tests/
│   └── test_rotten_tomatoes.py  # Scraper tests (pure logic, no network/browser)
├── deploy/
│   ├── setup_vm.sh              # GCP VM provisioning script
│   ├── backup_db.sh             # Daily GCS backup
│   ├── cleanup_csv.sh           # Daily reference CSV cleanup
│   └── ops-agent-config.yaml    # Cloud Logging agent config
├── web/                         # Analytics dashboard (FastAPI + HTMX + Plotly.js)
│   ├── pyproject.toml           # Dashboard dependencies
│   ├── app/                     # FastAPI app (routers, services, math, templates)
│   ├── tests/                   # Dashboard tests
│   └── deploy/
│       └── rt-dashboard.service # systemd unit for dashboard
├── .github/
│   └── workflows/
│       └── deploy.yml           # GitHub Actions: auto-deploy on push to main
├── pyproject.toml               # Scraper dependencies (uv managed)
└── uv.lock                      # Lockfile
```
