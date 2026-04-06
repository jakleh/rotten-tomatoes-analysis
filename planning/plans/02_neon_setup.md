# Neon Setup

Neon is serverless Postgres. Free tier: 500MB storage, auto-suspends when idle (first
connection after idle takes ~1-3 seconds — acceptable for our use case).

---

## 1. Create Neon Account and Project

1. Go to https://neon.tech and sign up (GitHub login works)
2. Create a new project: name it `rotten-tomatoes`
3. Select region: `US East (AWS us-east-1)` — Virginia, geographically closest to GCP `us-east1` (South Carolina). Avoid `us-east-2` (Ohio) which adds ~10ms latency per query.
4. Neon creates a default database named `neondb`

After creation, navigate to **Connection Details** in the Neon dashboard.
Copy the connection string. It will look like:

```
postgresql://user:password@ep-xxx-yyy.us-east-2.aws.neon.tech/neondb?sslmode=require
```

Save this — you'll store it as a secret in the next step (03_secret_manager).

NOTE: Verify the connection string starts with `postgresql://` (not `postgres://`).
psycopg2-binary accepts both, but `postgresql://` is canonical. If Neon gives you
`postgres://`, either prefix works — just be consistent.

---

## 2. Create the Schema

Connect to Neon using their SQL Editor in the dashboard, or use psql:

```bash
psql "postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require"
```

Run the following SQL:

```sql
CREATE TABLE reviews (
    id                    SERIAL PRIMARY KEY,
    unique_review_id      TEXT UNIQUE NOT NULL,
    movie_slug            TEXT NOT NULL,
    reviewer_name         TEXT,
    publication_name      TEXT,
    top_critic            BOOLEAN,
    tomatometer_sentiment TEXT,
    subjective_score      TEXT,
    written_review        TEXT,
    site_timestamp_text   TEXT,
    scrape_time           TIMESTAMPTZ NOT NULL,
    estimated_timestamp   TIMESTAMPTZ,
    timestamp_confidence  TEXT NOT NULL,
    page_position         INTEGER
);

CREATE INDEX idx_reviews_movie_slug ON reviews (movie_slug);
CREATE INDEX idx_reviews_movie_timestamp ON reviews (movie_slug, estimated_timestamp);
```

Verify:
```sql
\d reviews
```

Expected: table with 14 columns as defined above.

---

## 3. Column Reference

| Column | Type | Description |
|---|---|---|
| id | SERIAL | Auto-increment PK |
| unique_review_id | TEXT UNIQUE | MD5(movie_slug + reviewer_name + publication + subjective_score) |
| movie_slug | TEXT | e.g. "project_hail_mary" |
| reviewer_name | TEXT | Critic's name |
| publication_name | TEXT | Publication (e.g. "The Guardian") |
| top_critic | BOOLEAN | True if scraped from top-critics filter |
| tomatometer_sentiment | TEXT | "positive" or "negative" |
| subjective_score | TEXT | e.g. "3/5", "A-", "8/10" |
| written_review | TEXT | Review snippet text (if RT exposes in card HTML) |
| site_timestamp_text | TEXT | Raw RT timestamp: "5m", "2h", "3d" |
| scrape_time | TIMESTAMPTZ | datetime.now(utc) at time of scrape |
| estimated_timestamp | TIMESTAMPTZ | scrape_time minus the offset in site_timestamp_text |
| timestamp_confidence | TEXT | "m" (minute), "h" (hour), "d" (day) |
| page_position | INTEGER | 0-indexed position in scrape result (0 = newest) |

---

## 4. Why These Columns

- **site_timestamp_text + scrape_time + estimated_timestamp**: three-way provenance.
  If we ever need to re-derive the estimated time (e.g., bug in parsing logic), we have
  both the raw RT text and the exact scrape moment to recalculate from.

- **page_position**: enables order-based interpolation in the backfill script. If review C
  appears at position 5 between reviews A (position 4, timestamp 12:34) and B (position 6,
  timestamp 12:54), the backfill can assign C an estimated_timestamp of ~12:44 with
  confidence "d".

- **written_review**: scraped if available in the review card HTML. NULL otherwise.
  See questions.md — the exact HTML slot name needs to be verified against live RT page.

---

## 5. Neon Free Tier Limits

- 500MB storage — sufficient for this project (reviews are text, tiny)
- Auto-suspend after 5 minutes of inactivity
- 1 project on free tier
- No branching on free tier (branching is a paid feature)

The auto-suspend means the first connection in each Cloud Run Job invocation will take
1-3 seconds to resume the compute. This is built into the 10-minute job timeout — no action needed.
