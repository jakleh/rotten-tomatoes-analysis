# CLAUDE.md

## Project Overview

Rotten Tomatoes web scraper that builds a time-series database of movie reviews, plus a web dashboard for viewing and analyzing that data. The scraper runs on a GCP VM via cron, and the dashboard reads the same SQLite DB as a separate service.

## File Structure

```
├── rotten_tomatoes.py          # Main scraper (scraping, DB, reconciliation, pre-check)
├── movies.json                # Movie config: list of {slug, enabled} objects
├── scripts/
│   └── backfill.py            # One-time backfill of historical reviews (run locally)
├── tests/
│   └── test_rotten_tomatoes.py # 81 tests (all pure logic, no network/browser)
├── deploy/
│   ├── setup_vm.sh            # GCP VM setup script (installs deps, cron, Ops Agent, dashboard)
│   ├── backup_db.sh           # Daily GCS backup of reviews.db
│   ├── cleanup_csv.sh         # Daily cleanup of reference CSVs older than 30 days
│   └── ops-agent-config.yaml  # Ops Agent config (ships scraper + dashboard logs to Cloud Logging)
├── web/                       # Analytics dashboard (FastAPI + Jinja2 + HTMX)
│   ├── pyproject.toml         # Separate deps (fastapi, uvicorn, jinja2, plotly, fpdf2)
│   ├── app/
│   │   ├── main.py            # FastAPI app factory
│   │   ├── config.py          # Settings: DB_PATH, HOST, PORT, MOVIES_JSON_PATH
│   │   ├── db.py              # Read-only SQLite connection dependency
│   │   ├── cache.py           # In-memory TTL cache (60s, keyed by func+movie+params)
│   │   ├── templating.py      # Shared Jinja2 templates instance
│   │   ├── routers/
│   │   │   ├── reviews.py     # /reviews endpoints (full page + HTMX table partial)
│   │   │   ├── analytics.py   # /analytics endpoints (full page + chart/calc partials)
│   │   │   └── reports.py     # /reports endpoints (full page + preview partial + PDF download)
│   │   ├── services/
│   │   │   ├── review_service.py    # Paginated review queries
│   │   │   ├── analytics_service.py # Math + DB + Plotly JSON orchestration + caching
│   │   │   └── report_service.py    # Report data collection + fpdf2/matplotlib PDF generation
│   │   ├── math/
│   │   │   ├── sentiment.py   # Tomatometer over time, sentiment counts, current score
│   │   │   ├── timing.py      # Reviews per bucket, cumulative count, avg per day
│   │   │   ├── critics.py     # Top critic vs regular split, publication counts
│   │   │   └── scoring.py     # Score distribution histogram
│   │   ├── templates/
│   │   │   ├── base.html      # Shared layout (nav, HTMX/Plotly CDN)
│   │   │   ├── reviews.html   # Reviews page
│   │   │   ├── analytics.html # Analytics page (sidebar + chart area)
│   │   │   ├── reports.html   # Reports page (controls bar + document preview)
│   │   │   └── partials/
│   │   │       ├── review_table.html   # HTMX partial: review table + pagination
│   │   │       ├── chart.html          # HTMX partial: Plotly chart
│   │   │       ├── calculation.html    # HTMX partial: stats panel
│   │   │       └── report_preview.html # HTMX partial: report document preview
│   │   └── static/
│   │       ├── style.css      # Full styling (nav, table, analytics, reports, stats grid)
│   │       └── app.js         # Minimal JS (Plotly resize hook)
│   ├── tests/                 # 87 tests (review service, cache, math, analytics, reports)
│   └── deploy/
│       └── rt-dashboard.service  # systemd unit for dashboard
├── .github/
│   └── workflows/
│       └── deploy.yml         # GitHub Actions: auto-deploy to GCP VM on push to main
├── pyproject.toml             # Scraper dependencies (uv managed, Python >=3.14)
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
- **Dashboard**: FastAPI + Jinja2 + HTMX + Plotly.js (read-only against DB)
- **Deployment**: GCP e2-micro VM (free tier, Debian 12) + cron (scraper) + systemd (dashboard)
- **CI/CD**: GitHub Actions (auto-deploy on push to main via Workload Identity Federation)

## Resolved Design Decisions

- **Database**: SQLite (local file `reviews.db`) with WAL mode (set in `init_reviews_table`)
- **Deployment**: GCP e2-micro VM (`rt-scraper`, zone `us-east1-b`) with cron
- **Scraping method**: Selenium (RT's `/napi/` endpoint returns 404; verified via curl)
- **Interpolation**: Even distribution between known timestamps, all marked `reconciled_timestamp=True`
- **Top critic detection**: Filter-based — scrape `top-critics` first (all are top critics), then `all-critics`. Isolated in one line, easy to change later.
- **Dashboard frontend**: Jinja2 + HTMX + Plotly.js (no JS build pipeline, all-Python stack)
- **Dashboard hosting**: Same e2-micro VM, separate systemd service, binds to `127.0.0.1:8000` (SSH tunnel for access)
- **Dashboard DB access**: Read-only SQLite connections (`?mode=ro`), WAL mode allows concurrent reads during scraper writes
- **PDF generation**: fpdf2 + matplotlib (lightweight, ~50MB peak vs ~350MB+ for WeasyPrint + kaleido)
- **Dashboard repo structure**: Monorepo `web/` subdirectory with its own `pyproject.toml` — shares `movies.json` and DB file, separate CI/CD workflow

## Implementation Status

### Fully Implemented and Deployed
- **`get_reviews(movie_slug, critic_filter, stop_at_unit)`** — Selenium scraper with early stopping. `stop_at_unit='h'` for hour window, `'d'` for day window. Returns list of review dicts.
- **`scrape_hour_sliding_window(movie_slug)`** — Runs every 5 min via cron. Pre-checks review count via HTTP first; only launches Selenium if count changed. Frequency ensures lagging reviews are caught within a short time horizon.
- **`scrape_day_sliding_window(movie_slug)`** — Runs every 6 hours via cron. Always does full scrape. Reconciles lagging reviews missed by the hour window, exports reference CSV, calibrates pre-check state.
- **SQLite layer** — `init_reviews_table` (single unified table with `movie_slug` column, schema versioning via `schema_version` table), `insert_review` (dedup via MD5 unique_review_id), `update_sentiment` (fills NULL tomatometer_sentiment only), `get_db_review_ids`, `get_db_reviews_sorted`, `export_reference_csv`
- **Pre-check system** — `fetch_review_count()` hits main movie page (`/m/{slug}`) with `requests`, extracts count via regex `(\d+) Reviews`. `has_new_reviews()` compares against stored count in `precheck_state` table. Tracks consecutive failures; logs WARNING each time, ERROR after 10+. Falls back to full Selenium scrape on failure.
- **Reconciliation** — `reconcile_missing_reviews()` groups consecutive missing reviews, interpolates timestamps from DB anchor neighbors. Only reconciles reviews with at least one DB anchor (no false reconciliation on first run/empty DB).
- **Deduplication** — MD5 hash of `(movie_slug + reviewer_name + publication_name + subjective_score)` as `unique_review_id`, enforced via SQLite UNIQUE constraint. Schema migration v1 rehashes existing rows automatically.
- **Logging** — to `scraper.log` (FileHandler) + console (StreamHandler)
- **Multi-movie config** — `movies.json` with `[{slug, enabled}]` entries. `load_movie_config()` reads enabled slugs. CLI `--movie <slug>` overrides the config for one-off runs.
- **CLI** — `--window hour|day|both` and `--movie <slug>` (override) via argparse
- **GCS backups** — `deploy/backup_db.sh` copies `reviews.db` to `gs://rotten-tomatoes-scraper-backups/reviews-YYYY-MM-DD.db` daily at 3 AM via cron. Uses VM's default service account (needs `Storage Object Admin` role on the bucket, `cloud-platform` scope on the VM).
- **CSV cleanup** — `deploy/cleanup_csv.sh` deletes `*_reference.csv` files older than 30 days. Runs daily at 4 AM via cron.
- **Email notifications** — Google Cloud Ops Agent ships `scraper.log` and `cron.log` to Cloud Logging. Cloud Monitoring alert policy emails on ERROR-level entries (pre-check failures, Selenium errors, backup failures).
- **GCP deployment** — `deploy/setup_vm.sh` handles everything: installs Chromium, uv, Python deps (scraper + dashboard), Ops Agent, sets up cron (including daily backup and CSV cleanup), and registers `rt-dashboard` systemd service. VM has 2GB swap file (needed for e2-micro's 1GB RAM).
- **CI/CD** — `.github/workflows/deploy.yml` auto-deploys to GCP VM on push to main. Uses Workload Identity Federation (no stored keys). SCPs scraper files + `web/` directory as `jakelehner@rt-scraper`, runs `uv sync` for both, restarts `rt-dashboard` service.
- **Timestamp year heuristic** — `convert_rel_timestamp_to_abs()` rolls back to previous year when parsed date ("Mar 22") is in the future. Known limitation: reviews 2+ years old may be off by 1 year.
- **Backfill script** — `scripts/backfill.py` one-time tool to scrape all historical reviews and fill missing sentiment. Two-pass (top-critics → all-critics) preserves `top_critic` flag. Run locally against a copy of `reviews.db`. Supports `--movie`, `--db`, `--dry-run`.
- **81 tests** — covering timestamp utils (incl. year heuristic), MD5 hashing (incl. cross-movie uniqueness), hash migration, interpolation, DB dedup, reconciliation, pre-check state, fetch_review_count, has_new_reviews, movie config loading, tomatometer_sentiment persistence, update_sentiment, backfill logic. All use in-memory SQLite and mocks.

### Dashboard (web/) — Complete

**Phase 1 complete (Foundation + Reviews Table):**
- **FastAPI app** — `web/app/main.py` with Jinja2 templates, static file serving, HTMX partials
- **Config** — `web/app/config.py` reads DB_PATH, HOST, PORT, MOVIES_JSON_PATH from env vars with defaults
- **DB layer** — `web/app/db.py` provides read-only SQLite connections as FastAPI dependencies, loads movie slugs from `movies.json`
- **Review service** — `web/app/services/review_service.py` with paginated queries (newest-first, movie filter, page size clamping)
- **Reviews page** — Full page at `/reviews`, HTMX partial at `/reviews/table` for arrow-click pagination without scrolling
- **Templates** — `base.html` (shared nav + HTMX/Plotly.js CDN), `reviews.html`, `partials/review_table.html`
- **16 tests** — covering ReviewPage dataclass, paginated queries, movie filtering, edge cases

**Phase 2 complete (Math Layer + Analytics Dashboard):**
- **Cache** — `web/app/cache.py` in-memory TTL cache (60s default), keyed by `(func_name, movie, frozen_params)`. Functions: `cache_get`, `cache_set`, `cache_clear`, `make_key`.
- **Math layer** — four pure-function modules in `web/app/math/`:
  - `sentiment.py` — `sentiment_counts()`, `current_tomatometer()`, `tomatometer_over_time()`
  - `timing.py` — `reviews_per_bucket(bucket="day"|"hour")`, `cumulative_reviews()`, `avg_reviews_per_day()`
  - `critics.py` — `top_critic_split()`, `publication_counts(top_n=10)`
  - `scoring.py` — `score_distribution()`
- **Analytics service** — `web/app/services/analytics_service.py` orchestrates DB → math → Plotly JSON specs with caching. 6 chart types: `tomatometer_over_time`, `review_volume`, `sentiment_breakdown`, `top_critic_comparison`, `cumulative_reviews`, `score_distribution`. Tomatometer chart y-axis is computed from data with 5-point padding, clamped to [0, 100]. Stats panel: tomatometer %, total reviews, positive/negative counts, top critic score, avg reviews/day.
- **Analytics router** — `web/app/routers/analytics.py` with `GET /analytics` (full page), `GET /analytics/chart` (HTMX chart partial), `GET /analytics/calc` (HTMX stats partial)
- **Analytics templates** — `analytics.html` (sidebar with per-movie dropdown + chart type selector + stats panel, main area with Plotly chart), `partials/chart.html` (inline Plotly.newPlot script), `partials/calculation.html` (stat grid cards). No "All Movies" option — analytics are per-movie only.
- **HTMX interactions** — Movie dropdown triggers both chart and stats reload. Chart type dropdown triggers chart reload. Both use `hx-include` to pass sibling control values.
- **56 new tests** (72 total) — covering cache (key building, get/set, expiry, clear), all four math modules (empty data, single review, mixed sentiment, edge cases), and analytics service (chart JSON validity for all 6 types, stats computation, movie filtering, caching)

**Phase 3 complete (VM Deployment):**
- **systemd service** — `web/deploy/rt-dashboard.service` runs uvicorn as `jakelehner` user, binds to `127.0.0.1:8000`, single worker, memory-limited (`MemoryHigh=150M`, `MemoryMax=200M`)
- **VM setup** — `deploy/setup_vm.sh` updated to [7/7] steps: installs dashboard deps (`uv sync` in `web/`), copies unit file, enables and starts service
- **CI/CD** — `.github/workflows/deploy.yml` triggers on `web/**`, SCPs `web/` recursively, syncs deps, restarts `rt-dashboard` service
- **Log shipping** — `deploy/ops-agent-config.yaml` adds `dashboard_log` receiver for `dashboard.log`, shipped to Cloud Logging via `rt_dashboard` pipeline
- **Memory budget** — Dashboard uses ~40-60MB RSS (no plotly/matplotlib imports at runtime). `MemoryHigh=150M`, `MemoryMax=200M` leaves 800MB+ for intermittent Selenium scraper

**Phase 4 complete (Report Page + PDF Generation):**
- **Report service** — `web/app/services/report_service.py` collects all stats, tables, and chart data via `get_report_data()`. `generate_pdf()` renders a multi-page PDF using fpdf2 for layout and matplotlib (Agg backend) for chart images.
- **Report router** — `web/app/routers/reports.py` with `GET /reports` (full page), `GET /reports/preview` (HTMX document preview partial), `GET /reports/download` (PDF binary response). Uses `asyncio.Semaphore(1)` to serialize PDF renders + `asyncio.to_thread()` to offload CPU-bound rendering.
- **Report templates** — `reports.html` (controls bar with per-movie dropdown + download button, preview area), `partials/report_preview.html` (document-style HTML preview with stats table, 4 Plotly charts reused from analytics, publications table, score distribution table). No "All Movies" option — reports are per-movie only.
- **Memory safety** — Matplotlib figures closed immediately after saving to buffer; chart buffers closed after embedding in PDF (one at a time, not accumulated). Estimated peak: ~45MB additional over baseline (~118MB total, well under 200MB cap). Semaphore prevents concurrent renders.
- **15 new tests** (87 total) — covering `get_report_data` (empty DB, with reviews, movie filter, caching, data structure) and `generate_pdf` (valid PDF bytes, empty DB, movie filter, edge cases).

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

### `schema_version` table

| Field | Type | Description |
|---|---|---|
| version | INTEGER | Current schema version (1 = review IDs rehashed with movie_slug) |

## Architecture

### Hour Sliding Window (every 5 min)
1. **Pre-check**: HTTP GET to `/m/{slug}`, regex for `(\d+) Reviews`, compare to stored count
2. If count unchanged → skip, log, done
3. If count changed or pre-check failed → launch Selenium
4. Scrape reviews with `stop_at_unit='h'` (only "m"-timestamped reviews)
5. Insert new reviews (skip duplicates)
6. High frequency ensures lagging reviews are caught within a short time horizon

### Day Sliding Window (every 6 hours)
1. Always runs full Selenium scrape with `stop_at_unit='d'`
2. Compare scraped reviews against DB
3. Reconcile missing reviews that the hour window missed (interpolate timestamps between DB anchors)
4. Export reference CSV
5. Calibrate pre-check state with authoritative count

### Dashboard Architecture
- **Layering**: Router → Service → Math. Routers handle HTTP/HTMX, services orchestrate DB + math, math functions are pure (data in, result out).
- **HTMX pattern**: User interaction → HTMX sends GET → FastAPI returns HTML fragment → HTMX swaps into page. Trigger strategies: `change` for dropdowns, `click` for buttons, `keyup changed delay:500ms` for text inputs.
- **Concurrency**: Read-only SQLite + WAL mode. Fresh connection per request, closed after response.
- **Caching**: In-memory dict with 60s TTL keyed by `(function, movie, params_frozen_tuple)`.
- **PDF generation**: `asyncio.Semaphore(1)` serializes renders to cap memory. `asyncio.to_thread()` offloads CPU work off the event loop. Matplotlib uses Agg backend (non-interactive); each figure is created, saved to BytesIO, and closed immediately.

### Reconciliation Rules
- Only reconciles reviews that have **at least one DB anchor neighbor** (proving the hour window was running during that time period)
- No anchors = reviews are just unseen (first run / empty DB), not lagging → skip
- Timestamps are evenly distributed between anchor points
- All reconciled reviews marked `reconciled_timestamp=True`
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

# Run scraper tests
uv run --group dev pytest tests/ -v

# === Dashboard ===
# Start dev server (from web/ directory)
cd web && uv run uvicorn app.main:app --reload

# Run dashboard tests
cd web && uv run --group dev pytest tests/ -v
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
- **Logs**: `cron.log` (cron stdout/stderr), `scraper.log` (Python logging), `dashboard.log` (uvicorn output)
- **Dashboard**: `rt-dashboard.service` (systemd), binds to `127.0.0.1:8000`, memory-limited (`MemoryHigh=150M`, `MemoryMax=200M`)
- **Dashboard access**: `ssh -L 8000:127.0.0.1:8000 jakelehner@<vm-ip>` then open `http://localhost:8000`
- **SSH**: `gcloud compute ssh rt-scraper --zone=us-east1-b`
- **Deploy**: Push to `main` — GitHub Actions auto-deploys scraper + dashboard via `.github/workflows/deploy.yml`
- **Manual deploy** (if needed): `gcloud compute scp rotten_tomatoes.py movies.json jakelehner@rt-scraper:~/rotten-tomatoes-analysis/ --zone=us-east1-b`
- **CI/CD auth**: Workload Identity Federation — pool `github`, provider `github-actions`, SA `github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com`
- **GitHub secrets**: `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`

## Dependencies

**Scraper** (`pyproject.toml`): beautifulsoup4, selenium, requests, pandas, matplotlib. Dev: pytest.

**Dashboard** (`web/pyproject.toml`): fastapi, uvicorn, jinja2, plotly, fpdf2, matplotlib, python-multipart. Dev: pytest, httpx.

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
Detailed context and implementation plans for items 1-4: `.claude/backlog-context.md`

1. Add timestamp filter to reviews page (`after` date picker, `WHERE timestamp > ?`)
2. Normalize subjective scores into a 0-1 scale; replace raw count chart with "fresh review strength distribution"
3. Replace `reconciled_timestamp` boolean with `timestamp_confidence` column ("exact", "hour", "day", "interpolated", "backfill") — includes migration + backfill identification
4. Extract `parse_review_cards(html)` from `get_reviews()` + add mocked HTTP boundary tests
- Security test suite: SQL injection, XSS, and parsing robustness tests with adversarial inputs

## Security Decisions & Tradeoffs

Running log of security-relevant choices and their rationale.

- **`check_same_thread=False` on SQLite connections** (`web/app/db.py`): Required because uvicorn's thread pool dispatches async endpoint handlers across threads, but SQLite's default `check_same_thread=True` forbids cross-thread use. Safe here because connections are read-only (`?mode=ro`), scoped to a single request (opened and closed in the `get_connection` generator), and never shared between concurrent requests.
- **Dashboard binds to `127.0.0.1:8000`**: Not exposed to the internet. Access is SSH-tunnel-only, so there's no public attack surface. Authentication is deferred — the SSH tunnel itself is the access control.
- **`MemoryMax=200M` on dashboard service**: Hard OOM kill boundary prevents a runaway dashboard process from starving the scraper (which needs 500-800MB for Selenium/Chrome). Trades dashboard availability for system stability.
- **CI/CD uses passwordless sudo for `systemctl restart`**: Relies on GCP Compute Engine's default sudoers config (`/etc/sudoers.d/google_sudoers`) granting the primary SSH user full passwordless sudo. Acceptable because the VM is single-purpose and single-user.
