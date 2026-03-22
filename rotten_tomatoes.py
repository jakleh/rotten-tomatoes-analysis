"""
rotten_tomatoes.py — Rotten Tomatoes review scraper with time-series database.

Architecture:
  - scrape_hour_sliding_window(): runs every N minutes, captures reviews from the past hour
  - scrape_day_sliding_window():  runs every N hours, reconciles lagging reviews
  - SQLite database: single `reviews` table with movie_slug column, deduplication via MD5 hash
"""

import csv
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

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
    handlers=[
        logging.FileHandler("scraper.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── Constants ─────────────────────────────────────────────────────────────────

DB_PATH = "reviews.db"

# Timestamp age units ordered youngest → oldest.
UNIT_ORDER = {"m": 0, "h": 1, "d": 2, "date": 3}

# Scrape top-critics before all-critics so the top_critic flag is set correctly
# before the all-critics pass attempts (and skips) the same review IDs.
CRITIC_FILTERS = ["top-critics", "all-critics"]

# Pre-check: lightweight HTTP request to detect new reviews before launching Selenium.
PRECHECK_TIMEOUT = 10  # seconds
PRECHECK_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
}
PRECHECK_FAILURE_ERROR_THRESHOLD = 10  # log ERROR after this many consecutive failures

MOVIES_CONFIG_PATH = "movies.json"


# ── Config ───────────────────────────────────────────────────────────────────

def load_movie_config() -> list[str]:
    """
    Load enabled movie slugs from movies.json.

    Returns a list of slug strings. Logs a warning and returns an empty list
    if the file is missing or malformed.
    """
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

    slugs = []
    for entry in data:
        if not isinstance(entry, dict) or "slug" not in entry:
            log.warning("Skipping invalid entry in %s: %r", MOVIES_CONFIG_PATH, entry)
            continue
        if entry.get("enabled", True):
            slugs.append(entry["slug"])

    return slugs


# ── Timestamp utilities ───────────────────────────────────────────────────────

def get_timestamp_unit(rel_timestamp: str) -> str:
    """Return 'm', 'h', 'd', or 'date' for a relative timestamp string."""
    rel_timestamp = rel_timestamp.strip()
    if not rel_timestamp:
        return "date"
    last = rel_timestamp[-1]
    return last if last in ("m", "h", "d") else "date"


def is_at_or_older_than(rel_timestamp: str, unit: str) -> bool:
    """Return True if the timestamp is as old as or older than the given unit."""
    return UNIT_ORDER.get(get_timestamp_unit(rel_timestamp), 3) >= UNIT_ORDER.get(unit, 0)


def convert_rel_timestamp_to_abs(rel_timestamp: str) -> datetime | None:
    """
    Convert RT's relative timestamps to absolute UTC datetimes.

    Formats handled:
      "5m"    → 5 minutes ago
      "2h"    → 2 hours ago
      "3d"    → 3 days ago
      "Mar 20"→ March 20 of the current year (UTC)
    """
    rel_timestamp = rel_timestamp.strip()
    if not rel_timestamp:
        return None

    unit = get_timestamp_unit(rel_timestamp)

    if unit == "date":
        try:
            current_year = datetime.now(timezone.utc).year
            parsed = datetime.strptime(f"{rel_timestamp} {current_year}", "%b %d %Y").replace(
                tzinfo=timezone.utc
            )
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
    return datetime.now(timezone.utc) - delta_map[unit]


def _ts_to_str(dt: datetime | None) -> str | None:
    """Format a datetime as the DB timestamp string, or None."""
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else None


# ── Database ──────────────────────────────────────────────────────────────────

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_reviews_table(conn: sqlite3.Connection) -> None:
    """Create the unified reviews table if it doesn't already exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reviews (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            movie_slug           TEXT NOT NULL,
            timestamp            TEXT NOT NULL,
            unique_review_id     TEXT UNIQUE NOT NULL,
            subjective_score     TEXT,
            reconciled_timestamp INTEGER NOT NULL DEFAULT 0,
            reviewer_name        TEXT,
            publication_name     TEXT,
            top_critic           INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()
    log.debug("Reviews table ready.")


def init_precheck_table(conn: sqlite3.Connection) -> None:
    """Create the pre-check state table if it doesn't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS precheck_state (
            movie_slug           TEXT PRIMARY KEY,
            last_review_count    INTEGER NOT NULL DEFAULT 0,
            consecutive_failures INTEGER NOT NULL DEFAULT 0,
            last_checked         TEXT
        )
    """)
    conn.commit()


def get_last_review_count(conn: sqlite3.Connection, movie_slug: str) -> int:
    """Return the last known review count for a movie, or 0 if unknown."""
    row = conn.execute(
        "SELECT last_review_count FROM precheck_state WHERE movie_slug = ?",
        (movie_slug,),
    ).fetchone()
    return row["last_review_count"] if row else 0


def update_last_review_count(conn: sqlite3.Connection, movie_slug: str, count: int) -> None:
    """Update the stored review count and reset the failure counter."""
    conn.execute(
        """INSERT INTO precheck_state (movie_slug, last_review_count, consecutive_failures, last_checked)
           VALUES (?, ?, 0, ?)
           ON CONFLICT(movie_slug) DO UPDATE SET
               last_review_count = excluded.last_review_count,
               consecutive_failures = 0,
               last_checked = excluded.last_checked""",
        (movie_slug, count, _ts_to_str(datetime.now(timezone.utc))),
    )
    conn.commit()


def record_precheck_failure(conn: sqlite3.Connection, movie_slug: str) -> int:
    """Increment the consecutive failure counter. Returns the new count."""
    conn.execute(
        """INSERT INTO precheck_state (movie_slug, consecutive_failures, last_checked)
           VALUES (?, 1, ?)
           ON CONFLICT(movie_slug) DO UPDATE SET
               consecutive_failures = precheck_state.consecutive_failures + 1,
               last_checked = excluded.last_checked""",
        (movie_slug, _ts_to_str(datetime.now(timezone.utc))),
    )
    conn.commit()
    row = conn.execute(
        "SELECT consecutive_failures FROM precheck_state WHERE movie_slug = ?",
        (movie_slug,),
    ).fetchone()
    return row["consecutive_failures"]


def compute_review_id(name: str | None, publication: str | None, rating: str | None) -> str:
    """MD5 hash of (reviewer name + publication + rating) used as the unique review ID."""
    key = f"{name or ''}{publication or ''}{rating or ''}"
    return hashlib.md5(key.encode()).hexdigest()


def insert_review(conn: sqlite3.Connection, movie_slug: str, review: dict) -> bool:
    """
    Insert a review into the database.
    Returns True if inserted, False if the review already exists (duplicate ID).
    """
    try:
        conn.execute(
            """
            INSERT INTO reviews
                (movie_slug, timestamp, unique_review_id, subjective_score,
                 reconciled_timestamp, reviewer_name, publication_name, top_critic)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                movie_slug,
                review["timestamp"],
                review["unique_review_id"],
                review.get("subjective_score"),
                int(bool(review.get("reconciled_timestamp", False))),
                review.get("reviewer_name"),
                review.get("publication_name"),
                int(bool(review.get("top_critic", False))),
            ),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False  # Duplicate unique_review_id


def get_db_review_ids(conn: sqlite3.Connection, movie_slug: str) -> set:
    """Return the set of unique_review_ids currently in the database for a movie."""
    rows = conn.execute(
        "SELECT unique_review_id FROM reviews WHERE movie_slug = ?",
        (movie_slug,),
    ).fetchall()
    return {row["unique_review_id"] for row in rows}


def get_db_reviews_sorted(conn: sqlite3.Connection, movie_slug: str) -> list[dict]:
    """Return all DB reviews for a movie sorted by timestamp ascending."""
    rows = conn.execute(
        "SELECT * FROM reviews WHERE movie_slug = ? ORDER BY timestamp ASC",
        (movie_slug,),
    ).fetchall()
    return [dict(row) for row in rows]


def export_reference_csv(conn: sqlite3.Connection, movie_slug: str, critic_filter: str) -> str:
    """Write a reference CSV snapshot of the current DB state. Returns the filename."""
    reviews = get_db_reviews_sorted(conn, movie_slug)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    filename = f"{movie_slug}_{critic_filter}_{ts}_reference.csv"
    fieldnames = [
        "id", "movie_slug", "timestamp", "unique_review_id", "subjective_score",
        "reconciled_timestamp", "reviewer_name", "publication_name", "top_critic",
    ]
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(reviews)
    log.info("Reference CSV written: %s (%d rows)", filename, len(reviews))
    return filename


# ── Pre-check ─────────────────────────────────────────────────────────────────

def fetch_review_count(movie_slug: str) -> int | None:
    """
    Fetch the current total review count from the main movie page using a
    lightweight HTTP request (no Selenium).

    Returns the count as an int, or None if the request fails or the count
    can't be extracted.
    """
    url = f"https://www.rottentomatoes.com/m/{movie_slug}"
    try:
        resp = requests.get(url, headers=PRECHECK_HEADERS, timeout=PRECHECK_TIMEOUT)
        if resp.status_code != 200:
            log.debug("Pre-check: HTTP %d from %s", resp.status_code, url)
            return None

        match = re.search(r"(\d+) Reviews", resp.text)
        if match:
            count = int(match.group(1))
            log.debug("Pre-check: found %d reviews for %s", count, movie_slug)
            return count

        log.debug("Pre-check: no 'N Reviews' pattern found in page for %s", movie_slug)
        return None

    except requests.RequestException as e:
        log.debug("Pre-check: request failed for %s: %s", movie_slug, e)
        return None


def has_new_reviews(conn: sqlite3.Connection, movie_slug: str) -> bool:
    """
    Check if new reviews have been posted since the last scrape.

    Returns True if new reviews exist or if the check is inconclusive.
    Returns False only when confidently determined that no new reviews exist.
    """
    current_count = fetch_review_count(movie_slug)

    if current_count is None:
        failures = record_precheck_failure(conn, movie_slug)
        if failures >= PRECHECK_FAILURE_ERROR_THRESHOLD:
            log.error(
                "Pre-check has failed %d times in a row for %s — "
                "the pre-check may be broken. Falling back to full Selenium scrape.",
                failures, movie_slug,
            )
        else:
            log.warning(
                "Pre-check failed for %s (attempt %d) — falling back to full Selenium scrape.",
                movie_slug, failures,
            )
        return True

    last_count = get_last_review_count(conn, movie_slug)

    if last_count == 0:
        log.info(
            "Pre-check: first run for %s, storing count=%d — will scrape.",
            movie_slug, current_count,
        )
        update_last_review_count(conn, movie_slug, current_count)
        return True

    if current_count > last_count:
        log.info(
            "Pre-check: new reviews for %s: %d → %d (+%d) — will scrape.",
            movie_slug, last_count, current_count, current_count - last_count,
        )
        update_last_review_count(conn, movie_slug, current_count)
        return True

    if current_count < last_count:
        log.warning(
            "Pre-check: count decreased for %s: %d → %d — will scrape to be safe.",
            movie_slug, last_count, current_count,
        )
        update_last_review_count(conn, movie_slug, current_count)
        return True

    # current_count == last_count
    log.info(
        "Pre-check: no new reviews for %s (count=%d). Skipping Selenium.",
        movie_slug, current_count,
    )
    update_last_review_count(conn, movie_slug, current_count)
    return False


# ── Scraping ──────────────────────────────────────────────────────────────────

def _build_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")  # Required on VMs without a GPU
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument("--js-flags=--max-old-space-size=256")

    # Allow the Chrome/Chromium binary path to be overridden via env var.
    # Useful on Linux VMs where Chromium lives at /usr/bin/chromium.
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin

    return webdriver.Chrome(options=options)


def get_reviews(
    movie_slug: str,
    critic_filter: str = "all-critics",
    stop_at_unit: str | None = None,
) -> list[dict]:
    """
    Scrape reviews for a movie from Rotten Tomatoes.

    Args:
        movie_slug:    RT movie slug, e.g. "project_hail_mary"
        critic_filter: "all-critics" or "top-critics"
        stop_at_unit:  Stop loading/parsing when a review with this age unit (or older) is
                       encountered. 'h' → only return minute-old reviews;
                       'd' → return minute- and hour-old reviews; None → return all.

    Returns:
        List of review dicts with keys: unique_review_id, timestamp,
        tomatometer_sentiment, subjective_score, reviewer_name, publication_name,
        top_critic, reconciled_timestamp.
    """
    url = f"https://www.rottentomatoes.com/m/{movie_slug}/reviews/{critic_filter}"
    top_critic = (critic_filter == "top-critics")

    log.info(
        "Scraping %s (%s)%s",
        movie_slug, critic_filter,
        f" [stop at '{stop_at_unit}']" if stop_at_unit else "",
    )

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

                # If a stop unit is set, check the oldest loaded review.
                # If it's already as old as or older than the stop unit, no need to load more.
                if stop_at_unit:
                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    cards = soup.find_all("review-card")
                    if cards:
                        last_ts_tag = cards[-1].find("span", attrs={"slot": "timestamp"})
                        if last_ts_tag:
                            last_ts_text = last_ts_tag.get_text().strip()
                            if is_at_or_older_than(last_ts_text, stop_at_unit):
                                log.debug(
                                    "Reached stop unit '%s' at '%s' — stopping load.",
                                    stop_at_unit, last_ts_text,
                                )
                                break

            except (TimeoutException, ElementClickInterceptedException):
                log.debug("No more 'Load More' button — all reviews loaded.")
                break

        soup = BeautifulSoup(driver.page_source, "html.parser")
        review_items = soup.find_all("review-card")
        log.info(
            "Found %d review cards for %s (%s)", len(review_items), movie_slug, critic_filter
        )

    except Exception as e:
        log.error("Selenium error for %s (%s): %s", movie_slug, critic_filter, e)
        return []
    finally:
        driver.quit()

    reviews = []
    for item in review_items:
        # Timestamp
        ts_tag = item.find("span", attrs={"slot": "timestamp"})
        rel_ts = ts_tag.get_text().strip() if ts_tag else ""

        # Skip reviews at or older than the stop unit
        if stop_at_unit and rel_ts and is_at_or_older_than(rel_ts, stop_at_unit):
            continue

        abs_ts = convert_rel_timestamp_to_abs(rel_ts) if rel_ts else None
        ts_str = _ts_to_str(abs_ts)

        # Tomatometer sentiment
        sentiment_tag = item.find("score-icon-critics")
        tomatometer = sentiment_tag.get("sentiment") if sentiment_tag else None

        # Subjective (numerical) score
        rating_container = item.find("span", attrs={"slot": "rating"})
        subjective_score = None
        if rating_container:
            inner = rating_container.find("span", style=True)
            if inner:
                subjective_score = inner.get_text().strip()

        # Reviewer name
        name_tag = item.find("rt-link", attrs={"slot": "name"})
        reviewer_name = name_tag.get_text().strip() if name_tag else None

        # Publication
        pub_tag = item.find("rt-link", attrs={"slot": "publication"})
        publication = pub_tag.get_text().strip() if pub_tag else None

        # top_critic: simple filter-based approach — if scraping from top-critics page,
        # all reviews are top critics. Easy to replace with HTML-based detection later.
        reviews.append({
            "unique_review_id": compute_review_id(reviewer_name, publication, subjective_score),
            "timestamp": ts_str,
            "tomatometer_sentiment": tomatometer,
            "subjective_score": subjective_score,
            "reviewer_name": reviewer_name,
            "publication_name": publication,
            "top_critic": top_critic,
            "reconciled_timestamp": False,
        })

    log.info(
        "Parsed %d reviews (after filtering) for %s (%s)",
        len(reviews), movie_slug, critic_filter,
    )
    return reviews


# ── Reconciliation ────────────────────────────────────────────────────────────

def interpolate_timestamps(
    before_ts: str | None,
    after_ts: str | None,
    count: int,
) -> list[str | None]:
    """
    Generate `count` evenly-spaced timestamp strings between before_ts and after_ts.

    - If both boundaries are known: divide the interval evenly.
    - If only one boundary is known: all timestamps get that boundary's value.
    - If neither is known: return a list of None (caller should fall back to the
      review's own scraped timestamp).
    """
    fmt = "%Y-%m-%d %H:%M:%S"

    if before_ts and after_ts:
        t1 = datetime.strptime(before_ts, fmt)
        t2 = datetime.strptime(after_ts, fmt)
        delta = (t2 - t1) / (count + 1)
        return [(t1 + delta * i).strftime(fmt) for i in range(1, count + 1)]

    fallback = before_ts or after_ts
    return [fallback] * count


def reconcile_missing_reviews(
    conn: sqlite3.Connection,
    movie_slug: str,
    scraped_reviews: list[dict],
) -> int:
    """
    Find reviews present in `scraped_reviews` but absent from the database.
    Interpolate their timestamps from neighboring known reviews and insert them
    with reconciled_timestamp=True.

    Returns the number of reviews reconciled and inserted.
    """
    db_ids = get_db_review_ids(conn, movie_slug)
    db_reviews = get_db_reviews_sorted(conn, movie_slug)
    db_ts_by_id = {r["unique_review_id"]: r["timestamp"] for r in db_reviews}

    missing_ids = {r["unique_review_id"] for r in scraped_reviews} - db_ids
    if not missing_ids:
        log.info("No missing reviews to reconcile for %s.", movie_slug)
        return 0

    log.info("Found %d missing reviews to reconcile for %s.", len(missing_ids), movie_slug)

    # Work oldest-first through the scraped list, grouping consecutive missing reviews
    # so we can interpolate between their known neighbors.
    ordered = list(reversed(scraped_reviews))

    i = 0
    reconciled_count = 0
    while i < len(ordered):
        if ordered[i]["unique_review_id"] in db_ids:
            i += 1
            continue

        # Start of a consecutive run of missing reviews
        run_start = i
        while i < len(ordered) and ordered[i]["unique_review_id"] not in db_ids:
            i += 1
        run_end = i  # exclusive

        run = ordered[run_start:run_end]

        # Anchor timestamps from the DB (not the scraped approximations)
        before_id = ordered[run_start - 1]["unique_review_id"] if run_start > 0 else None
        after_id = ordered[run_end]["unique_review_id"] if run_end < len(ordered) else None
        before_ts = db_ts_by_id.get(before_id) if before_id else None
        after_ts = db_ts_by_id.get(after_id) if after_id else None

        # If neither anchor exists, we have no DB context for this time period —
        # the hour window wasn't running yet, so these reviews aren't "lagging",
        # they're just unseen. Skip them; the hour window will catch new ones.
        if before_ts is None and after_ts is None:
            log.debug(
                "Skipping %d review(s) with no DB context (no surrounding hour-window data).",
                len(run),
            )
            continue

        timestamps = interpolate_timestamps(before_ts, after_ts, len(run))

        for review, ts in zip(run, timestamps):
            reconciled = dict(review)
            reconciled["timestamp"] = ts
            reconciled["reconciled_timestamp"] = True

            if insert_review(conn, movie_slug, reconciled):
                reconciled_count += 1
                log.info(
                    "Reconciled '%s' (%s) → interpolated timestamp %s",
                    review.get("reviewer_name"),
                    review.get("publication_name"),
                    reconciled["timestamp"],
                )

    return reconciled_count


# ── Sliding windows ───────────────────────────────────────────────────────────

def scrape_hour_sliding_window(movie_slug: str, minute_increment: int = 5) -> None:
    """
    Scrape reviews posted in the past hour and insert new ones into the database.
    Intended to be called every `minute_increment` minutes via cron.
    """
    log.info("=== Hour sliding window: %s ===", movie_slug)
    conn = get_db_connection()
    try:
        init_reviews_table(conn)
        init_precheck_table(conn)

        if not has_new_reviews(conn, movie_slug):
            log.info("Hour window complete. Skipped Selenium (no new reviews).")
            return

        inserted_total = 0
        for critic_filter in CRITIC_FILTERS:
            reviews = get_reviews(movie_slug, critic_filter, stop_at_unit="h")
            for review in reviews:
                if insert_review(conn, movie_slug, review):
                    inserted_total += 1
                    log.info(
                        "Inserted: %s (%s)",
                        review.get("reviewer_name"),
                        review.get("publication_name"),
                    )
        log.info("Hour window complete. Inserted %d new reviews.", inserted_total)
    except Exception as e:
        log.error("Hour window error for %s: %s", movie_slug, e)
    finally:
        conn.close()


def scrape_day_sliding_window(movie_slug: str, hour_increment: int = 6) -> None:
    """
    Scrape all reviews from the past day, reconcile any missed by the hour window,
    and write a reference CSV snapshot.
    Intended to be called every `hour_increment` hours via cron.
    """
    log.info("=== Day sliding window: %s ===", movie_slug)
    conn = get_db_connection()
    try:
        init_reviews_table(conn)
        init_precheck_table(conn)
        for critic_filter in CRITIC_FILTERS:
            reviews = get_reviews(movie_slug, critic_filter, stop_at_unit="d")

            db_ids = get_db_review_ids(conn, movie_slug)
            scraped_ids = {r["unique_review_id"] for r in reviews}
            missing_count = len(scraped_ids - db_ids)

            log.info(
                "Day window (%s): %d scraped, %d in DB, %d missing",
                critic_filter, len(reviews), len(db_ids), missing_count,
            )

            if missing_count > 0:
                reconciled = reconcile_missing_reviews(conn, movie_slug, reviews)
                log.info(
                    "Reconciled %d reviews for %s (%s).",
                    reconciled, movie_slug, critic_filter,
                )

            export_reference_csv(conn, movie_slug, critic_filter)

            # Calibrate the pre-check state with the authoritative count from
            # this full scrape, correcting any drift from stale HTTP responses.
            update_last_review_count(conn, movie_slug, len(reviews))

    except Exception as e:
        log.error("Day window error for %s: %s", movie_slug, e)
    finally:
        conn.close()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Rotten Tomatoes review scraper")
    parser.add_argument(
        "--window",
        choices=["hour", "day", "both"],
        default="both",
        help="Which sliding window to run (default: both)",
    )
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
        if args.window in ("hour", "both"):
            scrape_hour_sliding_window(slug)
        if args.window in ("day", "both"):
            scrape_day_sliding_window(slug)
