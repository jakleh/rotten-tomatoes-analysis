"""
Backfill historical reviews and fill missing tomatometer_sentiment values.

One-time script — imports from the main scraper module. Designed to be run
locally against a copy of reviews.db (SCP from VM, backfill, SCP back).

Usage:
    # All enabled movies in movies.json
    uv run python scripts/backfill.py --db ./reviews.db

    # Single movie
    uv run python scripts/backfill.py --db ./reviews.db --movie project_hail_mary

    # Dry run (report only, no writes)
    uv run python scripts/backfill.py --db ./reviews.db --dry-run

Workflow:
    1. gcloud compute scp jakelehner@rt-scraper:~/rotten-tomatoes-analysis/reviews.db ./reviews.db --zone=us-east1-b
    2. gcloud compute ssh rt-scraper --zone=us-east1-b -- "crontab -l > /tmp/cron_backup && crontab -r"
    3. uv run python scripts/backfill.py --db ./reviews.db
    4. gcloud compute scp ./reviews.db jakelehner@rt-scraper:~/rotten-tomatoes-analysis/reviews.db --zone=us-east1-b
    5. gcloud compute ssh rt-scraper --zone=us-east1-b -- "crontab /tmp/cron_backup"
"""

import argparse
import logging
import os
import sqlite3
import sys

# Allow imports from the project root.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rotten_tomatoes import (
    get_db_review_ids,
    get_reviews,
    init_reviews_table,
    insert_review,
    load_movie_config,
    update_sentiment,
)

log = logging.getLogger("backfill")


def backfill_movie(
    movie_slug: str, conn: sqlite3.Connection, dry_run: bool = False
) -> dict:
    """Backfill all reviews for a single movie.

    Returns a stats dict: {inserted, sentiment_updated, skipped, errors}.
    """
    existing_ids = get_db_review_ids(conn, movie_slug)
    log.info("DB has %d reviews for %s", len(existing_ids), movie_slug)

    stats = {"inserted": 0, "sentiment_updated": 0, "skipped": 0, "errors": 0}

    for critic_filter in ["top-critics", "all-critics"]:
        log.info("Scraping %s / %s (stop_at_unit=None) ...", movie_slug, critic_filter)
        try:
            reviews = get_reviews(movie_slug, critic_filter, stop_at_unit=None)
        except Exception:
            log.exception("Selenium error scraping %s / %s — skipping", movie_slug, critic_filter)
            stats["errors"] += 1
            continue

        log.info("Got %d reviews from %s / %s", len(reviews), movie_slug, critic_filter)

        for review in reviews:
            rid = review["unique_review_id"]

            if rid in existing_ids:
                sentiment = review.get("tomatometer_sentiment")
                if not dry_run and update_sentiment(conn, rid, sentiment):
                    stats["sentiment_updated"] += 1
                else:
                    stats["skipped"] += 1
            else:
                review["reconciled_timestamp"] = False
                if not dry_run:
                    insert_review(conn, movie_slug, review)
                stats["inserted"] += 1
                existing_ids.add(rid)

    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill historical reviews.")
    parser.add_argument("--db", default="reviews.db", help="Path to SQLite database")
    parser.add_argument("--movie", help="Single movie slug (default: all enabled)")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no writes")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.movie:
        slugs = [args.movie]
    else:
        slugs = load_movie_config()
        if not slugs:
            log.error("No movies found in movies.json")
            sys.exit(1)

    db_size = os.path.getsize(args.db) if os.path.exists(args.db) else 0
    print(f"\n  DB path:   {os.path.abspath(args.db)}")
    print(f"  DB size:   {db_size / 1024:.1f} KB")
    print(f"  Movies:    {', '.join(slugs)}")
    print(f"  Dry run:   {args.dry_run}")
    print(f"\n  Reminders: Back up DB first. Pause cron on VM.\n")

    confirm = input("  Proceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    init_reviews_table(conn)

    totals = {"inserted": 0, "sentiment_updated": 0, "skipped": 0, "errors": 0}

    for slug in slugs:
        log.info("=== Backfilling %s ===", slug)
        stats = backfill_movie(slug, conn, dry_run=args.dry_run)
        for k in totals:
            totals[k] += stats[k]
        log.info("  %s: %s", slug, stats)

    conn.close()

    print(f"\n  Totals: {totals}")
    if args.dry_run:
        print("  (dry run — no changes written)")


if __name__ == "__main__":
    main()
