# Data Migration — ELIMINATED

This step has been removed from the plan.

## Why

The existing SQLite data is not worth migrating:
- The dataset is small — a fresh backfill from RT re-scrapes all the same reviews.
- The only data lost is "m"/"h" timestamp confidence on a handful of reviews that were
  caught during live scraping. After a backfill, those reviews will have "d" confidence
  (from date-format timestamps) instead. This is an acceptable tradeoff.
- Eliminating this step removes the migration script, `parse_ts()` format handling,
  `scrape_time` NULL concerns, and the cron-stopping choreography.

## What replaces it

After the new system is deployed and verified (steps 07-09, 11):
1. Run a backfill against RT to populate Neon with all historical reviews.
2. The backfill scrapes everything (all pages, no stop condition), inserts all reviews,
   and skips duplicates via `ON CONFLICT DO NOTHING`.
3. The 50-minute Cloud Scheduler then handles ongoing collection.

The backfill script (`scripts/backfill.py`) will be written as part of the scraper
rewrite (step 04) — it reuses the same `insert_review()` and `get_db_connection()`.
