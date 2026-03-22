#!/bin/bash
# cleanup_csv.sh — Delete reference CSVs older than 30 days.
# Intended to run daily via cron.

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

find "$PROJECT_DIR" -maxdepth 1 -name "*_reference.csv" -mtime +30 -delete -print | while read -r f; do
    echo "Deleted: $f"
done
