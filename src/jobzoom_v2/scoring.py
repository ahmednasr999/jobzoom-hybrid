from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass
from typing import Protocol

from .quality import complete_description
from .store import Store, canonical_sha


@dataclass(frozen=True)
class ScoreResult:
    score: float
    verdict: str
    rationale: dict

    def evidence_sha256(self) -> str:
        raw = json.dumps(
            {"score": self.score, "verdict": self.verdict, "rationale": self.rationale},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        return hashlib.sha256(raw).hexdigest()


class Scorer(Protocol):
    def score(self, job: dict) -> ScoreResult: ...


class ScoringEngine:
    def __init__(self, store: Store, scorer: Scorer):
        self.store = store
        self.scorer = scorer


    def run(self, run_id: str, jobs: list[dict]) -> dict:
        existing = self.store.score_bindings(run_id)
        completed = 0
        reused = 0
        for job in jobs:
            linkedin_id = str(job["linkedin_id"])
            if not complete_description(job):
                raise ValueError("complete_description_required")
            self.store.upsert_job(run_id, job)
            binding = (
                self.scorer.input_binding(job)
                if hasattr(self.scorer, "input_binding")
                else canonical_sha(job)
            )
            if existing.get(linkedin_id) == binding:
                reused += 1
                continue
            result = self.scorer.score(job)
            self.store.record_score(
                run_id,
                linkedin_id,
                result.score,
                result.verdict,
                result.evidence_sha256(),
                binding,
                str(getattr(self.scorer, "model", type(self.scorer).__name__)),
                result.rationale,
            )
            completed += 1
        return {
            "jobs": len(jobs),
            "new_scores": completed,
            "reused_scores": reused,
            "total_scores": self.store.score_count(run_id),
        }
