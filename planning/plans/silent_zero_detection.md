# Plan: Silent-Zero Detection + Config Date Gating

## Context

Jake wants to add movies to `movies.json` earlier in their lifecycle — as soon as the slug exists on RT, not just when reviews start appearing — to capture the first minute-level reviews. The blocker is distinguishing legitimate "no reviews yet" (pre-embargo, critics haven't hit publish) from a silent scraper failure (selector change, bot wall, redirect — all produce 0 cards without raising any errors).

Solution: gate log severity on two optional date fields in the config.

- Past `embargo_lift_date` with 0 reviews → **WARNING** (critics may still be publishing)
- Past `theatrical_release_date` with 0 reviews → **ERROR** (almost certainly broken)
- Neither date passed (or neither set) + 0 reviews → **INFO** (expected / ambiguous, don't alert)

The existing `rt-scraper-errors` log-metric alert gates on severity ≥ ERROR, so only the unambiguous-breakage case pages Jake.

This also closes a pre-existing gap in the main scraper: if the review card selector silently stops matching (as happened with the recent `review-card → review-card-critic` fix), no ERROR fires today. The new release-date gate catches that class of failure for every enabled movie past its release date.

## Files to Modify

| File | Action |
|---|---|
| `movies.json` | Add `embargo_lift_date` (optional) and `theatrical_release_date` (required for enabled entries — soft-enforced) |
| `rotten_tomatoes.py` | Change `load_movie_config()` return type; add `_parse_config_date()`, `_log_no_reviews()`; wire gate into `scrape()` |
| `tests/test_rotten_tomatoes.py` | Update `TestLoadMovieConfig`; add `TestLogNoReviews` and `TestParseConfigDate` |
| `planning/errors/silent_zero.md` | New error playbook (prefix K) |
| `CLAUDE.md` | Update multi-movie config bullet, add gating to design decisions, bump test count |
| `README.MD` | Show new config shape |

## Implementation Order

### Step 1: Change `load_movie_config()` return type (`rotten_tomatoes.py`)

Currently returns `list[str]` — just slugs. The scrape loop needs the date fields, so change to `list[dict]`.

```python
def load_movie_config() -> list[dict]:
    """Load enabled movie entries from movies.json.

    Returns list of dicts: {slug, embargo_lift_date, theatrical_release_date}.
    Date fields are datetime|None (parsed from YYYY-MM-DD).
    """
    config_path = Path(MOVIES_CONFIG_PATH)
    if not config_path.exists():
        log.warning("Config file not found: %s", MOVIES_CONFIG_PATH)
        return []
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        log.error("Failed to read %s: %s", MOVIES_CONFIG_PATH, e)
        return []
    if not isinstance(data, list):
        log.error("Expected a JSON array in %s", MOVIES_CONFIG_PATH)
        return []

    entries = []
    for e in data:
        if not isinstance(e, dict) or "slug" not in e:
            continue
        if not e.get("enabled", True):
            continue
        slug = e["slug"]
        entry = {
            "slug": slug,
            "embargo_lift_date": _parse_config_date(
                e.get("embargo_lift_date"), slug, "embargo_lift_date"
            ),
            "theatrical_release_date": _parse_config_date(
                e.get("theatrical_release_date"), slug, "theatrical_release_date"
            ),
        }
        if entry["theatrical_release_date"] is None:
            log.error(
                "Enabled movie %s has no theatrical_release_date in movies.json. "
                "Silent-zero failures for this movie will not trigger ERROR escalation. "
                "Add the field (YYYY-MM-DD) to close this detection gap.",
                slug,
            )
        elif (entry["embargo_lift_date"] is not None
                and entry["embargo_lift_date"] > entry["theatrical_release_date"]):
            log.warning(
                "Movie %s: embargo_lift_date (%s) is after theatrical_release_date (%s). "
                "Likely a typo — WARNING tier will be unreachable for this movie.",
                slug,
                entry["embargo_lift_date"].date(),
                entry["theatrical_release_date"].date(),
            )
        entries.append(entry)
    return entries


def _parse_config_date(value, slug: str, field: str) -> datetime | None:
    """Parse YYYY-MM-DD as midnight Eastern Time, return UTC datetime.

    Log WARNING on bad format.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        log.warning(
            "Invalid %s for movie %s (expected YYYY-MM-DD string, got %r). Treating as None.",
            field, slug, type(value).__name__,
        )
        return None
    try:
        return (
            datetime.strptime(value, "%Y-%m-%d")
            .replace(tzinfo=ZoneInfo("America/New_York"))
            .astimezone(timezone.utc)
        )
    except ValueError:
        log.warning(
            "Invalid %s %r for movie %s (expected YYYY-MM-DD). Treating as None.",
            field, value, slug,
        )
        return None
```

Add import at top of `rotten_tomatoes.py`:

```python
from zoneinfo import ZoneInfo
```

**Semantics decision:** Dates are anchored to **midnight Eastern Time** (04:00 UTC in EDT, 05:00 UTC in EST). Rationale: critics' reviews typically start landing many hours before midnight on the theatrical release date, so the choice of timezone is largely cosmetic — ET gives slightly earlier gate activation than PT (by 3 hours). Midnight itself is not a DST-transition hour, so `zoneinfo` handles fall-back / spring-forward correctly without edge cases.

**Enforcement** (inlined above): Log ERROR at config load for any enabled entry missing `theatrical_release_date`. Entry is still included in the returned list (scraping proceeds) so one bad config line doesn't block every other movie. Addresses K-4.

Only fires for enabled entries (the `not enabled` branch `continue`s earlier in the loop). Disabled entries don't scrape, so missing date is irrelevant. The check does not apply to the `--movie` CLI override path, which bypasses `load_movie_config()` entirely — ad-hoc invocations stay exempt.

**External callers verified safe:** `scripts/backfill.py` imports only helper utilities from `rotten_tomatoes` (not `load_movie_config` or `scrape`). `scripts/fix_top_critic.py` imports only `get_db_connection`. The return-type change and `scrape()` signature change affect only the `__main__` block within `rotten_tomatoes.py` and its tests.

### Step 2: Add `_log_no_reviews()` helper

```python
def _log_no_reviews(
    movie_slug: str,
    embargo_lift: datetime | None,
    theatrical_release: datetime | None,
    now: datetime,
) -> None:
    """Log appropriate severity when a movie has 0 reviews scraped.

    - Past theatrical_release: ERROR (scraper likely broken)
    - Past embargo_lift (not yet past release): WARNING (critics may still be publishing)
    - Before embargo, or no dates configured: INFO (legitimately no reviews yet)
    """
    if theatrical_release is not None and now >= theatrical_release:
        log.error(
            "No reviews found for %s after theatrical release (%s). "
            "Likely scraper issue — selector change, bot wall, or redirect.",
            movie_slug, theatrical_release.date(),
        )
    elif embargo_lift is not None and now >= embargo_lift:
        log.warning(
            "No reviews found for %s after embargo lift (%s). "
            "May be legitimate (critics still publishing) or early sign of scraper issue.",
            movie_slug, embargo_lift.date(),
        )
    else:
        log.info(
            "No reviews found for %s (pre-embargo or no dates configured).",
            movie_slug,
        )
```

### Step 3: Wire gate into `scrape()`

Change `scrape()` to accept a config dict. Check total review count after Phase 1 (scraping into memory, before DB connect). If 0, call `_log_no_reviews()` and return early — no DB connect needed (saves a Neon cold start per silent-zero movie).

```python
def scrape(movie_entry: dict) -> None:
    movie_slug = movie_entry["slug"]
    log.info("=== Scraping: %s ===", movie_slug)

    # Phase 1: Scrape into memory
    all_reviews = []
    total_count = 0
    for critic_filter in CRITIC_FILTERS:
        reviews = get_reviews(movie_slug, critic_filter)
        all_reviews.append((critic_filter, reviews))
        total_count += len(reviews)

    # No-reviews gate: log appropriate severity and skip DB
    if total_count == 0:
        _log_no_reviews(
            movie_slug,
            movie_entry.get("embargo_lift_date"),
            movie_entry.get("theatrical_release_date"),
            datetime.now(timezone.utc),
        )
        return

    # Phase 2: existing DB insert logic (unchanged below this line)
    conn = get_db_connection()
    ...
```

**Why check total across both filters, not per-filter?** Every top-critic review also appears in all-critics. A top-critics-only failure still leaves us with all the data (minus the `top_critic=True` flag on ~5 reviews, which is post-hoc fixable via `scripts/fix_top_critic.py`). Gating per-filter would false-positive on legit movies where top-critics is empty.

### Step 4: Update entry point

```python
if args.movie:
    movie_entries = [{
        "slug": args.movie,
        "embargo_lift_date": None,
        "theatrical_release_date": None,
    }]
else:
    movie_entries = load_movie_config()
    if not movie_entries:
        log.warning("No movies to scrape. Check %s or use --movie.", MOVIES_CONFIG_PATH)
        raise SystemExit(0)

mode = "manual" if args.movie else "scheduled"
slugs = [e["slug"] for e in movie_entries]
log.info("=== Run started: mode=%s, movies=%s ===", mode, slugs)

for entry in movie_entries:
    scrape(entry)
```

**CLI override behavior:** `--movie <slug>` runs with no dates set, so silent-zero always logs INFO. This is correct for ad-hoc investigations — we don't want a manual `--movie` invocation to trigger an alert.

### Step 5: Update `movies.json`

Existing entries stay valid (both date fields are optional). When adding a new movie pre-review-lifecycle:

```json
{
  "slug": "dune_part_three",
  "enabled": true,
  "embargo_lift_date": "2026-11-15",
  "theatrical_release_date": "2026-12-18"
}
```

**Migration for currently-enabled entries:** `lee_cronins_the_mummy` and `mother_mary` must have `theatrical_release_date` added before this change deploys, or the config-load ERROR will fire on every scrape cycle until fixed. Disabled entries (`the_odyssey_2026`, `avengers_doomsday`, `dune_part_three`, `you_me_and_tuscany`) don't need dates while they remain disabled — but they should get dates before flipping to enabled.

Per design: `theatrical_release_date` is **required for enabled movies**. Enforcement is soft — missing it logs ERROR at config load but the entry is still scraped, so one typo doesn't cascade into skipping every movie. `embargo_lift_date` remains fully optional (early-detection bonus).

### Step 6: Tests

**Update `TestLoadMovieConfig`** (7 existing tests, ~5 assertion updates):
- All existing tests assert on `list[str]`. Update to `list[dict]` shape: `[{"slug": "...", "embargo_lift_date": None, "theatrical_release_date": None}, ...]`.
- Existing coverage (enabled filtering, missing file, invalid JSON, non-array, entries without slug, default enabled=true) is preserved.

**New tests in `TestLoadMovieConfig`** (7 new):

| Test | Verifies |
|---|---|
| `test_parses_valid_embargo_and_release_dates` | Both dates present and valid → parsed UTC datetimes anchored to midnight ET |
| `test_missing_embargo_date_is_none_no_log` | Absent embargo → None, no ERROR (embargo is optional) |
| `test_missing_release_date_for_enabled_logs_error` | Enabled entry missing release_date → None + ERROR logged; entry still returned |
| `test_missing_release_date_for_disabled_no_log` | Disabled entry missing release_date → entry excluded, no ERROR (not scraping anyway) |
| `test_embargo_after_release_logs_warning` | Both dates set with embargo > release → WARNING logged; entry still returned |
| `test_invalid_date_format_is_none_with_warning` | Bad date string → None + WARNING logged |
| `test_non_string_date_is_none_with_warning` | Non-string (e.g., integer) → None + WARNING logged |

**New class `TestParseConfigDate`** (4 tests):

| Test | Verifies |
|---|---|
| `test_summer_date_gives_edt_offset` | `"2026-07-15"` → `2026-07-15T04:00:00Z` (midnight EDT) |
| `test_winter_date_gives_est_offset` | `"2026-01-15"` → `2026-01-15T05:00:00Z` (midnight EST) |
| `test_none_input_returns_none` | `None` → `None`, no log |
| `test_invalid_format_returns_none_with_warning` | `"not a date"` → `None` + WARNING |

Two dates in different DST seasons is sufficient to verify the zoneinfo path. Midnight itself is never a DST-transition hour (US transitions happen at 02:00 local), so no ambiguity-at-midnight test is needed.

**New class `TestLogNoReviews`** (6 tests, using `caplog`):

| Test | Verifies |
|---|---|
| `test_past_release_logs_error` | Both dates set, now > release → ERROR logged |
| `test_past_embargo_not_release_logs_warning` | now between embargo and release → WARNING, no ERROR |
| `test_pre_embargo_logs_info` | now < embargo → INFO only |
| `test_no_dates_set_logs_info` | Both None → INFO only |
| `test_only_release_past_logs_error` | embargo=None, release passed → ERROR |
| `test_only_embargo_past_logs_warning` | release=None, embargo passed → WARNING (documented: no ERROR escalation without release date) |

**Test count:** 87 → ~104 (17 new tests + existing `TestLoadMovieConfig` assertion updates for the new return-type shape; non-empty-list assertions in 4 of the 7 existing tests need updating, empty-list assertions stay unchanged).

### Step 7: Create `planning/errors/silent_zero.md` (prefix K)

See Error Playbook section below.

### Step 8: Update docs

- **CLAUDE.md:**
  - Under "Resolved Design Decisions," add bullet for silent-zero gate
  - Update "Multi-movie config" implementation status bullet with new fields
  - Update test count (87 → 104)
  - Add `silent_zero.md` to file structure
- **README.MD:**
  - Update movies.json example to show the new config shape

## Error Playbook: Silent-Zero Detection (Prefix K)

To be created at `planning/errors/silent_zero.md`. Content sketched below.

### What We Know

- The main scraper returns `[]` gracefully in multiple silent-failure modes — selector change with no matches, RT bot-wall serving a clean page with no cards, RT redirect to a different page, movie removed from RT. None of these paths log ERROR today.
- The new config date fields (`embargo_lift_date`, `theatrical_release_date`) are the distinguishing signal: they tell us *when* zero reviews stops being plausible.
- Existing ERROR alerting (`rt-scraper-errors` log-metric, gates on severity ≥ ERROR) inherits the gate — WARNING-tier silent zeros do not page.

### Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| K-1 | Review card selector silently stops matching (RT HTML change) | Medium (happened with `review-card → review-card-critic`) | Silent data loss until gate fires |
| K-2 | RT serves bot-wall / captcha page that parses to 0 cards | Low-Medium | Same as K-1 |
| K-3 | RT redirects movie page (slug renamed / movie removed) | Low | 0 reviews for that movie indefinitely |
| K-4 | `theatrical_release_date` missing for enabled movie | Low (user error) | ERROR logged every scrape cycle at config load until fixed |
| K-5 | Invalid date format in `movies.json` (not YYYY-MM-DD) | Low (user error) | WARNING at load, field treated as None (degrades silently) |
| K-6 | `embargo_lift_date` > `theatrical_release_date` (typo) | Very Low | WARNING logged at config load; WARNING tier unreachable until corrected |
| K-7 | ERROR fires for legit 0-review movie past release (very obscure film) | Very Low | False alert — operator confusion |
| K-8 | Trickle-review movie causes repeated WARNING spam between embargo and release | Low | Log noise only (below ERROR threshold, no page) |
| K-9 | Scraper broken but all enabled movies are pre-embargo | Very Low | Entire breakage invisible until first movie passes embargo |

### Prevention

**K-1 / K-2 / K-3 (the core failure modes the gate catches):**
- Release-date ERROR fires on next scrape after release date passes with 0 reviews
- Embargo-date WARNING fires on next scrape after embargo lift with 0 reviews (earlier, lower-severity signal)
- For diagnosis, cross-ref: `html_parsing.md` (selector changes), `anti_blocking.md` J-2 (bot wall), `selenium.md` (page load)

**K-4 (missing release date):**
- Enforced by `load_movie_config()`: any enabled entry missing `theatrical_release_date` logs ERROR at config load.
- Entry is still returned and scraped — soft enforcement, so one bad config entry doesn't block every other movie.
- ERROR at config load fires every scrape cycle until fixed, which is the right nudge (it's an alertable condition, not log noise).
- Fix: add `"theatrical_release_date": "YYYY-MM-DD"` to the movies.json entry and the error clears on next run.

**K-5 (invalid date format):**
- `_parse_config_date()` logs WARNING and treats invalid values as None.
- Config load does not fail — scraping continues, gate just degrades to the "no dates set" branch (INFO-only).

**K-6 (date ordering):**
- Detected at config load: if an enabled entry has `embargo_lift_date > theatrical_release_date`, log WARNING with both values so the typo is easy to spot.
- Entry is still returned — silent-zero gate still functions correctly via the ERROR tier, but the WARNING tier is unreachable for that movie until the typo is fixed.
- WARNING severity is below the `rt-scraper-errors` alert threshold, so this doesn't page Jake — it just surfaces in logs for the next time he reviews config hygiene.

**K-7 (false ERROR on obscure film):**
- Use `--movie <slug>` to manually re-verify.
- If legitimate, disable the movie (`enabled: false`). Do not clear `theatrical_release_date` — that triggers the K-4 enforcement ERROR instead, substituting one false alert for another.

**K-8 (trickle-review WARNING spam):**
- WARNING doesn't alert, so cost is log noise only.
- If it becomes an operational problem, consider a future `expected_review_date` field or a suppress-until mechanism. Out of scope for this plan.

**K-9 (all movies pre-embargo):**
- Rare. Only happens if Jake is tracking a set of upcoming releases and none have hit embargo yet.
- Mitigation: keep at least one already-released movie enabled as a canary, OR rely on other observability (Cloud Run job success/failure alerting, which is independent).

### Outside Our Control

1. **RT HTML changes:** The point of the gate is to detect these. Cross-ref `html_parsing.md`.
2. **RT bot detection:** Cross-ref `anti_blocking.md`.
3. **Critics' publishing schedule:** We assume reviews appear shortly after embargo. If a movie genuinely has zero reviews for days after release, K-7 applies.
4. **Date accuracy in config:** Jake maintains `movies.json` manually. Typos or missing dates are user-space problems.

### Detection

| Signal | Failure |
|---|---|
| `No reviews found for <slug> after theatrical release (YYYY-MM-DD)` | K-1, K-2, K-3 (or K-7 if legit) |
| `No reviews found for <slug> after embargo lift (YYYY-MM-DD)` | K-1, K-2, K-3 early signal (WARNING only) |
| `No reviews found for <slug> (pre-embargo or no dates configured)` | Expected/legit (no K-4 here — enforcement catches K-4 at config load) |
| `Enabled movie <slug> has no theatrical_release_date in movies.json` | K-4 (fix: add the field) |
| `Movie <slug>: embargo_lift_date (...) is after theatrical_release_date (...)` | K-6 (swap the two dates in movies.json) |
| `Invalid <field> <value> for movie <slug>, treating as None.` | K-5 |
| `rt-scraper-errors` alert fires for silent-zero | K-1/K-2/K-3 — check alert email for movie slug |
| `rt-scraper-errors` alert fires with "has no theatrical_release_date" | K-4 — config hygiene |

### Diagnosis Decision Tree

```
"No reviews found for <slug>" logged
|
+-> What severity?
    |
    +-> INFO
    |   +-> Pre-embargo, or no dates configured.
    |   +-> Expected for new movies awaiting first review.
    |   +-> If dates SHOULD be set for this slug, update movies.json.
    |
    +-> WARNING
    |   +-> Past embargo lift, not past release.
    |   +-> Legitimate "critics still publishing" window.
    |   +-> Action: watch next 1-2 scrape cycles.
    |       If still 0 at next cycle:
    |         - Open RT page in browser (incognito):
    |             https://www.rottentomatoes.com/m/<slug>/reviews/all-critics
    |         - Reviews render in incognito?
    |             YES → scraper broken. Cross-ref html_parsing.md + anti_blocking.md.
    |             NO  → likely legit pre-release trickle, or RT-side issue.
    |                   If release date is imminent, wait and re-check post-release.
    |
    +-> ERROR
        +-> Past theatrical release with 0 reviews. Almost certainly broken.
        +-> Step 1: Open RT in incognito. Reviews rendering?
        |   NO  → IP or bot-wall issue. Cross-ref anti_blocking.md J-2.
        |   YES → continue to Step 2.
        +-> Step 2: `curl -s https://www.rottentomatoes.com/m/<slug>/reviews/all-critics | grep -c review-card-critic`
        |   0 matches → selector changed. Check/update SELECTORS dict in rotten_tomatoes.py.
        |              Cross-ref html_parsing.md.
        |   >0 matches → Selenium-level issue (page load timeout, click interception, etc.).
        |                Cross-ref selenium.md.
        +-> Step 3: If movie was removed from RT, set `enabled: false` in movies.json.
        +-> Step 4: If movie is genuinely obscure and has no reviews post-release (K-7),
                     remove theatrical_release_date to downgrade the gate.
```

### Key Commands

```bash
# Open the movie page directly to check if reviews render
open "https://www.rottentomatoes.com/m/<slug>/reviews/all-critics"

# Re-run just this movie
DATABASE_URL="..." uv run python rotten_tomatoes.py --movie <slug>

# Check raw HTML for review card markers
curl -s "https://www.rottentomatoes.com/m/<slug>/reviews/all-critics" | grep -c review-card-critic

# Inspect current selectors
grep -A 12 "^SELECTORS" rotten_tomatoes.py

# Verify date fields are parsed correctly for a slug
python -c "
from rotten_tomatoes import load_movie_config
for e in load_movie_config():
    if e['slug'] == '<slug>':
        print(e)
"

# Sanity check: what was the most recent scrape across all movies?
psql $DATABASE_URL -c "SELECT movie_slug, MAX(scrape_time) FROM reviews GROUP BY movie_slug ORDER BY 2 DESC LIMIT 10"
```

### Cross-References

- `planning/errors/html_parsing.md` (C-*: selector/parsing failures — primary root cause for ERROR tier)
- `planning/errors/anti_blocking.md` (J-1, J-2: bot detection suppressing reviews)
- `planning/errors/selenium.md` (B-*: page load & interaction failures)
- `planning/errors/monitoring.md` (I-*: alert misfires, log-metric filtering — e.g., if WARNING starts firing as ERROR due to formatter bug)

## Verification

1. `uv run --group dev pytest tests/ -v` — all ~104 tests pass
2. **Local dry-run — past release:** Add test movie with past `theatrical_release_date` to a throwaway movies.json, run `uv run python rotten_tomatoes.py`. If the test movie genuinely has 0 reviews on RT, the gate returns before DB connect (no `DATABASE_URL` needed). If it has reviews, the script will try to connect — in that case, set a throwaway `DATABASE_URL` or pick a slug with confirmed 0 reviews. Verify ERROR log line fires.
3. **Local dry-run — pre-embargo:** Future `embargo_lift_date`, verify INFO only (no WARNING/ERROR).
4. **Local dry-run — between dates:** Past embargo, future release, verify WARNING only.
5. **Local dry-run — no dates:** Omit both fields, verify INFO only (backwards compat for existing entries).
6. **Backwards compat:** Run against current `movies.json` (disabled movies + the 2 currently-enabled slugs). No entries have the new fields yet — verify all enabled movies process as before and any 0-review cases log INFO.
7. **Deploy and monitor:** After merging, watch the next scheduled Cloud Run execution in Cloud Logging. Confirm expected log lines for each enabled movie.

## What This Does NOT Cover

- **Hard-exit enforcement of `theatrical_release_date`.** Enforcement is intentionally soft — ERROR log, not `SystemExit`. A hard exit would let one bad entry block every other movie from scraping, which is worse than the failure mode being enforced against.
- **Structural canary (checking non-card HTML elements on the page).** Decided against — adds selector maintenance burden, and the release-date gate closes the same gap with strictly less surface.
- **RT advertised review count comparison.** RT shows dashes (not "0 Reviews") when empty; not a clean signal. Defer unless a stable element surfaces.
- **Suppression of repeated WARNINGs** during legit extended trickle windows post-embargo (K-8). Logs-only cost, not an alert. Revisit if noisy.
- **Auto-disabling movies that trigger ERROR for N consecutive runs.** Would mask real breakage if the root cause is global. Manual disable via `enabled: false` remains the escape hatch.
- **Per-filter silent-zero detection (e.g., top-critics returns 0 but all-critics succeeds).** Intentional — data is still captured via all-critics, and gating per-filter would false-positive on movies with legitimately-empty top-critics. `scripts/fix_top_critic.py` exists for post-hoc correction if needed.
