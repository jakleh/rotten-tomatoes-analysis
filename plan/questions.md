# Open Questions

Things that need verification before or during implementation. Each is flagged at the
relevant step. Do not assume the answer — check the source.

---

## Q1: Cloud Scheduler "every N minutes" syntax — RESOLVED

**Answer:** Cloud Scheduler supports both standard unix cron AND Groc format (human-readable).
`every 50 minutes` is valid Groc syntax and gives true uniform 50-minute intervals.
Verified against GCP docs: https://cloud.google.com/scheduler/docs/configuring/cron-job-schedules

---

## Q2: Cloud Scheduler URI format for triggering Cloud Run Jobs — RESOLVED

**Answer:** The original URI was wrong (v1 format, wrong hostname). Correct v2 URI:
```
https://run.googleapis.com/v2/projects/PROJECT_ID/locations/REGION/jobs/JOB_NAME:run
```
Also requires `--message-body="{}"` (empty JSON body). Step 08 has been updated.
Verified against: https://cloud.google.com/run/docs/execute/jobs-on-schedule

---

## Q3: `chromium-driver` package name on Debian Bookworm

**Where it matters:** step 05_dockerfile.md

**Question:** Is `chromium-driver` the correct apt package name for ChromeDriver on
`python:3.14-slim-bookworm`? Or is it a different name (e.g., `chromium-chromedriver`)?

**Why it matters:** If the package name is wrong, `docker build` will fail.

**How to verify:**
```bash
docker run --rm python:3.14-slim-bookworm apt-cache search chromium
```
Run this before writing the Dockerfile. Find the package that provides `chromedriver`.

**Fallback:** Use a pre-built Selenium/Chrome Docker image as the base instead of
installing from apt. Search Docker Hub for `selenium/standalone-chrome` and use that
as the `FROM` image, then layer Python + uv on top.

---

## Q4: Written review slot name in RT `review-card` HTML

**Where it matters:** step 04_scraper_rewrite.md — the `get_reviews()` function

**Question:** What is the correct slot name for the review text in the RT `review-card`
web component? The plan uses `slot="content"` as a guess.

**Why it matters:** Wrong slot name means `written_review` is always NULL. Not a blocker
(we can store NULL and fix later), but worth getting right upfront.

**How to verify:** Inspect the RT reviews page HTML in browser DevTools. Open
`https://www.rottentomatoes.com/m/project_hail_mary/reviews/all-critics`, right-click
a review card, "Inspect Element," find the `<review-card>` element, look at its children
for a slot that contains the review text.

**Fallback:** If the review text isn't in the card HTML (RT may hide it), store NULL
and skip `written_review` for now. Add a note in CLAUDE.md.

---

## Q5: Neon connection string format for psycopg2

**Where it matters:** step 02_neon_setup.md, step 03_secret_manager.md

**Question:** Does Neon's connection string use `postgresql://` or `postgres://`?
Does psycopg2-binary accept both? Is `?sslmode=require` required?

**How to verify:** Check the Neon dashboard → Connection Details. Try both formats
locally with psycopg2 if unsure.

**Known:** psycopg2-binary accepts both `postgresql://` and `postgres://`. The
`?sslmode=require` suffix is required for Neon (TLS-only connections).

---

## Q6: `python:3.14-slim-bookworm` availability on Docker Hub

**Where it matters:** step 05_dockerfile.md

**Question:** Is `python:3.14-slim-bookworm` available on Docker Hub as of implementation time?

**How to verify:** `docker pull python:3.14-slim-bookworm`

**Fallback:** Use `python:3.14-slim` (without the `bookworm` suffix — defaults to latest
Debian). Or use `python:3.13-slim-bookworm` and update `pyproject.toml` to require `>=3.13`.

---

## Q7: Chrome/ChromeDriver version compatibility in the container

**Where it matters:** step 05_dockerfile.md

**Question:** When installing `chromium` and `chromium-driver` via apt on Bookworm,
are both packages guaranteed to be the same version?

**Why it matters:** A version mismatch between Chrome and ChromeDriver causes Selenium
to fail immediately with a version error.

**How to verify:** After building the Docker image, run:
```bash
docker run --rm IMAGE chromium --version
docker run --rm IMAGE chromedriver --version
```
The major version numbers must match.

**Mitigation:** Installing both packages from the same apt source (Debian repos) ensures
version matching. The risk only exists if we pin one version and not the other.

---

## Q8: Cloud Run Job default service account permissions

**Where it matters:** step 07_cloud_run_job.md, step 08_cloud_scheduler.md

**Question:** Does the Compute Engine default service account already have
`roles/run.invoker` (to allow Cloud Scheduler to trigger the job)?
Or does it need to be explicitly granted?

**How to verify:** Check the current IAM policy:
```bash
gcloud projects get-iam-policy rotten-tomatoes-scraper \
  --flatten="bindings[].members" \
  --filter="bindings.members:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --format="table(bindings.role)"
```

If `roles/run.invoker` is not listed, add it (step 08 includes this command).

---

## Q9: Neon free tier — does query compute count against limits?

**Where it matters:** step 02_neon_setup.md

**Question:** Neon's free tier includes "190 compute hours/month." Does this refer to:
a) Hours the Postgres server is awake (active connections), or
b) Something else?

**Why it matters:** If each Cloud Run Job invocation (holds a connection for ~5-8 minutes)
counts against compute hours: 870 runs/month × 7 min = ~100 compute hours/month.
That's within the 190-hour free tier but worth verifying.

**How to verify:** Check Neon's pricing page and free tier limits:
https://neon.tech/pricing

**Fallback:** If free tier is insufficient, Neon's Launch plan is $19/month.
