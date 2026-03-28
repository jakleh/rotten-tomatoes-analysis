# Design Decisions — Rationale

Why each major decision was made. Use this to evaluate whether a decision should be
revisited, and to avoid re-litigating settled questions.

---

## Scraping interval: 50 minutes (not 5)

The original 5-minute interval was designed to catch reviews within their "minute-level"
timestamp window on RT (reviews show "5m", "10m", etc. for the first ~59 minutes).
The goal was high timestamp confidence.

**Why it was changed:**
- The 5-minute scraper was missing reviews anyway — the interval didn't guarantee capture
- Cloud Run Jobs at 5-minute intervals cost ~$3/month; at 50 minutes, ~$0.70/month
- GitHub Actions at 5-minute intervals would cost ~$99/month (billing per minute)
- The pre-check system (designed to avoid Selenium on every 5-min run) added significant
  code complexity for limited benefit
- Reviews rarely appear in bursts that require sub-minute precision
- 50 minutes is a practical threshold: a review published 45 minutes ago will still show
  "45m" on the next run, preserving minute-level confidence

**If you want to revisit:** The interval is set in Cloud Scheduler (step 08). Changing it
requires no code changes — just update the schedule. The scraper is interval-agnostic.

---

## Single scrape window (not hour + day)

The original architecture had two windows:
- Hour window (every 5 min): only scrape "m"-timestamped reviews
- Day window (every 6 hours): scrape everything, reconcile missed reviews, export CSVs

**Why it was collapsed into one:**
- The dual-window design existed solely because the hour window was too frequent to do
  a full scrape every time (Selenium is slow)
- At 50-minute intervals, doing a full scrape every time is fine (and cheap enough)
- The "day window" reconciliation logic was a workaround for reviews missed by the
  hour window — if the hour window never misses reviews, reconciliation is unnecessary
- One code path is dramatically simpler to reason about and test

---

## No pre-check system

The pre-check did a lightweight HTTP GET to check if the review count changed before
launching Selenium. It existed to avoid 80% of Selenium runs at 5-minute intervals.

**Why it was removed:**
- At 50-minute intervals, we want to run Selenium every time anyway
- The pre-check added ~200 lines of code (fetch_review_count, has_new_reviews,
  precheck_state table, failure tracking, deferred count updates)
- It had its own failure modes (count drift, consecutive failures, deferred updates)
- It was the source of the "missing reviews" bug: the pre-check sometimes incorrectly
  determined no new reviews existed, and the deferred count update logic was fragile

---

## No inline reconciliation

Reconciliation was the process of detecting reviews that appeared in the day-window
scrape but were absent from the DB (RT-delayed reviews), then interpolating their
timestamps from neighboring anchors.

**Why it was removed from the main scraper:**
- It was a response to the dual-window design's failure mode (hour window misses → day
  window catches → reconcile)
- With a single 50-minute window that scrapes everything, there's nothing to reconcile
  inline — either a review is on the page and gets inserted, or it isn't
- Reviews that genuinely appear late (RT delays) will be caught on the next run with
  whatever timestamp RT shows at that point — no interpolation needed
- Reconciliation/interpolation logic belongs in the backfill script, where it's a
  deliberate, manual operation with clear intent

---

## Stop at date-format timestamps (not stop_at_unit='d')

The original stop_at_unit system stopped loading pages at a given timestamp granularity.
`stop_at_unit='d'` stopped BEFORE processing "d" (day-old) reviews.

**Why it changed:**
- At 50-minute intervals, reviews from earlier today (a few hours old, "3h") or yesterday
  ("1d") could fall between two runs — we want to capture those
- Stopping at 'd' would mean we miss a review published 2 hours before our scrape
- The natural cutoff is absolute date-format reviews ("Mar 20") — these are old enough
  that the backfill script is the right tool for them
- "m", "h", and "d" relative timestamps represent roughly the last 7 days — a sensible
  live scraping window

---

## page_position stored in the DB

Each review records its 0-indexed position in the scrape result list (0 = newest).

**Why:**
- RT shows reviews newest-first. If review C appears between A (12:34pm) and B (12:54pm)
  in page order, we know C was published between those times even if C only has a
  date-level timestamp
- The backfill script uses this ordering to interpolate timestamps for historical reviews
- Without page_position, we lose the ordering information permanently once the review
  is inserted — we'd only have the timestamp (which may be low-confidence "d")
- Cost: one INTEGER column per row. Negligible storage impact.

---

## Postgres (Neon) over SQLite

SQLite worked fine on a VM because the file lived on the same machine as the scraper.
Cloud Run Jobs are ephemeral — each execution starts fresh with no persistent disk.

**Why Neon specifically:**
- Free tier (500MB) covers the expected data volume (movie reviews are small text data)
- Serverless (auto-suspends) means no idle compute cost
- Standard PostgreSQL — universally compatible with analytics tools (Streamlit, Grafana,
  Metabase, etc.)
- Same region as GCP (us-east1 area) means low latency from Cloud Run
- The existing SQLite schema migrates cleanly with minor syntax changes

**Alternatives considered:**
- Cloud SQL (PostgreSQL): ~$7/month minimum, more than needed
- PlanetScale (MySQL): branching/schema model is complex for this use case
- Turso (libsql): SQLite-compatible but requires libsql client instead of sqlite3/psycopg2;
  Datasette-turso plugin is less mature
- Supabase: free tier is 500MB, similar to Neon; Neon's serverless auto-suspend is
  better for a scraper with periodic access patterns

---

## Cloud Run Jobs + Cloud Scheduler over GitHub Actions

GitHub Actions was considered as the scheduler.

**Why Cloud Run Jobs + Cloud Scheduler instead:**
- GitHub Actions scheduled workflows are explicitly documented as unreliable during
  high load (can be delayed or skipped)
- GitHub Actions billing at 5-15 minute intervals is expensive ($26-99/month)
- Cloud Scheduler is a dedicated scheduling service with 99.9% SLA — never goes down
- Cloud Run Jobs are independent of GitHub — a CI/CD failure doesn't affect scraping
- Cloud Scheduler + Cloud Run is the standard GCP pattern for scheduled batch jobs

**GitHub Actions IS used for CI/CD** (building/pushing the image) — just not for scheduling.

---

## psycopg2-binary over psycopg (v3) or asyncpg

**Why psycopg2-binary:**
- The scraper is synchronous — no benefit to asyncpg
- psycopg2-binary includes pre-compiled libpq bindings, no apt dependencies needed in Docker
- psycopg (v3) is the future but psycopg2 is more battle-tested with Neon specifically
- Minimal code risk: placeholder syntax (`%s`) and the connect API are identical to
  what most Postgres Python tutorials use

---

## scrape_time captured once per scrape (not per card)

`convert_rel_timestamp_to_abs()` was changed to accept `scrape_time` as a parameter
rather than calling `datetime.now()` internally.

**Why:**
- A scrape of 50 review cards takes ~2-3 seconds to parse. If each card calls datetime.now()
  independently, review timestamps drift slightly across the parsing loop
- Using one consistent `scrape_time` for all cards in a single scrape is more accurate:
  "these 50 reviews were all scraped at this moment, and their estimated_timestamps are
  all relative to that moment"
- It also makes the function testable (pass a fixed scrape_time, get deterministic output)

---

## Analytics: Streamlit (deferred, not in this plan)

Grafana was initially considered.

**Why Streamlit instead:**
- The user wants to run custom Python math (Poisson probabilities, autoregressions)
- Grafana cannot execute Python — it only visualizes data from a connected source
- Streamlit is pure Python: scipy, statsmodels, pandas, numpy all work natively
- Streamlit Community Cloud deploys free from a GitHub repo
- Streamlit can also do visualizations (st.line_chart, Plotly, Altair), so it replaces
  both Datasette AND Grafana in one tool

This is deferred (not in the current plan) because it's independent of the infrastructure
migration. It can be added at any time after the DB is on Neon.

---

## No SQLite data migration (fresh backfill instead)

The original plan included a migration script to copy all existing `reviews.db` rows
into Neon. This was eliminated.

**Why:**
- The existing SQLite dataset is small — not worth the complexity of a migration script
  (format conversion, NULL handling, overlap choreography, cron-stopping timing)
- All the same reviews can be re-scraped from RT via a backfill
- The only data lost is "m"/"h" timestamp confidence on a handful of reviews caught
  during live scraping. After backfill, those reviews get "d" confidence instead.
  This is an acceptable tradeoff.

**What replaces it:** After the new system is deployed, run a backfill script that
scrapes all historical reviews from RT and inserts them into Neon. The backfill scrapes
everything (all pages, no stop condition), inserts all reviews, and skips duplicates
via `ON CONFLICT DO NOTHING`. The 50-minute scheduler then handles ongoing collection.
