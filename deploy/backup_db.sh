#!/bin/bash
# backup_db.sh — Copy reviews.db to GCS with a date-stamped filename.
# Intended to run daily via cron.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_PATH="$PROJECT_DIR/reviews.db"
BUCKET="gs://rotten-tomatoes-scraper-backups"
DATE="$(date +%Y-%m-%d)"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: No database file found at $DB_PATH — skipping backup."
    exit 1
fi

if ! gcloud storage cp "$DB_PATH" "$BUCKET/reviews-$DATE.db"; then
    echo "ERROR: GCS backup failed for $DB_PATH"
    exit 1
fi

echo "Backup complete: $BUCKET/reviews-$DATE.db"
