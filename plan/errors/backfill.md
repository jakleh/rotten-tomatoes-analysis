# Backfill Script Failures (Prefix H)

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| H-1 | Invalid `--time-end` format | Low (user error) | Script exits before scraping |
| H-2 | `--time-end` without `--movie` | Low (user error) | Script exits before scraping |
| H-11 | Neither `--movie` nor `--all` provided | Low (user error) | Argparse error, script exits |
| H-12 | Both `--movie` and `--all` provided | Low (user error) | Argparse error, script exits |
| H-13 | `backfill_movies.csv` missing or empty | Low (user error) | Script exits with "No movies found" |
| H-3 | None-timestamp reviews silently excluded | Medium | Incomplete backfill (missing reviews) |
| H-4 | Off-by-one at cutoff boundary | Low (covered by tests) | Wrong reviews included/excluded |
| H-5 | Selenium returns 0 reviews | Medium | No data for movie |
| H-6 | Duplicate reviews (hash collision) | Very Low | Old score kept, silent |
| H-7 | DB connection fails mid-backfill | Low | OperationalError, partial data |
| H-8 | Health check misleading after filtering | High (when --time-end used) | False alarm on count delta |
| H-9 | Wrong movie slug (typo) | Low (user error) | 0 reviews scraped |
| H-10 | Confirmation prompt bypassed (piped stdin) | Very Low | Unintended execution |

## Prevention

**H-1 / H-2 (argument validation):**
- Validate `--time-end` with `strptime("%Y-%m-%d")` at parse time; show expected format in error message
- Enforce `--time-end` requires `--movie` before any scraping begins

**H-11 / H-12 / H-13 (CLI and CSV config):**
- `--movie` and `--all` are in a mutually exclusive argparse group with `required=True`
- Neither flag = argparse error (safe default — prevents accidental batch run)
- Both flags = argparse error (mutual exclusion)
- `--all` reads from `scripts/backfill_movies.csv`; if missing/empty, script exits with error
- Cross-ref anti_blocking.md J-8, J-10

**H-3 (None-timestamp exclusion):**
- Log excluded count and total count after filtering
- WARN if >10% of reviews had `estimated_timestamp=None` (operator awareness of data quality)

**H-4 (off-by-one):**
- Strict `<` against next-day midnight; 4 boundary tests cover exact semantics

**H-5 / H-6 / H-7 (scraping & DB):**
- Cross-ref selenium.md (H-5), html_parsing.md (H-6), neon.md (H-7)
- Re-run is safe due to `ON CONFLICT DO NOTHING` idempotency

**H-8 (health check):**
- Skip health check when `--time-end` is active; log why it's skipped

**H-9 (typo):**
- Confirmation prompt shows slug before scraping begins; 0 reviews = likely typo

## Outside Our Control

1. **RT HTML changes**: May cause 0 reviews or broken parsing. Cross-ref html_parsing.md.
2. **Neon outage**: Connection fails. Cross-ref neon.md.
3. **RT rate limiting or blocking**: Selenium may fail to load pages. Cross-ref selenium.md and anti_blocking.md.

## Detection

| Signal | Failure |
|---|---|
| `error: one of the arguments --movie --all is required` | H-11 |
| `error: argument --all: not allowed with argument --movie` | H-12 |
| `No movies found in .../backfill_movies.csv` | H-13 |
| `error: --time-end requires --movie` or `invalid date format` | H-1, H-2 |
| `Filtered N reviews: kept 0` | H-3 (all None timestamps) or H-4 (cutoff too early) |
| `WARNING: >10% of reviews had None timestamps` | H-3 |
| `Parsed 0 reviews` or `Found 0 review cards` | H-5, H-9 |
| Health check delta > 10 when `--time-end` NOT active | H-6 |
| `OperationalError` or `Connection refused` | H-7 |

## Diagnosis Decision Tree

```
Backfill failure detected
|
+-> Script exited before scraping?
|   +-> "invalid date format"?
|   |   +-> YES: Fix --time-end value. Expected: YYYY-MM-DD
|   +-> "--time-end requires --movie"?
|   |   +-> YES: Add --movie <slug> to command
|   +-> "DATABASE_URL environment variable is required"?
|   |   +-> YES: Set DATABASE_URL. Cross-ref neon.md
|   +-> NO: Check traceback for import errors
|
+-> Reviews scraped but 0 kept after filter?
|   +-> Check filter log line: "Filtered N reviews: kept 0, excluded M"
|   +-> All excluded by cutoff?
|   |   +-> YES: Is --time-end date correct? Too early for this movie?
|   +-> All excluded by None timestamp?
|   |   +-> YES: Timestamp parsing broken. Cross-ref html_parsing.md C-2/C-3
|   +-> Mix of both?
|       +-> Check None count. If >10%, parsing may be partially broken.
|
+-> Reviews inserted but count seems wrong?
    +-> Unexpectedly high inserts?
    |   +-> Dedup hash inputs changed. Cross-ref html_parsing.md C-6/C-7
    +-> Unexpectedly low inserts?
        +-> Most reviews already in DB (expected for re-runs)
        +-> Or cutoff excluded more than expected
```

## Key Commands

```bash
# Dry run (no DB writes)
DATABASE_URL="..." uv run python scripts/backfill.py --movie <slug> --dry-run

# Single movie with time cutoff
DATABASE_URL="..." uv run python scripts/backfill.py --movie <slug> --time-end 2026-02-21

# Batch run from CSV
DATABASE_URL="..." uv run python scripts/backfill.py --all

# Verify in DB: count reviews before cutoff date
psql $DATABASE_URL -c "SELECT COUNT(*) FROM reviews WHERE movie_slug = '<slug>' AND estimated_timestamp < '2026-02-22T00:00:00Z'"

# Check what was inserted
psql $DATABASE_URL -c "SELECT reviewer_name, estimated_timestamp, site_timestamp_text FROM reviews WHERE movie_slug = '<slug>' ORDER BY estimated_timestamp DESC LIMIT 20"
```

## Research

- Python argparse docs: https://docs.python.org/3/library/argparse.html
- datetime.strptime format codes: https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior
- Existing playbooks for cross-references: `plan/errors/selenium.md`, `plan/errors/neon.md`, `plan/errors/html_parsing.md`, `plan/errors/anti_blocking.md`
