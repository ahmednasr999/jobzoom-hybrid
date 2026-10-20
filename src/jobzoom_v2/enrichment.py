from __future__ import annotations
import hashlib
import uuid
import time
import requests
from dataclasses import dataclass
from typing import Protocol

from .budget import BudgetExceeded
from .discovery import (
    PROVIDER_HOLD_REASONS,
    TRANSIENT_ATTEMPTS,
    backoff_delay,
    is_transient_restriction,
)
from .store import Store


@dataclass(frozen=True)
class DescriptionResult:
    description: str
    request_count: int
    status_code: int
    restriction: str | None = None
    request_events: tuple[dict, ...] = ()


class DescriptionClient(Protocol):
    def fetch(self, linkedin_id: str) -> DescriptionResult: ...


class EnrichmentEngine:
    def __init__(self, store: Store, client: DescriptionClient,
                 description_ttl_seconds: int = 86400, clock=time.time):
        self.store = store
        self.client = client
        self.description_ttl_seconds = int(description_ttl_seconds)
        self.clock = clock

    def run(self, run_id: str, jobs: list[dict]) -> dict:
        enriched = []
        requests = 0
        now = self.clock()
        existing = {
            str(row["linkedin_id"]): row
            for row in self.store.jobs(run_id)
            if row.get("description_sha256")
            and row.get("description")
            and isinstance(row.get("description_at"), (int, float))
            and 0 <= now - float(row["description_at"]) <= self.description_ttl_seconds
        }
        for job in jobs:
            linkedin_id = str(job["linkedin_id"])
            if linkedin_id in existing:
                enriched.append(existing[linkedin_id])
                continue
            result = None
            for attempt in range(TRANSIENT_ATTEMPTS):
                try:
                    result = self.client.fetch(linkedin_id)
                except BudgetExceeded as exc:
                    return {
                        "complete": False,
                        "restriction": str(exc),
                        "requests": requests,
                        "jobs": enriched,
                    }
                except (requests.RequestException, OSError, TimeoutError) as exc:
                    reason = "transport_exception:" + type(exc).__name__
                    result = DescriptionResult(
                        description="", request_count=1, status_code=0,
                        restriction=reason,
                        request_events=({"status_code": 0, "outcome": reason},),
                    )
                requests += result.request_count
                events = result.request_events or tuple(
                    {"status_code": result.status_code,
                     "outcome": result.restriction or "ok"}
                    for _ in range(result.request_count)
                )
                if len(events) != result.request_count:
                    raise ValueError("request_evidence_count_mismatch")
                for event in events:
                    if event.get("request_id"):
                        continue
                    self.store.record_request(
                        run_id, uuid.uuid4().hex, "enrichment", None,
                        int(event["status_code"]), str(event["outcome"]),
                    )
                if not is_transient_restriction(result.restriction):
                    break
                if attempt < TRANSIENT_ATTEMPTS - 1:
                    time.sleep(backoff_delay(attempt))
            if result.restriction:
                if result.restriction in PROVIDER_HOLD_REASONS:
                    self.store.activate_provider_hold(run_id, result.restriction)
                return {
                    "complete": False,
                    "restriction": result.restriction,
                    "requests": requests,
                    "jobs": enriched,
                }
            row = dict(job)
            row["description"] = result.description
            row["description_sha256"] = hashlib.sha256(
                result.description.encode()
            ).hexdigest()
            row["description_at"] = self.clock()
            self.store.upsert_job(run_id, row)
            enriched.append(row)
        return {
            "complete": True,
            "restriction": None,
            "requests": requests,
            "jobs": enriched,
        }
