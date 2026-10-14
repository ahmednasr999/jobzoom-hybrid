# JobZoom V2

JobZoom V2 is a self-hosted daily job-discovery workflow. It searches LinkedIn public guest job listings, validates and deduplicates results, retrieves descriptions, scores relevant roles against a master CV, generates tailored CVs, creates a daily PDF and ZIP package, delivers them to Telegram, and records owner-confirmed applications so they do not reappear.

This portable edition contains no user CV, Telegram IDs, tokens, historical job data, reports, application history, or production database.

## Workflow

1. Build the configured title × country search matrix.
2. Search LinkedIn public guest job listings with conservative pacing.
3. Validate job IDs, canonical URLs, locations, dates, and response markup.
4. Save completed queries and every provider request in SQLite.
5. Deduplicate by LinkedIn job ID.
6. Exclude jobs already marked applied.
7. Apply title, location, seniority, employment-type, and eligibility gates.
8. Retrieve full descriptions for surviving jobs.
9. Score complete descriptions against the user's master CV through an OpenClaw-compatible gateway.
10. Generate a tailored CV for every role above the report threshold.
11. Generate jobzoom-YYYYMMDD.pdf.
12. Build and integrity-check jobzoom-YYYYMMDD.zip.
13. Send a Telegram summary, PDF, and ZIP.
14. Require native Telegram receipts before the run becomes completed.
15. Record explicit owner-confirmed applications and suppress them in future runs.

## Important behavior

JobZoom does not log in to LinkedIn and does not use a LinkedIn account, cookies, browser session, or profile credentials.

JobZoom does not submit job applications.

A generated PDF is not completion. A run becomes completed only after the configured search matrix finishes and the summary, PDF, and ZIP have confirmed Telegram receipts.

HTTP 429, access restrictions, challenges, redirects, and unknown response markup stop the workflow while preserving completed checkpoints.

## Requirements

- Linux
- Python 3.11 or newer
- OpenClaw installed on the host
- A working OpenClaw model route
- A Telegram destination configured in OpenClaw
- pdfinfo is recommended for CV page-count validation
- Cron is optional

## Quick start

~~~bash
./scripts/install.sh
.venv/bin/jobzoom init
~~~

Replace config/cv-master.md with your real master CV in the same Markdown structure.

Review config/jobzoom.json, especially candidate name, timezone, titles, countries, scoring model, Telegram target, thresholds, request budgets, and optional compensation targets.

Then:

~~~bash
.venv/bin/jobzoom preflight
.venv/bin/jobzoom run
.venv/bin/jobzoom status
~~~

## Daily schedule

Print the cron entry:

~~~bash
.venv/bin/jobzoom schedule --hour 5 --minute 0
~~~

Install it:

~~~bash
.venv/bin/jobzoom schedule --hour 5 --minute 0 --install
~~~

The configured profile timezone is written as CRON_TZ.

The scheduled job uses the idempotent jobzoom daily command. It suppresses a duplicate launch after a completed same-day cycle, resumes exactly one unfinished same-day run, and fails closed on an older unresolved run.

## Search configuration

The example configuration contains 25 senior digital, transformation, PMO, program, portfolio, healthcare-technology, and AI titles across six GCC countries. That produces 150 queries.

The matrix is configurable. Ten titles across three countries produces 30 queries. Twenty-five titles across six countries produces 150.

For countries outside the built-in GCC location aliases, add search.location_aliases to config/jobzoom.json.

## Request controls

The example uses a four-second minimum provider-request gap, 1,200 requests per run, a 2,000-request rolling 24-hour limit, six-hour search checkpoints, and 24-hour description reuse.

Every provider request is reserved in SQLite before dispatch so a restart cannot erase request accounting.

## Scoring

The default rubric is title alignment 25, industry fit 25, skills match 25, seniority alignment 15, and location fit 10.

The scorer is instructed to use only the supplied master CV and job evidence.

The example thresholds are 70+ for the daily package and 82+ for readiness review. Both are configurable.

## Telegram delivery

The delivery order is:

1. 🔍 JobZoom Daily: X matches found across Y countries
2. jobzoom-YYYYMMDD.pdf
3. jobzoom-YYYYMMDD.zip

All three deliveries are bound to native Telegram message IDs and SHA256 evidence.

Files are staged in an OpenClaw-approved media root and must remain hash-identical to the original artifacts. Ambiguous delivery failures are not blindly resent.

## Applied-job tracking

JobZoom records applications only after explicit owner confirmation.

Use scripts/mark_report_applied.py with the report date, application date, confirmation message ID, delivered PDF message ID, exact confirmation text, timestamp, --all, and --dry-run.

Run the dry run first. If the report, manifest, score rows, delivered PDF hash, reply binding, and opportunity count validate, repeat the same command without --dry-run.

Applied jobs are excluded before enrichment, before scoring, and before final packaging.

## Data and privacy

Git ignores config/jobzoom.json, config/cv-master.md, data, reports, environment files, SQLite files, logs, keys, and secret files.

Do not commit personal CVs, Telegram targets, gateway tokens, production databases, reports, or application history.

## Tests

~~~bash
python -m unittest discover -s tests -v
~~~

Tests cover state transitions, request budgets, pacing, LinkedIn validation, enrichment, scoring checkpoints, CV generation, packaging, delivery receipts, application recording, and resume behavior.

## LinkedIn note

JobZoom is not affiliated with or endorsed by LinkedIn. The discovery adapter reads public guest job-listing endpoints and includes conservative pacing, request budgets, and fail-closed restriction handling. Users are responsible for operating the software in accordance with applicable terms, laws, and policies.

## License

MIT

## Hybrid note

JobZoom Hybrid combines the JobZoom V2 Python workflow with a portable Node.js CSV collection and verification reference. See HYBRID_NOTES.md.
