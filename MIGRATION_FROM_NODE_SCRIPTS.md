# Migration from ad-hoc Node scripts to JobZoom V2

This folder is a portable adaptation of `https://github.com/ahmednasr999/jobzoom-v2`, configured for the GCC search matrix used in the previous ad-hoc Node.js runs.

## What we did before

The previous approach consisted of two standalone Node.js scripts:

1. `scrape.js`
   - Read a 150-row CSV: `Country, Job Title, LinkedIn Guest Search URL`.
   - Converted `/jobs/search/?` URLs to `/jobs-guest/jobs/api/seeMoreJobPostings/search?`.
   - Paginated using `start=0,10,20,...`.
   - Extracted `/jobs/view/...` URLs with a regex.
   - Deduplicated within each search, but retained duplicates across searches.
   - Stopped on an empty page or a page with no newly added URLs.
   - Flushed the complete output CSV after every search, but did not resume from a partial file.
   - Used only a `User-Agent` header.
   - Used a roughly 0.25 second delay between pages.
   - Did not check HTTP status, redirects, challenge pages, or LinkedIn access restrictions.
   - Used naive CSV parsing.

2. `fetch_job_details.js`
   - Reduced 2,917 CSV rows to 737 unique LinkedIn job IDs.
   - Fetched `/jobs-guest/jobs/api/jobPosting/{id}`.
   - Extracted title, company, location, and full description.
   - Added retry logic and a 30 second timeout.
   - Checkpointed successful jobs in JSONL.
   - Merged all details back into the 2,917-row CSV.
   - Produced 2,917 descriptions with no missing values and no fetch failures.

Result of the previous approach:

- 150 searches attempted.
- 2,917 saved search links.
- 737 unique LinkedIn job IDs.
- 0 malformed job URLs.
- 0 duplicate `(Country, Job Title, Job ID)` records.
- 0 failed description fetches in the successful run.

## What JobZoom V2 improves

JobZoom V2 is the full production workflow. It specifically addresses the main weaknesses of the ad-hoc scripts:

| Area | Ad-hoc Node scripts | JobZoom V2 |
|---|---|---|
| Request cadence | ~0.25s page delay | Minimum 4 second provider gap by default |
| Provider accounting | No durable request accounting | Every request is reserved in SQLite before dispatch |
| Restart handling | Flushes CSV but does not resume correctly | Query checkpoints and provider budgets survive restarts |
| HTTP safety | Does not inspect status/restrictions | Stops on 429, 401, 403, 999, 5xx, redirects, and empty responses |
| Challenge handling | Empty body becomes empty results | Detects checkpoint/authwall/captcha markers explicitly |
| Discovery validation | Regex plus regex-only page parsing | Validates job ID, title, company, location, posted date, card markup |
| Date filtering | Relies on `f_TPR=r86400` only | Validates posted dates against the profile timezone and 24-hour window |
| Pagination safety | Stops on empty or no-new page | Detects repeated pages and known exhaustion fingerprints |
| Descriptions | Manual HTML markup extraction | Structured extraction and restriction handling |
| Description reuse | Not implemented | 24-hour description TTL |
| Deduplication | Per-search only | Global LinkedIn job ID deduplication |
| Applied exclusion | Not implemented | Explicit owner-confirmed applications suppress future runs |
| Resume/fail-closed behavior | Partial CSV written, but no real resume | Unfinished same-day run resumes; older unresolved run fails closed |
| Scraping only? | Yes | Adds scoring, tailored CV generation, PDF/ZIP packaging, Telegram receipts |
| Traceability | Progress text only | SQLite queries, jobs, restrictions, budgets, request events |

## Configuration in this package

`config/jobzoom.json` already contains the same 150-query matrix used by the previous CSV:

- 25 titles
- 6 countries
- 24-hour lookback
- Maximum 35 results per query

You must edit these before running:

- `profile.name`
- `profile.timezone` if needed
- `scoring.model`
- `telegram.target`
- OpenClaw settings if your host uses different defaults
- Replace `config/cv-master.md` with your actual CV in the supported Markdown structure

## Installation and first run

On Linux, install dependencies and the CLI:

```bash
./scripts/install.sh
.venv/bin/jobzoom init
```

Then review the config and CV:

```bash
.venv/bin/jobzoom preflight
.venv/bin/jobzoom run
.venv/bin/jobzoom status
```

## Daily scheduling

Print the cron line:

```bash
.venv/bin/jobzoom schedule --hour 5 --minute 0
```

Install it:

```bash
.venv/bin/jobzoom schedule --hour 5 --minute 0 --install
```

## Applied-job tracking

Use the provided helper after receiving a report:

```bash
python3 scripts/mark_report_applied.py ...
```

Consult `scripts/mark_report_applied.py --help` for required dates, message IDs, confirmation text, timestamp, `--all`, and `--dry-run`.

## Historical data from previous runs

The previous successful outputs are not imported into JobZoom automatically. They can be used as reference only:

- Original search plan: 150 queries
- Original saved links: 2,917 rows
- Unique jobs: 737
- Original successful run: 0 failed description fetches and 0 malformed URLs

If you want old jobs tracked as applied later, use the applied-job tracking workflow with explicit confirmation rather than automatically importing them.
