"""
One-off script to fix top_critic=False for movies where the top-critics
scrape failed or was incomplete during backfill.

Scrapes the top-critics page for each movie, computes the unique_review_id
hashes, then UPDATEs matching rows in the DB to top_critic=TRUE.

Reviews are filtered by time_end cutoff (from --time-end or backfill_movies.csv)
to avoid matching post-cutoff reviews that were correctly excluded from the DB.

Usage:
    # Single movie
    DATABASE_URL="postgresql://..." uv run python scripts/fix_top_critic.py --movie fly_me_to_the_moon_2024 --time-end 2024-07-15

    # Dry run
    DATABASE_URL="postgresql://..." uv run python scripts/fix_top_critic.py --movie fly_me_to_the_moon_2024 --time-end 2024-07-15 --dry-run

    # All movies from backfill_movies.csv (with per-movie cutoffs from time_end column)
    DATABASE_URL="postgresql://..." uv run python scripts/fix_top_critic.py --all
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rotten_tomatoes import get_db_connection

from backfill import (
    _parse_time_end,
    filter_reviews_by_cutoff,
    get_all_reviews,
    load_backfill_config,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


def fix_movie(movie_slug: str, dry_run: bool = False, time_end_cutoff=None) -> dict:
    """Scrape top-critics, then UPDATE matching rows to top_critic=TRUE."""
    log.info("=== Fixing top_critic for: %s (time_end=%s) ===", movie_slug,
             time_end_cutoff.strftime("%Y-%m-%d") if time_end_cutoff else "none")

    # Step 1: Scrape top-critics to get the review hashes
    reviews = get_all_reviews(movie_slug, critic_filter="top-critics")
    if not reviews:
        log.error("No top-critic reviews scraped for %s -- nothing to fix.", movie_slug)
        return {"movie": movie_slug, "scraped": 0, "filtered": 0, "updated": 0}

    total_scraped = len(reviews)

    # Step 2: Filter by time_end cutoff (same as backfill)
    if time_end_cutoff:
        reviews = filter_reviews_by_cutoff(reviews, time_end_cutoff)
        log.info(
            "Filtered %d -> %d reviews (cutoff %s)",
            total_scraped, len(reviews),
            time_end_cutoff.strftime("%Y-%m-%d"),
        )
        if not reviews:
            log.warning("All reviews excluded by cutoff -- nothing to fix.")
            return {"movie": movie_slug, "scraped": total_scraped, "filtered": 0, "updated": 0}

    hashes = [r["unique_review_id"] for r in reviews]
    log.info("Scraped %d top-critic reviews for %s (%d after cutoff filter)",
             total_scraped, movie_slug, len(hashes))

    if dry_run:
        log.info("[DRY RUN] Would UPDATE %d rows to top_critic=TRUE", len(hashes))
        return {"movie": movie_slug, "scraped": total_scraped, "filtered": len(hashes),
                "updated": 0, "dry_run": True}

    # Step 3: UPDATE matching rows in the DB
    try:
        conn = get_db_connection()
    except Exception:
        log.exception("DB connection failed for %s -- skipping.", movie_slug)
        return {"movie": movie_slug, "scraped": total_scraped, "filtered": len(hashes),
                "updated": 0, "error": True}
    try:
        with conn.cursor() as cur:
            # Check current state
            cur.execute(
                "SELECT COUNT(*) FROM reviews WHERE movie_slug = %s AND top_critic = TRUE",
                (movie_slug,),
            )
            before_count = cur.fetchone()[0]

            # Batch UPDATE
            cur.execute(
                "UPDATE reviews SET top_critic = TRUE WHERE unique_review_id = ANY(%s)",
                (hashes,),
            )
            updated = cur.rowcount

            # Verify after
            cur.execute(
                "SELECT COUNT(*) FROM reviews WHERE movie_slug = %s AND top_critic = TRUE",
                (movie_slug,),
            )
            after_count = cur.fetchone()[0]

        conn.commit()
        log.info(
            "Updated %d rows. top_critic=TRUE count: %d -> %d",
            updated, before_count, after_count,
        )
        if updated < len(hashes):
            log.warning(
                "Updated %d rows but had %d hashes after cutoff filter. "
                "%d hashes did not match any DB row (review may have "
                "changed on RT since backfill, or was never inserted).",
                updated, len(hashes), len(hashes) - updated,
            )
        return {"movie": movie_slug, "scraped": total_scraped, "filtered": len(hashes),
                "updated": updated, "before": before_count, "after": after_count}
    except Exception:
        conn.rollback()
        log.exception("DB error fixing %s", movie_slug)
        return {"movie": movie_slug, "scraped": total_scraped, "filtered": len(hashes),
                "updated": 0, "error": True}
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Fix top_critic flags for backfill movies.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--movie", type=str, help="Single movie slug to fix")
    group.add_argument("--all", action="store_true", help="Fix all known affected movies")
    parser.add_argument("--dry-run", action="store_true", help="Scrape only, no DB writes")
    parser.add_argument("--time-end", type=str,
                        help="Exclude reviews after this date (YYYY-MM-DD). "
                             "Requires --movie. --all reads cutoffs from backfill_movies.csv.")
    args = parser.parse_args()

    if args.time_end and not args.movie:
        parser.error("--time-end requires --movie")

    if args.movie:
        cutoff = _parse_time_end(args.time_end) if args.time_end else None
        movies = [(args.movie, cutoff)]
    else:
        # --all: read slugs and cutoffs from backfill_movies.csv (same as backfill.py)
        csv_config = load_backfill_config()
        if not csv_config:
            log.error("No movies found in backfill_movies.csv.")
            sys.exit(1)
        movies = []
        for entry in csv_config:
            cutoff = _parse_time_end(entry["time_end"]) if entry.get("time_end") else None
            movies.append((entry["slug"], cutoff))

    slugs = [m[0] for m in movies]
    log.info("=== fix_top_critic started: movies=%s, dry_run=%s ===", slugs, args.dry_run)

    results = []
    for slug, cutoff in movies:
        result = fix_movie(slug, dry_run=args.dry_run, time_end_cutoff=cutoff)
        results.append(result)

    log.info("=== Summary ===")
    for r in results:
        log.info("  %s: scraped=%d, after_cutoff=%s, updated=%s",
                 r["movie"], r["scraped"], r.get("filtered", "N/A"),
                 r.get("updated", "N/A"))


if __name__ == "__main__":
    main()
