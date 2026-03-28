# Error Playbook: RT HTML Parsing (Selectors & Element Structure)

## Failure Modes

| ID | Failure | Likelihood | Severity |
|---|---|---|---|
| C-1 | `review-card` tag renamed or restructured | Medium | 0 reviews parsed |
| C-2 | `slot="timestamp"` attribute changed | Medium | All timestamps NULL |
| C-3 | Timestamp format changes ("5 min" instead of "5m") | Low-Medium | Bad data or parse failures |
| C-4 | `score-icon-critics` or `sentiment` attr removed | Medium | All sentiment NULL |
| C-5 | Rating `span[slot="rating"]` structure changes | Medium | All scores NULL |
| C-6 | `rt-link[slot="name"]` changes | Medium | Names NULL, dedup hash changes |
| C-7 | `rt-link[slot="publication"]` changes | Medium | Publications NULL, dedup hash changes |
| C-8 | Written review slot name is wrong | High (TBD in plan) | written_review always NULL |
| C-9 | Dedup hash collision (reviewer changes score) | Very Low | Old score kept |

---

## Prevention

**C-1 through C-7 (selector changes):**
- All selectors extracted into `SELECTORS` dict at top of file
- When a selector fails, log the selector name and card position at WARNING
- When a critical selector fails across ALL cards → ERROR

**Critical vs non-critical fields:**
- **Critical** (ERROR if ALL cards miss it): `reviewer_name`, `tomatometer_sentiment`,
  `timestamp`, `subjective_score`
- **Non-critical** (WARNING if missing): `written_review`, `publication_name`

**C-3 (timestamp format):**
- Current `get_timestamp_unit()` checks last character. If RT changes "5m" to "5 min",
  it silently breaks (returns "date" for everything).
- Use regex: `r'^(\d+)\s*(m|min|h|hr|d|day)s?$'` for robustness
- Log WARNING when a timestamp can't be parsed

**C-6 / C-7 / C-9 (dedup hash sensitivity):**
- Hash = `MD5(movie_slug + reviewer_name + publication_name + subjective_score)`
- If a selector breaks and a field becomes NULL, the hash changes → duplicate rows
- Guard: if a scrape would insert >50 reviews for a single movie in one run, log ERROR
  and skip inserts for that movie (sudden spike suggests hash inputs changed)

---

## Detection

| Signal | Failure |
|---|---|
| `Parsed 0 reviews` but `Found N review cards` (N > 0) | Timestamp selector broken → stop-at-date triggers on first card |
| Sudden spike in inserts (200+ for a movie with 200 total) | Hash inputs changed, creating new hashes for all reviews |
| Specific field NULL for all reviews in a run | That field's selector broke |
| `Could not parse relative timestamp` warnings flooding | Timestamp format changed |

---

## Diagnosis Decision Tree

```
Unexpected parsing behavior
|
+-> Cards found but 0 reviews parsed?
|   +-> YES: Stop-at-date condition triggering on first card.
|   |        Timestamp selector returning empty strings?
|   |        get_timestamp_unit("") returns "date" (it does).
|   |        Fix: timestamp selector is broken. Inspect live RT HTML.
|   +-> NO: Continue
|
+-> Insert counts abnormally high (>50 for one movie)?
|   +-> YES: Dedup hash inputs changed. Check which field became NULL.
|   |        reviewer_name all NULL? -> name selector broke
|   |        publication_name all NULL? -> publication selector broke
|   |        subjective_score all NULL? -> rating selector broke
|   +-> NO: Continue
|
+-> Specific field NULL for all reviews?
    +-> tomatometer_sentiment: Check score-icon-critics tag + sentiment attr
    +-> subjective_score: Check span[slot="rating"] > span[style]
    +-> reviewer_name: Check rt-link[slot="name"]
    +-> publication_name: Check rt-link[slot="publication"]
    +-> All fields NULL: review-card internal structure changed entirely
```

---

## HTML Fixture Testing

Save RT page source to `tests/fixtures/reviews_page.html` (captured programmatically
from Selenium on first successful run). Tests verify each selector in `SELECTORS`
extracts expected data from the fixture.

When RT changes HTML: ERROR logs fire in production → save fresh HTML → update selectors
and fixture → tests pass again.

---

## Research

- RT does not publish an HTML contract. Selector stability depends on manual inspection.
- Web component slot docs: https://developer.mozilla.org/en-US/docs/Web/HTML/Element/slot
- BS4 attribute searching: https://www.crummy.com/software/BeautifulSoup/bs4/doc/
