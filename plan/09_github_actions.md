# GitHub Actions CI/CD

Replaces the existing deploy.yml (which SCP'd files to the VM) with a workflow that
builds a Docker image, pushes it to Artifact Registry, and updates the Cloud Run Job.

---

## What Changes

Old workflow:
1. SSH into VM
2. SCP files
3. Run `uv sync`
4. Restart `rt-datasette` service

New workflow:
1. Authenticate to GCP (same WIF setup, no change)
2. Authenticate Docker to Artifact Registry
3. Build Docker image
4. Push image to Artifact Registry
5. Update Cloud Run Job to use new image

The WIF provider and service account are the same. No new GitHub secrets needed beyond
the roles granted in step 01_gcp_setup.

---

## New `deploy.yml`

Replace `.github/workflows/deploy.yml` entirely with:

```yaml
name: Deploy to Cloud Run

on:
  workflow_dispatch:
  push:
    branches: [main]
    paths:
      - "rotten_tomatoes.py"
      - "movies.json"
      - "pyproject.toml"
      - "uv.lock"
      - "Dockerfile"
      - ".github/workflows/**"

jobs:
  deploy:
    runs-on: ubuntu-latest

    permissions:
      contents: read
      id-token: write

    steps:
      - uses: actions/checkout@v4

      - uses: google-github-actions/auth@v2
        with:
          workload_identity_provider: ${{ secrets.WIF_PROVIDER }}
          service_account: ${{ secrets.WIF_SERVICE_ACCOUNT }}

      - uses: google-github-actions/setup-gcloud@v2

      - name: Authenticate Docker to Artifact Registry
        run: gcloud auth configure-docker us-east1-docker.pkg.dev --quiet

      - name: Build Docker image
        run: |
          docker build \
            -t us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:${{ github.sha }} \
            -t us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:latest \
            .

      - name: Push Docker image
        run: |
          docker push us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:${{ github.sha }}
          docker push us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:latest

      - name: Update Cloud Run Job
        run: |
          gcloud run jobs update rt-scraper \
            --image=us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:${{ github.sha }} \
            --region=us-east1
```

---

## How a Deploy Works

1. Push to `main` (or manually trigger via `workflow_dispatch`)
2. GitHub Actions runner (ubuntu-latest) builds the Docker image
3. Tags it with the git commit SHA AND `latest`
4. Pushes both tags to Artifact Registry
5. Updates the Cloud Run Job config to pin to the commit SHA image
6. Cloud Scheduler's next trigger uses the new image

Note: the Cloud Run Job update (step 5) does NOT restart any running execution — it only
affects the NEXT execution. If a job is currently running when you deploy, it completes
with the old image. The new image is used starting from the next scheduled trigger.

---

## Reliability of This Approach

**Can a failed deploy break the scheduler?**
No. Steps 1-4 (build + push) happen on GitHub's infrastructure. Step 5 (job update)
runs `gcloud run jobs update` — this is an atomic metadata update. If it fails:
- Cloud Scheduler keeps triggering the previous image (the one the job was using before)
- No data loss, no downtime
- The job update failure is logged in GitHub Actions — you'll see a red build

**Can a bad image break the scheduler?**
A bad image (e.g., Python error on startup) causes the Cloud Run execution to fail,
not the scheduler. Cloud Scheduler retries once (max-retries=1), then marks the
execution as failed. The NEXT scheduled trigger tries again with the same (bad) image.
Fix: push a working image → GitHub Actions updates the job → next execution succeeds.

---

## Trigger Path on `movies.json` Change

If you add or enable a new movie in `movies.json`, push to main:
1. GitHub Actions triggers
2. New image built (with updated `movies.json`)
3. Cloud Run Job updated
4. Next scrape execution picks up the new movie

No manual intervention needed.

---

## GitHub Secrets Required (Unchanged)

| Secret | Value |
|---|---|
| `WIF_PROVIDER` | `projects/PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-actions` |
| `WIF_SERVICE_ACCOUNT` | `github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com` |

These are unchanged from the existing setup. Verify they exist:
Go to GitHub repo → Settings → Secrets and variables → Actions.
