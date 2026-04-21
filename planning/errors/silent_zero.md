# Error Playbook: Silent-Zero Detection (Prefix K)

## What We Know

- The main scraper returns `[]` gracefully in multiple silent-failure modes — selector change with no matches, RT bot-wall serving a clean page with no cards, RT redirect to a different page, movie removed from RT. None of these paths log ERROR today.
- Two optional config fields in `movies.json` (`embargo_lift_date`, `theatrical_release_date`) give us the distinguishing signal: they tell us *when* zero reviews stops being plausible.
- Severity gate in `scrape()`:
  - Past `theatrical_release_date` + 0 reviews → **ERROR** (almost certainly broken)
  - Past `embargo_lift_date` + 0 reviews (not yet past release) → **WARNING** (critics may still be publishing)
  - Before embargo, or no dates configured → **INFO** (legitimately no reviews yet)
- Existing `rt-scraper-errors` log-metric alert gates on severity ≥ ERROR, so WARNING-tier silent zeros do not page.
- Dates are anchored to midnight Eastern Time (via `zoneinfo.ZoneInfo("America/New_York")`). Critics' reviews typically land many hours before midnight ET on release day, so the choice of timezone is largely cosmetic.

---

## Failure Modes

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

---

## Prevention

**K-1 / K-2 / K-3 (the core failure modes the gate catches):**
- Release-date ERROR fires on next scrape after release date passes with 0 reviews.
- Embargo-date WARNING fires on next scrape after embargo lift with 0 reviews (earlier, lower-severity signal).
- For diagnosis, cross-ref: `html_parsing.md` (selector changes, C-*), `anti_blocking.md` J-1/J-2 (bot detection, IP blocking), `selenium.md` B-* (page load failures).

**K-4 (missing release date):**
- Enforced by `load_movie_config()`: any enabled entry missing `theatrical_release_date` logs ERROR at config load.
- Entry is still returned and scraped — soft enforcement, so one bad config entry doesn't block every other movie.
- ERROR at config load fires every scrape cycle until fixed. The existing `RT Scraper ERROR Alert` policy has 10-min alignment, so this doesn't produce 50-minute email spam — one alert email when the ERROR starts, one when it resolves.
- Fix: add `"theatrical_release_date": "YYYY-MM-DD"` to the `movies.json` entry and the error clears on next run.

**K-5 (invalid date format):**
- `_parse_config_date()` logs WARNING and treats invalid values as None.
- Config load does not fail — scraping continues, gate just degrades to the "no dates set" branch (INFO-only for that movie).
- Non-string values (e.g., an integer that slipped in) are also caught and logged with type info.

**K-6 (date ordering):**
- Detected at config load: if an enabled entry has `embargo_lift_date > theatrical_release_date`, log WARNING with both values so the typo is easy to spot.
- Entry is still returned — silent-zero gate still functions correctly via the ERROR tier, but the WARNING tier is unreachable for that movie until the typo is fixed.
- WARNING severity is below the `rt-scraper-errors` alert threshold, so this doesn't page — it just surfaces in logs for the next time config hygiene is reviewed.

**K-7 (false ERROR on obscure film):**
- Use `--movie <slug>` to manually re-verify in a local run.
- If legitimate, disable the movie (`enabled: false`). Do not clear `theatrical_release_date` — that triggers the K-4 enforcement ERROR instead, substituting one false alert for another.

**K-8 (trickle-review WARNING spam):**
- WARNING doesn't alert, so cost is log noise only.
- If it becomes an operational problem, consider a future `expected_review_date` field or a suppress-until mechanism. Out of scope for this playbook.

**K-9 (all movies pre-embargo):**
- Rare. Only happens if the enabled set is all upcoming releases and none have hit embargo yet.
- Mitigation: keep at least one already-released movie enabled as a canary, OR rely on other observability (Cloud Run job success/failure alerting, which is independent of review counts).

---

## Outside Our Control

1. **RT HTML changes:** The whole point of the gate is to *detect* these. Cross-ref `html_parsing.md`.
2. **RT bot detection / IP blocking:** Cross-ref `anti_blocking.md` (J-1, J-2).
3. **Critics' publishing schedule:** We assume reviews appear shortly after embargo lift. If a movie genuinely has zero reviews for days after release, K-7 applies.
4. **Date accuracy in config:** `movies.json` is maintained manually. Typos or missing dates are user-space problems, detected at load time by K-4 / K-5 / K-6.

---

## Detection

| Signal | Failure |
|---|---|
| `No reviews found for <slug> after theatrical release (YYYY-MM-DD)` | K-1, K-2, K-3 (or K-7 if legit) |
| `No reviews found for <slug> after embargo lift (YYYY-MM-DD)` | K-1, K-2, K-3 early signal (WARNING only) |
| `No reviews found for <slug> (pre-embargo or no dates configured)` | Expected/legit (no K-4 here — enforcement catches K-4 at config load) |
| `Enabled movie <slug> has no theatrical_release_date in movies.json` | K-4 (fix: add the field) |
| `Movie <slug>: embargo_lift_date (...) is after theatrical_release_date (...)` | K-6 (swap the two dates in `movies.json`) |
| `Invalid <field> <value> for movie <slug>, treating as None.` | K-5 (fix: use YYYY-MM-DD format) |
| `rt-scraper-errors` alert fires for silent-zero | K-1/K-2/K-3 — check alert email for movie slug |
| `rt-scraper-errors` alert fires with "has no theatrical_release_date" | K-4 — config hygiene |

---

## Diagnosis Decision Tree

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
        +-> Step 2: Check raw HTML for review card markers:
        |     curl -s "https://www.rottentomatoes.com/m/<slug>/reviews/all-critics" \
        |       | grep -c review-card-critic
        |   0 matches → selector changed. Check/update SELECTORS dict in rotten_tomatoes.py.
        |              Cross-ref html_parsing.md.
        |   >0 matches → Selenium-level issue (page load timeout, click interception, etc.).
        |                Cross-ref selenium.md.
        +-> Step 3: If movie was removed from RT, set `enabled: false` in movies.json.
        +-> Step 4: If movie is genuinely obscure and has no reviews post-release (K-7),
                     disable the movie (do NOT clear theatrical_release_date).

"Enabled movie <slug> has no theatrical_release_date" logged
|
+-> K-4. Add theatrical_release_date (YYYY-MM-DD) to the entry in movies.json.
|   Error clears on next run.

"Movie <slug>: embargo_lift_date ... is after theatrical_release_date" logged
|
+-> K-6. Swap the two dates in movies.json (likely a copy-paste or typo error).
|   WARNING clears on next run, WARNING tier becomes reachable again.
```

---

## Key Commands

```bash
# Open the movie page directly to check if reviews render
open "https://www.rottentomatoes.com/m/<slug>/reviews/all-critics"

# Re-run just this movie (gate skips DB connect for silent-zero case)
DATABASE_URL="..." uv run python rotten_tomatoes.py --movie <slug>

# Check raw HTML for review card markers
curl -s "https://www.rottentomatoes.com/m/<slug>/reviews/all-critics" | grep -c review-card-critic

# Inspect current selectors
grep -A 12 "^SELECTORS" rotten_tomatoes.py

# Verify date fields are parsed correctly for a slug
uv run python -c "
from rotten_tomatoes import load_movie_config
for e in load_movie_config():
    if e['slug'] == '<slug>':
        print(e)
"

# Sanity check: when was the last successful scrape for each movie?
psql $DATABASE_URL -c "SELECT movie_slug, MAX(scrape_time) FROM reviews GROUP BY movie_slug ORDER BY 2 DESC LIMIT 10"
```

---

## Cross-References

- `planning/errors/html_parsing.md` (C-*: selector/parsing failures — primary root cause for ERROR tier when cards stop matching)
- `planning/errors/anti_blocking.md` (J-1: mid-session bot flag; J-2: IP-based detection suppressing reviews)
- `planning/errors/selenium.md` (B-*: page load & interaction failures)
- `planning/errors/monitoring.md` (I-*: alert misfires, log-metric filtering — e.g., if WARNING starts firing as ERROR due to formatter bug)
