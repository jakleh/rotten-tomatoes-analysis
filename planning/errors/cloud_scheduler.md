# Error Playbook: Cloud Scheduler

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| F-1 | URI wrong (v1 vs v2 format) | Medium (first setup) | Job never triggers |
| F-2 | OAuth SA lacks `run.invoker` role | Medium (first setup) | 403 on trigger |
| F-3 | Scheduler job paused | Low | No triggers until resumed |
| F-4 | GCP billing issue suspends project | Very Low | Everything stops |
| F-5 | Cloud Scheduler regional outage | Very Low | Triggers delayed/missed |

---

## Prevention

**F-1 (URI):**
- Correct URI (v2): `https://run.googleapis.com/v2/projects/rotten-tomatoes-scraper/locations/us-east1/jobs/rt-scraper:run`
- Verify after creation: `gcloud scheduler jobs describe rt-scraper-schedule --location=us-east1`

**F-2 (IAM):**
- Grant `roles/run.invoker` to the compute SA (step 08 of plan)
- Verify: trigger manually with `gcloud scheduler jobs run rt-scraper-schedule --location=us-east1`

---

## Detection

| Signal | Failure |
|---|---|
| `lastAttemptTime` stale (>50 min old) | F-3, F-5 |
| No executions in Cloud Run | F-1, F-2, F-4 |
| Scheduler state: PAUSED | F-3 |
| Last status 403 | F-2 |

---

## Diagnosis Decision Tree

```
No new Cloud Run executions when expected
|
+-> Is Cloud Scheduler state ENABLED?
|   +-> NO: Resume: gcloud scheduler jobs resume rt-scraper-schedule --location=us-east1
|   +-> YES: Continue
|
+-> What was lastAttemptResult?
|   +-> 403: IAM issue. Grant run.invoker to scheduler SA.
|   +-> 404: URI is wrong. Check job name, region, project ID.
|   +-> 500: Cloud Run issue (see cloud_run.md).
|   +-> No result: Scheduler has never fired. Check schedule syntax.
```

---

## Key Commands

```bash
# Check scheduler status
gcloud scheduler jobs describe rt-scraper-schedule --location=us-east1

# Trigger manually
gcloud scheduler jobs run rt-scraper-schedule --location=us-east1

# Pause/resume
gcloud scheduler jobs pause rt-scraper-schedule --location=us-east1
gcloud scheduler jobs resume rt-scraper-schedule --location=us-east1
```

---

## Research

- Cloud Scheduler docs: https://cloud.google.com/scheduler/docs
- Triggering Cloud Run Jobs on schedule: https://cloud.google.com/run/docs/execute/jobs-on-schedule
- Schedule syntax (cron + Groc): https://cloud.google.com/scheduler/docs/configuring/cron-job-schedules
