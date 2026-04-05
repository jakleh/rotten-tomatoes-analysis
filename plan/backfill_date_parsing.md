# Plan: Backfill MM/DD/YYYY Timestamp Parsing

## Context

The backfill overnight run (attempt 1, 2026-04-05) revealed that Rotten Tomatoes uses `MM/DD/YYYY` format (e.g., `01/19/2025`, `12/29/2024`) for older reviews. The current `convert_rel_timestamp_to_abs()` in `rotten_tomatoes.py` only handles:

1. Relative formats: `5m`, `2h`, `3d` (via `RELATIVE_TS_PATTERN` regex)
2. Abbreviated date: `Mar 20` (via `strptime("%b %d %Y")`)

Reviews with `MM/DD/YYYY` timestamps hit the `except ValueError` branch, log a warning, and return `None`. The cutoff filter then excludes them (None timestamps are excluded by design). Result: 96-98% of reviews were dropped for most movies.

**Impact from overnight run:**
- `a_complete_unknown`: 403/416 reviews (97%) had None timestamps → 0 inserted
- `a_quiet_place_day_one`: 278/288 (97%) → 0 inserted
- `abigail_2024`: 257/263 (98%) → 0 inserted
- `alien_romulus`: 461/478 (96%) → 0 inserted

## Goals

1. Parse `MM/DD/YYYY` timestamps into absolute UTC datetimes
2. No change to main scraper behavior (it stops at "date" format before reaching these)
3. Minimal code change — one additional `try/except` block in the shared function

## Files to Modify

| File | Action |
|---|---|
| `rotten_tomatoes.py` | Add `MM/DD/YYYY` parsing branch in `convert_rel_timestamp_to_abs()` |
| `tests/test_rotten_tomatoes.py` | Add tests for `MM/DD/YYYY` format, edge cases |
| `plan/errors/backfill.md` | Update H-3 with root cause and fix; add H-17 for new format |
| `CLAUDE.md` | Update test count, timestamp format docs |

## Implementation

### Step 1: Add MM/DD/YYYY parsing to `convert_rel_timestamp_to_abs()`

In `rotten_tomatoes.py`, between the existing `"Mar 20"` try/except and the warning log, add:

```python
# Absolute date format with slashes ("01/19/2025")
try:
    parsed = datetime.strptime(rel_timestamp, "%m/%d/%Y").replace(tzinfo=timezone.utc)
    return parsed
except ValueError:
    pass
```

No year heuristic needed — the format already includes the full year.

Order of parsing attempts:
1. Relative (`5m`, `2h`, `3d`) — regex match
2. `"Mar 20"` — `strptime("%b %d %Y")` with year inference
3. `"01/19/2025"` — `strptime("%m/%d/%Y")` ← **NEW**
4. Warning + return None (fallback)

### Step 2: Update `get_timestamp_unit()` (no change needed)

`MM/DD/YYYY` doesn't match `RELATIVE_TS_PATTERN`, so `get_timestamp_unit()` returns `"date"`. This is correct — the main scraper uses this as a stop condition and would never try to parse these. The backfill script maps `"date"` to confidence `"d"`, which is correct for day-level precision.

### Step 3: Tests

New tests for `convert_rel_timestamp_to_abs()`:
- `test_slash_date_basic`: `"01/19/2025"` → `2025-01-19T00:00:00Z`
- `test_slash_date_dec_31`: `"12/31/2024"` → `2024-12-31T00:00:00Z`
- `test_slash_date_feb_29_leap`: `"02/29/2024"` → `2024-02-29T00:00:00Z`
- `test_slash_date_invalid`: `"13/40/2025"` → `None` (falls through to warning)

New test for `get_timestamp_unit()`:
- `test_slash_date_returns_date`: `"01/19/2025"` → `"date"`

## Risk Assessment

| Risk | Likelihood | Mitigation |
|---|---|---|
| Main scraper accidentally parses MM/DD/YYYY | None | Main scraper `_parse_cards()` breaks on `get_timestamp_unit() == "date"` before calling `convert_rel_timestamp_to_abs()` |
| RT uses other date formats (e.g., DD/MM/YYYY) | Very Low | All examples in logs are US-format MM/DD/YYYY; RT is a US site |
| Performance impact from extra try/except | None | Only reached for non-relative, non-"Mar 20" timestamps |

## What This Does NOT Cover

- **Neon SSL reconnect**: Separate issue (connection dropped on movie #8). Documented in error playbook, deferred.
- **Chrome retry with fresh driver**: Separate issue (dead session reused across retries). Documented in error playbook, deferred.
