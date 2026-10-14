# JobZoom Hybrid

This repository is a hybrid composition of two LinkedIn job-discovery approaches:

1. The robust JobZoom V2 Python workflow: pacing, SQLite accounting, validation, restriction detection, deduplication, enrichment, scoring, packaging, Telegram delivery, and application tracking.
2. A portable Node.js collection contract that demonstrates the CSV inputs/outputs and verification flow developed during the original extraction.

The Python implementation in `src/jobzoom_v2` remains the backend. The `legacy/node` scripts are retained as reference implementations and handoff tooling, not as the production runtime.

## v0.1.1 changes

- Unlimited pagination: set `results_per_query: null` in config to collect every page instead of stopping at 35.
- Exponential backoff with jitter on HTTP 429 responses (60s base, 30m max).
- Provider holds auto-expire after a configurable TTL (default 4 hours).
- `jobzoom recover` command to mark all unresolved runs as failed.
- `jobzoom daily --force` to override cross-day / multiple-unresolved blocks.
- `jobzoom run --backend node` to use the Node.js collection adapter.
- `NodeCollectionAdapter` implements the same QueryClient protocol as the Python backend.
