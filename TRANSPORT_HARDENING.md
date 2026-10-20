# Transport-error hardening

## Problem

A transient network blip (connection reset, timeout, TLS failure, or an
upstream 5xx) previously paused the whole run. The run had to be resumed by
hand, and an unresolved run from a previous day could block the next day's
scheduled cycle.

## What changed

### 1. Engines retry transient restrictions

Both `DiscoveryEngine` and `EnrichmentEngine` now retry transient
restrictions with exponential backoff before pausing:

- `transport_or_server_error`
- `provider_server_error`
- `node_timeout`
- `node_error`
- `transport_exception:*`
- `node_*`

Behavior:

- Up to 3 attempts.
- Backoff: 30s, 60s, 120s (capped at 600s).
- Every attempt is recorded in the request ledger.
- Provider holds (rate limit, access block, challenge, redirect) are **not**
  retried — they still pause immediately so the run stops hammering LinkedIn.

This is backend-agnostic: it works for the Python LinkedIn client and for the
Node transport, because the retry happens in the Python engine.

### 2. Stale runs no longer block the next day

`jobzoom daily` now auto-fails an unresolved run from a previous day
(`auto_fail_stale_runs`, default `true`) and starts a fresh cycle, instead of
raising `older_unresolved_run`. Same-day paused runs are still resumed.

### 3. Provider holds still expire

Live commands auto-expire holds older than `provider_hold_ttl_seconds`
(default 4 hours).

## VPS Node transport

The engine-level retry above covers the Node transport too. For best results,
also make the Node transport retry transient network errors itself, not just
HTTP 429:

- Retry on `fetch` rejection / `AbortError` / non-2xx 5xx, with backoff
  (for example 30s, 60s, 120s plus jitter).
- Only return a restriction after the retries are exhausted.
- Keep 429 backoff at 60/120/240s as before.

If the Node transport returns `transport_or_server_error` after its own
retries, the Python engine will retry the whole query/job again. That is a
belt-and-braces fallback, not the preferred path.

## Configuration

```json
"runtime": {
  "auto_fail_stale_runs": true,
  "provider_hold_ttl_seconds": 14400,
  "minimum_request_gap_seconds": 15.0
}
```

## Manual recovery

```bash
.venv/bin/jobzoom status          # see runs and holds
.venv/bin/jobzoom clear-hold      # release a provider hold early
.venv/bin/jobzoom recover         # mark unresolved runs failed
.venv/bin/jobzoom daily --force   # override same-day resume rules
```

## Testing

`tests/test_transient_retry.py` covers transient detection, backoff growth,
retry-once-success, and pause-after-exhaustion.
