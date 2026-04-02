# Plan: Backfill Anti-Blocking Mitigations + CSV Config

## Context

The backfill script runs locally from a single IP, scraping ALL pages of reviews per movie (potentially 20+ "Load More" clicks each), two passes per movie (top-critics + all-critics), and multiple movies back-to-back. This volume from one IP in a short window risks triggering RT's rate limiting or bot detection. Additionally, the backfill script was using `movies.json` (active bets) as its movie list, but "enabled for scraping" and "needs backfilling" are different concepts — past Kalshi bets vs. open bets.

## Goals

1. Separate backfill movie config from active scraper config
2. Make the CLI safer by default (require explicit intent for batch runs)
3. Reduce bot detection risk via User-Agent, randomized timing, and inter-movie delays
4. Document failure modes and create error playbook

## Files to Modify

| File | Action |
|---|---|
| `scripts/backfill.py` | New CSV reader, `--movie`/`--all` mutual exclusion, randomized sleeps, inter-movie delay |
| `scripts/backfill_movies.csv` | New file (gitignored) — one slug per row |
| `rotten_tomatoes.py` | Add User-Agent to `_build_driver()` |
| `tests/test_rotten_tomatoes.py` | Tests for CSV loading, new argparse rules |
| `plan/errors/anti_blocking.md` | New error playbook (prefix J) |
| `plan/errors/backfill.md` | Update for new CLI structure |
| `CLAUDE.md` | Update test count, file structure, backfill docs |
| `README.MD` | Update backfill usage examples |

## Implementation Order

### Step 1: Create `scripts/backfill_movies.csv`

Simple CSV with `slug` header. One slug per line. Already gitignored by `*.csv` rule.

```
slug
project_hail_mary
thunderbolts
```

### Step 2: Add `load_backfill_config()` to `scripts/backfill.py`

```python
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
```

Remove `load_movie_config` import — backfill no longer reads `movies.json`.

### Step 3: Replace argparse with `--movie` / `--all` mutual exclusion

```python
group = parser.add_mutually_exclusive_group(required=True)
group.add_argument("--movie", help="Single movie slug to backfill")
group.add_argument("--all", action="store_true", help="Backfill all movies from backfill_movies.csv")
```

- Neither flag = argparse error (safe default)
- Both flags = argparse error (mutual exclusion)
- `--time-end` still requires `--movie` (unchanged)

### Step 4: Add User-Agent to `_build_driver()` in `rotten_tomatoes.py`

```python
options.add_argument(
    "user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
)
```

This applies to both the main scraper and backfill (shared function). Matches a real Chrome-on-Mac UA string.

### Step 5: Randomize inter-click sleeps in backfill

Replace fixed `time.sleep(1)` / `time.sleep(3)` around "Load More" clicks with:
- `time.sleep(random.uniform(0.5, 1.5))` before click (scroll settle)
- `time.sleep(random.uniform(2, 5))` after click (page load)

Breaks the predictable timing pattern that fingerprinting systems look for.

### Step 6: Add 30s inter-movie delay in backfill loop

```python
for i, slug in enumerate(slugs):
    # ... scrape and insert ...
    if i < len(slugs) - 1:
        log.info("Waiting 30s before next movie...")
        time.sleep(30)
```

Only applies when multiple movies are being backfilled (i.e., `--all`). Skipped after last movie.

### Step 7: Tests

New tests:
- `TestLoadBackfillConfig`: CSV parsing, blank lines, whitespace, missing file, empty CSV (5 tests)
- `TestBackfillArgparse`: neither flag exits, both flags exits (2 tests)
- Update existing `test_time_end_requires_movie` to use `--all` instead of bare invocation

### Step 8: Error playbook + documentation

- Create `plan/errors/anti_blocking.md` (prefix J)
- Update `plan/errors/backfill.md` for new CLI structure
- Update CLAUDE.md and README.MD

## Risk Assessment

| Mitigation | Prevents | Effort |
|---|---|---|
| User-Agent | Trivial headless Chrome fingerprinting | One line in `_build_driver()` |
| Randomized click timing | Timing-based bot pattern detection | Two-line change in backfill |
| 30s inter-movie delay | Volume spike from single IP | Three-line change in backfill loop |
| CSV config separation | Accidental batch run of active movies | New function + CLI changes |
| `--movie`/`--all` exclusion | Accidental full backfill | Argparse mutual exclusion group |

## What This Does NOT Cover

- **Proxy rotation**: Overkill for RT's current detection level. Revisit if blocking becomes an issue.
- **Cookie management**: Headless Chrome starts fresh each `_build_driver()` call. No persistent session needed.
- **CAPTCHA solving**: Out of scope. If RT adds CAPTCHAs, manual intervention required.
- **Main scraper timing**: Cloud Run IPs rotate naturally and scrape volume is low. No changes needed there.
