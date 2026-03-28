# Cloud Run Job Setup

A Cloud Run Job is the unit of execution. Cloud Scheduler triggers it on a schedule.
The job runs the container, executes the scraper, then exits. No persistent process.

---

## 1. Create the Job

```bash
gcloud run jobs create rt-scraper \
  --image=us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:latest \
  --region=us-east1 \
  --set-secrets=DATABASE_URL=DATABASE_URL:latest \
  --set-env-vars=CHROME_BIN=/usr/bin/chromium \
  --memory=2Gi \
  --cpu=1 \
  --task-timeout=600s \
  --max-retries=1 \
  --project=rotten-tomatoes-scraper
```

Parameter notes:
- `--memory=2Gi`: Chrome + Python + BeautifulSoup needs ~800MB-1.2GB peak. 2GB gives headroom.
  The old VM (1GB RAM + 2GB swap) was regularly hitting swap — this removes that problem.
- `--cpu=1`: 1 vCPU is sufficient. Chrome doesn't parallelize significantly.
- `--task-timeout=600s`: 10 minutes. A full scrape of 4 movies takes ~5-8 minutes.
  If a run exceeds 10 minutes, something is wrong (hung Chrome, infinite loop) — kill it.
- `--max-retries=1`: If a job fails, retry once. After 2 failures, Cloud Scheduler logs
  the error. This handles transient Neon cold starts or RT network blips.
- `--set-secrets`: Injects DATABASE_URL from Secret Manager as an environment variable.

---

## 2. Verify the Job Exists

```bash
gcloud run jobs describe rt-scraper --region=us-east1
```

Check that:
- Image is correct
- `DATABASE_URL` appears in the secrets section
- Memory and timeout match what was set

---

## 3. Run the Job Manually (Smoke Test)

Execute the job once to verify it works end-to-end before setting up the scheduler:

```bash
gcloud run jobs execute rt-scraper \
  --region=us-east1 \
  --wait
```

The `--wait` flag blocks until the job completes (or fails) and streams logs.

Expected output: scraper logs, then "Inserted N new reviews for [slug]" for each movie.

If the job fails, check logs:
```bash
gcloud run jobs executions list --job=rt-scraper --region=us-east1
```

Then view a specific execution's logs:
```bash
gcloud run jobs executions logs EXECUTION_NAME --region=us-east1
```

Or in Cloud Console: Cloud Run → Jobs → rt-scraper → Executions → select one → Logs.

---

## 4. Common Failure Modes

**"Failed to connect to DATABASE_URL"**
- Check the secret value: `gcloud secrets versions access latest --secret=DATABASE_URL`
- Verify the Cloud Run SA has secretAccessor role (step 03_secret_manager)

**"Chrome failed to start" / "DevToolsActivePort file doesn't exist"**
- The `--no-sandbox` flag in `_build_driver()` must be present
- Memory may be insufficient — try increasing to `--memory=4Gi` temporarily

**"No module named 'psycopg2'"**
- The Docker image doesn't include the updated dependencies
- Rebuild and repush the image (step 06), then update the job:
  ```bash
  gcloud run jobs update rt-scraper \
    --image=us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:latest \
    --region=us-east1
  ```

**Job times out (600s exceeded)**
- A movie page with hundreds of reviews may take longer
- Increase timeout: `gcloud run jobs update rt-scraper --task-timeout=1200s --region=us-east1`
- Or reduce enabled movies in movies.json

---

## 5. Updating the Job Config

If you need to change memory, timeout, env vars, or secrets after initial creation:

```bash
gcloud run jobs update rt-scraper \
  --region=us-east1 \
  [--memory=4Gi] \
  [--task-timeout=900s] \
  [--set-env-vars=KEY=VALUE]
```

Updating the image (what GitHub Actions does on each deploy):
```bash
gcloud run jobs update rt-scraper \
  --image=us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:NEW_SHA \
  --region=us-east1
```

---

## 6. Viewing Logs Ongoing

Cloud Run Jobs write logs to Cloud Logging. View recent executions:
```bash
gcloud logging read \
  'resource.type="cloud_run_job" AND resource.labels.job_name="rt-scraper"' \
  --limit=50 \
  --format="table(timestamp, textPayload)"
```

Or in Cloud Console: Logging → Log Explorer → filter by `resource.type="cloud_run_job"`.
