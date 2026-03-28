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
3. Dedup hash spike guard: if >50 inserts for one movie/filter batch AND existing reviews >= 50, rollback + ERROR instead of committing (html_parsing.md C-6/C-7/C-9). `INSERT_SPIKE_THRESHOLD = 50` constant. DB-aware: skips guard when movie has fewer existing reviews than the threshold (fresh DB / newly added movie). Refined during Step 07 smoke testing.
4. Load More stall detection: tracks card count before/after click, bails after 2 consecutive no-change clicks (selenium.md B-3/B-8). Applied to both `get_reviews()` and backfill's `get_all_reviews()`.
5. Replaced `time.sleep(5)` with `WebDriverWait` for `review-card` presence after initial page load (selenium.md B-7). Faster and more reliable. Applied to both files.

### Notes
- `load_movie_config()` one-liner needed `"slug" in e` guard (caught by existing test)
- All 10 corrections from consolidated plan review applied

## Step 05: Dockerfile — DONE (2026-03-27)

### Files Created
1. `Dockerfile` — `python:3.14-slim-bookworm` base, Chromium + ChromeDriver via apt, uv for deps, layer-cached dependency install
2. `.dockerignore` — excludes reviews.db, logs, CSVs, .git, tests, scripts, plan, deploy, .github, *.md

### Chrome Error Prevention Applied
- `--no-install-recommends` on apt to keep image lean
- Build-time `chromium --version && chromedriver --version` verification (A-3/A-5)
- Both `chromium` and `chromium-driver` from same apt source (A-3/A-4 version match)
- `ENV CHROME_BIN=/usr/bin/chromium` set in image (A-9)
- `_build_driver()` already has: `--no-sandbox` (A-1/A-6), `--disable-dev-shm-usage` (A-7), `--js-flags=--max-old-space-size=256` (A-2), timeouts (A-8)

### Notes
- Docker is not installed on this Mac — build and local test deferred to after Docker Desktop (or equivalent) is installed
- Python 3.14 base image availability to be confirmed at build time; fallback is `python:3.13-slim-bookworm` + update `requires-python` in pyproject.toml
- If `chromium-driver` package name is wrong on slim-bookworm, run `docker run --rm python:3.14-slim-bookworm apt-cache search chromium` to find the correct name

## Step 06: Artifact Registry Push — DONE (2026-03-27)

### Commands Executed
1. Built image locally: `docker build -t rt-scraper:local .`
2. Verified Chrome versions: Chromium 146.0.7680.164, ChromeDriver 146.0.7680.164 (matched)
3. Tagged with `latest` and SHA `35bc2631630710a2c872f61772a5e7b15b9bb919`
4. Pushed both tags to `us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper`
5. Verified in Artifact Registry: image present, ~347MB compressed

### Notes
- Python 3.14-slim-bookworm base image available (no fallback to 3.13 needed)
- `chromium` and `chromium-driver` package names correct on slim-bookworm
- **Platform gotcha**: initial build produced ARM image (Apple Silicon default). Cloud Run rejected it: `must support amd64/linux`. Rebuilt with `docker buildx build --platform linux/amd64 --push .` — this cross-compiles via QEMU emulation. GitHub Actions (Step 09) will build natively on amd64 runners, so this is a one-time local issue.
- Old ARM images left as untagged manifests in AR — will be cleaned up by the 30-day retention policy (or Step 12)

## Step 07: Cloud Run Job — DONE (2026-03-27)

### Commands Executed
1. Created job: `gcloud run jobs create rt-scraper --memory=2Gi --cpu=1 --task-timeout=900s --max-retries=1 --set-secrets=DATABASE_URL=DATABASE_URL:latest --set-env-vars=CHROME_BIN=/usr/bin/chromium`
2. First attempt failed: image was ARM (Apple Silicon), Cloud Run requires amd64. Rebuilt with `docker buildx build --platform linux/amd64 --push .`
3. Deleted partial job from failed create, recreated successfully
4. Smoke test #1: job succeeded but spike guard rolled back all-critics batches for `project_hail_mary` (73 inserts) and `they_will_kill_you` (72 inserts) — threshold of 50 too aggressive for fresh DB
5. Fixed spike guard: skip when `existing_count < INSERT_SPIKE_THRESHOLD` (not enough existing reviews to indicate hash collision)
6. Smoke test #2: spike guard still triggered — existing_count was 16 (top-critics from first run), still > 0 but < threshold logic needed refinement
7. Final fix: `existing_count >= INSERT_SPIKE_THRESHOLD and inserted_batch > INSERT_SPIKE_THRESHOLD`
8. Smoke test #3: clean run. All 4 movies fully populated, zero errors, zero spike triggers

### Key Values
| Value | Used In |
|---|---|
| Job name: `rt-scraper` | Steps 08, 09, 11 |
| Region: `us-east1` | Steps 08, 09 |
| Timeout: 900s | Reference |
| Service account: `1065819890045-compute@developer.gserviceaccount.com` | Step 08 |

### Notes
- Timeout set to 900s (plan review increased from 600s for 8 Selenium sessions)
- Spike guard fix required 2 iterations: `> 0` check insufficient, needed `>= threshold` comparison
- All 4 movies now populated in Neon: project_hail_mary, ready_or_not_2_here_i_come, forbidden_fruits_2026, they_will_kill_you

## Step 08: Cloud Scheduler — DONE (2026-03-27)

### Commands Executed
1. Granted `roles/run.invoker` to Compute Engine default SA (`1065819890045-compute@developer.gserviceaccount.com`)
2. Created scheduler job: `gcloud scheduler jobs create http rt-scraper-schedule --location=us-east1 --schedule="every 50 minutes" --uri="https://run.googleapis.com/v2/projects/rotten-tomatoes-scraper/locations/us-east1/jobs/rt-scraper:run" --http-method=POST --message-body="{}" --oauth-service-account-email="1065819890045-compute@developer.gserviceaccount.com" --time-zone="America/New_York"`
3. Manual trigger: `gcloud scheduler jobs run rt-scraper-schedule --location=us-east1`
4. Verified execution `rt-scraper-zt7d6` completed successfully (triggered by SA, not user account)

### Key Values
| Value | Used In |
|---|---|
| Scheduler job name: `rt-scraper-schedule` | Steps 11, 12 |
| Schedule: `every 50 minutes` (Groc format) | Reference |
| Time zone: `America/New_York` | Reference |
| Next scheduled run confirmed in `scheduleTime` field | Reference |

### Notes
- `every 50 minutes` Groc syntax works as expected (no fallback to cron needed)
- First manual trigger showed `status.code: -1` for ~60 seconds (IAM propagation delay after granting `run.invoker`). Second trigger succeeded immediately.
- Execution completed successfully: all 4 movies scraped, no errors, only expected `missing 'subjective_score'` warnings

## Step 09: GitHub Actions — DONE (2026-03-27)

### Changes Made
1. Replaced `.github/workflows/deploy.yml` entirely — removed VM-based SCP/SSH deploy, replaced with Docker build + push + Cloud Run Job update
2. Added G-2 prevention step: `docker run --rm IMAGE python -c "import rotten_tomatoes"` sanity check between build and push
3. Updated path triggers: removed `metadata.yml` and `deploy/**`, added `Dockerfile`

### Workflow Steps
1. Checkout → WIF auth → setup gcloud
2. Authenticate Docker to Artifact Registry
3. Build image (tagged with commit SHA + `latest`)
4. Verify image imports cleanly (G-2 sanity check)
5. Push both tags to AR
6. Update Cloud Run Job to pin to commit SHA image

### Notes
- First push triggered workflow successfully — all steps green
- No new GitHub secrets needed (same WIF_PROVIDER + WIF_SERVICE_ACCOUNT)
- G-6 (stale uv.lock) covered by `uv sync --frozen` in Dockerfile
- `workflow_dispatch` retained as manual trigger safety net

## Step 10: Data Migration — ELIMINATED

## Step 11: Verification — DONE (2026-03-27)

### Checks Performed
1. **Schema in Neon** — confirmed in Step 02 (14 columns + 2 indexes)
2. **Review counts in Neon** — confirmed in Step 07 (all 4 movies populated via smoke tests)
3. **Manual Cloud Run execution** — confirmed in Step 08 (scheduler manual trigger succeeded)
4. **Cloud Scheduler firing autonomously** — confirmed: execution `rt-scraper-zt7d6` at 02:38 UTC triggered by Compute Engine SA (`1065819890045-compute@developer.gserviceaccount.com`), not user account. All 4 executions succeeded.
5. **GitHub Actions deploy** — confirmed: workflow passed on push, Cloud Run Job pinned to image SHA `afe5dfda385bdacef0eaadae6a625b6ebc0eef69` matching `HEAD`
6. **Force insert test** — skipped (Step 07 smoke tests already confirmed inserts work)

### Notes
- All verification items pass. System is fully operational: scheduler fires every 50 min, deploys update the image automatically on push to main.
- CLAUDE.md and README.MD updates deferred to Step 12 (cleanup) per plan

## Step 12: Cleanup — DONE (2026-03-27)

### Phase A: GCP Infra Teardown
1. Deleted VM: `gcloud compute instances delete rt-scraper --zone=us-east1-b` — confirmed, disk (reviews.db) destroyed
2. Deleted GCS bucket: `gsutil rm -r gs://rotten-tomatoes-scraper-backups` — 6 daily backups removed
3. Removed old IAM role: `compute.instanceAdmin.v1` from `github-deploy` SA. Remaining roles: `artifactregistry.writer`, `run.developer`
4. Deleted old Cloud Monitoring alert policy (Ops Agent ERROR-level log entries) from console

### Phase B: Repo Cleanup
1. `git rm -r deploy/` — removed 6 files (setup_vm.sh, rt-datasette.service, backup_db.sh, cleanup_csv.sh, ops-agent-config.yaml, alert-policy.json)
2. `git rm metadata.yml` — removed Datasette config
3. Cleaned up local-only files: 2 reference CSVs, reviews.db-shm, reviews.db-wal
4. Kept `scripts/backfill.py` (already rewritten for Postgres in Step 04)

### Phase C: Documentation
1. Rewrote `CLAUDE.md` — updated all sections for Cloud Run + Neon architecture
2. Rewrote `README.MD` — updated all sections for Cloud Run + Neon architecture

### Notes
- This commit should NOT trigger the deploy workflow (none of the changed files match the path filter in deploy.yml)
