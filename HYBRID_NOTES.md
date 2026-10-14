# JobZoom Hybrid

This repository is a hybrid composition of two LinkedIn job-discovery approaches:

1. The robust JobZoom V2 Python workflow: pacing, SQLite accounting, validation, restriction detection, deduplication, enrichment, scoring, packaging, Telegram delivery, and application tracking.
2. A portable Node.js collection contract that demonstrates the CSV inputs/outputs and verification flow developed during the original extraction.

The Python implementation in `src/jobzoom_v2` remains the backend. The `legacy/node` scripts are retained as reference implementations and handoff tooling, not as the production runtime.
