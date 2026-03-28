# Implementation Overview

## What We're Building

| Component | Old | New |
|---|---|---|
| Scheduler | cron on e2-micro VM | Cloud Scheduler (managed GCP service) |
| Compute | e2-micro VM (always on) | Cloud Run Jobs (ephemeral, per-execution) |
| Database | SQLite file on VM | Neon (serverless PostgreSQL) |
| Secrets | env vars in crontab | Google Secret Manager |
| CI/CD | SCP files to VM | Build + push Docker image |
| Analytics | Datasette | Streamlit (future, not in this plan) |

---

## Execution Order

Each step depends on the previous. Do not skip ahead.

```
01_gcp_setup           — enable APIs, create Artifact Registry repo, set IAM roles
02_neon_setup          — create Postgres DB, define schema
03_secret_manager      — store DATABASE_URL, grant Cloud Run access
04_scraper_rewrite     — update rotten_tomatoes.py + pyproject.toml (includes backfill script)
05_dockerfile          — write and locally test Docker image
06_artifact_registry   — build and push image to GCP
07_cloud_run_job       — create the Cloud Run Job, wire in secrets
08_cloud_scheduler     — schedule the job every 50 minutes
09_github_actions      — rewrite deploy.yml to build/push image on push to main
10_data_migration      — ELIMINATED (no SQLite data migration; backfill from RT instead)
11_verification        — end-to-end smoke test before tearing anything down
12_cleanup             — delete VM, GCS bucket, old deploy files, old code
```

---

## Key Design Decisions (locked)

- **Single scrape function**: no hour/day window split, no pre-check, no reconciliation inline.
- **Stop condition**: stop loading "Load More" when the last visible review has an absolute
  date-format timestamp ("Mar 20"). Captures all "m", "h", "d" relative reviews (~last 7 days).
- **Interpolation lives in backfill only**: main scraper inserts; backfill script handles
  historical timestamp estimation using page_position + neighboring DB anchors.
- **page_position stored**: each review records its 0-indexed position in the scrape result
  (within a filter/run), enabling order-based interpolation in the backfill script.
- **Postgres over SQLite**: Cloud Run Jobs are ephemeral (no persistent disk). Postgres is
  universally supported by analytics tools.
- **psycopg2-binary**: synchronous, no compilation needed in Docker, well-tested with Neon.
- **scrape_time captured once per scrape**: not per-card. All reviews in a scrape share
  the same reference time for estimated_timestamp calculation.

---

## Failure Scenarios

| Scenario | Behavior |
|---|---|
| GitHub Actions build fails | Cloud Scheduler keeps triggering the previous image. No scraping downtime. |
| Neon cold start (~1-3s delay) | Acceptable. Each Cloud Run Job connects fresh per invocation. |
| Chrome fails to launch | Job exits non-zero. Cloud Scheduler logs failure and retries once (max-retries=1). |
| RT page structure changes | get_reviews() returns 0 reviews. Insert loop runs 0 times. Logged as warning. |
| Duplicate review inserted | ON CONFLICT DO NOTHING. Silent skip. Idempotent. |
| Job exceeds timeout (600s) | Cloud Run kills the job. Next scheduled run retries from scratch. |
| Neon connection refused | psycopg2 raises exception. Job exits non-zero. Logged as error. |
| Bad image deployed | Execution fails, retries once, then stops. Next trigger retries the same bad image. Fix: push a working image. |

---

## Open Questions

Before starting implementation, review `questions.md`. Several steps have verification
commands that should be run before writing code (especially Q3 re: chromium-driver
package name and Q4 re: written_review slot name in RT HTML).
