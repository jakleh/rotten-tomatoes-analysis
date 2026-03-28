# Secret Manager Setup

The only secret is DATABASE_URL (Neon connection string).
It is stored in Google Secret Manager and injected into the Cloud Run Job as an env var.

---

## 1. Create the Secret

```bash
gcloud secrets create DATABASE_URL \
  --replication-policy="automatic" \
  --project=rotten-tomatoes-scraper
```

---

## 2. Set the Secret Value

Replace the placeholder with your actual Neon connection string from step 02_neon_setup:

```bash
echo -n "postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require" \
  | gcloud secrets versions add DATABASE_URL --data-file=-
```

The `-n` flag on echo is critical — it prevents a trailing newline from being included
in the secret value. A trailing newline would cause psycopg2 connection errors.

Verify the value was stored correctly (will print the connection string):
```bash
gcloud secrets versions access latest --secret=DATABASE_URL
```

---

## 3. Grant Cloud Run Job Access

Cloud Run Jobs run as the Compute Engine default service account. Grant it read access
to this secret.

First, get the project number (if you didn't note it in step 01):
```bash
PROJECT_NUMBER=$(gcloud projects describe rotten-tomatoes-scraper --format="get(projectNumber)")
```

Grant access:
```bash
gcloud secrets add-iam-policy-binding DATABASE_URL \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor" \
  --project=rotten-tomatoes-scraper
```

Verify:
```bash
gcloud secrets get-iam-policy DATABASE_URL --project=rotten-tomatoes-scraper
```

Expected: the Compute Engine default SA listed with role `roles/secretmanager.secretAccessor`.

---

## 4. Routine Maintenance Commands

**Rotate the secret** (e.g., if Neon password changes):
```bash
echo -n "new-connection-string" \
  | gcloud secrets versions add DATABASE_URL --data-file=-
```

The new version becomes `latest` immediately. Cloud Run Jobs pick it up on next execution
(because the job config references `DATABASE_URL:latest`).

After confirming the new version works, disable the old version:
```bash
# List versions
gcloud secrets versions list DATABASE_URL

# Disable old version (replace VERSION_ID with the old version number, e.g. "1")
gcloud secrets versions disable VERSION_ID --secret=DATABASE_URL
```

**View current value:**
```bash
gcloud secrets versions access latest --secret=DATABASE_URL
```

**List all secrets in project:**
```bash
gcloud secrets list --project=rotten-tomatoes-scraper
```

---

## 5. How the Secret Reaches the Scraper

In the Cloud Run Job config (step 07), the job is created with:
```
--set-secrets=DATABASE_URL=DATABASE_URL:latest
```

This tells Cloud Run: inject the latest version of the `DATABASE_URL` secret as an
environment variable named `DATABASE_URL`.

In `rotten_tomatoes.py`, the scraper reads it as:
```python
os.environ["DATABASE_URL"]
```

No secrets ever appear in code, config files, or logs.
