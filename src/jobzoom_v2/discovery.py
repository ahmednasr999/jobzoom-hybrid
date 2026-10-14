from __future__ import annotations
import hashlib
import json
import uuid
import requests
from dataclasses import dataclass
from typing import Protocol

from .budget import BudgetExceeded
from .model import QueryKey, QueryStatus
from .pipeline import Pipeline
from .store import Store


PROVIDER_HOLD_REASONS = {
    "provider_rate_limit",
    "provider_access_restriction",
    "provider_challenge",
    "redirect_response",
}


@dataclass(frozen=True)
class QueryResult:
    jobs: list[dict]
    request_count: int
    status_code: int = 200
    restriction: str | None = None
    request_events: tuple[dict, ...] = ()

    def evidence_sha256(self) -> str:
        payload = {
            "jobs": self.jobs,
            "request_count": self.request_count,
            "status_code": self.status_code,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()


class QueryClient(Protocol):
    def search(self, query: QueryKey) -> QueryResult: ...


class DiscoveryEngine:
    def __init__(self, store: Store, pipeline: Pipeline, client: QueryClient):
        self.store = store
        self.pipeline = pipeline
        self.client = client

    def run(self, run_id: str) -> dict:
        for seq, query in enumerate(self.pipeline.matrix, 1):
            status = self.store.query_status(run_id, seq)
            if status == QueryStatus.COMPLETED.value:
                continue
            if status != QueryStatus.PENDING.value:
                raise ValueError(f"query_not_resumable:{seq}:{status}")
            self.store.start_query(run_id, seq)
            try:
                result = self.client.search(query)
            except BudgetExceeded as exc:
                self.store.fail_query(run_id, seq, str(exc), deferred=True)
                self.pipeline.pause(run_id, str(exc))
                return self.summary(run_id)
            except (requests.RequestException, OSError, TimeoutError) as exc:
                reason = "transport_exception:" + type(exc).__name__
                self.store.fail_query(run_id, seq, reason, deferred=True)
                self.pipeline.pause(run_id, reason)
                return self.summary(run_id)
            if result.request_count < 1:
                self.store.fail_query(run_id, seq, "invalid_request_count")
                self.pipeline.pause(run_id, "invalid_transport_evidence")
                return self.summary(run_id)
            events = result.request_events or tuple(
                {"status_code": result.status_code,
                 "outcome": result.restriction or "ok"}
                for _ in range(result.request_count)
            )
            if len(events) != result.request_count:
                raise ValueError("request_evidence_count_mismatch")
            for n, event in enumerate(events, 1):
                if event.get("request_id"):
                    continue
                request_id = f"{run_id}:{seq}:{n}:{uuid.uuid4().hex[:8]}"
                self.store.record_request(
                    run_id, request_id, "discovery", seq,
                    int(event["status_code"]), str(event["outcome"]),
                )
            if result.restriction:
                self.store.fail_query(run_id, seq, result.restriction, deferred=True)
                if result.restriction in PROVIDER_HOLD_REASONS:
                    self.store.activate_provider_hold(run_id, result.restriction)
                self.pipeline.pause(run_id, result.restriction)
                return self.summary(run_id)
            for job in result.jobs:
                row = dict(job)
                row.setdefault("country", query.country)
                row.setdefault("search_title", query.title)
                row.setdefault("search_country", query.country)
                self.store.upsert_job(run_id, row)
            self.store.complete_query(
                run_id, seq, len(result.jobs), result.evidence_sha256()
            )
        self.pipeline.assert_discovery_complete(run_id)
        return self.summary(run_id)

    def summary(self, run_id: str) -> dict:
        row = self.store.run(run_id)
        counts = self.store.query_counts(run_id)
        return {
            "run_id": run_id,
            "stage": row["stage"],
            "queries": counts,
            "requests": self.store.request_count(run_id),
            "unique_jobs": self.store.job_count(run_id),
            "planned_queries": len(self.pipeline.matrix),
        }
