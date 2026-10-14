from __future__ import annotations
from dataclasses import dataclass
from enum import Enum




class Stage(str, Enum):
    NEW = "new"
    DISCOVERY = "discovery"
    ENRICHMENT = "enrichment"
    SCORING = "scoring"
    CVS = "cvs"
    REPORT = "report"
    DELIVERY = "delivery"
    COMPLETED = "completed"
    PAUSED = "paused"
    FAILED = "failed"


class QueryStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    DEFERRED = "deferred"


@dataclass(frozen=True)
class QueryKey:
    title: str
    country: str

    @property
    def identity(self) -> str:
        return f"{self.title}\n{self.country}"


@dataclass(frozen=True)
class DeliveryReceipt:
    kind: str
    message_id: str
    artifact_sha256: str


ALLOWED_TRANSITIONS = {
    Stage.NEW: {Stage.DISCOVERY},
    Stage.DISCOVERY: {Stage.ENRICHMENT, Stage.PAUSED, Stage.FAILED},
    Stage.ENRICHMENT: {Stage.SCORING, Stage.PAUSED, Stage.FAILED},
    Stage.SCORING: {Stage.CVS, Stage.PAUSED, Stage.FAILED},
    Stage.CVS: {Stage.REPORT, Stage.FAILED},
    Stage.REPORT: {Stage.DELIVERY, Stage.FAILED},
    Stage.DELIVERY: {Stage.COMPLETED, Stage.FAILED},
    Stage.PAUSED: {Stage.DISCOVERY, Stage.ENRICHMENT, Stage.SCORING},
    Stage.FAILED: set(),
    Stage.COMPLETED: set(),
}


def transition_allowed(current: Stage, target: Stage) -> bool:
    return target in ALLOWED_TRANSITIONS[current]
