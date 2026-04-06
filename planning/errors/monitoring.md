# Error Playbook: Monitoring (JSON Logging, Alerts, Notifications)

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| M-1 | JSON format invalid → Cloud Logging falls back to textPayload, severity DEFAULT | Medium (first deploy) | Alerts silently stop working |
| M-2 | `severity` key wrong case or misspelled → severity not mapped | Low | Same as M-1 |
| M-3 | Traceback not captured in JSON (multi-line output breaks JSON-per-line) | High (if not handled) | Exception details lost from logs |
| M-4 | Log-based metric filter doesn't match (resource type, job name, severity field) | Medium (first setup) | Metric never counts, alerts never fire |
| M-5 | Alert policy fires but email not delivered (spam, wrong address, unverified channel) | Medium | Silent alert failure |
| M-6 | GCP requires email channel verification before delivery | High (first setup) | No emails until verified |
| M-7 | Alert fires too often on transient errors (noisy) | Low | Alert fatigue, user ignores real errors |
| M-8 | Alert auto-closes before user sees it (Cloud Monitoring auto-resolve) | Low | Missed incidents |
| M-9 | Monitoring resources deleted accidentally (no IaC, gcloud-only) | Low | Alerts silently disappear |

---

## Prevention (build into implementation)

**M-1 / M-2 (invalid JSON / wrong severity key):**
- Cloud Run expects exactly `{"severity": "...", "message": "..."}` — key must be lowercase `severity`, value must be a standard level name (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`).
- Python's `record.levelname` produces exactly these values. No mapping needed.
- **Test before deploying**: run the scraper locally and verify each line is valid JSON:
  ```bash
  DATABASE_URL="..." uv run python rotten_tomatoes.py --movie some_slug 2>&1 | head -5 | python -m json.tool
  ```
  If `json.tool` parses every line without error, the format is correct.

**M-3 (tracebacks in JSON):**
- Python's `log.exception()` and `log.error("...", exc_info=True)` append a traceback to the log record. The default `Formatter.format()` appends it as multi-line text after the message.
- If our JSON formatter only emits `record.getMessage()`, the traceback is **lost**.
- If the formatter naively concatenates the traceback, newlines inside the JSON string are fine (JSON allows `\n` in strings), but the formatter must explicitly check `record.exc_info` and format it.
- **Prevention**: In the custom formatter's `format()` method, check `record.exc_info` and append the formatted traceback to the `message` field:
  ```python
  msg = record.getMessage()
  if record.exc_info:
      msg += "\n" + self.formatException(record.exc_info)
  ```
- The `backfill.py` script uses `log.exception()` (line 215). The main scraper does not currently use `exc_info=True`, but it could in the future, so handle it now.

**M-4 (metric filter mismatch):**
- The log-based metric filter must exactly match the resource labels Cloud Run produces.
- After creating the metric, verify it's counting by triggering a run and checking:
  ```bash
  gcloud logging read \
    'resource.type="cloud_run_job" AND resource.labels.job_name="rt-scraper" AND severity>=ERROR' \
    --limit=1 --project=rotten-tomatoes-scraper
  ```
  If this returns results but the metric shows 0, the metric filter is wrong.

**M-5 / M-6 (email delivery / verification):**
- GCP sends a verification email when you create an email notification channel. **The channel does not deliver alerts until you click the verification link.** This is easy to miss.
- After creating the channel, immediately check your inbox (including spam) for the verification email.
- Verify channel status:
  ```bash
  gcloud beta monitoring channels list \
    --project=rotten-tomatoes-scraper \
    --format="table(displayName, verificationStatus)"
  ```
  Status must be `VERIFIED`, not `UNVERIFIED`.

**M-9 (accidental deletion):**
- Document the exact gcloud commands used to create each resource (in CLAUDE.md or a script) so they can be recreated.
- Periodically verify resources exist:
  ```bash
  gcloud logging metrics list --project=rotten-tomatoes-scraper
  gcloud alpha monitoring policies list --project=rotten-tomatoes-scraper
  gcloud beta monitoring channels list --project=rotten-tomatoes-scraper
  ```

---

## Outside Our Control

1. **GCP Cloud Monitoring outage**: Alerts may not fire during a Monitoring service disruption. Mitigation: none needed for a hobby project.
2. **Email provider spam filtering**: Gmail, Outlook, etc. may classify GCP alert emails as spam. Mitigation: check spam folder after first alert; add `monitoring-noreply@google.com` to contacts.
3. **GCP free tier changes**: Google could change Cloud Monitoring free tier limits. Currently generous (500 email notifications/month, 50 log-based metrics). Unlikely to affect this project.

---

## Detection

| Signal | Failure |
|---|---|
| Cloud Logging shows `severity: DEFAULT` for all scraper entries | M-1, M-2 (JSON not parsed) |
| Cloud Logging shows `textPayload` instead of `jsonPayload` | M-1 (output is not valid JSON) |
| Log-based metric stays at 0 despite ERROR entries in logs | M-4 (filter mismatch) |
| No email received after confirmed ERROR in logs | M-5, M-6 (delivery or verification) |
| Multiple emails for the same issue within minutes | M-7 (noisy alerts) |
| Alert incident auto-closed, no email for subsequent errors | M-8 (auto-resolve window) |
| `gcloud alpha monitoring policies list` returns empty | M-9 (resources deleted) |
| Traceback missing from ERROR log entries (only first line visible) | M-3 (traceback not captured) |

---

## Diagnosis Decision Tree

```
Expected an alert email but didn't receive one
|
+-> Is there an ERROR entry in Cloud Logging?
|   +-> NO: The scraper didn't log an error. Alert is working correctly.
|   |        Check if the issue is a job failure (no Python logs at all).
|   |        See cloud_run.md for container-level failures.
|   +-> YES: Continue.
|
+-> Does the log entry have severity=ERROR (not DEFAULT)?
|   +-> NO: JSON severity mapping is broken.
|   |        Check: is the entry textPayload or jsonPayload?
|   |        +-> textPayload: Scraper output is not valid JSON.
|   |        |   Run locally and check:
|   |        |   uv run python rotten_tomatoes.py --movie <slug> 2>&1 | head -1
|   |        |   Is it valid JSON? If not, fix the formatter.
|   |        +-> jsonPayload but severity=DEFAULT: JSON is valid but missing
|   |            or misspelling the "severity" key. Check formatter code.
|   +-> YES: Severity mapping works. Continue.
|
+-> Is the log-based metric counting?
|   +-> Check: gcloud logging metrics describe rt-scraper-errors
|   +-> Metric doesn't exist? Recreate it (Step 3 in plan).
|   +-> Metric exists but count is 0?
|       +-> Compare the metric's filter with the actual log entry's resource labels.
|       +-> Common issue: resource.labels.job_name doesn't match filter.
|   +-> Metric count > 0? Continue.
|
+-> Does the alert policy exist and reference the correct metric?
|   +-> Check: gcloud alpha monitoring policies list --project=rotten-tomatoes-scraper
|   +-> Policy missing? Recreate it (Step 4 in plan).
|   +-> Policy exists? Check its condition filter references
|       "logging.googleapis.com/user/rt-scraper-errors".
|   +-> Condition correct? Continue.
|
+-> Is the notification channel verified?
|   +-> Check: gcloud beta monitoring channels list --format="table(displayName, verificationStatus)"
|   +-> UNVERIFIED: Check email inbox (including spam) for GCP verification link. Click it.
|   +-> VERIFIED: Continue.
|
+-> Is the alert policy linked to the notification channel?
|   +-> Check: gcloud alpha monitoring policies describe <POLICY_ID>
|   +-> notificationChannels list empty? Update policy to add the channel.
|   +-> Channel listed? Continue.
|
+-> Check email spam folder for alerts from monitoring-noreply@google.com.
    +-> Found in spam? Add sender to contacts / safe senders list.
    +-> Not in spam? Check email address in the notification channel is correct:
        gcloud beta monitoring channels describe <CHANNEL_ID>
```

```
Severity shows DEFAULT for all log entries (JSON mapping broken)
|
+-> Is the latest deployed image the one with the JSON formatter?
|   +-> Check: gcloud run jobs describe rt-scraper --region=us-east1 --format="value(template.template.containers.image)"
|   +-> Image tag matches the commit with the JSON change?
|   |   +-> NO: Redeploy. The old image is still running.
|   |   +-> YES: Continue.
|
+-> Check a recent log entry's raw payload:
|   gcloud logging read 'resource.type="cloud_run_job" AND resource.labels.job_name="rt-scraper"' --limit=1 --format=json
|   +-> Has "textPayload" key? Output is not valid JSON.
|   |   Look at the textPayload value. Is it almost-JSON with a syntax error?
|   |   Common: trailing comma, unescaped quotes in message, non-UTF8 chars.
|   +-> Has "jsonPayload" key but no severity mapping?
|       Check if jsonPayload contains a "severity" key (lowercase).
|       Cloud Run maps: jsonPayload.severity -> LogEntry.severity.
|       Key must be exactly "severity" (not "level", "levelname", "log_level").
```

```
Too many alert emails (noisy)
|
+-> Are the errors real (actual scraper failures)?
|   +-> YES: Fix the underlying scraper issue. Alerts are working correctly.
|   +-> NO: False positives. Continue.
|
+-> Is the same error generating multiple emails?
|   +-> YES: Cloud Monitoring may create a new incident per alignment period (10 min).
|   |        If the error recurs every 50 min (every run), you get ~29 emails/day.
|   |        Fix the root cause, or increase the alignment period to 3600s (1 hour)
|   |        to batch errors into hourly alerts.
|   +-> NO: Different errors each time. Investigate each one.
```

---

## Key Commands

```bash
# Check if JSON severity mapping is working
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="rt-scraper" AND severity=INFO' \
  --limit=3 --format="table(timestamp, severity, jsonPayload.message)" \
  --project=rotten-tomatoes-scraper

# Check log-based metric exists and its filter
gcloud logging metrics describe rt-scraper-errors --project=rotten-tomatoes-scraper

# List alert policies
gcloud alpha monitoring policies list --project=rotten-tomatoes-scraper

# Check notification channel verification status
gcloud beta monitoring channels list \
  --project=rotten-tomatoes-scraper \
  --format="table(displayName, type, verificationStatus)"

# View recent alert incidents
gcloud alpha monitoring policies conditions list <POLICY_ID> --project=rotten-tomatoes-scraper

# Test: read raw log entry to inspect payload type
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="rt-scraper"' \
  --limit=1 --format=json --project=rotten-tomatoes-scraper
```

---

## Research

- Cloud Run structured logging (JSON auto-parsing): https://cloud.google.com/run/docs/logging#writing_structured_logs
  - Confirmed: `severity` (lowercase) is stripped from `jsonPayload` and mapped to `LogEntry.severity`
  - Confirmed: `message` (lowercase) is "used as the main display text of the log entry if present"
  - Confirmed: output via `print(json.dumps(entry))` — matches official Python example
- LogSeverity enum values: https://cloud.google.com/logging/docs/reference/v2/rest/v2/LogEntry#LogSeverity
  - Accepted values: DEFAULT(0), DEBUG(100), INFO(200), NOTICE(300), WARNING(400), ERROR(500), CRITICAL(600), ALERT(700), EMERGENCY(800)
  - Python `record.levelname` produces DEBUG, INFO, WARNING, ERROR, CRITICAL — all exact matches, no translation needed
- Log-based metrics: https://cloud.google.com/logging/docs/logs-based-metrics
- Cloud Monitoring alerting: https://cloud.google.com/monitoring/alerts
- Notification channels: https://cloud.google.com/monitoring/support/notification-options
- Cloud Monitoring incident lifecycle: https://cloud.google.com/monitoring/alerts/incidents-events
