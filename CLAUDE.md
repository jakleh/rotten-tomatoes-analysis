# CLAUDE.md

## Project Overview

Rotten Tomatoes web scraper that builds a time-series database of movie reviews. The scraper runs on a GCP VM via cron, and Datasette provides a zero-code web UI for browsing and querying the SQLite database.

## File Structure

```
├── rotten_tomatoes.py          # Main scraper (scraping, DB, reconciliation, pre-check)
├── movies.json                # Movie config: list of {slug, enabled} objects
├── metadata.yml               # Datasette config (facets, column descriptions, canned queries)
├── scripts/
│   └── backfill.py            # One-time backfill of historical reviews (run locally)
├── tests/
│   └── test_rotten_tomatoes.py # 92 tests (all pure logic, no network/browser)
├── deploy/
│   ├── setup_vm.sh            # GCP VM setup script (installs deps, cron, Ops Agent, Datasette)
│   ├── rt-datasette.service   # systemd unit for Datasette
│   ├── backup_db.sh           # Daily GCS backup of reviews.db
│   ├── cleanup_csv.sh         # Daily cleanup of reference CSVs older than 30 days
│   └── ops-agent-config.yaml  # Ops Agent config (ships scraper + Datasette logs to Cloud Logging)
├── .github/
│   └── workflows/
│       └── deploy.yml         # GitHub Actions: auto-deploy to GCP VM on push to main
├── pyproject.toml             # Dependencies (uv managed, Python >=3.14)
├── .gitignore
├── README.MD                  # Project documentation
└── .claude/                   # Claude Code config
```

## Tech Stack

- **Language**: Python 3.14
- **Package manager**: uv
- **Scraping**: Selenium WebDriver (headless Chrome) + BeautifulSoup4
- **Database**: SQLite (`reviews.db`) with WAL mode for concurrent read/write
- **Pre-check**: `requests` library (lightweight HTTP to skip unnecessary Selenium runs)
- **Data exploration**: Datasette (read-only SQLite browser with faceted search, SQL editor, charts via datasette-vega)
- **Deployment**: GCP e2-micro VM (free tier, Debian 12) + cron (scraper) + systemd (Datasette)
- **CI/CD**: GitHub Actions (auto-deploy on push to main via Workload Identity Federation)

## Resolved Design Decisions

- **Database**: SQLite (local file `reviews.db`) with WAL mode (set in `init_reviews_table`)
- **Deployment**: GCP e2-micro VM (`rt-scraper`, zone `us-east1-b`) with cron
- **Scraping method**: Selenium (RT's `/napi/` endpoint returns 404; verified via curl)
- **Interpolation**: Even distribution between known timestamps, all marked `timestamp_confidence='d'`
- **Top critic detection**: Filter-based — scrape `top-critics` first (all are top critics), then `all-critics`. Isolated in one line, easy to change later.
- **Data exploration**: Datasette replaces a custom FastAPI + HTMX + Plotly.js dashboard. Zero application code to maintain; provides filterable table browser, SQL editor, JSON/CSV export, and chart plugin out of the box.

## Implementation Status

### Fully Implemented and Deployed
- **`get_reviews(movie_slug, critic_filter, stop_at_unit)`** — Selenium scraper with early stopping. `stop_at_unit='h'` for hour window, `'d'` for day window. Returns list of review dicts including `scraped_at` and `raw_timestamp_text`.
- **`scrape_hour_sliding_window(movie_slug)`** — Runs every 5 min via cron. Pre-checks review count via HTTP first; only launches Selenium if count changed. Frequency ensures lagging reviews are caught within a short time horizon.
- **`scrape_day_sliding_window(movie_slug)`** — Runs every 6 hours via cron. Always does full scrape. Reconciles lagging reviews missed by the hour window, exports reference CSV, calibrates pre-check state.
- **SQLite layer** — `init_reviews_table` (single unified table with `movie_slug` column, schema versioning via `schema_version` table up to v3), `insert_review` (dedup via MD5 unique_review_id), `update_sentiment` (fills NULL tomatometer_sentiment only), `get_db_review_ids`, `get_db_reviews_sorted`, `export_reference_csv`
- **Pre-check system** — `fetch_review_count()` hits main movie page (`/m/{slug}`) with `requests`, extracts count via regex `(\d+) Reviews`. `has_new_reviews()` compares against stored count in `precheck_state` table. Tracks consecutive failures; logs WARNING each time, ERROR after 10+. Falls back to full Selenium scrape on failure. When count increases, the stored count is NOT updated until the hour window confirms it captured reviews — this ensures the next cycle retries if the scrape found 0 minute-level reviews.
- **Reconciliation** — `reconcile_missing_reviews()` groups consecutive missing reviews, interpolates timestamps from DB anchor neighbors. Only reconciles reviews with at least one DB anchor (no false reconciliation on first run/empty DB).
- **Deduplication** — MD5 hash of `(movie_slug + reviewer_name + publication_name + subjective_score)` as `unique_review_id`, enforced via SQLite UNIQUE constraint. Schema migration v1 rehashes existing rows automatically.
- **Timestamp confidence** — `timestamp_confidence` column records the granularity of each review's timestamp: `"m"` (minute-level from RT), `"h"` (hour-level), `"d"` (day/date-level or interpolated). Set at scrape time from the RT time marker. Schema migration v2 replaces the old `reconciled_timestamp` boolean, defaulting all existing rows to `"d"`.
- **Data provenance** — `scraped_at` records when the review was first captured. `raw_timestamp_text` preserves the original RT relative timestamp (e.g. "5m", "3h", "Mar 20") for reprocessing without re-scraping. Schema migration v3 adds both columns plus performance indexes.
- **Logging** — to `scraper.log` (FileHandler) + console (StreamHandler)
- **Multi-movie config** — `movies.json` with `[{slug, enabled}]` entries. `load_movie_config()` reads enabled slugs. CLI `--movie <slug>` overrides the config for one-off runs.
- **CLI** — `--window hour|day|both` and `--movie <slug>` (override) via argparse
- **GCS backups** — `deploy/backup_db.sh` copies `reviews.db` to `gs://rotten-tomatoes-scraper-backups/reviews-YYYY-MM-DD.db` daily at 3 AM via cron. Uses VM's default service account (needs `Storage Object Admin` role on the bucket, `cloud-platform` scope on the VM).
- **CSV cleanup** — `deploy/cleanup_csv.sh` deletes `*_reference.csv` files older than 30 days. Runs daily at 4 AM via cron.
- **Email notifications** — Google Cloud Ops Agent ships `scraper.log`, `cron.log`, and `datasette.log` to Cloud Logging. Cloud Monitoring alert policy emails on ERROR-level entries (pre-check failures, Selenium errors, backup failures).
- **GCP deployment** — `deploy/setup_vm.sh` handles everything: installs Chromium, uv, Python deps, Ops Agent, sets up cron (including daily backup and CSV cleanup), and registers `rt-datasette` systemd service. VM has 2GB swap file (needed for e2-micro's 1GB RAM).
- **CI/CD** — `.github/workflows/deploy.yml` auto-deploys to GCP VM on push to main. Uses Workload Identity Federation (no stored keys). SCPs scraper files, metadata, and deploy scripts as `jakelehner@rt-scraper`, runs `uv sync`, restarts `rt-datasette` service.
- **Timestamp year heuristic** — `convert_rel_timestamp_to_abs()` rolls back to previous year when parsed date ("Mar 22") is in the future. Known limitation: reviews 2+ years old may be off by 1 year.
- **Backfill script** — `scripts/backfill.py` one-time tool to scrape all historical reviews and fill missing sentiment. Two-pass (top-critics → all-critics) preserves `top_critic` flag. Run locally against a copy of `reviews.db`. Supports `--movie`, `--db`, `--dry-run`.
- **Datasette** — `metadata.yml` configures facets (`movie_slug`, `tomatometer_sentiment`, `top_critic`, `timestamp_confidence`), column descriptions, and canned queries (tomatometer over time, review volume, sentiment breakdown, top critic split, publication counts). `datasette-vega` plugin provides inline chart visualizations. Binds to `127.0.0.1:8001`, accessed via SSH tunnel.
- **92 tests** — covering timestamp utils (incl. year heuristic), MD5 hashing (incl. cross-movie uniqueness), hash migration (v1 + v2 + v3), interpolation, DB dedup, reconciliation, pre-check state, fetch_review_count, has_new_reviews, movie config loading, tomatometer_sentiment persistence, update_sentiment, provenance columns, backfill logic. All use in-memory SQLite and mocks.

## Database Schema

### `reviews` table (single unified table for all movies)

| Field | Type | Description |
|---|---|---|
| id | INTEGER | Auto-increment primary key |
| movie_slug | TEXT | Movie being tracked (e.g., "project_hail_mary") |
| timestamp | TEXT | UTC datetime string |
| unique_review_id | TEXT (UNIQUE) | MD5 hash of (movie_slug + name + publication + rating) |
| subjective_score | TEXT | e.g., "3/5", "A-" |
| tomatometer_sentiment | TEXT | e.g., "positive", "negative" (from score-icon-critics element) |
| timestamp_confidence | TEXT | Timestamp granularity: "m" (minute), "h" (hour), "d" (day/date) |
| reviewer_name | TEXT | |
| publication_name | TEXT | |
| top_critic | INTEGER | 1 if from top-critics filter |
| scraped_at | TEXT | UTC datetime when the review was first captured |
| raw_timestamp_text | TEXT | Original RT relative timestamp (e.g., "5m", "3h", "Mar 20") |

Indexes: `idx_reviews_movie_slug` on `(movie_slug)`, `idx_reviews_movie_timestamp` on `(movie_slug, timestamp)`

### `precheck_state` table

| Field | Type | Description |
|---|---|---|
| movie_slug | TEXT (PK) | Movie being tracked |
| last_review_count | INTEGER | Last known review count from HTTP pre-check |
| consecutive_failures | INTEGER | Consecutive pre-check failures (resets on success) |
| last_checked | TEXT | UTC datetime of last check |

### `schema_version` table

| Field | Type | Description |
|---|---|---|
| version | INTEGER | Current schema version (3 = provenance columns + indexes) |

## Architecture

### Hour Sliding Window (every 5 min)
1. **Pre-check**: HTTP GET to `/m/{slug}`, regex for `(\d+) Reviews`, compare to stored count
2. If count unchanged → skip, log, done
3. If count changed or pre-check failed → launch Selenium (stored count deferred until capture confirmed)
4. Scrape reviews with `stop_at_unit='h'` (only "m"-timestamped reviews)
5. Insert new reviews (skip duplicates)
6. High frequency ensures lagging reviews are caught within a short time horizon

### Day Sliding Window (every 6 hours)
1. Always runs full Selenium scrape with `stop_at_unit='d'`
2. Compare scraped reviews against DB
3. Reconcile missing reviews that the hour window missed (interpolate timestamps between DB anchors)
4. Export reference CSV
5. Calibrate pre-check state with authoritative count

### Reconciliation Rules
- Only reconciles reviews that have **at least one DB anchor neighbor** (proving the hour window was running during that time period)
- No anchors = reviews are just unseen (first run / empty DB), not lagging → skip
- Timestamps are evenly distributed between anchor points
- All reconciled reviews marked `timestamp_confidence='d'`
- **Reviews are never deleted** — insert-only system

## How to Run

```bash
# === Scraper ===
# Run both windows for all movies in movies.json
uv run python rotten_tomatoes.py

# Run specific window for all movies
uv run python rotten_tomatoes.py --window hour

# Override config: scrape a single movie
uv run python rotten_tomatoes.py --window hour --movie project_hail_mary

# Run tests
uv run --group dev pytest tests/ -v

# === Datasette ===
# Start locally (read-only, immutable mode)
uv run datasette --immutable reviews.db --metadata metadata.yml
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
- **Logs**: `cron.log` (cron stdout/stderr), `scraper.log` (Python logging), `datasette.log` (Datasette output)
- **Datasette**: `rt-datasette.service` (systemd), binds to `127.0.0.1:8001`, memory-limited (`MemoryHigh=100M`, `MemoryMax=150M`)
- **Datasette access**: `ssh -L 8001:127.0.0.1:8001 jakelehner@<vm-ip>` then open `http://localhost:8001`
- **SSH**: `gcloud compute ssh rt-scraper --zone=us-east1-b`
- **Deploy**: Push to `main` — GitHub Actions auto-deploys scraper + Datasette config via `.github/workflows/deploy.yml`
- **Manual deploy** (if needed): `gcloud compute scp rotten_tomatoes.py movies.json metadata.yml pyproject.toml uv.lock jakelehner@rt-scraper:~/rotten-tomatoes-analysis/ --zone=us-east1-b`
- **CI/CD auth**: Workload Identity Federation — pool `github`, provider `github-actions`, SA `github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com`
- **GitHub secrets**: `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`

## Dependencies

`pyproject.toml`: beautifulsoup4, selenium, requests, datasette, datasette-vega. Dev: pytest.

## Workflow: Milestone Checklist

After completing any non-trivial task, walk through this checklist with the user before considering the work done.

### Code Quality
- [ ] Tests added or updated for new/changed behavior
- [ ] All tests pass: `uv run --group dev pytest tests/ -v`
- [ ] No leftover TODOs, debug prints, or commented-out code from this change
- [ ] DB schema changes include migration logic in `init_reviews_table`

### Documentation
- [ ] `CLAUDE.md` updated if: schema changed, new files added, new features implemented, deployment process changed, or design decisions were made
- [ ] `README.MD` updated if: user-facing behavior changed, setup steps changed, or project structure changed
- [ ] File structure section in both docs reflects any new/removed files

### Git & Deployment
- [ ] Changes committed with a descriptive message
- [ ] Pushed to `origin main` (use SSH remote, not HTTPS, to include workflow changes)
- [ ] GitHub Actions deploy workflow ran successfully (check Actions tab)
- [ ] If deploy files changed (`deploy/`, `.github/workflows/`): verify the workflow ran and passed

### Loose Ends
- [ ] No stale counts in docs (e.g., test count in CLAUDE.md matches actual)
- [ ] If a new dependency was added: `pyproject.toml` updated, `uv.lock` committed
- [ ] If cron or VM config changed: `deploy/setup_vm.sh` updated and re-run on VM

### Tech Backlog
Track known improvements or deferred work here. Remove items as they're completed.
Detailed context and implementation plans: `.claude/backlog-context.md`

1. Normalize subjective scores into a 0-1 scale
2. Extract `parse_review_cards(html)` from `get_reviews()` + add mocked HTTP boundary tests

## Security Decisions & Tradeoffs

Running log of security-relevant choices and their rationale.

- **Datasette binds to `127.0.0.1:8001`**: Not exposed to the internet. Access is SSH-tunnel-only, so there's no public attack surface. The SSH tunnel itself is the access control.
- **Datasette runs with `--immutable` flag**: Opens the database in read-only mode. No writes possible through the web interface.
- **`MemoryMax=150M` on Datasette service**: Hard OOM kill boundary prevents a runaway Datasette process from starving the scraper (which needs 500-800MB for Selenium/Chrome). Trades Datasette availability for system stability.
- **CI/CD uses passwordless sudo for `systemctl restart`**: Relies on GCP Compute Engine's default sudoers config (`/etc/sudoers.d/google_sudoers`) granting the primary SSH user full passwordless sudo. Acceptable because the VM is single-purpose and single-user.
