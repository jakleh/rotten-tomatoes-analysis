"""
Backfill historical reviews into Neon (PostgreSQL).

One-time script -- run locally with DATABASE_URL set. Not run via Cloud Run
(no timeout constraint). Scrapes ALL reviews (not just recent), including
date-format timestamps.

Usage:
    # Single movie (required: --movie or --all)
    DATABASE_URL="postgresql://..." uv run python scripts/backfill.py --movie project_hail_mary

    # All movies from scripts/backfill_movies.csv
    DATABASE_URL="postgresql://..." uv run python scripts/backfill.py --all

    # Dry run (report only, no writes)
    DATABASE_URL="postgresql://..." uv run python scripts/backfill.py --movie project_hail_mary --dry-run

    # Exclude reviews after a date (requires --movie)
    DATABASE_URL="postgresql://..." uv run python scripts/backfill.py --movie project_hail_mary --time-end 2026-02-21
"""

import argparse
import csv
import logging
import os
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.request import Request, urlopen
from urllib.error import URLError

from bs4 import BeautifulSoup
from selenium.common.exceptions import TimeoutException, ElementClickInterceptedException
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# Allow imports from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rotten_tomatoes import (
    CRITIC_FILTERS,
    SELECTORS,
    _build_driver,
    _find_selector,
    _parse_cards,
    compute_review_id,
    convert_rel_timestamp_to_abs,
    get_db_connection,
    get_timestamp_unit,
    insert_review,
)

BACKFILL_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backfill_movies.csv")


def load_backfill_config() -> list[str]:
    """Read movie slugs from backfill_movies.csv."""
    if not os.path.exists(BACKFILL_CSV_PATH):
        log.error("Backfill CSV not found: %s", BACKFILL_CSV_PATH)
        return []
    with open(BACKFILL_CSV_PATH, newline="") as f:
        reader = csv.DictReader(f)
        slugs = [row["slug"].strip() for row in reader if row.get("slug", "").strip()]
    return slugs

log = logging.getLogger("backfill")


def get_all_reviews(
    movie_slug: str,
    critic_filter: str = "all-critics",
) -> list[dict]:
    """Scrape ALL reviews for a movie, including date-format timestamps.

    Unlike the main scraper's get_reviews(), this does NOT stop at date-format
    cards. It loads all pages and parses all cards, assigning date-format
    timestamps confidence='d'.
    """
    url = f"https://www.rottentomatoes.com/m/{movie_slug}/reviews/{critic_filter}"
    top_critic = (critic_filter == "top-critics")
    scrape_time = datetime.now(timezone.utc)

    log.info("Scraping ALL reviews: %s (%s)", movie_slug, critic_filter)

    driver = _build_driver()
    try:
        # Retry loop for initial page load
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

        # Load ALL pages (no date-format stop condition)
        wait = WebDriverWait(driver, 15)
        page_count = 0
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
                time.sleep(random.uniform(0.5, 1.5))
                driver.execute_script("arguments[0].click();", btn)
                page_count += 1
                log.debug("Clicked 'Load More' (page %d)", page_count)
                time.sleep(random.uniform(2, 5))

                # Stall detection: bail after 2 consecutive clicks that add no cards
                soup = BeautifulSoup(driver.page_source, "html.parser")
                current_card_count = len(soup.find_all(SELECTORS["review_card"]))
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

            except (TimeoutException, ElementClickInterceptedException):
                log.info("All pages loaded (%d 'Load More' clicks).", page_count)
                break

        soup = BeautifulSoup(driver.page_source, "html.parser")
        cards = soup.find_all(SELECTORS["review_card"])
        log.info(
            "Found %d total review cards for %s (%s)",
            len(cards), movie_slug, critic_filter,
        )
        if len(cards) == 0:
            snippet = driver.page_source[:500] if driver.page_source else "(empty)"
            log.warning("0 cards found. Page source snippet:\n%s", snippet)
    except Exception as e:
        log.error("Selenium error for %s (%s): %s", movie_slug, critic_filter, e)
        return []
    finally:
        driver.quit()

    # Parse ALL cards (including date-format) -- don't use _parse_cards which
    # stops at date-format. Implement full parsing here.
    reviews = []
    for i, card in enumerate(cards):
        ts_tag = _find_selector(card, "timestamp")
        rel_ts = ts_tag.get_text().strip() if ts_tag else ""

        abs_ts = convert_rel_timestamp_to_abs(rel_ts, scrape_time) if rel_ts else None

        unit = get_timestamp_unit(rel_ts)
        confidence = unit if unit in ("m", "h", "d") else "d"

        sentiment_tag = _find_selector(card, "sentiment")
        tomatometer = sentiment_tag.get("sentiment") if sentiment_tag else None

        rating_container = _find_selector(card, "rating")
        subjective_score = None
        if rating_container:
            inner = rating_container.find("span", style=True)
            if inner:
                subjective_score = inner.get_text().strip()

        name_tag = _find_selector(card, "reviewer_name")
        reviewer_name = name_tag.get_text().strip() if name_tag else None

        pub_tag = _find_selector(card, "publication")
        publication = pub_tag.get_text().strip() if pub_tag else None

        review_tag = _find_selector(card, "written_review")
        written_review = review_tag.get_text().strip() if review_tag else None

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

    log.info("Parsed %d reviews for %s (%s)", len(reviews), movie_slug, critic_filter)
    return reviews


def filter_reviews_by_cutoff(reviews: list[dict], time_end_cutoff: datetime) -> list[dict]:
    """Filter reviews to only those with estimated_timestamp before cutoff.
    Reviews with estimated_timestamp=None are excluded.
    """
    return [
        r for r in reviews
        if r["estimated_timestamp"] is not None
        and r["estimated_timestamp"] < time_end_cutoff
    ]


def backfill_movie(movie_slug: str, conn, dry_run: bool = False, time_end_cutoff: datetime | None = None) -> dict:
    """Backfill all reviews for a single movie.

    Returns a stats dict: {inserted, skipped, errors}.
    """
    stats = {"inserted": 0, "skipped": 0, "errors": 0}

    # Phase 1: Scrape all reviews into memory
    all_reviews = []
    for i, critic_filter in enumerate(CRITIC_FILTERS):
        if i > 0:
            delay = random.uniform(5, 15)
            log.info("Waiting %.1fs before next critic filter...", delay)
            time.sleep(delay)
        log.info("Scraping %s / %s ...", movie_slug, critic_filter)
        try:
            reviews = get_all_reviews(movie_slug, critic_filter)
        except Exception:
            log.exception(
                "Selenium error scraping %s / %s -- skipping",
                movie_slug, critic_filter,
            )
            stats["errors"] += 1
            continue
        log.info("Got %d reviews from %s / %s", len(reviews), movie_slug, critic_filter)
        all_reviews.extend(reviews)

    # Filter by time_end_cutoff if provided
    if time_end_cutoff is not None:
        total_before = len(all_reviews)
        none_count = sum(1 for r in all_reviews if r["estimated_timestamp"] is None)
        all_reviews = filter_reviews_by_cutoff(all_reviews, time_end_cutoff)
        excluded = total_before - len(all_reviews)
        log.info(
            "Filtered %d reviews: kept %d, excluded %d (cutoff %s)",
            total_before, len(all_reviews), excluded, time_end_cutoff.isoformat(),
        )
        if total_before > 0 and none_count / total_before > 0.10:
            log.warning(
                "%d of %d reviews (%.0f%%) had None timestamps and were excluded",
                none_count, total_before, 100 * none_count / total_before,
            )

    # Phase 2: Insert into DB
    seen_ids = set()
    for review in all_reviews:
        rid = review["unique_review_id"]
        if rid in seen_ids:
            stats["skipped"] += 1
            continue
        seen_ids.add(rid)

        if dry_run:
            stats["inserted"] += 1
            continue

        if insert_review(conn, movie_slug, review):
            stats["inserted"] += 1
        else:
            stats["skipped"] += 1

    if not dry_run:
        conn.commit()

    return stats


def health_check(movie_slug: str, conn) -> None:
    """Compare RT's total review count against DB count. ERROR if delta > 10."""
    url = f"https://www.rottentomatoes.com/m/{movie_slug}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    }
    try:
        req = Request(url, headers=headers)
        with urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        match = re.search(r"(\d+) Reviews", html)
        if not match:
            log.warning("Health check: could not extract review count from %s", url)
            return
        rt_count = int(match.group(1))
    except (URLError, OSError) as e:
        log.warning("Health check: HTTP request failed for %s: %s", movie_slug, e)
        return

    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM reviews WHERE movie_slug = %s", (movie_slug,)
        )
        db_count = cur.fetchone()[0]

    delta = abs(rt_count - db_count)
    if delta > 10:
        log.error(
            "Health check FAIL for %s: RT=%d, DB=%d, delta=%d",
            movie_slug, rt_count, db_count, delta,
        )
    else:
        log.info(
            "Health check OK for %s: RT=%d, DB=%d, delta=%d",
            movie_slug, rt_count, db_count, delta,
        )


def main():
    parser = argparse.ArgumentParser(description="Backfill historical reviews into Neon.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--movie", help="Single movie slug to backfill")
    group.add_argument("--all", action="store_true", help="Backfill all movies from backfill_movies.csv")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    parser.add_argument("--time-end", help="Exclude reviews after this date (YYYY-MM-DD)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Validate --time-end
    time_end_cutoff = None
    if args.time_end:
        if not args.movie:
            parser.error("--time-end requires --movie")
        try:
            end_date = datetime.strptime(args.time_end, "%Y-%m-%d")
        except ValueError:
            parser.error(f"invalid date format '{args.time_end}', expected YYYY-MM-DD")
        time_end_cutoff = datetime(
            end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc
        ) + timedelta(days=1)

    if "DATABASE_URL" not in os.environ:
        log.error("DATABASE_URL environment variable is required.")
        sys.exit(1)

    if args.movie:
        slugs = [args.movie]
    else:
        slugs = load_backfill_config()
        if not slugs:
            log.error("No movies found in %s", BACKFILL_CSV_PATH)
            sys.exit(1)

    print(f"\n  Movies:    {', '.join(slugs)}")
    print(f"  Dry run:   {args.dry_run}")
    if time_end_cutoff:
        print(f"  Time end:  {args.time_end} (exclude reviews after {time_end_cutoff.isoformat()})")
    print()

    confirm = input("  Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    conn = get_db_connection()

    totals = {"inserted": 0, "skipped": 0, "errors": 0}

    for i, slug in enumerate(slugs):
        log.info("=== Backfilling %s ===", slug)
        stats = backfill_movie(slug, conn, dry_run=args.dry_run, time_end_cutoff=time_end_cutoff)
        for k in totals:
            totals[k] += stats[k]
        log.info("  %s: %s", slug, stats)
        if i < len(slugs) - 1:
            log.info("Waiting 30s before next movie...")
            time.sleep(30)

    # Post-run health check (skip on dry run or --time-end)
    if args.dry_run:
        pass  # no health check on dry run
    elif time_end_cutoff:
        log.info("Skipping health check: --time-end filtering makes count comparison meaningless")
    else:
        log.info("=== Running health checks ===")
        for slug in slugs:
            health_check(slug, conn)

    conn.close()

    print(f"\n  Totals: {totals}")
    if args.dry_run:
        print("  (dry run -- no changes written)")
    if time_end_cutoff:
        print("\n  Verify in psql (should return 0):")
        for slug in slugs:
            print(f"    SELECT COUNT(*) FROM reviews WHERE movie_slug = '{slug}' AND estimated_timestamp >= '{time_end_cutoff.isoformat()}';")


if __name__ == "__main__":
    main()
