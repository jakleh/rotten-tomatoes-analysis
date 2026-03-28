# Error Playbook: Cloud Run Job Execution

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| E-1 | Job exceeds timeout | Medium | Kills run, retries once |
| E-2 | Job exceeds memory limit (OOM) | Medium | Kills run, retries once |
| E-3 | Image pull fails (Artifact Registry unavailable) | Very Low | Job can't start |
| E-4 | Image pull slow (1.5GB, cold cache) | Low | Eats into timeout |
| E-5 | Secret Manager unavailable | Very Low | Job can't start |
| E-6 | Concurrent execution overlap | Low | Two runs hit Neon simultaneously |
| E-7 | Bad image deployed (syntax error, missing dep) | Medium | Immediate crash |
| E-8 | Container startup crash (import error, missing file) | Medium (after rewrite) | Immediate crash |

---

## Prevention

**E-1 (timeout):**
- 8 Selenium sessions × 1-2 min each can exceed 10 min
- Set timeout to **900s (15 min)** instead of 600s. Costs nothing extra (Cloud Run bills
  actual usage, not timeout ceiling).
- Add per-movie timing logs to identify slow movies

**E-2 (memory):**
- 2Gi is sufficient for sequential Chrome sessions (one at a time)
- `driver.quit()` in `finally` block is critical — prevents memory accumulation
- Monitor Cloud Run memory metrics after first few runs

**E-7 / E-8 (bad image):**
- Add import sanity check to GitHub Actions:
  `docker run --rm IMAGE python -c "import rotten_tomatoes"` before pushing
- Cloud Run retries once (same bad image), then fails. Next trigger: same bad image.
  Continues failing until good image is pushed.

**E-6 (concurrent execution):**
- 50-min schedule + typical 7-min run = no overlap
- If a run hangs for 50+ min, next trigger fires concurrently
- Both hit Neon simultaneously — this is fine (Neon handles concurrent connections)
- Optional: `--max-instances=1` on the job to prevent overlap

---

## Detection

| Signal | Failure |
|---|---|
| Execution status FAILED, reason "Timeout" | E-1 |
| Exit code 137 (OOM) | E-2 |
| FAILED with no logs at all | E-3, E-5 (container never started) |
| FAILED with Python traceback | E-7, E-8 |

---

## Diagnosis Decision Tree

```
Cloud Run execution failed
|
+-> Any logs from this execution?
|   +-> NO: Container never started.
|   |   Check: Image exists? gcloud artifacts docker images list ...
|   |   Check: Secret accessible? gcloud secrets versions access latest ...
|   |   Check: IAM roles on compute SA?
|   +-> YES: Container started. Continue.
|
+-> Exit code?
|   +-> 137 (SIGKILL): OOM.
|   |   Check Cloud Run memory metrics.
|   |   Increase: gcloud run jobs update rt-scraper --memory=4Gi --region=us-east1
|   +-> 143 (SIGTERM): Timeout.
|   |   Increase: gcloud run jobs update rt-scraper --task-timeout=1200s --region=us-east1
|   +-> 1: General error. Check logs for traceback.
|   +-> 2: argparse error or SystemExit(1). Check movies.json.
|
+-> ImportError in traceback?
    +-> YES: Missing dependency. Rebuild image after `uv lock`.
    +-> NO: Check specific exception (DB → neon.md, Selenium → chrome.md)
```

---

## Key Commands

```bash
# List recent executions
gcloud run jobs executions list --job=rt-scraper --region=us-east1 --limit=5

# View logs for a specific execution
gcloud run jobs executions logs EXECUTION_NAME --region=us-east1

# Update timeout
gcloud run jobs update rt-scraper --task-timeout=900s --region=us-east1

# Update memory
gcloud run jobs update rt-scraper --memory=4Gi --region=us-east1
```

---

## Research

- Cloud Run Jobs execution lifecycle: https://cloud.google.com/run/docs/execute/jobs
- Cloud Run timeout and retries: https://cloud.google.com/run/docs/configuring/task-timeout
- Cloud Run memory limits: https://cloud.google.com/run/docs/configuring/memory-limits
- Viewing Cloud Run logs: https://cloud.google.com/run/docs/logging
