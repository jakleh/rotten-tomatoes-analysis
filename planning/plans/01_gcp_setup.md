# GCP Setup

## Prerequisites

- `gcloud` CLI installed and authenticated (`gcloud auth login`)
- Active project: `rotten-tomatoes-scraper`
- Billing enabled on the project

Verify active project:
```bash
gcloud config get-value project
# Expected: rotten-tomatoes-scraper
```

If not set:
```bash
gcloud config set project rotten-tomatoes-scraper
```

---

## 1. Enable Required APIs

```bash
gcloud services enable \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com
```

This takes ~30 seconds. Verify:
```bash
gcloud services list --enabled \
  --filter="name:(run.googleapis.com OR cloudscheduler.googleapis.com OR secretmanager.googleapis.com OR artifactregistry.googleapis.com)"
```

Expected: all four listed as ENABLED.

---

## 2. Create Artifact Registry Repository

This is where Docker images will be stored.

```bash
gcloud artifacts repositories create rt-scraper \
  --repository-format=docker \
  --location=us-east1 \
  --description="RT scraper Docker images"
```

Verify:
```bash
gcloud artifacts repositories list --location=us-east1
```

Expected: `rt-scraper` listed with format DOCKER.

---

## 3. Configure Local Docker Auth for Artifact Registry

Run this once on your local machine so `docker push` can authenticate:

```bash
gcloud auth configure-docker us-east1-docker.pkg.dev
```

This writes credentials to your Docker config. You'll need this in step 06_artifact_registry.

---

## 4. Grant GitHub Actions Service Account New Permissions

The existing `github-deploy` service account (used by GitHub Actions via WIF) currently has
permissions for SSH/SCP to the VM. It needs two new roles for the new setup.

Get the current project number:
```bash
gcloud projects describe rotten-tomatoes-scraper --format="get(projectNumber)"
# Note this value — you'll need it in step 07 for the Cloud Run service account
```

Grant Artifact Registry write permission (to push Docker images):
```bash
gcloud projects add-iam-policy-binding rotten-tomatoes-scraper \
  --member="serviceAccount:github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com" \
  --role="roles/artifactregistry.writer"
```

Grant Cloud Run Jobs update permission (to update the job image after each deploy):
```bash
gcloud projects add-iam-policy-binding rotten-tomatoes-scraper \
  --member="serviceAccount:github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com" \
  --role="roles/run.developer"
```

Verify both roles are attached:
```bash
gcloud projects get-iam-policy rotten-tomatoes-scraper \
  --flatten="bindings[].members" \
  --filter="bindings.members:github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com" \
  --format="table(bindings.role)"
```

Expected output includes both `roles/artifactregistry.writer` and `roles/run.developer`.

NOTE: You may want to remove the old VM-related roles from this service account once the VM
is deleted (step 12_cleanup). For now, leave them — they're harmless and the VM still exists.

---

## 5. Identify the Cloud Run Default Service Account

Cloud Run Jobs run as the Compute Engine default service account unless overridden.
This account needs permission to read secrets from Secret Manager.

```bash
PROJECT_NUMBER=$(gcloud projects describe rotten-tomatoes-scraper --format="get(projectNumber)")
echo "Default SA: ${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
```

Note this email — you'll use it in step 03_secret_manager.
