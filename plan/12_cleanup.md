# Cleanup

Only run this AFTER step 11_verification confirms the new system is working correctly.
These actions are destructive and irreversible.

---

## 1. Delete the GCP VM

```bash
gcloud compute instances delete rt-scraper \
  --zone=us-east1-b \
  --project=rotten-tomatoes-scraper
```

Type `Y` to confirm. This is permanent — the VM's disk (including reviews.db) is deleted.
The SQLite data is not being migrated (see 10_data_migration.md for rationale).

---

## 2. Delete the GCS Backup Bucket

The GCS bucket stored daily SQLite backups. No longer needed.

Delete the bucket:
```bash
gsutil rm -r gs://rotten-tomatoes-scraper-backups
```

---

## 3. Remove Old GitHub Actions Roles from `github-deploy` SA

The old VM-related IAM roles are no longer needed. Remove them:

```bash
# List current roles
gcloud projects get-iam-policy rotten-tomatoes-scraper \
  --flatten="bindings[].members" \
  --filter="bindings.members:github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com" \
  --format="table(bindings.role)"
```

Remove any roles related to `compute.instanceAdmin` or SSH access:
```bash
gcloud projects remove-iam-policy-binding rotten-tomatoes-scraper \
  --member="serviceAccount:github-deploy@rotten-tomatoes-scraper.iam.gserviceaccount.com" \
  --role="roles/compute.instanceAdmin.v1"
```

Keep: `roles/artifactregistry.writer`, `roles/run.developer`.

---

## 4. Remove Files from the Repository

```bash
# Remove deploy directory
git rm -r deploy/

# Remove Datasette config
git rm metadata.yml

# Remove reference CSVs (not tracked by git, but clean up locally)
rm -f *_reference.csv

# Remove SQLite WAL files (not tracked by git)
rm -f reviews.db-shm reviews.db-wal
```

---

## 5. Remove Old Code from the Repository

Delete the old SQLite backfill script (replaced by new Postgres-native backfill):
```bash
git rm scripts/backfill.py
```

---

## 6. Update CLAUDE.md

After cleanup, CLAUDE.md has stale information:
- File structure section references `deploy/`, `metadata.yml`, `scripts/backfill.py`
- Implementation status references pre-check, reconciliation, dual-window, Datasette
- Database schema section references the old SQLite columns
- GCP VM Details section references the deleted VM
- Dependencies section references old packages
- Test count (92) will change after test rewrite

Update CLAUDE.md to reflect the new architecture. This is not optional — stale docs
cause confusion in future sessions.

---

## 7. Commit the Cleanup

```bash
# Stage only the specific changes — avoid git add -A which may pick up local files
git add rotten_tomatoes.py pyproject.toml uv.lock Dockerfile .dockerignore \
  .github/workflows/deploy.yml scripts/backfill.py \
  CLAUDE.md README.MD
# Stage removals explicitly
git rm -r deploy/ metadata.yml
git commit -m "Migrate to Cloud Run Jobs + Neon; remove VM deploy infrastructure"
git push origin main
```

Confirm GitHub Actions runs successfully with the new workflow (builds Docker image,
pushes to Artifact Registry, updates Cloud Run Job).

---

## 8. Disable Old Cloud Monitoring Alert

The old Cloud Monitoring alert was based on `scraper.log` entries from the VM via
Ops Agent. With the VM gone, the Ops Agent is gone, and those log entries no longer exist.

In Cloud Console: Monitoring → Alerting → find the scraper alert → delete it.

New alerting option (optional): set up a Cloud Monitoring alert on Cloud Run Job
execution failures. This is simpler — Cloud Run automatically reports failed executions
as metrics.

---

## Post-Cleanup State

What remains in GCP:
- Artifact Registry: `rt-scraper` repository with Docker images
- Cloud Run Job: `rt-scraper` in `us-east1`
- Cloud Scheduler: `rt-scraper-schedule` in `us-east1`
- Secret Manager: `DATABASE_URL`
- Neon: `rotten-tomatoes` project with reviews data

What remains in the repo:
```
rotten_tomatoes.py      # rewritten
movies.json             # unchanged
pyproject.toml          # updated dependencies
uv.lock                 # regenerated
Dockerfile              # new
.dockerignore           # new
scripts/
  backfill.py           # rewritten for Postgres
tests/
  test_rotten_tomatoes.py  # needs updating (SQLite → Postgres)
.github/
  workflows/
    deploy.yml          # rewritten
plan/                   # this plan directory
CLAUDE.md               # updated
README.MD               # updated
```
