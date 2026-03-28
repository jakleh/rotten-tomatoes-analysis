# Scraper Rewrite

This is the largest code change. The scraper logic (Selenium, BeautifulSoup parsing,
timestamp utilities, dedup hash) is preserved. Everything else changes.

---

## What Gets Deleted

From `rotten_tomatoes.py`:
- `DB_PATH` constant
- `PRECHECK_TIMEOUT`, `PRECHECK_HEADERS`, `PRECHECK_FAILURE_ERROR_THRESHOLD` constants
- `get_db_connection()` (sqlite3 version)
- `init_reviews_table()` and all `_migrate_v*` functions
- `init_precheck_table()`
- `get_last_review_count()`
- `update_last_review_count()`
- `record_precheck_failure()`
- `has_new_reviews()`
- `fetch_review_count()`
- `get_db_review_ids()`
- `get_db_reviews_sorted()`
- `export_reference_csv()`
- `update_sentiment()`
- `reconcile_missing_reviews()`
- `interpolate_timestamps()`
- `scrape_hour_sliding_window()`
- `scrape_day_sliding_window()`
- The `stop_at_unit` parameter from `get_reviews()`
- `--window` CLI argument

From `pyproject.toml` dependencies:
- `requests`
- `datasette`
- `datasette-vega`

Add to `pyproject.toml` dependencies:
- `psycopg2-binary`

From imports:
- `csv`
- `sqlite3`
- `requests`

Add to imports:
- `psycopg2`
- `psycopg2.extras`

---

## What Gets Modified

### `get_reviews()` — three changes

**Change 1: Stop condition.**
Remove `stop_at_unit` parameter entirely. Instead, stop loading pages when the last
visible review card has an absolute date timestamp ("Mar 20" format). When parsing,
stop (break) at the first date-format card — since RT shows newest-first, all
subsequent cards will also be date-format or older.

**Change 2: Add `page_position`.**
Add `"page_position": i` to each review dict, where `i` is the 0-indexed loop counter
over the parsed cards (before filtering). This captures the review's position in the
RT page as of this scrape.

**Change 3: Add `written_review`.**
Attempt to extract the review text from the card HTML. The slot name is TBD — see
questions.md. Add a `written_review` key to the review dict. If the slot doesn't exist,
set it to `None`.

**Change 4: `scrape_time` as a single value.**
Capture `scrape_time = datetime.now(timezone.utc)` once before the parsing loop (not
per-card). All cards from a single scrape get the same `scrape_time`.

### `insert_review()` — full rewrite for Postgres

Replace sqlite3 with psycopg2. Key syntax differences:
- `?` placeholders → `%s`
- `INSERT OR IGNORE` → `INSERT ... ON CONFLICT (unique_review_id) DO NOTHING`
- `conn.commit()` is the same
- Use `psycopg2.extras.RealDictCursor` for dict-like row access (if needed elsewhere)

---

## Target `rotten_tomatoes.py`

```python
"""
rotten_tomatoes.py — Rotten Tomatoes review scraper.

Runs every 50 minutes via Cloud Run Jobs + Cloud Scheduler.
Scrapes reviews with relative timestamps (m/h/d), stops at absolute date format.
Writes to Neon (PostgreSQL) via DATABASE_URL environment variable.
"""

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import psycopg2
import psycopg2.extras
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException


# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

UNIT_ORDER = {"m": 0, "h": 1, "d": 2, "date": 3}
CRITIC_FILTERS = ["top-critics", "all-critics"]
MOVIES_CONFIG_PATH = "movies.json"


# ── Config ────────────────────────────────────────────────────────────────────

def load_movie_config() -> list[str]:
    config_path = Path(MOVIES_CONFIG_PATH)
    if not config_path.exists():
        log.warning("Config file not found: %s", MOVIES_CONFIG_PATH)
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to read %s: %s", MOVIES_CONFIG_PATH, e)
        return []
    if not isinstance(data, list):
        log.error("Expected a JSON array in %s", MOVIES_CONFIG_PATH)
        return []
    return [e["slug"] for e in data if isinstance(e, dict) and e.get("enabled", True)]


# ── Timestamp utilities ───────────────────────────────────────────────────────

def get_timestamp_unit(rel_timestamp: str) -> str:
    """Return 'm', 'h', 'd', or 'date' for a relative timestamp string."""
    rel_timestamp = rel_timestamp.strip()
    if not rel_timestamp:
        return "date"
    last = rel_timestamp[-1]
    return last if last in ("m", "h", "d") else "date"


def convert_rel_timestamp_to_abs(rel_timestamp: str, scrape_time: datetime) -> datetime | None:
    """
    Convert RT's relative timestamp to an absolute UTC datetime.

    Uses scrape_time as the reference point (not datetime.now() at call time),
    so all reviews in a single scrape have a consistent reference.

    Formats:
      "5m"    → 5 minutes before scrape_time
      "2h"    → 2 hours before scrape_time
      "3d"    → 3 days before scrape_time
      "Mar 20"→ March 20, inferred year
    """
    rel_timestamp = rel_timestamp.strip()
    if not rel_timestamp:
        return None

    unit = get_timestamp_unit(rel_timestamp)

    if unit == "date":
        try:
            current_year = scrape_time.year
            parsed = datetime.strptime(
                f"{rel_timestamp} {current_year}", "%b %d %Y"
            ).replace(tzinfo=timezone.utc)
            if parsed > scrape_time:
                parsed = parsed.replace(year=current_year - 1)
            return parsed
        except ValueError:
            log.warning("Could not parse date timestamp: %r", rel_timestamp)
            return None

    try:
        value = int(rel_timestamp[:-1])
    except ValueError:
        log.warning("Could not parse relative timestamp: %r", rel_timestamp)
        return None

    delta_map = {
        "m": timedelta(minutes=value),
        "h": timedelta(hours=value),
        "d": timedelta(days=value),
    }
    return scrape_time - delta_map[unit]


# ── Database ──────────────────────────────────────────────────────────────────

def get_db_connection():
    """Open a Postgres connection using DATABASE_URL from environment."""
    return psycopg2.connect(os.environ["DATABASE_URL"])


def compute_review_id(
    movie_slug: str, name: str | None, publication: str | None, rating: str | None
) -> str:
    """MD5 hash of (movie_slug + reviewer_name + publication + subjective_score)."""
    key = f"{movie_slug}{name or ''}{publication or ''}{rating or ''}"
    return hashlib.md5(key.encode()).hexdigest()


def insert_review(conn, movie_slug: str, review: dict) -> bool:
    """
    Insert a review. Returns True if inserted, False if already exists.
    ON CONFLICT DO NOTHING — idempotent, safe to call multiple times.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO reviews (
                unique_review_id, movie_slug, reviewer_name, publication_name,
                top_critic, tomatometer_sentiment, subjective_score, written_review,
                site_timestamp_text, scrape_time, estimated_timestamp,
                timestamp_confidence, page_position
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (unique_review_id) DO NOTHING
            """,
            (
                review["unique_review_id"],
                movie_slug,
                review.get("reviewer_name"),
                review.get("publication_name"),
                review.get("top_critic", False),
                review.get("tomatometer_sentiment"),
                review.get("subjective_score"),
                review.get("written_review"),
                review.get("site_timestamp_text"),
                review["scrape_time"],
                review.get("estimated_timestamp"),
                review["timestamp_confidence"],
                review.get("page_position"),
            ),
        )
        return cur.rowcount > 0


# ── Scraping ──────────────────────────────────────────────────────────────────

def _build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--js-flags=--max-old-space-size=256")
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin
    return webdriver.Chrome(options=options)


def get_reviews(
    movie_slug: str,
    critic_filter: str = "all-critics",
) -> list[dict]:
    """
    Scrape reviews for a movie. Loads pages until the oldest visible review has
    an absolute date timestamp ("Mar 20" format), then parses all relative-timestamp
    reviews (m/h/d). Stops parsing at the first date-format card (RT is newest-first).

    Returns list of review dicts including page_position (0 = newest).
    """
    url = f"https://www.rottentomatoes.com/m/{movie_slug}/reviews/{critic_filter}"
    top_critic = (critic_filter == "top-critics")
    scrape_time = datetime.now(timezone.utc)

    log.info("Scraping %s (%s)", movie_slug, critic_filter)

    driver = _build_driver()
    try:
        driver.get(url)
        time.sleep(5)

        wait = WebDriverWait(driver, 15)
        while True:
            try:
                btn = wait.until(EC.element_to_be_clickable((
                    By.XPATH,
                    '//*[@id="main-page-content"]/div/section/div/div[2]/div[2]/rt-button',
                )))
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", btn)
                time.sleep(1)
                driver.execute_script("arguments[0].click();", btn)
                log.debug("Clicked 'Load More'")
                time.sleep(3)

                # Stop loading more pages once the oldest visible review is date-format.
                soup = BeautifulSoup(driver.page_source, "html.parser")
                cards = soup.find_all("review-card")
                if cards:
                    last_ts_tag = cards[-1].find("span", attrs={"slot": "timestamp"})
                    if last_ts_tag:
                        last_ts = last_ts_tag.get_text().strip()
                        if get_timestamp_unit(last_ts) == "date":
                            log.debug("Reached date-format timestamp '%s' — stopping load.", last_ts)
                            break

            except (TimeoutException, ElementClickInterceptedException):
                log.debug("No more 'Load More' button — all reviews loaded.")
                break

        soup = BeautifulSoup(driver.page_source, "html.parser")
        cards = soup.find_all("review-card")
        log.info("Found %d review cards for %s (%s)", len(cards), movie_slug, critic_filter)

    except Exception as e:
        log.error("Selenium error for %s (%s): %s", movie_slug, critic_filter, e)
        return []
    finally:
        driver.quit()

    reviews = []
    for i, card in enumerate(cards):
        ts_tag = card.find("span", attrs={"slot": "timestamp"})
        rel_ts = ts_tag.get_text().strip() if ts_tag else ""

        # Stop at date-format reviews — RT is newest-first, so all remaining are older.
        if get_timestamp_unit(rel_ts) == "date":
            log.debug("Stopping parse at date-format timestamp '%s' (position %d).", rel_ts, i)
            break

        abs_ts = convert_rel_timestamp_to_abs(rel_ts, scrape_time) if rel_ts else None

        unit = get_timestamp_unit(rel_ts)
        confidence = unit if unit in ("m", "h", "d") else "d"

        sentiment_tag = card.find("score-icon-critics")
        tomatometer = sentiment_tag.get("sentiment") if sentiment_tag else None

        rating_container = card.find("span", attrs={"slot": "rating"})
        subjective_score = None
        if rating_container:
            inner = rating_container.find("span", style=True)
            if inner:
                subjective_score = inner.get_text().strip()

        name_tag = card.find("rt-link", attrs={"slot": "name"})
        reviewer_name = name_tag.get_text().strip() if name_tag else None

        pub_tag = card.find("rt-link", attrs={"slot": "publication"})
        publication = pub_tag.get_text().strip() if pub_tag else None

        # Written review text — slot name TBD, see questions.md.
        # Attempt extraction; None if slot not found.
        review_tag = card.find("p", attrs={"slot": "content"})  # VERIFY SLOT NAME
        written_review = review_tag.get_text().strip() if review_tag else None

        reviews.append({
            "unique_review_id": compute_review_id(movie_slug, reviewer_name, publication, subjective_score),
            "scrape_time": scrape_time,
            "estimated_timestamp": abs_ts,
            "site_timestamp_text": rel_ts,
            "timestamp_confidence": confidence,
            "tomatometer_sentiment": tomatometer,
            "subjective_score": subjective_score,
            "reviewer_name": reviewer_name,
            "publication_name": publication,
            "top_critic": top_critic,
            "written_review": written_review,
            "page_position": i,
        })

    log.info("Parsed %d reviews for %s (%s)", len(reviews), movie_slug, critic_filter)
    return reviews


# ── Main scrape ───────────────────────────────────────────────────────────────

def scrape(movie_slug: str) -> None:
    """Scrape all recent reviews for a movie and insert new ones into Postgres."""
    log.info("=== Scraping: %s ===", movie_slug)
    conn = get_db_connection()
    try:
        inserted_total = 0
        for critic_filter in CRITIC_FILTERS:
            reviews = get_reviews(movie_slug, critic_filter)
            for review in reviews:
                if insert_review(conn, movie_slug, review):
                    inserted_total += 1
                    log.info(
                        "Inserted: %s (%s)",
                        review.get("reviewer_name"),
                        review.get("publication_name"),
                    )
            conn.commit()
        if inserted_total == 0:
            log.info("No new reviews for %s.", movie_slug)
        else:
            log.info("Inserted %d new reviews for %s.", inserted_total, movie_slug)
    except Exception as e:
        log.error("Scrape error for %s: %s", movie_slug, e)
        raise
    finally:
        conn.close()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rotten Tomatoes review scraper")
    parser.add_argument(
        "--movie",
        default=None,
        help="Override: scrape only this movie slug (ignores movies.json)",
    )
    args = parser.parse_args()

    if args.movie:
        movie_slugs = [args.movie]
    else:
        movie_slugs = load_movie_config()
        if not movie_slugs:
            log.error("No movies to scrape. Check %s or use --movie.", MOVIES_CONFIG_PATH)
            raise SystemExit(1)

    for slug in movie_slugs:
        scrape(slug)
```

---

## `pyproject.toml` Changes

Remove from `[project] dependencies`:
```
requests
datasette
datasette-vega
```

Add to `[project] dependencies`:
```
psycopg2-binary
```

Final dependencies section should look like:
```toml
[project]
dependencies = [
    "beautifulsoup4",
    "psycopg2-binary",
    "selenium",
]

[dependency-groups]
dev = [
    "pytest",
]
```

After editing, run:
```bash
uv lock
```

This regenerates `uv.lock`. Commit both `pyproject.toml` and `uv.lock`.

---

## Test Changes Locally

Set DATABASE_URL temporarily for local testing:
```bash
export DATABASE_URL="postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require"
```

Run against a single movie:
```bash
uv run python rotten_tomatoes.py --movie project_hail_mary
```

Expected: connects to Neon, scrapes reviews, inserts new ones, logs inserted count.

Verify in Neon SQL Editor:
```sql
SELECT COUNT(*) FROM reviews WHERE movie_slug = 'project_hail_mary';
```

---

## Existing Tests

The existing 92 tests use in-memory SQLite. After this rewrite:
- Tests that test pure logic (timestamp utils, compute_review_id, interpolation) still pass unchanged.
- Tests that test DB operations (insert_review, get_db_review_ids, etc.) will break because they use SQLite.
- DB tests need to be rewritten to use either:
  a) A real Neon test database (set TEST_DATABASE_URL env var)
  b) A local Postgres instance
  c) Mock the psycopg2 connection

Recommendation: defer test rewrite to after the migration is working. The scraper logic
is the critical part; DB tests can be updated separately. Note the test count in CLAUDE.md
will need updating after the rewrite.

---

## Notes on `convert_rel_timestamp_to_abs` Change

The function signature changed: it now takes `scrape_time` as a parameter instead of
calling `datetime.now()` internally. This ensures all cards from a single scrape use
the same reference time, which is more accurate and testable.

Update any tests that call this function to pass a fixed `scrape_time`.
