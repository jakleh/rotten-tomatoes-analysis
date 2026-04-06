# Error Playbook: GitHub Actions CI/CD

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| G-1 | WIF authentication fails | Low | Entire workflow fails |
| G-2 | Docker build fails (Dockerfile, apt, uv sync) | Medium | No new image |
| G-3 | Docker push fails (auth or quota) | Low | Image not available |
| G-4 | `gcloud run jobs update` fails (IAM, wrong job name) | Low | Image pushed but job not updated |
| G-5 | Workflow not triggered (path filter excludes changed files) | Medium | No deploy |
| G-6 | `uv.lock` stale (not regenerated after pyproject.toml change) | Medium | Build fails |

---

## Key Insight: CI/CD Failures Don't Break the Running System

Cloud Scheduler keeps triggering the last known-good image. A failed deploy only delays
new code — it never causes scraping downtime.

---

## Prevention

**G-2 (build failures):**
- Add sanity check before push:
  `docker run --rm IMAGE python -c "import rotten_tomatoes"` in the workflow
- Catches import errors before they make it into a deployed image

**G-5 (path filter):**
- deploy.yml triggers on: `rotten_tomatoes.py`, `movies.json`, `pyproject.toml`,
  `uv.lock`, `Dockerfile`, `.github/workflows/**`
- If you add new files to the Docker image, update the paths filter
- Safety net: `workflow_dispatch` allows manual triggering

**G-6 (stale lock):**
- `uv sync --frozen` in Dockerfile rejects stale lock files
- This is correct — forces `uv lock` locally before commit

---

## Diagnosis Decision Tree

```
GitHub Actions workflow failed
|
+-> Which step failed?
|
+-> "Authenticate to GCP"?
|   +-> Check WIF_PROVIDER and WIF_SERVICE_ACCOUNT secrets in GitHub
|   +-> Check Workload Identity Pool/Provider exists in GCP
|   +-> Check: was the service account disabled?
|
+-> "Build Docker image"?
|   +-> apt-get failure? -> Package name changed (see questions.md Q3)
|   +-> uv sync failure? -> Run `uv lock` locally, commit uv.lock
|   +-> Python syntax error? -> Fix code, push again
|
+-> "Push Docker image"?
|   +-> Auth failure? -> Docker auth step may have failed silently
|   +-> Quota? -> Check Artifact Registry billing
|
+-> "Update Cloud Run Job"?
|   +-> Permission denied? -> github-deploy SA needs roles/run.developer
|   +-> Job not found? -> Check job name and region match
|
+-> No workflow triggered at all?
    +-> Changed file match paths filter? Check deploy.yml paths
    +-> On main branch?
    +-> GitHub Actions enabled for repo?
    +-> Use workflow_dispatch to trigger manually
```

---

## Research

- GitHub Actions path filtering: https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions
- WIF for GitHub Actions: https://cloud.google.com/iam/docs/workload-identity-federation-with-other-providers
- google-github-actions/auth: https://github.com/google-github-actions/auth
