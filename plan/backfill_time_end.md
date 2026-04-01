# Plan: Add `--time-end` Flag to Backfill Script + Error Playbook

## Context

Jake is starting the data analysis phase and needs to backfill reviews for specific movies up to the date their corresponding Kalshi bets closed. The current backfill script scrapes ALL historical reviews with no date filtering. We need a `--time-end` flag so he can run targeted backfills, plus an error playbook following the project's established pattern.

## Files to Modify

| File | Action |
|---|---|
| `scripts/backfill.py` | Add `--time-end` flag, filtering logic, validation |
| `tests/test_rotten_tomatoes.py` | Add 10 new tests for filtering + arg validation |
| `plan/errors/backfill.md` | New error playbook (prefix H) |
| `CLAUDE.md` | Update test count, file structure, backfill docs |
| `README.MD` | Update backfill usage examples |

## Implementation Order

### Step 1: Add `filter_reviews_by_cutoff()` helper to `scripts/backfill.py`

New function (insert between `get_all_reviews` and `backfill_movie`, ~line 200):

```python
def filter_reviews_by_cutoff(reviews: list[dict], time_end_cutoff: datetime) -> list[dict]:
    """Filter reviews to only those with estimated_timestamp before cutoff.
    Reviews with estimated_timestamp=None are excluded.
    """
    return [
        r for r in reviews
        if r["estimated_timestamp"] is not None
        and r["estimated_timestamp"] < time_end_cutoff
    ]
```

Extracting this keeps it testable without mocking Selenium/DB.

### Step 2: Add `timedelta` import (line 25)

Change `from datetime import datetime, timezone` to `from datetime import datetime, timedelta, timezone`.

### Step 3: Wire filtering into `backfill_movie()`

- Add `time_end_cutoff: datetime | None = None` parameter
- After scraping completes (after the `all_reviews.extend()` loop, before Phase 2 dedup):
  - If `time_end_cutoff` is not None, call `filter_reviews_by_cutoff()`
  - Log counts: kept, total, excluded
  - Warn if >10% of reviews had None timestamps (operator awareness)

### Step 4: Update `main()` argument parsing

- Add `--time-end` argument (argparse auto-converts to `args.time_end`)
- Validation: `--time-end` requires `--movie` -- exit with error if missing
- Parse date: `datetime.strptime(args.time_end, "%Y-%m-%d")` with try/except ValueError
- Compute cutoff: `datetime(y, m, d, tzinfo=timezone.utc) + timedelta(days=1)` (midnight UTC of next day)
  - So `--time-end 2026-02-21` -> cutoff = `2026-02-22T00:00:00Z`
  - Reviews included when `estimated_timestamp < cutoff` (i.e., on or before Feb 21)

### Step 5: Update confirmation prompt

Show `Time end: 2026-02-21 (exclude reviews after 2026-02-22T00:00:00+00:00)` when flag is set.

### Step 6: Skip health check when `--time-end` is active

Health check compares RT total review count vs DB count -- meaningless when we intentionally filtered. Log why it's skipped.

### Step 7: Write tests (10 new)

**New test class: `TestFilterReviewsByCutoff`** (in `tests/test_rotten_tomatoes.py`):

| Test | Verifies |
|---|---|
| `test_includes_reviews_before_cutoff` | Reviews before cutoff kept |
| `test_excludes_reviews_after_cutoff` | Reviews after cutoff excluded |
| `test_includes_reviews_on_end_date` | Review at 23:59:59 on end date included |
| `test_excludes_reviews_at_exact_cutoff` | Review at exactly cutoff midnight excluded |
| `test_excludes_none_timestamps` | `estimated_timestamp=None` excluded |
| `test_empty_input` | Empty list -> empty list |
| `test_all_excluded` | All after cutoff -> empty list |
| `test_mixed_reviews` | Correct subset from mix of before/after/None |

**Argparse validation tests:**

| Test | Verifies |
|---|---|
| `test_time_end_requires_movie` | Exit with error when `--time-end` used without `--movie` |
| `test_time_end_invalid_format` | Exit with error on bad date string |

**Import mechanism:**

```python
import os
import sys

# Add scripts/ to path so we can import backfill module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from backfill import filter_reviews_by_cutoff
```

Note: importing `backfill` triggers its module-level imports (Selenium, BS4, psycopg2 via `from rotten_tomatoes import ...`). These packages are all available in the test environment, so this is fine — just be aware it's not a lightweight import.

**Argparse validation tests** use `pytest.raises(SystemExit)` with mocked `sys.argv`:

```python
def test_time_end_requires_movie(self):
    with patch("sys.argv", ["backfill.py", "--time-end", "2026-02-21"]):
        with pytest.raises(SystemExit):
            main()

def test_time_end_invalid_format(self):
    with patch("sys.argv", ["backfill.py", "--movie", "test", "--time-end", "bad"]):
        with pytest.raises(SystemExit):
            main()
```

This requires also importing `main` from `backfill`. The `main()` function will exit before any scraping/DB access due to the validation checks at the top.

### Step 8: Create error playbook `plan/errors/backfill.md`

Uses prefix **H** (A-G and M are taken). Must follow exact structure from existing playbooks (chrome.md, neon.md, html_parsing.md).

#### Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| H-1 | Invalid `--time-end` format | Low (user error) | Script exits before scraping |
| H-2 | `--time-end` without `--movie` | Low (user error) | Script exits before scraping |
| H-3 | None-timestamp reviews silently excluded | Medium | Incomplete backfill (missing reviews) |
| H-4 | Off-by-one at cutoff boundary | Low (covered by tests) | Wrong reviews included/excluded |
| H-5 | Selenium returns 0 reviews | Medium | No data for movie |
| H-6 | Duplicate reviews (hash collision) | Very Low | Old score kept, silent |
| H-7 | DB connection fails mid-backfill | Low | OperationalError, partial data |
| H-8 | Health check misleading after filtering | High (when --time-end used) | False alarm on count delta |
| H-9 | Wrong movie slug (typo) | Low (user error) | 0 reviews scraped |
| H-10 | Confirmation prompt bypassed (piped stdin) | Very Low | Unintended execution |

#### Prevention

**H-1 / H-2 (argument validation):**
- Validate `--time-end` with `strptime("%Y-%m-%d")` at parse time; show expected format in error message
- Enforce `--time-end` requires `--movie` before any scraping begins

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

#### Outside Our Control

1. **RT HTML changes**: May cause 0 reviews or broken parsing. Cross-ref html_parsing.md.
2. **Neon outage**: Connection fails. Cross-ref neon.md.
3. **RT rate limiting or blocking**: Selenium may fail to load pages. Cross-ref selenium.md.

#### Detection

| Signal | Failure |
|---|---|
| `error: --time-end requires --movie` or `invalid date format` | H-1, H-2 |
| `Filtered N reviews: kept 0` | H-3 (all None timestamps) or H-4 (cutoff too early) |
| `WARNING: >10% of reviews had None timestamps` | H-3 |
| `Parsed 0 reviews` or `Found 0 review cards` | H-5, H-9 |
| Health check delta > 10 when `--time-end` NOT active | H-6 |
| `OperationalError` or `Connection refused` | H-7 |

#### Diagnosis Decision Tree

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

#### Key Commands

```bash
# Dry run (no DB writes)
DATABASE_URL="..." uv run python scripts/backfill.py --movie <slug> --time-end 2026-02-21 --dry-run

# Real run
DATABASE_URL="..." uv run python scripts/backfill.py --movie <slug> --time-end 2026-02-21

# Verify in DB: count reviews before cutoff date
psql $DATABASE_URL -c "SELECT COUNT(*) FROM reviews WHERE movie_slug = '<slug>' AND estimated_timestamp < '2026-02-22T00:00:00Z'"

# Check what was inserted
psql $DATABASE_URL -c "SELECT reviewer_name, estimated_timestamp, site_timestamp_text FROM reviews WHERE movie_slug = '<slug>' ORDER BY estimated_timestamp DESC LIMIT 20"
```

#### Research

- Python argparse docs: https://docs.python.org/3/library/argparse.html
- datetime.strptime format codes: https://docs.python.org/3/library/datetime.html#strftime-strptime-behavior
- Existing playbooks for cross-references: `plan/errors/selenium.md`, `plan/errors/neon.md`, `plan/errors/html_parsing.md`

### Step 9: Update docs

- `CLAUDE.md`: Update test count (50 -> 60), add `--time-end` to "How to Run" examples, mention in Implementation Status
- `README.MD`: Update backfill usage examples

## Verification

1. `uv run --group dev pytest tests/ -v` -- all 60 tests pass
2. Manual arg validation (no DB needed):
   - `uv run python scripts/backfill.py --time-end 2026-02-21` -> error (no --movie)
   - `uv run python scripts/backfill.py --movie test --time-end bad` -> error (bad format)
   - `uv run python scripts/backfill.py --movie test --time-end 2026-02-21` -> shows confirmation with cutoff
3. Dry run: `DATABASE_URL="..." uv run python scripts/backfill.py --movie <slug> --time-end 2026-02-21 --dry-run`
   - Verify filter log line shows sensible counts
   - Verify health check skipped message
4. Backwards compat: run without `--time-end` to confirm no behavior change
