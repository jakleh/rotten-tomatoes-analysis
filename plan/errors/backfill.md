# Backfill Script Failures (Prefix H)

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| H-1 | Invalid `--time-end` format | Low (user error) | Script exits before scraping |
| H-2 | `--time-end` without `--movie` | Low (user error) | Script exits before scraping |
| H-11 | Neither `--movie` nor `--all` provided | Low (user error) | Argparse error, script exits |
| H-12 | Both `--movie` and `--all` provided | Low (user error) | Argparse error, script exits |
| H-13 | `backfill_movies.csv` missing or empty | Low (user error) | Script exits with "No movies found" |
| H-14 | Invalid `time_end` date in CSV row | Low (user error) | Movie skipped, error logged |
| H-15 | CSV missing `time_end` column | Low (backward compat) | All movies get no cutoff (None) |
| H-3 | None-timestamp reviews silently excluded | Medium | Incomplete backfill (missing reviews) |
| H-17 | RT uses MM/DD/YYYY format for older reviews | High (confirmed) | 96-98% of reviews dropped if not parsed |
| H-18 | Neon SSL connection dropped during long batch run | Medium | Script crashes, remaining movies not processed |
| H-19 | Chrome retry loop reuses dead session | Low | One critic-filter pass returns 0 reviews (other pass recovers) |
| H-4 | Off-by-one at cutoff boundary | Low (covered by tests) | Wrong reviews included/excluded |
| H-5 | Selenium returns 0 reviews | Medium | No data for movie |
| H-16 | JS extraction fails for a click batch | Low (incremental extraction) | Batch skipped; reviews from prior clicks preserved. |
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

**H-14 (invalid CSV date):**
- Each row's `time_end` is parsed with `_parse_time_end()` (validates `YYYY-MM-DD` format)
- Invalid dates log ERROR and skip that movie; other movies still proceed
- Same validation as `--time-end` CLI flag (shared `_parse_time_end()` helper)

**H-15 (missing time_end column):**
- `load_backfill_config()` uses `row.get("time_end", "")` — missing column returns `None`
- Movies without `time_end` run with no cutoff (full backfill), same as legacy behavior
- Health check runs only for movies without a cutoff

**H-3 (None-timestamp exclusion):**
- Log excluded count and total count after filtering
- WARN if >10% of reviews had `estimated_timestamp=None` (operator awareness of data quality)
- Root cause identified in overnight run (2026-04-05): RT uses `MM/DD/YYYY` for older reviews. See H-17.

**H-17 (MM/DD/YYYY timestamps):**
- **Fixed**: `convert_rel_timestamp_to_abs()` now parses `MM/DD/YYYY` as a third format (after relative and `"Mar 20"`)
- Parsing order: relative (`5m`) → abbreviated date (`Mar 20`) → slash date (`01/19/2025`) → None
- No year heuristic needed — full year is in the string
- Main scraper unaffected: `_parse_cards()` stops at `get_timestamp_unit() == "date"` before reaching these
- See plan doc: `plan/backfill_date_parsing.md`

**H-18 (Neon SSL drop during batch — deferred):**
- Observed: `psycopg2.OperationalError: SSL connection has been closed unexpectedly` on movie #8 of 141
- Current behavior: script crashes, remaining movies not processed. Completed movies are safe (committed).
- Workaround: trim CSV to remaining movies and re-run (idempotent inserts handle overlap)
- Future fix: wrap `insert_review()` calls with connection retry logic (catch `OperationalError`, reconnect, retry)
- Cross-ref neon.md D-8

**H-19 (Chrome retry reuses dead session — deferred):**
- Observed: `a_quiet_place_day_one` top-critics failed all 3 retries with `invalid session id`
- Root cause: retry loop calls `driver.get()` on a driver whose session is already dead. Retries 2 and 3 are guaranteed to fail.
- All-critics pass recovered because `get_all_reviews()` creates a fresh driver per call.
- Impact is low: only one critic-filter pass is lost, and all-critics is a superset (minus `top_critic=True` flag)
- Future fix: catch `InvalidSessionIdException` and create a new driver inside the retry loop
- Cross-ref chrome.md A-8

**H-4 (off-by-one):**
- Strict `<` against next-day midnight; 4 boundary tests cover exact semantics

**H-5 / H-6 / H-7 (scraping & DB):**
- Cross-ref selenium.md (H-5), html_parsing.md (H-6), neon.md (H-7)
- Re-run is safe due to `ON CONFLICT DO NOTHING` idempotency

**H-16 (JS extraction failure):**
- Backfill uses incremental JS extraction: after each click, only new cards are serialized via `outerHTML` (not full page DOM)
- Stall check uses `querySelectorAll('review-card').length` (JS, no serialization)
- If a batch extraction fails, that batch is skipped but all previously captured reviews are preserved
- Final sweep catches any cards missed by the last failed batch
- Page load timeout is 120s (vs 30s for main scraper) for initial page load
- V8 heap is 1024MB (vs 256MB for main scraper) to handle large DOMs without Chrome freezing
- Cross-ref selenium.md, chrome.md

**H-8 (health check):**
- Skip health check for any movie with a time cutoff (from `--time-end` or CSV `time_end`); log why it's skipped

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
| `Invalid time_end '<value>' for <slug> -- skipping` | H-14 |
| `Filtered N reviews: kept 0` | H-3 (all None timestamps) or H-4 (cutoff too early) |
| `WARNING: >10% of reviews had None timestamps` | H-3 |
| `Card count JS failed after click N` or `Card extraction failed after click N` | H-16 (non-fatal, prior reviews preserved) |
| `Selenium error for ... Returning N reviews captured so far` | H-16 (Chrome crash; partial reviews returned) |
| `Parsed 0 reviews` or `Found 0 total reviews` or `0 reviews found` | H-5, H-9 |
| Health check delta > 10 when `--time-end` NOT active | H-6 |
| `Could not parse timestamp: 'MM/DD/YYYY'` | H-17 (if still appearing, parsing not updated) |
| `SSL connection has been closed unexpectedly` | H-18 (Neon dropped mid-batch) |
| `invalid session id` across all 3 retries | H-19 (dead Chrome session reused) |
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
|   |   +-> YES: Is cutoff date correct?
|   |   |   +-> --movie mode: check --time-end value
|   |   |   +-> --all mode: check time_end in backfill_movies.csv for this slug
|   +-> All excluded by None timestamp?
|   |   +-> YES: New date format? Check warnings for pattern.
|   |   |   +-> "Could not parse timestamp: 'MM/DD/YYYY'"? H-17 — parsing not updated.
|   |   |   +-> Other format? Add new branch to convert_rel_timestamp_to_abs().
|   +-> Mix of both?
|       +-> Check None count. If >10%, parsing may be partially broken.
|
+-> Script crashed with "SSL connection has been closed unexpectedly"?
|   +-> H-18. Neon dropped the connection during a long batch run.
|   +-> Completed movies are safe. Trim CSV to remaining movies, re-run.
|   +-> Cross-ref neon.md D-8.
|
+-> All 3 retries failed with "invalid session id"?
|   +-> H-19. Chrome session died and retry loop reused the dead driver.
|   +-> Other critic-filter pass likely succeeded (fresh driver).
|   +-> Re-run for that movie to fill in missing top_critic flags.
|
+-> Chrome crashed mid-scrape ("Selenium error ... Returning N reviews")?
|   +-> Partial reviews were saved. Re-run to attempt remaining.
|   +-> If same crash point repeats: DOM too large for 1024MB heap.
|       +-> Increase js_heap_mb in backfill or accept partial data.
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

# Batch run from CSV (per-movie cutoffs from time_end column)
DATABASE_URL="..." uv run python scripts/backfill.py --all

# Verify in DB: count reviews before cutoff date
psql $DATABASE_URL -c "SELECT COUNT(*) FROM reviews WHERE movie_slug = '<slug>' AND estimated_timestamp < '2026-02-22T00:00:00Z'"

# Check what was inserted
psql $DATABASE_URL -c "SELECT reviewer_name, estimated_timestamp, site_timestamp_text FROM reviews WHERE movie_slug = '<slug>' ORDER BY estimated_timestamp DESC LIMIT 20"

# Check CSV for a specific movie's cutoff
grep '<slug>' scripts/backfill_movies.csv
```

## Research

- Python argparse docs: https://docs.python.org/3/library/argparse.html
- datetime.strptime format codes: https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior
- Existing playbooks for cross-references: `plan/errors/selenium.md`, `plan/errors/neon.md`, `plan/errors/html_parsing.md`, `plan/errors/anti_blocking.md`
