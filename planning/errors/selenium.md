# Error Playbook: Selenium Page Loads & Interaction

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| B-1 | Initial page load timeout (RT slow or down) | Medium | Returns 0 reviews |
| B-2 | "Load More" button XPATH changes (RT redesign) | Medium-High | Timeout or no pagination |
| B-3 | "Load More" click does nothing (JS error on RT side) | Low | Infinite loop or timeout |
| B-4 | RT returns CAPTCHA or bot-detection page | Medium | Returns 0 reviews |
| B-5 | RT rate-limits the IP (429) | Low (Cloud Run IPs rotate) | Returns 0 reviews |
| B-6 | `review-card` tag renamed | Medium (RT redesign) | 0 reviews parsed |
| B-7 | `time.sleep(5)` insufficient; page not fully rendered | Medium | Missing reviews |
| B-8 | `time.sleep(3)` after "Load More" insufficient | Medium | Missing reviews |
| B-9 | ElementClickInterceptedException (overlay covers button) | Low | Stops loading early |
| B-10 | Network timeout connecting to RT from Cloud Run | Low | Returns 0 reviews |

---

## Prevention

**B-1 / B-10 (page load timeouts):**
- Add `driver.set_page_load_timeout(30)` in `_build_driver()`
- Add application-level retry: 3 attempts, 5-second sleep between retries
- Without this, a hung page load blocks the entire job

**B-2 (XPATH change):**
- Current XPATH is brittle: positional indexing (`div[2]`) breaks if RT adds any wrapper div
- Store in `SELECTORS` dict for easy updates
- Log page source when 0 cards found (enables post-mortem diagnosis)

**B-3 / B-8 (click does nothing):**
- After clicking "Load More", verify card count increased before clicking again
- Bail after 2 consecutive no-change clicks (avoids infinite loop)

**B-4 (CAPTCHA/bot detection):**
- Add User-Agent string to Chrome options
- Add `--disable-blink-features=AutomationControlled`
- Monitor: consistent "Found 0 review cards" across all movies → bot detection likely

**B-7 (insufficient sleep):**
- Replace `time.sleep(5)` with explicit wait for review card presence:
  `WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.CSS_SELECTOR, "review-card")))`
- Faster (doesn't wait full 5s if content loads in 2s) and more reliable (waits up to 15s)

---

## Detection

| Signal | Failure |
|---|---|
| `Found 0 review cards` in logs | B-1, B-4, B-5, B-6, B-10 |
| `Selenium error for {slug}` in logs | Any Selenium exception |
| `No more 'Load More' button` on first iteration | B-2 (XPATH wrong) |
| Job times out at 900s | B-3 (infinite loop) |

---

## Diagnosis Decision Tree

```
"Found 0 review cards" in logs
|
+-> Selenium error logged before this?
|   +-> YES: Chrome failed (see chrome.md)
|   +-> NO: Page loaded but no cards found
|
+-> Examine page_source:
    +-> Contains "review-card" tag at all?
    |   +-> NO: RT changed HTML structure. Find new tag name.
    |   +-> YES: BS4 found cards but find_all returned empty? Check parsing.
    |
    +-> Contains "captcha", "challenge", or "blocked"?
    |   +-> YES: Bot detection. Add User-Agent, anti-automation flags.
    |   +-> NO: Continue
    |
    +-> Contains the expected movie title?
    |   +-> NO: Wrong URL, 404, or redirect. Check slug.
    |   +-> YES: Page loaded but reviews section missing. Layout changed.
    |
    +-> Page very small (<5KB)?
        +-> YES: Network issue / RT down / IP blocked.
        +-> NO: Full page loaded but structure changed. Manual inspection needed.
```

---

## Research

- Selenium WebDriverWait: https://www.selenium.dev/documentation/webdriver/waits/
- Chrome headless bot detection: https://intoli.com/blog/not-possible-to-block-chrome-headless/
- RT uses web components (`review-card`, `rt-button`). BS4 treats custom elements as
  regular tags, which works correctly.
