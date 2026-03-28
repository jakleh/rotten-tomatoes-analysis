# Error Playbook: Chrome / ChromeDriver

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| A-1 | `DevToolsActivePort file doesn't exist` | High (first Docker attempt) | Fatal |
| A-2 | Chrome OOM-killed inside container | Medium | Fatal |
| A-3 | ChromeDriver/Chrome version mismatch | Low (both from apt) | Fatal |
| A-4 | Debian apt updates Chromium major version on image rebuild | Low | Fatal until image fix |
| A-5 | `chromium-driver` package name wrong on slim-bookworm | Medium (first build) | Build fails |
| A-6 | Sandbox violation in container | High (without `--no-sandbox`) | Fatal |
| A-7 | `/dev/shm` too small (default 64MB in Docker) | Medium | Intermittent crashes |
| A-8 | Chrome hung, consuming memory until container killed | Low | Timeout then retry |
| A-9 | `CHROME_BIN` env var not set or wrong path | Low | Fatal |
| A-10 | Zombie chromedriver processes accumulating across movies | Low | OOM on later movies |

---

## Prevention (within our control)

**A-1 / A-6 (sandbox/DevToolsActivePort):**
`_build_driver()` already includes `--no-sandbox`. Required for containers. No change needed.

**A-2 / A-7 (memory):**
- `--disable-dev-shm-usage` already present (uses `/tmp` instead of 64MB `/dev/shm`)
- `--js-flags=--max-old-space-size=256` caps V8 heap
- 2Gi container memory is generous (Chrome peaks ~800MB-1.2GB)
- Consider adding: `--disable-background-networking`, `--disable-default-apps`

**A-3 / A-4 / A-5 (version management):**
- Both `chromium` and `chromium-driver` installed in the same `apt-get install` command
  share the same major version. This is the key protection.
- Add build-time verification to Dockerfile:
  ```dockerfile
  RUN chromium --version && chromedriver --version
  ```
- Optionally pin Docker base image by digest to prevent surprise Chromium updates:
  `FROM python:3.14-slim-bookworm@sha256:<digest>`

**A-8 / A-10 (hung processes, zombies):**
- `finally: driver.quit()` in `get_reviews()` ensures cleanup
- Add `driver.set_page_load_timeout(30)` and `driver.set_script_timeout(15)`

**A-9 (CHROME_BIN):**
- Dockerfile sets `ENV CHROME_BIN=/usr/bin/chromium`
- Cloud Run Job also sets `--set-env-vars=CHROME_BIN=/usr/bin/chromium`
- Double coverage

---

## Outside Our Control

1. **Debian Chromium version**: We don't control which version Debian packages. Mitigation:
   pin base image digest.
2. **Chromium bug regressions**: New Chromium versions may crash on pages they previously
   rendered. Mitigation: pin versions, monitor.
3. **Chrome headless mode changes**: Google periodically changes headless behavior
   (`--headless=new` vs `--headless`). Mitigation: monitor Selenium release notes.

---

## Detection

| Signal | Failure |
|---|---|
| `Chrome failed to start: exited abnormally` | A-1, A-6 |
| `DevToolsActivePort file doesn't exist` | A-1 |
| `session not created: This version of ChromeDriver only supports Chrome version XXX` | A-3, A-4 |
| Container exit code 137 (OOM) | A-2, A-7, A-10 |
| Job exceeds timeout | A-8 |

---

## Diagnosis Decision Tree

```
Chrome failure detected
|
+-> "DevToolsActivePort file doesn't exist"?
|   +-> YES: Check --no-sandbox is in _build_driver()
|   |        Check container memory (Cloud Run metrics)
|   |        Check --disable-dev-shm-usage is present
|   +-> NO: Continue
|
+-> "session not created: ... version ..."?
|   +-> YES: Version mismatch.
|   |        docker run --rm IMAGE chromium --version
|   |        docker run --rm IMAGE chromedriver --version
|   |        Major versions must match. Rebuild with both from same apt source.
|   +-> NO: Continue
|
+-> Container OOM-killed (exit 137)?
|   +-> YES: Check Cloud Run peak memory metrics.
|   |        Peak > 1.8Gi? -> Increase to 4Gi
|   |        Only on later movies (3rd, 4th)? -> driver.quit() not called; zombie processes
|   |        On first movie? -> Add --single-process flag
|   +-> NO: Continue
|
+-> Job timed out?
    +-> YES: Chrome is hung.
    |        Check: did "Load More" loop iterate excessively?
    |        Fix: add page_load_timeout and script_timeout to driver
    +-> NO: Check container logs for Python traceback
```

---

## Diagnostic Info to Capture on Chrome Failure

- `chromium --version` output (emit at container startup or embed in build)
- `chromedriver --version` output
- URL being loaded when Chrome failed
- Container memory at time of failure (Cloud Run metrics)

---

## Research (read before implementing)

- Selenium headless Chrome options: https://www.selenium.dev/documentation/webdriver/browsers/chrome/
- Chrome headless mode: https://developer.chrome.com/articles/new-headless/
- Debian Bookworm Chromium package tracker: https://packages.debian.org/bookworm/chromium
- Chromium command-line switches: https://peter.sh/experiments/chromium-command-line-switches/
