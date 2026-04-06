# Cloud Scheduler

Cloud Scheduler triggers the Cloud Run Job on a schedule. It is completely independent
of GitHub Actions — even if CI/CD is broken, the scheduler keeps running.

---

## 1. The 50-Minute Interval Problem

Standard unix cron does not support "every 50 minutes" as a clean interval (50 doesn't
divide 60 evenly). Cloud Scheduler supports two syntaxes:

**Option A — Standard cron (uneven intervals):**
`*/50 * * * *` means "at minute 0 and minute 50 of every hour."
Gaps are: 50 min, then 10 min to top of hour, then 50 min, etc. Not uniform.

**Option B — App Engine "every N" syntax (uniform):**
`every 50 minutes` triggers at a uniform 50-minute interval from a base time.
Cloud Scheduler supports this syntax.

**Recommendation: use `every 50 minutes`.**
This gives true 50-minute intervals rather than the uneven 50/10 pattern.

NOTE: Verify that `every 50 minutes` is supported in the current Cloud Scheduler API
version. See questions.md. If it's not, use `0 * * * *` (every hour) as a fallback —
the scraper is idempotent so running hourly instead of every 50 minutes has no downside
other than a slightly wider window for catching new reviews.

---

## 2. Create the Scheduler Job

The scheduler needs the Cloud Run Job's full resource name to invoke it.

Get the project number:
```bash
PROJECT_NUMBER=$(gcloud projects describe rotten-tomatoes-scraper --format="get(projectNumber)")
```

Create the scheduler job:
```bash
gcloud scheduler jobs create http rt-scraper-schedule \
  --location=us-east1 \
  --schedule="every 50 minutes" \
  --uri="https://run.googleapis.com/v2/projects/rotten-tomatoes-scraper/locations/us-east1/jobs/rt-scraper:run" \
  --http-method=POST \
  --message-body="{}" \
  --oauth-service-account-email="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --time-zone="America/New_York"
```

Parameter notes:
- `--uri`: The Cloud Run Jobs v2 API endpoint. Format:
  `https://run.googleapis.com/v2/projects/PROJECT_ID/locations/REGION/jobs/JOB_NAME:run`
- `--message-body`: Required empty JSON body for the POST request.
- `--oauth-service-account-email`: Cloud Scheduler uses this SA to authenticate to the
  Cloud Run Jobs API. The Compute Engine default SA has `run.jobs.run` permission by
  default on the same project.
- `--time-zone`: Cosmetic (affects when "midnight" falls in logs/console). Eastern time
  matches the existing setup.

---

## 3. Grant Scheduler Permission to Invoke the Job

The service account used by Cloud Scheduler needs permission to run Cloud Run Jobs:

```bash
gcloud projects add-iam-policy-binding rotten-tomatoes-scraper \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/run.invoker"
```

---

## 4. Test the Schedule Manually

Trigger the scheduler job immediately (without waiting for the next scheduled time):
```bash
gcloud scheduler jobs run rt-scraper-schedule --location=us-east1
```

This fires the HTTP request to Cloud Run, which starts a job execution. Monitor it:
```bash
gcloud run jobs executions list --job=rt-scraper --region=us-east1
```

---

## 5. Verify the Schedule is Active

```bash
gcloud scheduler jobs describe rt-scraper-schedule --location=us-east1
```

Check:
- `state: ENABLED`
- `schedule` matches what you set
- `lastAttemptTime` updates after each trigger

---

## 6. Pause / Resume (e.g., for maintenance)

Pause (stops triggering, job config preserved):
```bash
gcloud scheduler jobs pause rt-scraper-schedule --location=us-east1
```

Resume:
```bash
gcloud scheduler jobs resume rt-scraper-schedule --location=us-east1
```

---

## 7. Why the Scheduler Never "Goes Down"

Cloud Scheduler is a fully managed GCP service — Google operates it, not us. It does not
run on our VM, our container, or any resource we control. Even if:
- The Cloud Run Job fails, Cloud Scheduler retries (up to max-retries) and logs the failure
- GitHub Actions CI/CD is broken, Cloud Scheduler keeps triggering the last working image
- We push a bad image, Cloud Scheduler triggers it (it fails), old executions are unaffected

The scheduler only stops if:
1. We manually pause it
2. The GCP project is disabled (billing, quota)
3. Google's Cloud Scheduler has an outage (SLA: 99.9%)
