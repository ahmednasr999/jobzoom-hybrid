# Legacy Node.js collection tools retained for reference

These scripts mirror the earlier Windows/Node extraction workflow and keep the CSV data contract visible:

- `scrape_searches.js` - searches LinkedIn guest endpoints and emits `Country,Job Title,Job Link`.
- `fetch_job_details.js` - resolves unique job IDs and emits job details.
- `verify_search_output.js` - read-only verification of the search output.

They are optional. The production backend for this project is the Python JobZoom V2 implementation under `src/jobzoom_v2`.
