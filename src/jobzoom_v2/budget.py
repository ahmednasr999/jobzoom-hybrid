from __future__ import annotations
import time
import uuid

from .store import Store


class BudgetExceeded(RuntimeError):
    pass


class RunBudget:
    def __init__(self, store: Store, run_id: str,
                 per_run_limit: int, rolling_24h_limit: int,
                 clock=time.time, external_rolling_used: int = 0,
                 external_counter=None):
        self.store = store
        self.run_id = run_id
        self.per_run_limit = per_run_limit
        self.rolling_24h_limit = rolling_24h_limit
        self.clock = clock
        self.external_rolling_used = max(0, int(external_rolling_used))
        self.external_counter = external_counter

    def reserve(self, phase: str, query_seq: int | None) -> str:
        now = self.clock()
        external = (
            int(self.external_counter())
            if self.external_counter is not None
            else self.external_rolling_used
        )
        request_id = uuid.uuid4().hex
        try:
            self.store.reserve_request_atomic(
                self.run_id, request_id, phase, query_seq, now,
                self.per_run_limit, self.rolling_24h_limit,
                max(0, external),
            )
        except ValueError as exc:
            if str(exc) in {"per_run_request_limit", "rolling_24h_request_limit"}:
                raise BudgetExceeded(str(exc)) from exc
            raise
        return request_id

    def complete(self, request_id: str, status_code: int | None, outcome: str):
        self.store.complete_request(
            self.run_id, request_id, status_code, outcome
        )


    def reclassify(self, request_id: str, outcome: str):
        self.store.reclassify_request(self.run_id, request_id, outcome)
