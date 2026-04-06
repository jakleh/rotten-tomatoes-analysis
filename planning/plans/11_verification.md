# Verification — End-to-End Test

Run this before deleting the VM or making any cleanup changes.
The goal: confirm the new system is collecting data correctly.

---

## 1. Confirm Schema in Neon

```sql
\d reviews
```

Expected: 14 columns as defined in 02_neon_setup.md.

---

## 2. Confirm Migration Data

```sql
SELECT movie_slug, COUNT(*) as review_count
FROM reviews
GROUP BY movie_slug
ORDER BY review_count DESC;
```

Compare against SQLite counts:
```bash
sqlite3 reviews.db "SELECT movie_slug, COUNT(*) FROM reviews GROUP BY movie_slug;"
```

Counts should match.

---

## 3. Manually Trigger a Cloud Run Execution

```bash
gcloud run jobs execute rt-scraper --region=us-east1 --wait
```

Watch the logs for:
- Successful Neon connection (no connection error)
- Scrape output per movie ("Found N review cards...")
- Insert count ("Inserted N new reviews for...")

If 0 new reviews: that's correct if no reviews were published since migration. Check
the review count in Neon before and after to confirm the connection is working.

---

## 4. Verify a New Row Was Inserted (Force Test)

Temporarily enable a movie that hasn't been scraped yet (set `"enabled": true` in
`movies.json` for one of the disabled entries), push to main, wait for CI to deploy,
then manually trigger. You should see new rows for that movie in Neon.

Reset `movies.json` afterward if needed.

---

## 5. Verify Cloud Scheduler is Firing

Wait 50-60 minutes after enabling the scheduler, then check:
```bash
gcloud scheduler jobs describe rt-scraper-schedule --location=us-east1
```

Look at `lastAttemptTime` — it should be within the last 50 minutes.

Also check execution history:
```bash
gcloud run jobs executions list --job=rt-scraper --region=us-east1 --limit=5
```

Each execution should show `SUCCEEDED`.

---

## 6. Verify GitHub Actions Deploy Works

Push a trivial change (e.g., add a comment to `rotten_tomatoes.py`) to main.
Confirm:
1. GitHub Actions workflow runs and succeeds (green check)
2. New image tag appears in Artifact Registry:
   ```bash
   gcloud artifacts docker images list us-east1-docker.pkg.dev/rotten-tomatoes-scraper/rt-scraper
   ```
3. Cloud Run Job is updated to use the new SHA:
   ```bash
   gcloud run jobs describe rt-scraper --region=us-east1 | grep image
   ```

---

## 7. Failure Checklist

| Symptom | Likely Cause | Fix |
|---|---|---|
| "could not connect to server" in job logs | Neon connection string wrong | Re-check secret value |
| "permission denied for table reviews" | Schema not created in right DB | Re-run schema SQL in Neon |
| Job exits immediately, 0 scrapes | CHROME_BIN env var not set | Verify --set-env-vars in job config |
| GitHub Actions fails at "Push Docker image" | Missing artifactregistry.writer role | Re-run step 01 IAM grants |
| Cloud Scheduler never triggers | Wrong URI format | Check Cloud Scheduler docs (questions.md) |
| Inserted counts are 0 every run | All reviews already in DB (correct behavior) | Check estimated_timestamp in Neon |
