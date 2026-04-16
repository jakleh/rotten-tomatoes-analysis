# Selenium/Chrome Version Compatibility Research

Reference document for debugging Chrome/Selenium issues in the Rotten Tomatoes scraper.

## Context

The scraper uses Selenium WebDriver (headless Chromium) + BeautifulSoup4 to scrape review data from Rotten Tomatoes. After a period of inactivity (all movies disabled in `movies.json`), enabling new movies caused every scrape attempt to crash with an empty ChromeDriver error message and a Rust-based stacktrace.

---

## Root Cause Investigation

### Initial Symptoms

- Both new movies (`mother_mary`, `lee_cronins_the_mummy`) failed on all passes (top-critics and all-critics).
- Previously working movie slugs also failed, confirming a systemic issue rather than anything movie-specific.
- Error was caught at the broad `except Exception` in `get_reviews()`, producing: `"Selenium error for <movie> (<filter>): Message:"` followed by a ChromeDriver Rust stacktrace with `cxxbridge1$str$ptr` symbols.
- Same crash happened locally (macOS) and in Cloud Run (Debian bookworm container).

### Chrome/Selenium Versions at Time of Investigation

| Environment | Chrome Version | ChromeDriver Version |
|---|---|---|
| Local (macOS) | 147.0.7727.56 | -- |
| Docker (Debian apt) | 147.0.7727.55 | 147.0.7727.55 |

- Selenium was at **4.41.0** (supports CDP v143, v144, v145 -- does NOT support CDP v147).
- Upgraded to **Selenium 4.43.0** (supports CDP v145, v146, v147) -- crash persisted.

### The Actual Root Cause

The crash was **NOT** a Chrome/Selenium version mismatch. A minimal debug script revealed:

1. Driver creation: OK
2. Navigate to google.com: OK
3. Navigate to RT reviews page: OK (but page title said "Audience Reviews" -- suspicious)
4. Wait for `review-card` CSS selector: **FAILED with TimeoutException**

**Rotten Tomatoes changed their HTML structure.** The `<review-card>` custom element was renamed to `<review-card-critic>`. The `WebDriverWait` was looking for an element that no longer exists, causing a 15-second timeout. The scary Rust stacktrace is just how ChromeDriver 147 formats `TimeoutException` errors -- it is not actually a crash.

### Additional HTML Changes (RT Site Update)

- Review card element: `<review-card>` changed to `<review-card-critic>`
- Declarative Shadow DOM: RT now uses `<template shadowrootmode="open">` extensively throughout their components
- Full selector audit against new HTML still in progress

---

## Version Compatibility Reference

### Selenium CDP Version Support

Source: <https://github.com/SeleniumHQ/selenium/blob/trunk/py/CHANGES>

| Selenium Version | CDP Versions Supported |
|---|---|
| 4.40.0 | v142, v143, v144 |
| 4.41.0 | v143, v144, v145 |
| 4.42.0 | v144, v145, v146 |
| 4.43.0 | v145, v146, v147 |

When Chrome updates to a new major version, Selenium must be updated to a version that includes CDP support for that Chrome version. Check the CHANGES file linked above to find the right pairing.

### Chrome Headless Mode

Chrome has been migrating from `--headless` to `--headless=new` for several versions. As of Chrome 147, `--headless=new` is the recommended flag.

- <https://developer.chrome.com/blog/removing-headless-old-from-chrome>
- <https://developer.chrome.com/docs/chromium/headless>

### SwiftShader Removal (Chrome 137+)

Chrome 137+ removed the automatic SwiftShader software rendering fallback on macOS and Linux. This affects Docker containers and Cloud Run (GPU-less environments).

Mitigations:
- `--disable-gpu` (already in our config)
- `--enable-unsafe-swiftshader`

References:
- <https://chromestatus.com/feature/5166674414927872>
- <https://groups.google.com/a/chromium.org/g/blink-dev/c/yhFguWS_3pM>

### ChromeDriver Rust Rewrite

ChromeDriver is now Rust-based. This is visible from `cxxbridge1$str$ptr` and `_RNvCs` symbols in stacktraces.

Key implications:
- Error messages from `TimeoutException` now include a Rust stacktrace that **looks like a crash** but is actually just the normal timeout error format.
- The empty `"Message:"` followed by a stacktrace does NOT necessarily mean Chrome crashed.
- This can be very misleading when debugging.

Related issue: <https://github.com/SeleniumHQ/selenium/issues/13499>

### Dockerfile Considerations

Our Dockerfile installs Chromium from Debian apt (`python:3.14-slim-bookworm`) with no version pinning. Each Docker build pulls whatever Chromium version is current in the Debian repos. This means Chrome/ChromeDriver versions can drift between deploys. Consider pinning versions for stability if this becomes a recurring problem.

---

## Debugging Playbook

When Selenium errors appear, use a minimal debug script that isolates each step to quickly identify the failure layer:

1. **Driver creation** -- Can Chrome start at all? (binary missing, sandbox issues, GPU issues)
2. **Navigate to a known-good page** (e.g., google.com) -- Is the WebDriver functional?
3. **Navigate to the RT reviews page** -- Does the page load? Check the page title for redirects.
4. **Wait for the expected CSS selector** -- Does the expected HTML structure still exist?

If step 4 fails but steps 1-3 succeed, the problem is almost certainly an RT HTML change, not a Chrome/Selenium issue.

---

## Key Lessons

1. **The scary Rust stacktrace is the new normal for ChromeDriver errors.** Do not assume it means Chrome crashed. Isolate the actual exception type first.
2. **Keep Selenium version aligned with Chrome CDP version.** Check the Selenium CHANGES file when Chrome auto-updates to a new major version.
3. **RT can change their HTML at any time.** The scraper's selectors are fragile and need monitoring. Any deploy gap creates risk of silent breakage.
4. **Test with a minimal debug script** that isolates driver creation, navigation, and element detection as separate steps.
5. **`--headless=new`** is the correct flag going forward for Chrome 147+.

---

## Version Update Schedule

Chrome releases on a roughly **4-week cycle**. Each Selenium release supports ~3 Chrome CDP versions, giving a **12-week window** before falling out of support. Our Dockerfile pulls Chromium from Debian apt with no version pinning, so Chrome can jump versions on any rebuild.

### Monthly Check (1st of each month, or after any Chrome major release)

1. Check local Chrome version: `Google Chrome > About` or `chromium --version` in Docker
2. Check current Selenium CDP support: <https://github.com/SeleniumHQ/selenium/blob/trunk/py/CHANGES>
3. If Chrome's major version is within 1 of Selenium's max supported CDP version, update `pyproject.toml` and run `uv lock && uv sync`
4. Run `uv run --group dev pytest tests/ -v` to verify nothing broke
5. Test one movie locally: `DATABASE_URL="..." uv run python rotten_tomatoes.py --movie <slug>`

### Timeline Reference

| Chrome Version | ~Release Date | Selenium Version Needed |
|---|---|---|
| 145 | Late Feb 2026 | >= 4.41.0 |
| 146 | Late Mar 2026 | >= 4.42.0 |
| 147 | Late Apr 2026 | >= 4.43.0 |
| 148 | Late May 2026 | TBD (check CHANGES) |
| 149 | Late Jun 2026 | TBD (check CHANGES) |

Update this table as new versions are released.

---

## Sources

- Chrome 147 Release Notes: <https://developer.chrome.com/release-notes/147>
- Selenium Python CHANGES: <https://github.com/SeleniumHQ/selenium/blob/trunk/py/CHANGES>
- Selenium Chrome docs: <https://www.selenium.dev/documentation/webdriver/browsers/chrome/>
- Chrome Headless mode: <https://developer.chrome.com/docs/chromium/headless>
- Removing `--headless=old`: <https://developer.chrome.com/blog/removing-headless-old-from-chrome>
- SwiftShader removal: <https://chromestatus.com/feature/5166674414927872>
- Selenium issue #13499 (Rust stacktrace): <https://github.com/SeleniumHQ/selenium/issues/13499>
