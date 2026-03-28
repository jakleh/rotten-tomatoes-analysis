# Artifact Registry — Build and Push

This step pushes the first Docker image to GCP so Cloud Run has something to deploy.
After this, GitHub Actions handles all future builds automatically (step 09).

---

## Image Naming Convention

Full image name format:
```
REGION-docker.pkg.dev/PROJECT/REPOSITORY/IMAGE:TAG
```

For this project:
```
us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:latest
```

We also tag with the git commit SHA for traceability:
```
us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:COMMIT_SHA
```

---

## Build and Push (First Time, Manual)

Authenticate Docker with Artifact Registry (one-time, done in step 01):
```bash
gcloud auth configure-docker us-east1-docker.pkg.dev
```

Get current git commit SHA:
```bash
SHA=$(git rev-parse HEAD)
echo $SHA
```

Build and tag:
```bash
docker build \
  -t us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:latest \
  -t us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:${SHA} \
  .
```

Push both tags:
```bash
docker push us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:latest
docker push us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper/rt-scraper:${SHA}
```

Verify the image exists in Artifact Registry:
```bash
gcloud artifacts docker images list \
  us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper
```

Expected: one entry with tags `latest` and the SHA.

---

## After Initial Push

All future pushes are handled by GitHub Actions (step 09). You should never need to
manually push again unless debugging a broken CI build.

---

## Cleanup Policy (Optional, Recommended)

By default, every pushed image is retained forever. To avoid storage accumulating:

```bash
gcloud artifacts repositories update rt-scraper \
  --location=us-east1 \
  --cleanup-policy-file=- <<'EOF'
[
  {
    "name": "keep-last-10",
    "action": {"type": "Keep"},
    "condition": {"tagState": "tagged", "newerThan": "30d"}
  },
  {
    "name": "delete-old",
    "action": {"type": "Delete"},
    "condition": {"tagState": "any", "olderThan": "30d"}
  }
]
EOF
```

This keeps tagged images newer than 30 days and deletes older ones. The `latest` tag
is always kept (it gets re-tagged on each push). Adjust the policy to your preference.

NOTE: Artifact Registry storage is ~$0.10/GB/month. Each image is ~1.5GB compressed.
At 30 days retention, storage cost is negligible.
