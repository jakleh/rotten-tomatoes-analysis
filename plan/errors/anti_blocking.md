# Error Playbook: Anti-Blocking & Rate Limiting (Prefix J)

## What We Know

- RT uses **cookie-based bot detection**. When triggered, the page loads normally but JS suppresses review rendering (grey box). Confirmed firsthand: clearing cookies restored reviews immediately.
- **Headless Selenium starts with a clean cookie jar** per `_build_driver()` call. Bot-flag cookies from previous sessions don't carry over. This makes cross-session cookie flags a non-issue.
- **Within a single session**, RT could still set a bot-flag cookie mid-scrape (e.g., after 15+ "Load More" clicks). This would cause a partial scrape — some reviews captured, the rest suppressed. The session persists through all pagination clicks for one critic-filter pass.
- **IP-based or fingerprint-based detection** cannot be ruled out. The cookie fix worked in the observed case, but that doesn't exclude other mechanisms.

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| J-1 | Bot flag set mid-session during heavy pagination | Medium | Partial scrape — reviews stop loading partway through |
| J-2 | IP flagged across sessions (if RT uses IP-based detection) | Low-Medium (unverified) | All scrapes return 0 reviews until flag expires |
| J-3 | User-Agent string becomes stale (Chrome version outdated) | Low (months to matter) | May contribute to detection |
| J-4 | Click timing too uniform or too fast, triggers detection | Low | Bot flag set, partial or zero reviews |
| J-5 | Inter-movie delay insufficient; flagged across movies | Low-Medium | Later movies in batch fail |
| J-6 | `backfill_movies.csv` missing or malformed | Low (user error) | Script exits with error |
| J-7 | `backfill_movies.csv` contains slug not on RT | Low (user error) | 0 reviews scraped for that slug |
| J-8 | `--all` run with large CSV (100+ movies) | Low (requires explicit flag) | Very long run, increased chance of triggering detection |

---

## Prevention

**J-1 (mid-session bot flag — primary concern):**
- Randomized click timing (`uniform(2, 5)` after clicks) is the main mitigation
- Stall detection catches the symptom: card count stops increasing, bails after 2 no-change clicks
- But stall detection **cannot distinguish** "all pages loaded" from "bot flag activated" — both look the same
- Best diagnostic after the fact: health check comparing RT total vs. DB count. Delta > 10 suggests a partial scrape
- Note: health check only runs when `--time-end` is not active. With `--time-end`, partial scrape detection is unavailable — the count comparison would be meaningless due to filtering
- If suspected: widen click timing to `uniform(3, 8)` and re-run the affected movie

**J-2 (IP-based detection — unverified):**
- No programmatic mitigation if RT uses IP-level blocking
- Quick check: open RT in a real browser (incognito). If reviews render, the issue isn't IP-based.
- If reviews don't render in incognito either: wait 1-2 hours or switch network

**J-3 (stale User-Agent):**
- Current UA: `Chrome/134.0.0.0` (set 2026-04)
- To update: google "what is my user agent", update `_build_driver()` in `rotten_tomatoes.py` and health check in `scripts/backfill.py`
- Low urgency — becomes implausible after ~6 months

**J-4 (click timing):**
- Current: `uniform(2, 5)` after click, `uniform(0.5, 1.5)` before click
- If detection suspected: widen to `uniform(3, 8)` after click
- If backfill is too slow and detection is not occurring: narrow to `uniform(1.5, 3)`

**J-5 (inter-movie delay):**
- Current: 30s between movies
- If movies fail mid-batch: increase to 60s, or switch to manual `--movie` runs with longer gaps

**J-6 / J-7 (CSV issues):**
- Missing CSV: `load_backfill_config()` logs error and returns `[]`, script exits
- Malformed CSV (no `slug` header): script exits with "No movies found"
- Bad slug: confirmation prompt shows slug before scraping; 0 reviews = likely typo
- Cross-ref backfill.md H-9

**J-8 (large batch):**
- `--all` is explicit opt-in (not the default)
- Confirmation prompt shows full movie list before proceeding
- Ctrl-C at any point; completed movies are already committed

---

## Outside Our Control

1. **RT tightens detection**: We can only observe effects and adjust timing. If RT adds aggressive detection (e.g., Cloudflare), scraping may become impractical.
2. **Detection thresholds are unknown**: We don't know what patterns or volumes trigger the flag. Current timing is conservative but not guaranteed safe.

---

## Detection

| Signal | Failure |
|---|---|
| `Found 0 review cards` for ONE movie, others succeed | J-7 (bad slug) or transient issue |
| `Found 0 review cards` for ALL movies | J-2 (IP/fingerprint flagged) |
| `Found 0 review cards` starting mid-batch (first N succeed, rest fail) | J-5 (flagged mid-batch) |
| Card count lower than expected but nonzero | J-1 (bot flag mid-session, partial data) |
| `Load More stalled 2x` much earlier than expected | J-1 or J-4 (detection stopped page loads mid-session) |
| Health check delta > 10 | J-1 (partial scrape) |
| `No movies found in backfill_movies.csv` | J-6 (missing/empty/malformed CSV) |
| Page source snippet shows full HTML but no review cards | Cookie/JS suppression within session (J-1) |

---

## Diagnosis Decision Tree

```
Fewer reviews than expected (0 or partial)
|
+-> How many movies affected?
|   |
|   +-> ONE movie, others succeed
|   |   +-> Is the slug correct? Check RT URL in a browser.
|   |   +-> Does the movie have reviews on RT? (Some have very few.)
|   |   +-> Re-run with --movie <slug>. If it works, transient issue.
|   |
|   +-> ALL movies fail (0 reviews each)
|   |   +-> Open RT in incognito browser.
|   |   |   +-> Reviews don't render (grey box)?
|   |   |   |   +-> IP may be flagged. Wait 1-2 hours or switch network.
|   |   |   +-> Reviews render normally?
|   |   |       +-> Not IP-based. Selenium's clean cookie jar should
|   |   |           avoid cookie flags. Check if HTML structure changed.
|   |   |           Cross-ref selenium.md / html_parsing.md.
|   |
|   +-> FIRST N succeed, THEN failures start
|       +-> Flagged mid-batch after sustained activity.
|       +-> Note which movie failed first.
|       +-> Wait, then resume from failed movie with --movie.
|       +-> Consider increasing inter-movie delay to 60s.
|
+-> Reviews returned but count seems low (health check delta > 10)?
    +-> Likely J-1: bot flag set mid-session during pagination.
    +-> Re-run that movie with --movie. If full count now, was transient.
    +-> If still low: widen click timing to uniform(3, 8) and re-run.
    +-> If still low after widened timing: RT may be limiting page loads
        server-side. Manual verification needed.
```

---

## Key Commands

```bash
# Always start with a single movie dry run
DATABASE_URL="..." uv run python scripts/backfill.py --movie <slug> --dry-run

# Single movie for real
DATABASE_URL="..." uv run python scripts/backfill.py --movie <slug>

# Batch from CSV
DATABASE_URL="..." uv run python scripts/backfill.py --all

# Resume after mid-batch failure
DATABASE_URL="..." uv run python scripts/backfill.py --movie <failed_slug>

# Quick browser check: are you being blocked?
# Open in incognito: https://www.rottentomatoes.com/m/<slug>/reviews

# Check what's in the CSV
cat scripts/backfill_movies.csv
```

---

## Cross-References

- `plan/errors/selenium.md` (B-4: bot detection, B-5: rate limiting)
- `plan/errors/backfill.md` (H-5: 0 reviews, H-9: bad slug)
