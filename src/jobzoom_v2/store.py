from __future__ import annotations
import hashlib
import json
import sqlite3
import time
from pathlib import Path

from .model import QueryKey, QueryStatus, Stage, transition_allowed


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
CREATE TABLE IF NOT EXISTS runs(
  run_id TEXT PRIMARY KEY,
  run_date TEXT NOT NULL,
  stage TEXT NOT NULL,
  matrix_sha256 TEXT NOT NULL,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  failure_reason TEXT
);
CREATE TABLE IF NOT EXISTS queries(
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  seq INTEGER NOT NULL,
  title TEXT NOT NULL,
  country TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at REAL,
  completed_at REAL,
  result_count INTEGER,
  evidence_sha256 TEXT,
  error TEXT,
  PRIMARY KEY(run_id, seq),
  UNIQUE(run_id, title, country)
);
CREATE TABLE IF NOT EXISTS requests(
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  request_id TEXT NOT NULL,
  phase TEXT NOT NULL,
  query_seq INTEGER,
  dispatched_at REAL NOT NULL,
  status_code INTEGER,
  outcome TEXT,
  PRIMARY KEY(run_id, request_id)
);
CREATE TABLE IF NOT EXISTS jobs(
  run_id TEXT NOT NULL REFERENCES runs(run_id),
  linkedin_id TEXT NOT NULL,
  title TEXT NOT NULL,
  company TEXT,
  country TEXT,
  url TEXT,
  description_sha256 TEXT,
  description_at REAL,
  payload_json TEXT NOT NULL,
  PRIMARY KEY(run_id, linkedin_id)
);
CREATE TABLE IF NOT EXISTS scores(
  run_id TEXT NOT NULL,
  linkedin_id TEXT NOT NULL,
  score REAL NOT NULL,
  verdict TEXT NOT NULL,
  evidence_sha256 TEXT NOT NULL,
  input_sha256 TEXT NOT NULL,
  model TEXT NOT NULL,
  rationale_json TEXT NOT NULL,
  scored_at REAL NOT NULL,
  PRIMARY KEY(run_id, linkedin_id)
);
CREATE TABLE IF NOT EXISTS artifacts(
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  created_at REAL NOT NULL,
  PRIMARY KEY(run_id, kind, path)
);
CREATE TABLE IF NOT EXISTS deliveries(
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  message_id TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  target TEXT NOT NULL,
  delivered_at REAL NOT NULL,
  PRIMARY KEY(run_id, kind, message_id)
);
CREATE TABLE IF NOT EXISTS delivery_attempts(
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  artifact_sha256 TEXT NOT NULL,
  target TEXT NOT NULL,
  status TEXT NOT NULL,
  message_id TEXT,
  attempted_at REAL NOT NULL,
  PRIMARY KEY(run_id, kind)
);
CREATE TABLE IF NOT EXISTS provider_holds(
  id INTEGER PRIMARY KEY CHECK(id=1),
  active INTEGER NOT NULL,
  reason TEXT NOT NULL,
  run_id TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS scoring_calls(
  call_id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  run_date TEXT NOT NULL,
  linkedin_id TEXT NOT NULL,
  status TEXT NOT NULL,
  dispatched_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS applied_jobs(
  event_id TEXT PRIMARY KEY,
  linkedin_id TEXT NOT NULL UNIQUE,
  applied_date TEXT NOT NULL,
  company TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL,
  signature TEXT NOT NULL,
  source_run_id TEXT NOT NULL,
  source_report_sha256 TEXT NOT NULL,
  confirmation_message_id TEXT NOT NULL,
  confirmation_reply_to_id TEXT NOT NULL,
  confirmation_text_sha256 TEXT NOT NULL,
  confirmed_at TEXT NOT NULL,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_applied_jobs_signature
  ON applied_jobs(signature);
"""


def canonical_sha(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self):
        self.db.close()

    def create_run(self, run_id: str, run_date: str, matrix: list[QueryKey]):
        now = time.time()
        digest = canonical_sha([[q.title, q.country] for q in matrix])
        with self.db:
            self.db.execute(
                "INSERT INTO runs VALUES(?,?,?,?,?,?,NULL)",
                (run_id, run_date, Stage.NEW.value, digest, now, now),
            )
            self.db.executemany(
                "INSERT INTO queries(run_id,seq,title,country,status) VALUES(?,?,?,?,?)",
                [(run_id, i, q.title, q.country, QueryStatus.PENDING.value)
                 for i, q in enumerate(matrix, 1)],
            )
        return digest

    def run(self, run_id: str):
        row = self.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return dict(row)


    def transition(self, run_id: str, target: Stage, reason: str | None = None):
        row = self.run(run_id)
        current = Stage(row["stage"])
        if not transition_allowed(current, target):
            raise ValueError(f"invalid_transition:{current.value}->{target.value}")
        with self.db:
            self.db.execute(
                "UPDATE runs SET stage=?,updated_at=?,failure_reason=? WHERE run_id=?",
                (target.value, time.time(), reason, run_id),
            )

    def start_query(self, run_id: str, seq: int):
        with self.db:
            row = self.db.execute(
                "SELECT status FROM queries WHERE run_id=? AND seq=?", (run_id, seq)
            ).fetchone()
            if row is None or row["status"] != QueryStatus.PENDING.value:
                raise ValueError("query_not_pending")
            self.db.execute(
                "UPDATE queries SET status=?,started_at=? WHERE run_id=? AND seq=?",
                (QueryStatus.RUNNING.value, time.time(), run_id, seq),
            )

    def complete_query(self, run_id: str, seq: int, result_count: int, evidence_sha256: str):
        if result_count < 0 or len(evidence_sha256) != 64:
            raise ValueError("invalid_query_evidence")
        with self.db:
            row = self.db.execute(
                "SELECT status FROM queries WHERE run_id=? AND seq=?", (run_id, seq)
            ).fetchone()
            if row is None or row["status"] != QueryStatus.RUNNING.value:
                raise ValueError("query_not_running")
            self.db.execute(
                """UPDATE queries SET status=?,completed_at=?,result_count=?,
                   evidence_sha256=?,error=NULL WHERE run_id=? AND seq=?""",
                (QueryStatus.COMPLETED.value, time.time(), result_count,
                 evidence_sha256, run_id, seq),
            )

    def fail_query(self, run_id: str, seq: int, error: str, deferred: bool = False):
        status = QueryStatus.DEFERRED if deferred else QueryStatus.FAILED
        with self.db:
            self.db.execute(
                "UPDATE queries SET status=?,error=? WHERE run_id=? AND seq=?",
                (status.value, error[:500], run_id, seq),
            )

    def record_request(self, run_id: str, request_id: str, phase: str,
                       query_seq: int | None, status_code: int | None, outcome: str):
        with self.db:
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,?,?)",
                (run_id, request_id, phase, query_seq, time.time(),
                 status_code, outcome[:120]),
            )

    def query_counts(self, run_id: str):
        rows = self.db.execute(
            "SELECT status,COUNT(*) n FROM queries WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}


    def request_count(self, run_id: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM requests WHERE run_id=?", (run_id,)
        ).fetchone()[0]

    def add_artifact(self, run_id: str, kind: str, path: str, sha256: str):
        if len(sha256) != 64:
            raise ValueError("invalid_artifact_hash")
        with self.db:
            self.db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?,?)",
                (run_id, kind, path, sha256, time.time()),
            )

    def add_delivery(self, run_id: str, kind: str, message_id: str,
                     artifact_sha256: str, target: str):
        if not message_id or len(artifact_sha256) != 64 or not target:
            raise ValueError("invalid_delivery_receipt")
        with self.db:
            self.db.execute(
                "INSERT INTO deliveries VALUES(?,?,?,?,?,?)",
                (run_id, kind, message_id, artifact_sha256, target, time.time()),
            )

    def completion_ready(self, run_id: str) -> bool:
        counts = self.query_counts(run_id)
        if counts != {QueryStatus.COMPLETED.value: 150}:
            return False
        required_kinds = ("summary", "pdf", "zip")
        confirmed_target = None
        for kind in required_kinds:
            artifacts = self.db.execute(
                "SELECT path,sha256 FROM artifacts WHERE run_id=? AND kind=?",
                (run_id, kind),
            ).fetchall()
            deliveries = self.db.execute(
                """SELECT message_id,artifact_sha256,target FROM deliveries
                   WHERE run_id=? AND kind=?""",
                (run_id, kind),
            ).fetchall()
            attempts = self.db.execute(
                """SELECT artifact_sha256,target,status,message_id
                   FROM delivery_attempts WHERE run_id=? AND kind=?""",
                (run_id, kind),
            ).fetchall()
            if len(artifacts) != 1 or len(deliveries) != 1 or len(attempts) != 1:
                return False
            artifact, delivery, attempt = artifacts[0], deliveries[0], attempts[0]
            path = Path(artifact["path"])
            if not path.is_file() or path.is_symlink():
                return False
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != artifact["sha256"]:
                return False
            if delivery["artifact_sha256"] != digest:
                return False
            if attempt["artifact_sha256"] != digest or attempt["status"] != "confirmed":
                return False
            if attempt["message_id"] != delivery["message_id"]:
                return False
            target = str(delivery["target"] or "")
            if not target or attempt["target"] != target:
                return False
            if confirmed_target is None:
                confirmed_target = target
            elif target != confirmed_target:
                return False
        return True


    def upsert_job(self, run_id: str, job: dict):
        linkedin_id = str(job.get("linkedin_id") or "")
        if not linkedin_id:
            raise ValueError("missing_linkedin_id")
        with self.db:
            self.db.execute(
                """INSERT OR REPLACE INTO jobs
                   (run_id,linkedin_id,title,company,country,url,description_sha256,description_at,payload_json)
                   VALUES(?,?,?,?,?,?,?,?,?)""",
                (run_id, linkedin_id, str(job.get("title") or ""),
                 str(job.get("company") or ""), str(job.get("country") or ""),
                 str(job.get("url") or ""), job.get("description_sha256"),
                 job.get("description_at"), json.dumps(job, sort_keys=True)),
            )

    def record_score(self, run_id: str, linkedin_id: str, score: float,
                     verdict: str, evidence_sha256: str, input_sha256: str,
                     model: str, rationale: dict):
        if (not 0 <= score <= 100 or len(evidence_sha256) != 64
                or len(input_sha256) != 64 or not model):
            raise ValueError("invalid_score")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO scores VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, linkedin_id, score, verdict, evidence_sha256,
                 input_sha256, model, json.dumps(rationale, sort_keys=True),
                 time.time()),
            )


    def score_count(self, run_id: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM scores WHERE run_id=?", (run_id,)
        ).fetchone()[0]

    def scored_ids(self, run_id: str) -> set[str]:
        return {
            row[0] for row in self.db.execute(
                "SELECT linkedin_id FROM scores WHERE run_id=?", (run_id,)
            )
        }

    def job_count(self, run_id: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM jobs WHERE run_id=?", (run_id,)
        ).fetchone()[0]


    def jobs(self, run_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT payload_json FROM jobs WHERE run_id=? ORDER BY linkedin_id",
            (run_id,),
        ).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]


    def reserve_request(self, run_id: str, request_id: str, phase: str,
                        query_seq: int | None, dispatched_at: float):
        with self.db:
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,NULL,?)",
                (run_id, request_id, phase, query_seq, dispatched_at, "reserved"),
            )

    def complete_request(self, run_id: str, request_id: str,
                         status_code: int | None, outcome: str):
        with self.db:
            changed = self.db.execute(
                """UPDATE requests SET status_code=?,outcome=?
                   WHERE run_id=? AND request_id=? AND outcome='reserved'""",
                (status_code, outcome[:120], run_id, request_id),
            ).rowcount
            if changed != 1:
                raise ValueError("request_reservation_missing_or_closed")

    def rolling_request_count(self, since_epoch: float) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM requests WHERE dispatched_at>=?",
            (since_epoch,),
        ).fetchone()[0]


    def begin_delivery_attempt(self, run_id: str, kind: str,
                               artifact_sha256: str, target: str):
        if len(artifact_sha256) != 64 or not target:
            raise ValueError("invalid_delivery_hash")
        with self.db:
            existing = self.db.execute(
                "SELECT status FROM delivery_attempts WHERE run_id=? AND kind=?",
                (run_id, kind),
            ).fetchone()
            if existing is not None:
                raise ValueError("delivery_already_attempted_no_resend")
            self.db.execute(
                "INSERT INTO delivery_attempts VALUES(?,?,?,?,?,NULL,?)",
                (run_id, kind, artifact_sha256, target, "attempting", time.time()),
            )

    def close_delivery_attempt(self, run_id: str, kind: str,
                               status: str, message_id: str | None = None):
        if status not in {"confirmed", "uncertain", "local_rejected"}:
            raise ValueError("invalid_delivery_status")
        with self.db:
            changed = self.db.execute(
                """UPDATE delivery_attempts SET status=?,message_id=?
                   WHERE run_id=? AND kind=? AND status='attempting'""",
                (status, message_id, run_id, kind),
            ).rowcount
            if changed != 1:
                raise ValueError("delivery_attempt_not_open")


    def query_status(self, run_id: str, seq: int) -> str:
        row = self.db.execute(
            "SELECT status FROM queries WHERE run_id=? AND seq=?",
            (run_id, seq),
        ).fetchone()
        if row is None:
            raise KeyError((run_id, seq))
        return str(row["status"])

    def requeue_deferred_query(self, run_id: str) -> int:
        with self.db:
            rows = self.db.execute(
                "SELECT seq FROM queries WHERE run_id=? AND status='deferred' ORDER BY seq",
                (run_id,),
            ).fetchall()
            if len(rows) != 1:
                raise ValueError("expected_exactly_one_deferred_query")
            seq = int(rows[0]["seq"])
            self.db.execute(
                """UPDATE queries SET status='pending',started_at=NULL,error=NULL
                   WHERE run_id=? AND seq=?""",
                (run_id, seq),
            )
            return seq


    def scores(self, run_id: str) -> list[dict]:
        rows = self.db.execute(
            """SELECT linkedin_id,score,verdict,evidence_sha256,input_sha256,
                      model,rationale_json,scored_at
               FROM scores WHERE run_id=? ORDER BY score DESC,linkedin_id""",
            (run_id,),
        ).fetchall()
        return [dict(row) for row in rows]


    def reclassify_request(self, run_id: str, request_id: str, outcome: str):
        with self.db:
            changed = self.db.execute(
                """UPDATE requests SET outcome=?
                   WHERE run_id=? AND request_id=?""",
                (outcome[:120], run_id, request_id),
            ).rowcount
            if changed != 1:
                raise ValueError("request_not_found_for_reclassification")


    def reserve_request_atomic(self, run_id: str, request_id: str, phase: str,
                               query_seq: int | None, dispatched_at: float,
                               per_run_limit: int, rolling_limit: int,
                               external_rolling_used: int):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            run_used = self.db.execute(
                "SELECT COUNT(*) FROM requests WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            rolling_used = self.db.execute(
                "SELECT COUNT(*) FROM requests WHERE dispatched_at>=?",
                (dispatched_at - 86400,),
            ).fetchone()[0]
            if run_used >= per_run_limit:
                raise ValueError("per_run_request_limit")
            if external_rolling_used + rolling_used >= rolling_limit:
                raise ValueError("rolling_24h_request_limit")
            self.db.execute(
                "INSERT INTO requests VALUES(?,?,?,?,?,NULL,?)",
                (run_id, request_id, phase, query_seq, dispatched_at, "reserved"),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise


    def activate_provider_hold(self, run_id: str, reason: str):
        with self.db:
            self.db.execute(
                """INSERT INTO provider_holds(id,active,reason,run_id,created_at)
                   VALUES(1,1,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                     active=1,reason=excluded.reason,run_id=excluded.run_id,
                     created_at=excluded.created_at""",
                (reason[:200], run_id, time.time()),
            )

    def provider_hold(self):
        row = self.db.execute(
            "SELECT active,reason,run_id,created_at FROM provider_holds WHERE id=1"
        ).fetchone()
        return dict(row) if row else None

    def clear_provider_hold(self):
        with self.db:
            self.db.execute("DELETE FROM provider_holds WHERE id=1")


    def completed_query_times(self, run_id: str) -> list[float]:
        rows = self.db.execute(
            """SELECT completed_at FROM queries
               WHERE run_id=? AND status='completed'""",
            (run_id,),
        ).fetchall()
        return [float(row[0]) for row in rows if row[0] is not None]


    def score_bindings(self, run_id: str) -> dict[str, str]:
        return {
            str(row["linkedin_id"]): str(row["input_sha256"])
            for row in self.db.execute(
                "SELECT linkedin_id,input_sha256 FROM scores WHERE run_id=?",
                (run_id,),
            )
        }


    def reserve_scoring_call(self, run_id: str, linkedin_id: str,
                             call_id: str, daily_limit: int):
        try:
            self.db.execute("BEGIN IMMEDIATE")
            run_date = self.db.execute(
                "SELECT run_date FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run_date is None:
                raise KeyError(run_id)
            day = str(run_date[0])
            used = self.db.execute(
                "SELECT COUNT(*) FROM scoring_calls WHERE run_date=?",
                (day,),
            ).fetchone()[0]
            if used >= daily_limit:
                raise ValueError("daily_scoring_call_limit")
            self.db.execute(
                "INSERT INTO scoring_calls VALUES(?,?,?,?,?,?)",
                (call_id, run_id, day, linkedin_id, "reserved", time.time()),
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def complete_scoring_call(self, call_id: str, status: str):
        with self.db:
            changed = self.db.execute(
                "UPDATE scoring_calls SET status=? WHERE call_id=? AND status='reserved'",
                (status[:80], call_id),
            ).rowcount
            if changed != 1:
                raise ValueError("scoring_call_not_reserved")

    def scoring_call_count(self, run_date: str) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM scoring_calls WHERE run_date=?", (run_date,)
        ).fetchone()[0]


    def total_query_results(self, run_id: str) -> int:
        return int(self.db.execute(
            "SELECT COALESCE(SUM(result_count),0) FROM queries WHERE run_id=?",
            (run_id,),
        ).fetchone()[0])


    def delivery_attempt(self, run_id: str, kind: str):
        row = self.db.execute(
            """SELECT artifact_sha256,target,status,message_id,attempted_at
               FROM delivery_attempts WHERE run_id=? AND kind=?""",
            (run_id, kind),
        ).fetchone()
        return dict(row) if row else None


    def artifacts(self, run_id: str, kind: str | None = None) -> list[dict]:
        if kind is None:
            rows = self.db.execute(
                "SELECT kind,path,sha256,created_at FROM artifacts WHERE run_id=?",
                (run_id,),
            ).fetchall()
        else:
            rows = self.db.execute(
                """SELECT kind,path,sha256,created_at FROM artifacts
                   WHERE run_id=? AND kind=?""",
                (run_id, kind),
            ).fetchall()
        return [dict(row) for row in rows]


    def reopen_local_rejected_delivery(self, run_id: str, kind: str,
                                       artifact_sha256: str, target: str):
        with self.db:
            changed = self.db.execute(
                """UPDATE delivery_attempts
                   SET status='attempting',message_id=NULL,attempted_at=?
                   WHERE run_id=? AND kind=? AND status='local_rejected'
                     AND artifact_sha256=? AND target=?""",
                (time.time(), run_id, kind, artifact_sha256, target),
            ).rowcount
            if changed != 1:
                raise ValueError("local_rejected_delivery_not_reopenable")


    def applied_ids(self) -> set[str]:
        return {
            str(row[0]) for row in self.db.execute(
                "SELECT linkedin_id FROM applied_jobs"
            )
        }

    def applied_signatures(self) -> set[str]:
        return {
            str(row[0]) for row in self.db.execute(
                "SELECT signature FROM applied_jobs"
            )
        }

    def applied_count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM applied_jobs"
        ).fetchone()[0]

    def applied_rows(self) -> list[dict]:
        return [
            dict(row) for row in self.db.execute(
                "SELECT * FROM applied_jobs ORDER BY created_at,event_id"
            )
        ]

    def record_applied(self, row: dict) -> bool:
        required = (
            "event_id", "linkedin_id", "applied_date", "company", "title",
            "url", "signature", "source_run_id", "source_report_sha256",
            "confirmation_message_id", "confirmation_reply_to_id",
            "confirmation_text_sha256", "confirmed_at",
        )
        if any(not str(row.get(key) or "").strip() for key in required):
            raise ValueError("applied_record_missing_required_field")
        if len(str(row["event_id"])) != 64:
            raise ValueError("invalid_applied_event_id")
        if len(str(row["source_report_sha256"])) != 64:
            raise ValueError("invalid_applied_report_hash")
        if len(str(row["confirmation_text_sha256"])) != 64:
            raise ValueError("invalid_applied_confirmation_hash")
        existing = self.db.execute(
            "SELECT event_id FROM applied_jobs WHERE linkedin_id=?",
            (str(row["linkedin_id"]),),
        ).fetchone()
        if existing is not None:
            return False
        with self.db:
            self.db.execute(
                """INSERT INTO applied_jobs(
                   event_id,linkedin_id,applied_date,company,title,url,signature,
                   source_run_id,source_report_sha256,confirmation_message_id,
                   confirmation_reply_to_id,confirmation_text_sha256,
                   confirmed_at,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(row["event_id"]), str(row["linkedin_id"]),
                    str(row["applied_date"]), str(row["company"]),
                    str(row["title"]), str(row["url"]), str(row["signature"]),
                    str(row["source_run_id"]), str(row["source_report_sha256"]),
                    str(row["confirmation_message_id"]),
                    str(row["confirmation_reply_to_id"]),
                    str(row["confirmation_text_sha256"]),
                    str(row["confirmed_at"]), time.time(),
                ),
            )
        return True
