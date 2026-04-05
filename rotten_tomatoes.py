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
import re
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


# -- Logging -------------------------------------------------------------------


class _CloudRunFormatter(logging.Formatter):
    """Emit one JSON object per line for Cloud Run severity mapping."""

    def format(self, record):
        msg = record.getMessage()
        if record.exc_info:
            msg += "\n" + self.formatException(record.exc_info)
        return json.dumps({
            "severity": record.levelname,
            "message": msg,
            "time": self.formatTime(record),
        })


_handler = logging.StreamHandler()
_handler.setFormatter(_CloudRunFormatter())
logging.basicConfig(level=logging.INFO, handlers=[_handler])
log = logging.getLogger(__name__)


# -- Constants -----------------------------------------------------------------

UNIT_ORDER = {"m": 0, "h": 1, "d": 2, "date": 3}
CRITIC_FILTERS = ["top-critics", "all-critics"]
MOVIES_CONFIG_PATH = "movies.json"

# Regex for relative timestamps: "5m", "2h", "3d", "5min", "2hrs", "3days"
RELATIVE_TS_PATTERN = re.compile(r"^(\d+)\s*(m|min|h|hr|d|day)s?$", re.IGNORECASE)

# Map regex capture groups to canonical unit letters
UNIT_ALIASES = {"m": "m", "min": "m", "h": "h", "hr": "h", "d": "d", "day": "d"}

# Centralized selectors for RT review card HTML parsing
SELECTORS = {
    "review_card": "review-card",
    "timestamp": {"tag": "span", "attrs": {"slot": "timestamp"}},
    "reviewer_name": {"tag": "rt-link", "attrs": {"slot": "name"}},
    "publication": {"tag": "rt-link", "attrs": {"slot": "publication"}},
    "rating": {"tag": "span", "attrs": {"slot": "rating"}},
    "sentiment": {"tag": "score-icon-critics"},
    "written_review": {"tag": "div", "attrs": {"slot": "review"}},
    "load_more_xpath": '//*[@id="main-page-content"]/div/section/div/div[2]/div[2]/rt-button',
}

# Fields considered critical -- ERROR logged if ALL cards miss one of these
CRITICAL_FIELDS = {"reviewer_name", "tomatometer_sentiment", "timestamp", "subjective_score"}

# If a single scrape run inserts more than this many reviews for one movie,
# it likely means a selector broke and hash inputs changed (creating new hashes
# for every existing review). Rollback the batch instead of committing bad data.
INSERT_SPIKE_THRESHOLD = 50


# -- Config --------------------------------------------------------------------

def load_movie_config() -> list[str]:
    """Load enabled movie slugs from movies.json."""
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
    return [
        e["slug"] for e in data
        if isinstance(e, dict) and "slug" in e and e.get("enabled", True)
    ]


# -- Timestamp utilities -------------------------------------------------------

def get_timestamp_unit(rel_timestamp: str) -> str:
    """Return 'm', 'h', 'd', or 'date' for a relative timestamp string."""
    rel_timestamp = rel_timestamp.strip()
    if not rel_timestamp:
        return "date"
    match = RELATIVE_TS_PATTERN.match(rel_timestamp)
    if match:
        return UNIT_ALIASES[match.group(2).lower()]
    return "date"


def convert_rel_timestamp_to_abs(rel_timestamp: str, scrape_time: datetime) -> datetime | None:
    """
    Convert RT's relative timestamp to an absolute UTC datetime.

    Uses scrape_time as the reference point so all reviews in a single scrape
    have a consistent reference.

    Formats:
      "5m"     -> 5 minutes before scrape_time
      "2h"     -> 2 hours before scrape_time
      "3d"     -> 3 days before scrape_time
      "Mar 20" -> March 20, inferred year
    """
    rel_timestamp = rel_timestamp.strip()
    if not rel_timestamp:
        return None

    match = RELATIVE_TS_PATTERN.match(rel_timestamp)
    if match:
        value = int(match.group(1))
        unit = UNIT_ALIASES[match.group(2).lower()]
        delta_map = {
            "m": timedelta(minutes=value),
            "h": timedelta(hours=value),
            "d": timedelta(days=value),
        }
        return scrape_time - delta_map[unit]

    # Absolute date format ("Mar 20")
    try:
        current_year = scrape_time.year
        parsed = datetime.strptime(
            f"{rel_timestamp} {current_year}", "%b %d %Y"
        ).replace(tzinfo=timezone.utc)
        if parsed > scrape_time:
            parsed = parsed.replace(year=current_year - 1)
        return parsed
    except ValueError:
        log.warning("Could not parse timestamp: %r", rel_timestamp)
        return None


# -- Database ------------------------------------------------------------------

def get_db_connection():
    """Open a Postgres connection using DATABASE_URL from environment."""
    return psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)


def compute_review_id(
    movie_slug: str, name: str | None, publication: str | None, rating: str | None
) -> str:
    """MD5 hash of (movie_slug + reviewer_name + publication + subjective_score)."""
    key = f"{movie_slug}{name or ''}{publication or ''}{rating or ''}"
    return hashlib.md5(key.encode()).hexdigest()


def insert_review(conn, movie_slug: str, review: dict) -> bool:
    """
    Insert a review. Returns True if inserted, False if already exists.
    ON CONFLICT DO NOTHING -- idempotent, safe to call multiple times.
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


# -- Scraping ------------------------------------------------------------------

def _build_driver(js_heap_mb: int = 256) -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-plugins")
    options.add_argument(f"--js-flags=--max-old-space-size={js_heap_mb}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument(
        "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
    )
    chrome_bin = os.environ.get("CHROME_BIN")
    if chrome_bin:
        options.binary_location = chrome_bin
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(30)
    driver.set_script_timeout(15)
    return driver


def _find_selector(card, selector_name: str):
    """Find an element in a card using the centralized SELECTORS dict."""
    sel = SELECTORS[selector_name]
    if isinstance(sel, str):
        return card.find(sel)
    return card.find(sel["tag"], attrs=sel.get("attrs", {}))


def get_reviews(
    movie_slug: str,
    critic_filter: str = "all-critics",
) -> list[dict]:
    """
    Scrape reviews for a movie. Loads pages until the oldest visible review has
    an absolute date timestamp, then parses all relative-timestamp reviews.
    Stops parsing at the first date-format card (RT is newest-first).

    Returns list of review dicts including page_position (0 = newest).
    """
    url = f"https://www.rottentomatoes.com/m/{movie_slug}/reviews/{critic_filter}"
    top_critic = (critic_filter == "top-critics")
    scrape_time = datetime.now(timezone.utc)

    log.info("Scraping %s (%s)", movie_slug, critic_filter)

    driver = _build_driver()
    try:
        # Retry loop for initial page load (3 attempts, 5s between retries)
        for attempt in range(1, 4):
            try:
                driver.get(url)
                break
            except Exception as e:
                if attempt < 3:
                    log.warning(
                        "Page load attempt %d/3 failed for %s: %s", attempt, url, e
                    )
                    time.sleep(5)
                else:
                    log.error("All 3 page load attempts failed for %s: %s", url, e)
                    return []

        # Wait for at least one review card to appear (replaces fixed sleep)
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SELECTORS["review_card"]))
        )

        wait = WebDriverWait(driver, 15)
        prev_card_count = 0
        stall_count = 0
        while True:
            try:
                btn = wait.until(EC.element_to_be_clickable((
                    By.XPATH,
                    SELECTORS["load_more_xpath"],
                )))
                driver.execute_script(
                    "arguments[0].scrollIntoView({block: 'center'});", btn
                )
                time.sleep(1)
                driver.execute_script("arguments[0].click();", btn)
                log.debug("Clicked 'Load More'")
                time.sleep(3)

                soup = BeautifulSoup(driver.page_source, "html.parser")
                cards = soup.find_all(SELECTORS["review_card"])
                current_card_count = len(cards)

                # Stall detection: bail after 2 consecutive clicks that add no cards
                if current_card_count <= prev_card_count:
                    stall_count += 1
                    log.debug(
                        "'Load More' click did not increase card count "
                        "(still %d). Stall %d/2.",
                        current_card_count, stall_count,
                    )
                    if stall_count >= 2:
                        log.warning(
                            "Load More stalled 2x at %d cards -- stopping load.",
                            current_card_count,
                        )
                        break
                else:
                    stall_count = 0
                prev_card_count = current_card_count

                # Stop loading when oldest visible review has date-format timestamp
                if cards:
                    last_ts_tag = _find_selector(cards[-1], "timestamp")
                    if last_ts_tag:
                        last_ts = last_ts_tag.get_text().strip()
                        if get_timestamp_unit(last_ts) == "date":
                            log.debug(
                                "Reached date-format timestamp '%s' -- stopping load.",
                                last_ts,
                            )
                            break

            except (TimeoutException, ElementClickInterceptedException):
                log.debug("No more 'Load More' button -- all reviews loaded.")
                break

        soup = BeautifulSoup(driver.page_source, "html.parser")
        cards = soup.find_all(SELECTORS["review_card"])
        log.info(
            "Found %d review cards for %s (%s)", len(cards), movie_slug, critic_filter
        )

    except Exception as e:
        log.error("Selenium error for %s (%s): %s", movie_slug, critic_filter, e)
        return []
    finally:
        driver.quit()

    return _parse_cards(cards, movie_slug, critic_filter, top_critic, scrape_time)


def _parse_cards(
    cards,
    movie_slug: str,
    critic_filter: str,
    top_critic: bool,
    scrape_time: datetime,
) -> list[dict]:
    """Parse review cards into dicts. Stops at first date-format timestamp."""
    # Track which critical fields are present across all cards
    critical_field_present = {f: False for f in CRITICAL_FIELDS}

    reviews = []
    for i, card in enumerate(cards):
        ts_tag = _find_selector(card, "timestamp")
        rel_ts = ts_tag.get_text().strip() if ts_tag else ""

        if not ts_tag:
            log.warning("Card %d: missing 'timestamp' selector", i)

        # Stop at date-format reviews -- RT is newest-first
        if get_timestamp_unit(rel_ts) == "date":
            log.debug(
                "Stopping parse at date-format timestamp '%s' (position %d).",
                rel_ts, i,
            )
            break

        abs_ts = convert_rel_timestamp_to_abs(rel_ts, scrape_time) if rel_ts else None

        unit = get_timestamp_unit(rel_ts)
        confidence = unit if unit in ("m", "h", "d") else "d"

        sentiment_tag = _find_selector(card, "sentiment")
        tomatometer = sentiment_tag.get("sentiment") if sentiment_tag else None
        if not sentiment_tag:
            log.warning("Card %d: missing 'sentiment' selector", i)

        rating_container = _find_selector(card, "rating")
        subjective_score = None
        if rating_container:
            inner = rating_container.find("span", style=True)
            if inner:
                subjective_score = inner.get_text().strip()
        if not subjective_score:
            log.warning("Card %d: missing 'subjective_score'", i)

        name_tag = _find_selector(card, "reviewer_name")
        reviewer_name = name_tag.get_text().strip() if name_tag else None
        if not name_tag:
            log.warning("Card %d: missing 'reviewer_name' selector", i)

        pub_tag = _find_selector(card, "publication")
        publication = pub_tag.get_text().strip() if pub_tag else None

        review_tag = _find_selector(card, "written_review")
        written_review = review_tag.get_text().strip() if review_tag else None

        # Track critical field presence
        if rel_ts:
            critical_field_present["timestamp"] = True
        if reviewer_name:
            critical_field_present["reviewer_name"] = True
        if tomatometer:
            critical_field_present["tomatometer_sentiment"] = True
        if subjective_score:
            critical_field_present["subjective_score"] = True

        reviews.append({
            "unique_review_id": compute_review_id(
                movie_slug, reviewer_name, publication, subjective_score
            ),
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

    # ERROR if a critical field was NULL across ALL cards (likely selector breakage)
    if reviews:
        for field, present in critical_field_present.items():
            if not present:
                log.error(
                    "Critical field '%s' was NULL across ALL %d cards for %s (%s) "
                    "-- selector may be broken.",
                    field, len(reviews), movie_slug, critic_filter,
                )

    log.info("Parsed %d reviews for %s (%s)", len(reviews), movie_slug, critic_filter)
    return reviews


# -- Main scrape ---------------------------------------------------------------

def scrape(movie_slug: str) -> None:
    """Scrape all recent reviews for a movie and insert new ones into Postgres.

    Uses "connect late" pattern: scrapes into memory first, connects to DB
    only for batch insert.
    """
    log.info("=== Scraping: %s ===", movie_slug)

    # Phase 1: Scrape all reviews into memory (no DB connection during Selenium)
    all_reviews = []
    for critic_filter in CRITIC_FILTERS:
        reviews = get_reviews(movie_slug, critic_filter)
        all_reviews.append((critic_filter, reviews))

    # Phase 2: Connect to DB and batch insert
    conn = get_db_connection()
    try:
        # Check existing review count for spike guard context.
        # On a fresh DB (0 existing reviews), all inserts are expected — skip the guard.
        cur = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM reviews WHERE movie_slug = %s", (movie_slug,)
        )
        existing_count = cur.fetchone()[0]

        inserted_total = 0
        for critic_filter, reviews in all_reviews:
            inserted_batch = 0
            for review in reviews:
                if insert_review(conn, movie_slug, review):
                    inserted_batch += 1
                    log.info(
                        "Inserted: %s (%s)",
                        review.get("reviewer_name"),
                        review.get("publication_name"),
                    )

            # Spike guard: if insert count is abnormally high, a selector likely
            # broke (changing hash inputs → new hashes for every existing review).
            # Rollback instead of committing bad data.
            # Skip when existing reviews < threshold — not enough to collide with,
            # so a large batch is legitimate (fresh DB or newly added movie).
            if (existing_count >= INSERT_SPIKE_THRESHOLD
                    and inserted_batch > INSERT_SPIKE_THRESHOLD):
                log.error(
                    "INSERT SPIKE: %d inserts for %s (%s) exceeds threshold %d. "
                    "Possible selector breakage changing dedup hashes. "
                    "Rolling back batch.",
                    inserted_batch, movie_slug, critic_filter,
                    INSERT_SPIKE_THRESHOLD,
                )
                conn.rollback()
            else:
                conn.commit()
                inserted_total += inserted_batch

        if inserted_total == 0:
            log.info("No new reviews for %s.", movie_slug)
        else:
            log.info("Inserted %d new reviews for %s.", inserted_total, movie_slug)
    except Exception as e:
        log.error("Scrape error for %s: %s", movie_slug, e)
        raise
    finally:
        conn.close()


# -- Entry point ---------------------------------------------------------------

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
            log.error(
                "No movies to scrape. Check %s or use --movie.", MOVIES_CONFIG_PATH
            )
            raise SystemExit(1)

    mode = "manual" if args.movie else "scheduled"
    log.info("=== Run started: mode=%s, movies=%s ===", mode, movie_slugs)

    for slug in movie_slugs:
        scrape(slug)
