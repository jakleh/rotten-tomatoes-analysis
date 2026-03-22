# CLAUDE.md

## Project Overview

Rotten Tomatoes web scraper that builds a time-series database of movie reviews. The system scrapes reviews at regular intervals using a sliding window architecture to capture new reviews as they appear and reconcile any that arrive late.

## File Structure

```
├── rotten_tomatoes.py          # Main scraper (scraping, DB, reconciliation, pre-check)
├── movies.json                # Movie config: list of {slug, enabled} objects
├── tests/
│   └── test_rotten_tomatoes.py # 64 tests (all pure logic, no network/browser)
├── deploy/
│   ├── setup_vm.sh            # GCP VM setup script (installs deps, configures cron)
│   ├── backup_db.sh           # Daily GCS backup of reviews.db
│   └── cleanup_csv.sh         # Daily cleanup of reference CSVs older than 30 days
├── pyproject.toml             # Dependencies (uv managed, Python >=3.14)
├── .gitignore
├── README.MD                  # Original architecture spec
└── .claude/                   # Claude Code config
```

## Tech Stack

- **Language**: Python 3.14
- **Package manager**: uv
- **Scraping**: Selenium WebDriver (headless Chrome) + BeautifulSoup4
- **Database**: SQLite (`reviews.db`)
- **Pre-check**: `requests` library (lightweight HTTP to skip unnecessary Selenium runs)
- **Deployment**: GCP e2-micro VM (free tier, Debian 12) + cron

## Resolved Design Decisions

- **Database**: SQLite (local file `reviews.db`)
- **Deployment**: GCP e2-micro VM (`rt-scraper`, zone `us-east1-b`) with cron
- **Scraping method**: Selenium (RT's `/napi/` endpoint returns 404; verified via curl)
- **Interpolation**: Even distribution between known timestamps, all marked `reconciled_timestamp=True`
- **Top critic detection**: Filter-based — scrape `top-critics` first (all are top critics), then `all-critics`. Isolated in one line, easy to change later.

## Implementation Status

### Fully Implemented and Deployed
- **`get_reviews(movie_slug, critic_filter, stop_at_unit)`** — Selenium scraper with early stopping. `stop_at_unit='h'` for hour window, `'d'` for day window. Returns list of review dicts.
- **`scrape_hour_sliding_window(movie_slug)`** — Runs every 5 min via cron. Pre-checks review count via HTTP first; only launches Selenium if count changed.
- **`scrape_day_sliding_window(movie_slug)`** — Runs every 6 hours via cron. Always does full scrape. Reconciles lagging reviews, exports reference CSV, calibrates pre-check state.
- **SQLite layer** — `init_reviews_table` (single unified table with `movie_slug` column), `insert_review` (dedup via MD5 unique_review_id), `get_db_review_ids`, `get_db_reviews_sorted`, `export_reference_csv`, `migrate_to_single_table` (auto-migrates legacy per-movie tables on startup)
- **Pre-check system** — `fetch_review_count()` hits main movie page (`/m/{slug}`) with `requests`, extracts count via regex `(\d+) Reviews`. `has_new_reviews()` compares against stored count in `precheck_state` table. Tracks consecutive failures; logs WARNING each time, ERROR after 10+. Falls back to full Selenium scrape on failure.
- **Reconciliation** — `reconcile_missing_reviews()` groups consecutive missing reviews, interpolates timestamps from DB anchor neighbors. Only reconciles reviews with at least one DB anchor (no false reconciliation on first run/empty DB).
- **Deduplication** — MD5 hash of `(reviewer_name + publication_name + subjective_score)` as `unique_review_id`, enforced via SQLite UNIQUE constraint.
- **Logging** — to `scraper.log` (FileHandler) + console (StreamHandler)
- **Multi-movie config** — `movies.json` with `[{slug, enabled}]` entries. `load_movie_config()` reads enabled slugs. CLI `--movie <slug>` overrides the config for one-off runs.
- **CLI** — `--window hour|day|both` and `--movie <slug>` (override) via argparse
- **GCS backups** — `deploy/backup_db.sh` copies `reviews.db` to `gs://rotten-tomatoes-scraper-backups/reviews-YYYY-MM-DD.db` daily at 3 AM via cron. Uses VM's default service account (needs `Storage Object Admin` role on the bucket, `cloud-platform` scope on the VM).
- **CSV cleanup** — `deploy/cleanup_csv.sh` deletes `*_reference.csv` files older than 30 days. Runs daily at 4 AM via cron.
- **GCP deployment** — `deploy/setup_vm.sh` handles everything: installs Chromium, uv, Python deps, sets up cron (including daily backup and CSV cleanup). VM has 2GB swap file (needed for e2-micro's 1GB RAM).
- **64 tests** — covering timestamp utils, MD5 hashing, interpolation, DB dedup, reconciliation, pre-check state, fetch_review_count, has_new_reviews, single-table migration, movie config loading. All use in-memory SQLite and mocks.

### Not Yet Implemented (Planned Next)
- **Email notifications** — Alert when scraper stops working (e.g., repeated pre-check failures, Selenium errors).

## Database Schema

### `reviews` table (single unified table for all movies)

| Field | Type | Description |
|---|---|---|
| id | INTEGER | Auto-increment primary key |
| movie_slug | TEXT | Movie being tracked (e.g., "project_hail_mary") |
| timestamp | TEXT | UTC datetime string |
| unique_review_id | TEXT (UNIQUE) | MD5 hash of (name + publication + rating) |
| subjective_score | TEXT | e.g., "3/5", "A-" |
| reconciled_timestamp | INTEGER | 1 if timestamp was interpolated |
| reviewer_name | TEXT | |
| publication_name | TEXT | |
| top_critic | INTEGER | 1 if from top-critics filter |

### `precheck_state` table

| Field | Type | Description |
|---|---|---|
| movie_slug | TEXT (PK) | Movie being tracked |
| last_review_count | INTEGER | Last known review count from HTTP pre-check |
| consecutive_failures | INTEGER | Consecutive pre-check failures (resets on success) |
| last_checked | TEXT | UTC datetime of last check |

## Architecture

### Hour Sliding Window (every 5 min)
1. **Pre-check**: HTTP GET to `/m/{slug}`, regex for `(\d+) Reviews`, compare to stored count
2. If count unchanged → skip, log, done
3. If count changed or pre-check failed → launch Selenium
4. Scrape reviews with `stop_at_unit='h'` (only "m"-timestamped reviews)
5. Insert new reviews (skip duplicates)

### Day Sliding Window (every 6 hours)
1. Always runs full Selenium scrape with `stop_at_unit='d'`
2. Compare scraped reviews against DB
3. Reconcile missing reviews (interpolate timestamps between DB anchors)
4. Export reference CSV
5. Calibrate pre-check state with authoritative count

### Reconciliation Rules
- Only reconciles reviews that have **at least one DB anchor neighbor** (proving the hour window was running during that time period)
- No anchors = reviews are just unseen (first run / empty DB), not lagging → skip
- Timestamps are evenly distributed between anchor points
- All reconciled reviews marked `reconciled_timestamp=True`
- **Reviews are never deleted** — insert-only system

## How to Run

```bash
# Run both windows for all movies in movies.json
uv run python rotten_tomatoes.py

# Run specific window for all movies
uv run python rotten_tomatoes.py --window hour

# Override config: scrape a single movie
uv run python rotten_tomatoes.py --window hour --movie project_hail_mary

# Run tests
uv run --group dev pytest tests/ -v
```

## GCP VM Details

- **Instance**: `rt-scraper` in `us-east1-b`
- **Machine type**: e2-micro (free tier, 1GB RAM + 2GB swap)
- **OS**: Debian 12
- **Timezone**: America/New_York (Eastern)
- **Chrome binary**: `/usr/bin/chromium` (set via `CHROME_BIN` env var in crontab)
- **GCS bucket**: `gs://rotten-tomatoes-scraper-backups` (daily DB backups)
- **Cron schedule**:
  - `*/5 * * * *` — hour window
  - `0 */6 * * *` — day window
  - `0 3 * * *` — daily DB backup to GCS
  - `0 4 * * *` — daily CSV cleanup (30+ days old)
- **Logs**: `cron.log` (cron stdout/stderr), `scraper.log` (Python logging)
- **SSH**: `gcloud compute ssh rt-scraper --zone=us-east1-b`
- **Deploy code**: `gcloud compute scp ~/Desktop/rotten-tomatoes-analysis/rotten_tomatoes.py rt-scraper:~/rotten-tomatoes-analysis/ --zone=us-east1-b`
- **Deploy config**: `gcloud compute scp ~/Desktop/rotten-tomatoes-analysis/movies.json rt-scraper:~/rotten-tomatoes-analysis/ --zone=us-east1-b`

## Dependencies

Managed via `pyproject.toml` + `uv.lock`:
- beautifulsoup4, selenium, requests, pandas, matplotlib
- Dev: pytest
