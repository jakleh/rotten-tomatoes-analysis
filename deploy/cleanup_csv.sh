#!/bin/bash
# cleanup_csv.sh — Delete reference CSVs older than 30 days.
# Intended to run daily via cron.

set -uo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! find "$PROJECT_DIR" -maxdepth 1 -name "*_reference.csv" -mtime +30 -delete -print | while read -r f; do
    echo "Deleted: $f"
done; then
    echo "ERROR: CSV cleanup failed"
    exit 1
fi

echo "CSV cleanup complete."
