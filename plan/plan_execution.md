# Migration Execution Record

Tracks actual outputs, values, and completion status for each step.

---

## Step 01: GCP Setup — DONE (2026-03-27)

### Commands Executed
1. Verified active project: `rotten-tomatoes-scraper`
2. Enabled 4 APIs: `run`, `cloudscheduler`, `secretmanager`, `artifactregistry`
3. Created Artifact Registry repo: `rt-scraper` (us-east1, DOCKER format)
4. Configured local Docker auth for `us-east1-docker.pkg.dev`
5. Granted `github-deploy` SA two new roles: `roles/artifactregistry.writer`, `roles/run.developer`

### Key Values
| Value | Used In |
|---|---|
| Project number: `1065819890045` | Steps 03, 07, 08 |
| Default SA: `1065819890045-compute@developer.gserviceaccount.com` | Steps 03, 08 |
| AR image path: `us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper` | Steps 06, 07, 09 |
| `github-deploy` SA: `github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com` | Steps 08, 09 |

### Notes
- `docker` not in shell PATH warning during auth config — harmless, Docker Desktop handles it
- Old VM role `roles/compute.instanceAdmin.v1` left on `github-deploy` SA — remove in Step 12

---

## Step 02: Neon Setup — DONE (2026-03-27)

### Actions Taken
1. Created Neon project `rotten-tomatoes` in `US East (AWS us-east-1)` (Virginia)
2. Default database: `neondb`
3. Created `reviews` table (14 columns) + 2 indexes via SQL Editor
4. Verified schema with `information_schema.columns` query — all 14 columns correct

### Key Values
| Value | Used In |
|---|---|
| Neon connection string (stored in Secret Manager, not here) | Steps 03, 04, 11 |
| Neon host: `ep-divine-dew-amazcryi-pooler.c-5.us-east-1.aws.neon.tech` | Reference only |
| Database: `neondb` | Reference only |

### Notes
- Postgres version: latest offered by Neon at time of creation
- Neon Auth: skipped (not needed — connection string auth is sufficient)
- Demo template SQL deleted before running schema
- Pasted schema + verify SQL together; table/indexes created successfully, verify text caused harmless error

## Step 03: Secret Manager — DONE (2026-03-27)

### Commands Executed
1. Created secret: `gcloud secrets create DATABASE_URL --replication-policy="automatic"`
2. Stored Neon connection string as version 1 (via `echo -n | gcloud secrets versions add`)
3. Verified value: `gcloud secrets versions access latest --secret=DATABASE_URL` — correct, no trailing newline
4. Granted `1065819890045-compute@developer.gserviceaccount.com` role `roles/secretmanager.secretAccessor`

### Notes
- First attempt at storing the value failed due to line break splitting `--data-file=-` onto a new line; second attempt succeeded
- Connection string includes `&channel_binding=require` (Neon default) — should work fine with psycopg2

## Step 04: Scraper Rewrite — DONE (2026-03-27)

### Changes Made

**`rotten_tomatoes.py` — full rewrite:**
1. Replaced SQLite with psycopg2 (Postgres via `DATABASE_URL` env var)
2. Deleted: all SQLite functions, precheck system, reconciliation, interpolation, sliding windows, `--window` CLI arg, `stop_at_unit` param, FileHandler logging
3. Added `SELECTORS` dict centralizing all BeautifulSoup selectors
4. Added robust timestamp regex (`r'^(\d+)\s*(m|min|h|hr|d|day)s?$'`) with `UNIT_ALIASES` mapping
5. `convert_rel_timestamp_to_abs()` now takes `scrape_time` param (not `datetime.now()`)
6. `get_reviews()` stops loading at date-format timestamps, stops parsing at first date-format card, includes `page_position` and `written_review` (via `<div slot="review">`)
7. Retry loop (3 attempts, 5s between) for `driver.get(url)`
8. Driver timeouts: `set_page_load_timeout(30)`, `set_script_timeout(15)`
9. WARNING logging per-card when selector fields are NULL; ERROR when critical field NULL across ALL cards
10. "Connect late" pattern: scrapes all reviews into memory, connects to DB only for batch insert
11. Run mode traceability: logs `"=== Run started: mode=scheduled/manual, movies=[...] ==="`
12. Extracted `_parse_cards()` helper and `_find_selector()` for testability

**`pyproject.toml`:**
- Removed: `requests`, `datasette`, `datasette-vega`
- Added: `psycopg2-binary>=2.9`
- `uv.lock` regenerated

**`scripts/backfill.py` — rewrite for Postgres:**
- Uses `DATABASE_URL` env var (no `--db` flag)
- `get_all_reviews()` loads ALL pages and parses ALL cards (including date-format)
- Post-run health check: HTTP GET to RT main page, extracts count via regex, compares to DB count, ERROR if delta > 10
- Uses `urllib.request` (stdlib) for health check HTTP (no `requests` dependency)

**`tests/test_rotten_tomatoes.py` — updated:**
- 45 tests (down from 92): kept pure-logic tests, removed all SQLite/precheck/reconciliation tests
- Updated `convert_rel_timestamp_to_abs` tests to pass `scrape_time` param
- Added tests for robust regex variants (min, hr, hrs, day, days, mins, case insensitive)
- Added `TestFindSelector` class testing the SELECTORS dict against sample HTML
- DB integration tests deferred to post-migration

### Error Playbook Hardening (applied same session)
After reviewing `plan/errors/` playbooks, applied 5 additional preventative measures:
1. `connect_timeout=10` on `psycopg2.connect()` — guards against Neon cold-start hangs (neon.md D-1)
2. `--disable-blink-features=AutomationControlled` Chrome flag — reduces bot detection risk (selenium.md B-4)
3. Dedup hash spike guard: if >50 inserts for one movie/filter batch, rollback + ERROR instead of committing (html_parsing.md C-6/C-7/C-9). `INSERT_SPIKE_THRESHOLD = 50` constant.
4. Load More stall detection: tracks card count before/after click, bails after 2 consecutive no-change clicks (selenium.md B-3/B-8). Applied to both `get_reviews()` and backfill's `get_all_reviews()`.
5. Replaced `time.sleep(5)` with `WebDriverWait` for `review-card` presence after initial page load (selenium.md B-7). Faster and more reliable. Applied to both files.

### Notes
- `load_movie_config()` one-liner needed `"slug" in e` guard (caught by existing test)
- All 10 corrections from consolidated plan review applied

## Step 05: Dockerfile — PENDING

## Step 06: Artifact Registry Push — PENDING

## Step 07: Cloud Run Job — PENDING

## Step 08: Cloud Scheduler — PENDING

## Step 09: GitHub Actions — PENDING

## Step 10: Data Migration — ELIMINATED

## Step 11: Verification — PENDING

## Step 12: Cleanup — PENDING
