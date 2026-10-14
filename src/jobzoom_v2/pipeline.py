from __future__ import annotations
import json
from dataclasses import dataclass
from pathlib import Path

from .model import QueryKey, Stage
from .store import Store, canonical_sha


@dataclass(frozen=True)
class Runtime:
    daily_query_target: int
    max_requests_per_run: int
    max_requests_rolling_24h: int
    minimum_request_gap_seconds: float
    query_checkpoint_ttl_seconds: int
    description_ttl_seconds: int


def _config(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_matrix(path: Path) -> list[QueryKey]:
    cfg = _config(path)
    search = cfg.get("search", cfg)
    titles = [str(value).strip() for value in search["titles"] if str(value).strip()]
    countries = [str(value).strip() for value in search["countries"] if str(value).strip()]
    matrix = [QueryKey(title, country) for title in titles for country in countries]
    if not matrix:
        raise ValueError("search_matrix_empty")
    if len({q.identity for q in matrix}) != len(matrix):
        raise ValueError("matrix_contains_duplicates")
    return matrix


def load_runtime(path: Path) -> Runtime:
    cfg = _config(path)
    runtime_cfg = cfg.get("runtime", cfg)
    search = cfg.get("search", {})
    daily_query_target = int(
        runtime_cfg.get(
            "daily_query_target",
            len(search.get("titles", [])) * len(search.get("countries", [])),
        )
    )
    runtime = Runtime(
        daily_query_target=daily_query_target,
        max_requests_per_run=int(runtime_cfg["max_requests_per_run"]),
        max_requests_rolling_24h=int(runtime_cfg["max_requests_rolling_24h"]),
        minimum_request_gap_seconds=float(runtime_cfg["minimum_request_gap_seconds"]),
        query_checkpoint_ttl_seconds=int(runtime_cfg["query_checkpoint_ttl_seconds"]),
        description_ttl_seconds=int(runtime_cfg["description_ttl_seconds"]),
    )
    if runtime.daily_query_target < 1:
        raise ValueError("daily_query_target_must_be_positive")
    if runtime.max_requests_per_run < runtime.daily_query_target:
        raise ValueError("per_run_limit_below_query_target")
    if runtime.max_requests_rolling_24h < runtime.max_requests_per_run:
        raise ValueError("rolling_limit_below_per_run_limit")
    if runtime.minimum_request_gap_seconds < 4:
        raise ValueError("request_gap_too_small")
    return runtime


class Pipeline:
    def __init__(self, store: Store, matrix: list[QueryKey], runtime: Runtime):
        self.store = store
        self.matrix = matrix
        self.runtime = runtime

    def create(self, run_id: str, run_date: str):
        digest = self.store.create_run(run_id, run_date, self.matrix)
        self.store.transition(run_id, Stage.DISCOVERY)
        return digest

    def assert_discovery_complete(self, run_id: str):
        counts = self.store.query_counts(run_id)
        if counts != {"completed": len(self.matrix)}:
            raise ValueError(f"discovery_incomplete:{counts}")
        self.store.transition(run_id, Stage.ENRICHMENT)

    def finish_enrichment(self, run_id: str):
        if Stage(self.store.run(run_id)["stage"]) != Stage.ENRICHMENT:
            raise ValueError("wrong_stage")
        self.store.transition(run_id, Stage.SCORING)

    def finish_scoring(self, run_id: str):
        if Stage(self.store.run(run_id)["stage"]) != Stage.SCORING:
            raise ValueError("wrong_stage")
        self.store.transition(run_id, Stage.CVS)

    def finish_cvs(self, run_id: str):
        self.store.transition(run_id, Stage.REPORT)

    def finish_report(self, run_id: str):
        self.store.transition(run_id, Stage.DELIVERY)

    def finish_delivery(self, run_id: str):
        if not self.store.completion_ready(run_id):
            raise ValueError("completion_evidence_missing")
        self.store.transition(run_id, Stage.COMPLETED)

    def pause(self, run_id: str, reason: str):
        stage = Stage(self.store.run(run_id)["stage"])
        if stage not in {Stage.DISCOVERY, Stage.ENRICHMENT, Stage.SCORING}:
            raise ValueError("pause_not_allowed_here")
        self.store.transition(run_id, Stage.PAUSED, reason)

    def matrix_sha256(self) -> str:
        return canonical_sha([[q.title, q.country] for q in self.matrix])


    def resume_discovery(self, run_id: str) -> int:
        row = self.store.run(run_id)
        if Stage(row["stage"]) != Stage.PAUSED:
            raise ValueError("run_not_paused")
        seq = self.store.requeue_deferred_query(run_id)
        self.store.transition(run_id, Stage.DISCOVERY)
        return seq

    def resume_enrichment(self, run_id: str):
        row = self.store.run(run_id)
        if Stage(row["stage"]) != Stage.PAUSED:
            raise ValueError("run_not_paused")
        self.store.transition(run_id, Stage.ENRICHMENT)


    def resume_scoring(self, run_id: str):
        row = self.store.run(run_id)
        if Stage(row["stage"]) != Stage.PAUSED:
            raise ValueError("run_not_paused")
        self.store.transition(run_id, Stage.SCORING)
