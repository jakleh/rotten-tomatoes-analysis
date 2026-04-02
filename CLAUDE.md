# CLAUDE.md

## Project Overview

Rotten Tomatoes web scraper that builds a time-series database of movie reviews. Runs every 50 minutes via Cloud Run Jobs + Cloud Scheduler. Stores reviews in Neon (serverless PostgreSQL). Deployed automatically via GitHub Actions on push to main.

## File Structure

```
├── rotten_tomatoes.py          # Main scraper (scraping, DB insert, spike guard)
├── movies.json                 # Movie config: list of {slug, enabled} objects
├── Dockerfile                  # Python 3.14 + Chromium + ChromeDriver
├── .dockerignore               # Excludes tests, scripts, plan, docs from image
├── scripts/
│   └── backfill.py             # One-time backfill of historical reviews (run locally)
├── tests/
│   └── test_rotten_tomatoes.py # 60 tests (all pure logic, no network/browser)
├── .github/
│   └── workflows/
│       └── deploy.yml          # GitHub Actions: build image, push to AR, update Cloud Run Job
├── plan/
│   └── errors/                 # Error playbooks with decision trees
│       ├── chrome.md           # Chrome/ChromeDriver failures
│       ├── cloud_run.md        # Cloud Run Job execution failures
│       ├── cloud_scheduler.md  # Cloud Scheduler trigger failures
│       ├── github_actions.md   # GitHub Actions CI/CD failures
│       ├── html_parsing.md     # RT HTML parsing errors
│       ├── backfill.md         # Backfill script failures
│       ├── monitoring.md       # Monitoring/alerting failures
│       ├── neon.md             # Neon database connection errors
│       └── selenium.md         # Selenium page load & interaction failures
├── pyproject.toml              # Dependencies (uv managed, Python >=3.14)
├── .gitignore
├── README.MD                   # Project documentation
└── .claude/                    # Claude Code config
```

## Tech Stack

- **Language**: Python 3.14
- **Package manager**: uv
- **Scraping**: Selenium WebDriver (headless Chromium) + BeautifulSoup4
- **Database**: Neon (serverless PostgreSQL) via `psycopg2-binary`
- **Compute**: Cloud Run Jobs (ephemeral containers, per-execution)
- **Scheduling**: Cloud Scheduler (`every 50 minutes`)
- **Secrets**: Google Secret Manager (`DATABASE_URL`)
- **Container registry**: Artifact Registry (`us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper`)
- **CI/CD**: GitHub Actions (Docker build + push + Cloud Run Job update via Workload Identity Federation)

## Resolved Design Decisions

- **Database**: Neon (serverless Postgres). Cloud Run Jobs are ephemeral (no persistent disk), so SQLite is not viable. Neon auto-suspends when idle; first connection takes ~1-3s (cold start).
- **Single scrape function**: No hour/day window split, no pre-check, no reconciliation. One function scrapes recent reviews (relative timestamps only), inserts into Postgres.
- **Stop condition**: Stop loading "Load More" when the last visible review has an absolute date-format timestamp (e.g., "Mar 20"). Captures all "m", "h", "d" relative reviews (~last 7 days).
- **Scraping method**: Selenium (RT's `/napi/` endpoint returns 404; verified via curl).
- **Top critic detection**: Filter-based -- scrape `top-critics` first, then `all-critics`. Isolated in one line.
- **Connect late pattern**: Scrape all reviews into memory first (no DB connection during Selenium). Connect to Neon only for batch insert (~30 seconds), then disconnect. Minimizes Neon compute hours.
- **Interpolation lives in backfill only**: Main scraper inserts with `estimated_timestamp` derived from `scrape_time - relative_offset`. Backfill script handles historical timestamp estimation using `page_position` + neighboring DB anchors.
- **page_position stored**: Each review records its 0-indexed position in the scrape result (within a filter/run), enabling order-based interpolation in the backfill script.

## Implementation Status

### Fully Implemented and Deployed
- **`get_reviews(movie_slug, critic_filter)`** -- Selenium scraper. Loads pages until oldest visible review has date-format timestamp. Parses all relative-timestamp cards. Returns list of review dicts with `page_position`, `written_review`, `site_timestamp_text`, `scrape_time`, `estimated_timestamp`, `timestamp_confidence`.
- **`scrape(movie_slug)`** -- Orchestrates scraping for a single movie. Two-pass (top-critics, all-critics). "Connect late" pattern: scrapes into memory, connects to Neon only for batch insert. Spike guard rolls back if insert count exceeds threshold (possible selector breakage).
- **`_parse_cards()`** -- Extracts review data from BeautifulSoup cards. WARNING per-card for missing fields, ERROR if a critical field is NULL across ALL cards.
- **`_find_selector()`** -- Uses centralized `SELECTORS` dict for all BeautifulSoup lookups. Single place to update if RT HTML changes.
- **`_build_driver()`** -- Headless Chromium with `--no-sandbox`, `--disable-dev-shm-usage`, `--js-flags=--max-old-space-size=256`, `--disable-blink-features=AutomationControlled`. Page load timeout 30s, script timeout 15s.
- **Retry loop** -- 3 attempts for `driver.get(url)`, 5s between retries. WARNING on retry, ERROR if all fail. Returns `[]` for that movie (others proceed).
- **Load More stall detection** -- Tracks card count before/after click, bails after 2 consecutive no-change clicks.
- **Postgres layer** -- `get_db_connection()` uses `DATABASE_URL` env var with `connect_timeout=10`. `insert_review()` uses `ON CONFLICT DO NOTHING` for idempotent dedup.
- **Spike guard** -- If a single batch inserts more than `INSERT_SPIKE_THRESHOLD` (50) reviews AND the movie already has 50+ existing reviews, rollback instead of committing (likely selector breakage creating new hashes for every existing review). Skipped for fresh DB / newly added movies.
- **Deduplication** -- MD5 hash of `(movie_slug + reviewer_name + publication_name + subjective_score)` as `unique_review_id`, enforced via Postgres UNIQUE constraint.
- **Timestamp utilities** -- Robust regex `r'^(\d+)\s*(m|min|h|hr|d|day)s?$'` with `UNIT_ALIASES` mapping. `convert_rel_timestamp_to_abs()` takes `scrape_time` param for consistency within a scrape run. Year heuristic rolls back when parsed date is in the future.
- **Logging** -- JSON structured logging via `_CloudRunFormatter`. Emits `{"severity", "message", "time"}` per line so Cloud Run auto-maps severity to Cloud Logging. Handles tracebacks via `record.exc_info`.
- **Multi-movie config** -- `movies.json` with `[{slug, enabled}]` entries. `load_movie_config()` reads enabled slugs. CLI `--movie <slug>` overrides config.
- **Run mode traceability** -- Logs `"=== Run started: mode=scheduled/manual, movies=[...] ==="` at start of every run.
- **Backfill script** -- `scripts/backfill.py` one-time tool to scrape ALL historical reviews (including date-format timestamps). Two-pass (top-critics, all-critics). Post-run health check: HTTP GET to RT main page, compares count to DB, ERROR if delta > 10. Run locally with `DATABASE_URL` set. Supports `--movie`, `--dry-run`, `--time-end YYYY-MM-DD` (exclude reviews after a date; requires `--movie`; skips health check).
- **CI/CD** -- `.github/workflows/deploy.yml` builds Docker image on push to main, pushes to Artifact Registry (tagged with commit SHA + `latest`), updates Cloud Run Job. Import sanity check (`python -c "import rotten_tomatoes"`) runs before push to catch broken images. Uses Workload Identity Federation (no stored keys).
- **60 tests** -- Covering timestamp utils (incl. robust regex variants, year heuristic), MD5 hashing (incl. cross-movie uniqueness), `_find_selector` against sample HTML, movie config loading, JSON log formatter (valid output, severity mapping, traceback inclusion, non-ASCII), backfill `filter_reviews_by_cutoff` (boundary conditions, None handling, mixed input), and backfill argparse validation (`--time-end` requires `--movie`, invalid date format). All use mocks, no network/browser.

## Database Schema

### `reviews` table (Neon PostgreSQL)

| Field | Type | Description |
|---|---|---|
| id | SERIAL | Auto-increment primary key |
| unique_review_id | TEXT (UNIQUE) | MD5 hash of (movie_slug + name + publication + rating) |
| movie_slug | TEXT | Movie being tracked (e.g., "project_hail_mary") |
| reviewer_name | TEXT | Critic's name |
| publication_name | TEXT | Publication (e.g., "The Guardian") |
| top_critic | BOOLEAN | True if scraped from top-critics filter |
| tomatometer_sentiment | TEXT | "positive" or "negative" (from score-icon-critics element) |
| subjective_score | TEXT | e.g., "3/5", "A-" |
| written_review | TEXT | Review snippet text from card HTML |
| site_timestamp_text | TEXT | Raw RT relative timestamp (e.g., "5m", "3h", "Mar 20") |
| scrape_time | TIMESTAMPTZ | UTC datetime when the scrape ran |
| estimated_timestamp | TIMESTAMPTZ | scrape_time minus the offset in site_timestamp_text |
| timestamp_confidence | TEXT | Timestamp granularity: "m" (minute), "h" (hour), "d" (day/date) |
| page_position | INTEGER | 0-indexed position in scrape result (0 = newest) |

Indexes: `idx_reviews_movie_slug` on `(movie_slug)`, `idx_reviews_movie_timestamp` on `(movie_slug, estimated_timestamp)`

## Architecture

### Scrape Cycle (every 50 minutes)
1. Cloud Scheduler triggers Cloud Run Job via HTTP POST
2. Container starts, reads `movies.json` for enabled movie slugs
3. For each movie: scrape `top-critics` then `all-critics`
4. Each scrape: load pages until date-format timestamp reached, parse relative-timestamp cards
5. "Connect late": after all scraping is done, open Neon connection
6. Batch insert new reviews (ON CONFLICT DO NOTHING for dedup)
7. Spike guard: rollback if insert count is abnormally high (possible selector breakage)
8. Container exits, Cloud Run reclaims resources

### Key Properties
- **Insert-only** -- reviews are never deleted or updated
- **Idempotent** -- safe to re-run; duplicates are silently skipped via UNIQUE constraint
- **Ephemeral compute** -- no persistent state on the container; all state lives in Neon
- **Failure isolation** -- one movie failing doesn't prevent others from being scraped

## How to Run

```bash
# Run scraper locally (requires DATABASE_URL env var)
DATABASE_URL="postgresql://..." uv run python rotten_tomatoes.py

# Override config: scrape a single movie
DATABASE_URL="postgresql://..." uv run python rotten_tomatoes.py --movie project_hail_mary

# Run tests
uv run --group dev pytest tests/ -v

# Backfill historical reviews (run locally, not via Cloud Run)
DATABASE_URL="postgresql://..." uv run python scripts/backfill.py
DATABASE_URL="postgresql://..." uv run python scripts/backfill.py --movie project_hail_mary --dry-run

# Backfill with time cutoff (exclude reviews after a date)
DATABASE_URL="postgresql://..." uv run python scripts/backfill.py --movie project_hail_mary --time-end 2026-02-21

# Build Docker image locally
docker build -t rt-scraper:local .
```

## GCP Infrastructure

- **Cloud Run Job**: `rt-scraper` in `us-east1`, 2Gi memory, 1 CPU, 900s timeout, max-retries=1
- **Cloud Scheduler**: `rt-scraper-schedule` in `us-east1`, `every 50 minutes`, America/New_York timezone
- **Artifact Registry**: `us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper`
- **Secret Manager**: `DATABASE_URL` (Neon connection string)
- **Service accounts**:
  - `1065819890045-compute@developer.gserviceaccount.com` (Compute Engine default SA) -- runs Cloud Run Jobs, triggers via Cloud Scheduler, accesses secrets
  - `github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com` -- CI/CD: `artifactregistry.writer` + `run.developer`
- **CI/CD auth**: Workload Identity Federation -- pool `github`, provider `github-actions`
- **GitHub secrets**: `WIF_PROVIDER`, `WIF_SERVICE_ACCOUNT`
- **Neon**: Project `rotten-tomatoes` in `us-east-1` (AWS Virginia), database `neondb`
- **Monitoring**:
  - Log-based metric: `rt-scraper-errors` (counts `severity>=ERROR` from Cloud Run Job logs)
  - Alert policy: `RT Scraper ERROR Alert` (emails on any ERROR log, 10-min alignment)
  - Alert policy: `RT Scraper Job Failure Alert` (emails on job execution failures — OOM, timeout, image pull)
  - Notification channel: `RT Scraper Email` (email to `jslehner16@gmail.com`)

## Dependencies

`pyproject.toml`: beautifulsoup4, selenium, psycopg2-binary. Dev: pytest.

## Workflow: Milestone Checklist

After completing any non-trivial task, walk through this checklist with the user before considering the work done.

### Code Quality
- [ ] Tests added or updated for new/changed behavior
- [ ] All tests pass: `uv run --group dev pytest tests/ -v`
- [ ] No leftover TODOs, debug prints, or commented-out code from this change

### Documentation
- [ ] `CLAUDE.md` updated if: schema changed, new files added, new features implemented, deployment process changed, or design decisions were made
- [ ] `README.MD` updated if: user-facing behavior changed, setup steps changed, or project structure changed
- [ ] File structure section in both docs reflects any new/removed files

### Git & Deployment
- [ ] Changes committed with a descriptive message
- [ ] Pushed to `origin main` (use SSH remote, not HTTPS, to include workflow changes)
- [ ] GitHub Actions deploy workflow ran successfully (check Actions tab)

### Loose Ends
- [ ] No stale counts in docs (e.g., test count in CLAUDE.md matches actual)
- [ ] If a new dependency was added: `pyproject.toml` updated, `uv.lock` committed

### Tech Backlog
Track known improvements or deferred work here. Remove items as they're completed.

1. Normalize subjective scores into a 0-1 scale
2. Add mocked HTTP boundary tests for `_parse_cards()`
3. Add Postgres integration tests (currently deferred -- all tests are pure logic)
4. `bet-check` command: pre-bet data quality check comparing DB reviews against live RT site to catch meaningful edits (especially sentiment flips affecting tomatometer %)

## Security Decisions & Tradeoffs

Running log of security-relevant choices and their rationale.

- **DATABASE_URL in Secret Manager**: Connection string (with credentials) never stored in code, env files, or GitHub secrets. Mounted into Cloud Run Job at runtime via `--set-secrets`.
- **Workload Identity Federation for CI/CD**: No long-lived service account keys stored in GitHub. Authentication is token-based per workflow run.
- **`github-deploy` SA has minimal roles**: Only `artifactregistry.writer` (push images) and `run.developer` (update jobs). No access to secrets, no ability to execute jobs.
- **Spike guard**: Prevents a broken selector from silently replacing all existing reviews with bad data. Rolls back the batch and logs ERROR instead of committing.
- **`connect_timeout=10`**: Prevents the scraper from hanging indefinitely if Neon is unreachable (cold start or outage).
