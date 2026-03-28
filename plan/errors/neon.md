# Error Playbook: Neon (Serverless Postgres)

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| D-1 | Cold start exceeds psycopg2 connect timeout | Low-Medium | Job fails |
| D-2 | Free tier compute hours exhausted | Medium (see analysis) | All connections refused for rest of month |
| D-3 | Free tier storage (500MB) exhausted | Very Low | Inserts fail |
| D-4 | Neon endpoint unreachable (AWS outage) | Very Low | Connection timeout |
| D-5 | DATABASE_URL malformed (trailing newline, wrong format) | Medium (first setup) | Connection error |
| D-6 | DATABASE_URL missing from Cloud Run env | Low | KeyError crash |
| D-7 | SSL handshake failure (`sslmode=require` needed) | Low | Connection refused |
| D-8 | Connection dropped mid-transaction (Neon scales to zero) | Very Low | OperationalError |
| D-9 | Too many concurrent connections | Low | Connection refused |
| D-10 | Neon password rotation or endpoint change | Low | Auth failure |

---

## Compute Hours Analysis (Critical)

| Parameter | Value |
|---|---|
| Free tier limit | 190 compute hours/month |
| Schedule interval | Every 50 minutes |
| Runs per month | ~864 |
| Auto-suspend delay | 5 minutes after last connection closes |

**With "connect late" pattern (recommended):**
- DB connection open for ~30 seconds per run (batch insert only)
- Neon awake for: 30 sec + 5 min cooldown = ~5.5 min per run
- Total: 864 x 5.5 / 60 = **79 compute hours/month** (42% of limit)

**Without "connect late" (connection open during scrape):**
- DB connection open for ~7 min per run
- Neon awake for: 7 min + 5 min cooldown = ~12 min per run
- Total: 864 x 12 / 60 = **173 compute hours/month** (91% of limit, 9% margin)

**The "connect late" pattern is required to stay safely within free tier.**

---

## Prevention

**D-1 (cold start timeout):**
- Neon auto-suspends after 5 min idle. With 50-min intervals, DB is ALWAYS cold.
- Cold start: typically 1-3 sec, up to 5-7 sec under load.
- Add explicit connect timeout: `psycopg2.connect(os.environ["DATABASE_URL"], connect_timeout=10)`
- Cloud Run `max-retries=1` provides a second attempt if first connection fails.

**D-2 (compute hours):**
- Use "connect late" pattern (connect only for insert, not during scrape)
- Monitor usage weekly: Neon dashboard → Project Settings → Usage
- Set mental alert at 150 hours (79% of limit)
- Have upgrade path ready: Neon Launch plan, $19/month, 300 compute hours

**D-5 (malformed secret):**
- Use `echo -n` (no trailing newline) when setting the secret
- Verify: `gcloud secrets versions access latest --secret=DATABASE_URL | xxd | tail -1`
  should NOT show `0a` (newline) at end

**D-6 (missing env var):**
- `os.environ["DATABASE_URL"]` throws `KeyError` if missing — fails fast, clear error
- Log presence (not value) of DATABASE_URL at startup

---

## When Would Neon Refuse a Connection?

1. **Cold start wakeup fails**: Rare. Retry handles it.
2. **Free tier compute hours exhausted**: Compute stops, all connections refused until
   billing cycle resets (1st of month).
3. **Too many concurrent connections**: Unlikely with single-connection pattern.
4. **Password/endpoint changed**: Immediate auth failure.
5. **Neon platform outage**: Check https://neonstatus.com/

---

## What Happens When Free Tier is Exhausted

- Neon compute endpoint suspends and will NOT resume until next billing cycle
- All connections refused for rest of month
- Scraper runs fine (Chrome works), but all inserts fail → reviews lost
- Cloud Run logs fill with identical psycopg2 connection errors every 50 min
- Fix: upgrade to Launch plan ($19/month) or wait for month reset

---

## Detection

| Signal | Failure |
|---|---|
| `could not connect to server: Connection refused` | D-1, D-2, D-4, D-9 |
| `FATAL: password authentication failed` | D-5, D-10 |
| `KeyError: 'DATABASE_URL'` | D-6 |
| `server closed the connection unexpectedly` | D-8 |
| `remaining connection slots are reserved` | D-9 |

---

## Diagnosis Decision Tree

```
Database connection error
|
+-> "password authentication failed"?
|   +-> YES: Check DATABASE_URL secret value:
|   |        gcloud secrets versions access latest --secret=DATABASE_URL
|   |        Check for trailing newline
|   |        Check if Neon password was rotated
|   +-> NO: Continue
|
+-> "Connection refused"?
|   +-> YES: Check Neon dashboard for compute status.
|   |        Project suspended (quota exceeded)?
|   |        +-> YES: Free tier exhausted. Upgrade or wait for month reset.
|   |        +-> NO: Check https://neonstatus.com/ for outage.
|   |              Did Cloud Run retry succeed?
|   |              +-> YES: Transient cold start. No action needed.
|   |              +-> NO: Neon endpoint may be down. Wait and retry manually.
|   +-> NO: Continue
|
+-> "KeyError: 'DATABASE_URL'"?
|   +-> YES: Secret not injected.
|   |        gcloud run jobs describe rt-scraper --region=us-east1 | grep -i secret
|   |        Check IAM: secretAccessor role on compute SA
|   +-> NO: Continue
|
+-> "server closed the connection unexpectedly"?
    +-> YES: Connection dropped mid-transaction.
    |        With "connect late" pattern, this is rare (connection open ~30 sec).
    |        Retry should handle it.
    +-> NO: Examine full traceback and psycopg2 error code.
```

---

## Research

- Neon free tier and compute hours: https://neon.tech/docs/introduction/plans#free-tier
- Neon cold start / auto-suspend: https://neon.tech/docs/introduction/auto-suspend
- Neon connection pooling (PgBouncer): https://neon.tech/docs/connect/connection-pooling
- psycopg2 connect parameters: https://www.psycopg.org/docs/module.html#psycopg2.connect
- Neon status: https://neonstatus.com/
- Neon compute hours FAQ: https://neon.tech/docs/introduction/usage-metrics
