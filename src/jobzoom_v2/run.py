from __future__ import annotations
import argparse
import fcntl
import json
import re
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .budget import RunBudget
from .cv import render as render_cv, select_highlights
from .dedup import AppliedIndex
from .discovery import DiscoveryEngine
from .enrichment import EnrichmentEngine
from .gateway_scoring import GatewayScorer
from .linkedin import LinkedInDescriptionClient, LinkedInGuestClient
from .model import ALLOWED_TRANSITIONS, Stage
from .package import build_manifest, build_zip, sha256
from .pacing import SharedPacer
from .pipeline import Pipeline, load_matrix, load_runtime
from .quality import complete_description, salary_floor, scoring_candidates, title_eligible
from .report import generate as generate_report
from .scoring import ScoringEngine
from .store import Store, canonical_sha
from .telegram import TelegramDelivery
from .settings import (
    config_path, cv_path, data_dir, load_settings, lock_path, media_root,
    pacer_path, project_root, reports_dir,
)


ROOT = project_root()


def runtime_config():
    return load_settings()


def timezone(cfg: dict) -> ZoneInfo:
    return ZoneInfo(str(cfg["profile"].get("timezone", "UTC")))


def db_path(cfg: dict) -> Path:
    return data_dir(cfg) / "jobzoom.sqlite"

def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-")
    return value[:80] or "Role"


def daily_summary(opportunities: list[dict]) -> str:
    countries = {
        str(job.get("country") or "").strip()
        for job in opportunities
        if str(job.get("country") or "").strip()
    }
    country_word = "country" if len(countries) == 1 else "countries"
    return (
        f"🔍 JobZoom Daily: {len(opportunities)} matches found across "
        f"{len(countries)} {country_word}"
    )


def acquire_lock(cfg: dict):
    path = lock_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        handle.close()
        raise RuntimeError("jobzoom_v2_already_running") from exc
    return handle


def v2_state_gate(db_file: Path):
    if not db_file.is_file():
        return {"provider_hold": None, "unresolved_runs": []}
    uri = "file:" + str(db_file) + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        hold = db.execute(
            "SELECT active,reason,run_id,created_at FROM provider_holds WHERE id=1"
        ).fetchone()
        runs = db.execute(
            """SELECT run_id,run_date,stage,failure_reason FROM runs
               WHERE stage NOT IN ('completed','failed')
               ORDER BY created_at"""
        ).fetchall()
    return {
        "provider_hold": dict(hold) if hold and hold["active"] else None,
        "unresolved_runs": [dict(row) for row in runs],
    }


def expire_provider_holds(db_file: Path, ttl_seconds: float | None = None) -> None:
    """Auto-expire provider holds older than the configured TTL."""
    import sqlite3
    cfg = load_settings()
    ttl = ttl_seconds or float(cfg.get("runtime", {}).get("provider_hold_ttl_seconds", 14400))
    db = sqlite3.connect(db_file)
    try:
        db.execute(
            """DELETE FROM provider_holds
               WHERE active=1 AND created_at < ?""",
            (time.time() - ttl,),
        )
        db.commit()
    finally:
        db.close()


def recover_stale_runs(db_file: Path, reason: str = "manual_recovery") -> int:
    """Mark all unresolved runs as failed. Returns count of affected rows."""
    import sqlite3
    db = sqlite3.connect(db_file)
    try:
        cur = db.execute(
            """UPDATE runs SET stage=?, failure_reason=?
               WHERE stage NOT IN ('completed','failed')""",
            (Stage.FAILED.value, reason),
        )
        db.commit()
        return cur.rowcount
    finally:
        db.close()



def preflight() -> dict:
    cfg = runtime_config()
    matrix = load_matrix(config_path())
    gate = v2_state_gate(db_path(cfg))
    return {
        "observed_at": datetime.now(timezone(cfg)).isoformat(),
        "provider_hold": gate["provider_hold"],
        "unresolved_runs": gate["unresolved_runs"],
        "rolling_limit": int(cfg["runtime"]["max_requests_rolling_24h"]),
        "per_run_limit": int(cfg["runtime"]["max_requests_per_run"]),
        "matrix_queries": len(matrix),
        "live_allowed": gate["provider_hold"] is None and not gate["unresolved_runs"],
    }


def new_run_id(cfg: dict) -> str:
    return datetime.now(timezone(cfg)).strftime("%Y%m%d-%H%M%S-v2")



def make_runtime(store: Store, run_id: str):
    cfg = runtime_config()
    runtime = load_runtime(config_path())
    budget = RunBudget(
        store, run_id,
        runtime.max_requests_per_run,
        runtime.max_requests_rolling_24h,
    )
    pacer = SharedPacer(
        pacer_path(cfg),
        runtime.minimum_request_gap_seconds,
    )
    return cfg, runtime, budget, pacer


def run_discovery(store: Store, pipeline: Pipeline, run_id: str, runtime, budget, pacer):
    cfg = runtime_config()
    results_per_query_raw = cfg["search"].get("results_per_query", 35)
    results_per_query = None if results_per_query_raw in (None, "unlimited", "all") else int(results_per_query_raw)
    client = LinkedInGuestClient(
        results_per_query=results_per_query,
        hours_old=int(cfg["search"].get("hours_old", 24)),
        minimum_gap=runtime.minimum_request_gap_seconds,
        budget=budget,
        pacer=pacer,
    )
    return DiscoveryEngine(store, pipeline, client).run(run_id)


def filter_for_enrichment(store: Store, run_id: str, cfg: dict | None = None) -> list[dict]:
    cfg = cfg or runtime_config()
    applied = AppliedIndex(None, store=store)
    return [
        job for job in store.jobs(run_id)
        if not applied.applied(job) and title_eligible(
            job,
            cfg["search"]["titles"],
            cfg["search"].get("location_aliases"),
        )
    ]


def run_enrichment(store: Store, run_id: str, runtime, budget, pacer, jobs: list[dict]):
    client = LinkedInDescriptionClient(
        runtime.minimum_request_gap_seconds, budget, pacer
    )
    return EnrichmentEngine(
        store, client, runtime.description_ttl_seconds
    ).run(run_id, jobs)


def score_and_package(store: Store, pipeline: Pipeline, run_id: str,
                      run_date: str, cfg: dict, enriched: list[dict],
                      output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    applied = AppliedIndex(None, store=store)
    candidates = [
        job for job in scoring_candidates(
            enriched,
            cfg["search"]["titles"],
            cfg["search"].get("location_aliases"),
        )
        if not applied.applied(job)
    ]
    cv_text = cv_path(cfg).read_text(encoding="utf-8")
    scorer = GatewayScorer(
        cv_text,
        str(cfg["scoring"]["model"]),
        str(cfg["profile"]["name"]),
        gateway_url=str(cfg["openclaw"].get("gateway_url", "http://127.0.0.1:18789")),
        store=store,
        run_id=run_id,
        daily_limit=int(cfg["scoring"].get("max_calls_per_day", 80)),
    )
    ScoringEngine(store, scorer).run(run_id, candidates)
    pipeline.finish_scoring(run_id)

    applied = AppliedIndex(None, store=store)
    jobs_by_id = {
        str(j["linkedin_id"]): j for j in candidates
        if not applied.applied(j)
    }
    opportunities = []
    for score in store.scores(run_id):
        if float(score["score"]) < float(cfg["scoring"].get("report_threshold", 70)):
            continue
        if score["linkedin_id"] not in jobs_by_id:
            continue
        job = dict(jobs_by_id[score["linkedin_id"]])
        rationale = json.loads(str(score["rationale_json"]))
        job.update({
            "score": int(score["score"]),
            "verdict": str(score["verdict"]),
            "rationale": str(rationale.get("rationale") or score["verdict"]),
            "score_input_sha256": str(score["input_sha256"]),
            "score_evidence_sha256": str(score["evidence_sha256"]),
            "salary_floor": salary_floor(job.get("country", ""), cfg.get("compensation_targets")),
        })
        opportunities.append(job)

    cv_rows = []
    master_path = cv_path(cfg)
    master_sha = sha256(master_path)
    for rank, job in enumerate(opportunities, 1):
        filename = f"{rank:02d}-{safe_name(cfg['profile']['name'])}-{safe_name(job['title'])}.pdf"
        path = output_dir / filename
        highlights = select_highlights(master_path, job, limit=8)
        render_cv(master_path, str(cfg["profile"]["name"]), job["title"], highlights, path)
        cv_sha = sha256(path)
        binding = canonical_sha({
            "linkedin_id": job["linkedin_id"],
            "description_sha256": job.get("description_sha256"),
            "score": job["score"],
            "score_input_sha256": job["score_input_sha256"],
            "score_evidence_sha256": job["score_evidence_sha256"],
            "master_cv_sha256": master_sha,
            "cv_sha256": cv_sha,
        })
        store.add_artifact(run_id, "cv", str(path), cv_sha)
        cv_rows.append({
            "linkedin_id": job["linkedin_id"],
            "filename": filename,
            "filepath": str(path),
            "sha256": cv_sha,
            "binding_sha256": binding,
            "master_cv_sha256": master_sha,
            "description_sha256": job.get("description_sha256"),
            "score_input_sha256": job["score_input_sha256"],
        })
    pipeline.finish_cvs(run_id)

    counts = store.query_counts(run_id)
    report = output_dir / f"jobzoom-{run_date.replace('-', '')}.pdf"
    generate_report({
        "run_id": run_id,
        "run_date": run_date,
        "planned_searches": len(pipeline.matrix),
        "completed_searches": counts.get("completed", 0),
        "failed_searches": counts.get("failed", 0) + counts.get("deferred", 0),
        "pending_searches": counts.get("pending", 0) + counts.get("running", 0),
        "raw_hits": store.total_query_results(run_id),
        "unique_jobs": store.job_count(run_id),
        "eligible_for_description": len(filter_for_enrichment(store, run_id, cfg)),
        "descriptions_complete": sum(complete_description(job) for job in enriched),
        "scored_count": store.score_count(run_id),
        "qualified_count": len(opportunities),
        "candidate_name": str(cfg["profile"]["name"]),
        "title_count": len(cfg["search"]["titles"]),
        "country_count": len(cfg["search"]["countries"]),
        "countries": list(cfg["search"]["countries"]),
        "hours_old": int(cfg["search"].get("hours_old", 24)),
        "readiness_threshold": int(cfg["scoring"].get("readiness_threshold", 82)),
    }, opportunities, cv_rows, report)
    pipeline.finish_report(run_id)

    manifest = output_dir / "manifest.json"
    cv_paths = [Path(row["filepath"]) for row in cv_rows]
    bindings = {
        row["filename"]: {
            "linkedin_id": row["linkedin_id"],
            "cv_binding_sha256": row["binding_sha256"],
            "master_cv_sha256": row["master_cv_sha256"],
            "description_sha256": row["description_sha256"],
            "score_input_sha256": row["score_input_sha256"],
        }
        for row in cv_rows
    }
    bindings[report.name] = {
        "run_id": run_id,
        "matrix_sha256": pipeline.matrix_sha256(),
        "completed_searches": counts.get("completed", 0),
        "scored_count": store.score_count(run_id),
    }
    build_manifest(
        run_id, [report, *cv_paths], manifest, bindings=bindings
    )
    archive = output_dir / f"jobzoom-{run_date.replace('-', '')}.zip"
    build_zip(manifest, [report, *cv_paths], archive)
    summary = output_dir / "summary.txt"
    summary.write_text(daily_summary(opportunities), encoding="utf-8")
    store.add_artifact(run_id, "summary", str(summary), sha256(summary))
    store.add_artifact(run_id, "pdf", str(report), sha256(report))
    store.add_artifact(run_id, "zip", str(archive), sha256(archive))
    return summary, report, archive, opportunities, cv_rows


def deliver(store: Store, pipeline: Pipeline, run_id: str,
            summary: Path, report: Path, archive: Path):
    cfg = runtime_config()
    sender = TelegramDelivery(
        store,
        str(cfg["telegram"]["target"]),
        media_root(cfg),
        str(cfg["openclaw"].get("binary", "openclaw")),
    )
    summary_text = summary.read_text(encoding="utf-8")
    summary_receipt = sender.send_text(run_id, "summary", summary_text)
    pdf_receipt = sender.send_file(run_id, "pdf", report)
    zip_receipt = sender.send_file(run_id, "zip", archive)
    pipeline.finish_delivery(run_id)
    return {
        "summary": summary_receipt,
        "pdf": pdf_receipt,
        "zip": zip_receipt,
    }


def save_status(output_dir: Path, store: Store, run_id: str, extra=None):
    row = store.run(run_id)
    payload = {
        "observed_at": datetime.now(timezone(runtime_config())).isoformat(),
        "run": row,
        "queries": store.query_counts(run_id),
        "requests": store.request_count(run_id),
        "jobs": store.job_count(run_id),
        "scores": store.score_count(run_id),
    }
    if extra:
        payload.update(extra)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "status.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n"
    )
    return payload


def continue_run(store: Store, pipeline: Pipeline, run_id: str,
                 run_date: str, output_dir: Path, resume: bool):
    row = store.run(run_id)
    stage = row["stage"]
    if stage == "delivery":
        cfg = runtime_config()
        runtime = load_runtime(config_path())
        budget = pacer = None
    else:
        cfg, runtime, budget, pacer = make_runtime(store, run_id)

    if stage == "paused":
        counts = store.query_counts(run_id)
        if counts.get("completed", 0) < len(pipeline.matrix):
            if not resume:
                raise RuntimeError("explicit_resume_required")
            pipeline.resume_discovery(run_id)
            stage = "discovery"
        elif str(row.get("failure_reason") or "").startswith("scoring_or_package:"):
            if not resume:
                raise RuntimeError("explicit_resume_required")
            pipeline.resume_scoring(run_id)
            stage = "scoring"
        else:
            if not resume:
                raise RuntimeError("explicit_resume_required")
            pipeline.resume_enrichment(run_id)
            stage = "enrichment"

    if stage == "discovery":
        discovery = run_discovery(
            store, pipeline, run_id, runtime, budget, pacer
        )
        if discovery["stage"] == "paused":
            return save_status(output_dir, store, run_id, {"discovery": discovery})
        stage = "enrichment"


    if stage == "enrichment":
        jobs = filter_for_enrichment(store, run_id, cfg)
        result = run_enrichment(
            store, run_id, runtime, budget, pacer, jobs
        )
        if not result["complete"]:
            pipeline.pause(run_id, result["restriction"] or "enrichment_incomplete")
            return save_status(output_dir, store, run_id, {"enrichment": result})
        pipeline.finish_enrichment(run_id)
        stage = "scoring"

    if stage == "scoring":
        enriched = store.jobs(run_id)
        try:
            summary, report, archive, opportunities, cv_rows = score_and_package(
                store, pipeline, run_id, run_date, cfg,
                enriched, output_dir,
            )
        except Exception as exc:
            current = Stage(store.run(run_id)["stage"])
            reason = "scoring_or_package:" + type(exc).__name__
            if current == Stage.SCORING:
                pipeline.pause(run_id, reason)
            elif Stage.FAILED in ALLOWED_TRANSITIONS[current]:
                store.transition(run_id, Stage.FAILED, reason)
            return save_status(
                output_dir, store, run_id,
                {"error": type(exc).__name__ + ": " + str(exc)},
            )
        stage = "delivery"
    elif stage == "delivery":
        summary_rows = store.artifacts(run_id, "summary")
        pdf_rows = store.artifacts(run_id, "pdf")
        zip_rows = store.artifacts(run_id, "zip")
        if (len(summary_rows) != 1 or len(pdf_rows) != 1
                or len(zip_rows) != 1):
            raise RuntimeError("delivery_artifacts_missing_or_ambiguous")
        summary = Path(summary_rows[0]["path"])
        report = Path(pdf_rows[0]["path"])
        archive = Path(zip_rows[0]["path"])
        opportunities = []
        cv_rows = store.artifacts(run_id, "cv")
    else:
        raise RuntimeError("unsupported_resume_stage:" + stage)

    if stage == "delivery":
        try:
            receipts = deliver(
                store, pipeline, run_id, summary, report, archive
            )
        except Exception as exc:
            return save_status(
                output_dir, store, run_id,
                {"delivery_error": type(exc).__name__ + ": " + str(exc)},
            )
        return save_status(
            output_dir, store, run_id,
            {
                "delivery_receipts": receipts,
                "qualified_roles": len(cv_rows),
                "cv_count": len(cv_rows),
                "report": str(report),
                "zip": str(archive),
            },
        )
    raise RuntimeError("run_did_not_reach_terminal_or_paused_state")


def validate_resume(store: Store, pipeline: Pipeline, run_id: str, runtime):
    row = store.run(run_id)
    if str(row["stage"]) != "delivery":
        today = datetime.now(timezone(runtime_config())).date().isoformat()
        if str(row["run_date"]) != today:
            raise RuntimeError("resume_requires_same_configured_date")
        if str(row["matrix_sha256"]) != pipeline.matrix_sha256():
            raise RuntimeError("resume_matrix_changed")
        now = time.time()
        for completed_at in store.completed_query_times(run_id):
            age = now - completed_at
            if age < 0 or age > runtime.query_checkpoint_ttl_seconds:
                raise RuntimeError("completed_query_checkpoint_expired")


def execute(live: bool, resume_run_id: str | None = None):
    cfg = runtime_config()
    if live:
        expire_provider_holds(db_path(cfg))
    check = preflight()
    if not live:
        return check
    unresolved_rows = check["unresolved_runs"]
    unresolved = [row["run_id"] for row in unresolved_rows]
    if resume_run_id:
        if unresolved != [resume_run_id]:
            raise RuntimeError("resume_run_does_not_match_only_unresolved_run")
    elif unresolved:
        raise RuntimeError("unresolved_v2_run_blocks_new_cycle")
    if check["provider_hold"] is not None:
        raise RuntimeError("provider_hold_active_live_run_blocked")

    lock = acquire_lock(cfg)
    store = Store(db_path(cfg))
    try:
        matrix = load_matrix(config_path())
        runtime = load_runtime(config_path())
        pipeline = Pipeline(store, matrix, runtime)
        if resume_run_id:
            run_id = resume_run_id
            row = store.run(run_id)
            validate_resume(store, pipeline, run_id, runtime)
            run_date = str(row["run_date"])
            resume = True
        else:
            run_id = new_run_id(cfg)
            run_date = datetime.now(timezone(cfg)).date().isoformat()
            pipeline.create(run_id, run_date)
            resume = False
        output_dir = reports_dir(cfg) / run_id
        return continue_run(
            store, pipeline, run_id, run_date, output_dir, resume
        )
    finally:
        store.close()
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


def main():
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--resume", metavar="RUN_ID")
    args = parser.parse_args()
    result = execute(
        live=args.live or bool(args.resume),
        resume_run_id=args.resume,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
