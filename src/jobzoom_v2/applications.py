from __future__ import annotations
import hashlib
import json
from pathlib import Path

from .dedup import signature
from .package import sha256
from .store import Store, canonical_sha


class ApplicationRecordingError(RuntimeError):
    pass


def resolve_report(store: Store, root: Path, report_date: str, telegram_target: str) -> dict:
    rows = [
        row for row in store.db.execute(
            """SELECT run_id,run_date,stage FROM runs
               WHERE run_date=? AND stage='completed'
               ORDER BY created_at""",
            (report_date,),
        )
    ]
    if len(rows) != 1:
        raise ApplicationRecordingError("completed_daily_run_not_unique")
    run_id = str(rows[0]["run_id"])
    output = Path(root) / "reports/runs" / run_id
    report = output / f"jobzoom-{report_date.replace('-', '')}.pdf"
    archive = output / f"jobzoom-{report_date.replace('-', '')}.zip"
    manifest_path = output / "manifest.json"
    if not report.is_file() or not archive.is_file() or not manifest_path.is_file():
        raise ApplicationRecordingError("v2_delivered_package_missing")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("run_id") != run_id:
        raise ApplicationRecordingError("manifest_run_binding_invalid")
    pdf_delivery = store.db.execute(
        """SELECT message_id,artifact_sha256,target FROM deliveries
           WHERE run_id=? AND kind='pdf'""",
        (run_id,),
    ).fetchall()
    if len(pdf_delivery) != 1:
        raise ApplicationRecordingError("pdf_delivery_not_unique")
    if str(pdf_delivery[0]["artifact_sha256"]) != sha256(report):
        raise ApplicationRecordingError("pdf_delivery_hash_mismatch")
    if str(pdf_delivery[0]["target"]) != str(telegram_target):
        raise ApplicationRecordingError("pdf_delivery_target_mismatch")
    return {
        "run_id": run_id,
        "output": output,
        "report": report,
        "archive": archive,
        "manifest": manifest,
        "report_sha256": sha256(report),
        "pdf_message_id": str(pdf_delivery[0]["message_id"]),
    }


def report_opportunities(store: Store, resolved: dict) -> list[dict]:
    run_id = resolved["run_id"]
    manifest = resolved["manifest"]
    bindings = manifest.get("bindings")
    if not isinstance(bindings, dict):
        raise ApplicationRecordingError("manifest_bindings_missing")
    ordered = []
    for name, binding in bindings.items():
        if not name[:2].isdigit() or not name.lower().endswith(".pdf"):
            continue
        linkedin_id = str((binding or {}).get("linkedin_id") or "")
        if not linkedin_id:
            raise ApplicationRecordingError("manifest_linkedin_binding_missing")
        row = store.db.execute(
            """SELECT j.linkedin_id,j.title,j.company,j.country,j.url,s.score
               FROM jobs j JOIN scores s
                 ON s.run_id=j.run_id AND s.linkedin_id=j.linkedin_id
               WHERE j.run_id=? AND j.linkedin_id=?""",
            (run_id, linkedin_id),
        ).fetchone()
        if row is None or float(row["score"]) < 70:
            raise ApplicationRecordingError("manifest_opportunity_db_binding_invalid")
        ordered.append((int(name[:2]), dict(row)))
    ordered.sort(key=lambda item: item[0])
    opportunities = [row for _, row in ordered]
    if len(opportunities) != len({
        str(row["linkedin_id"]) for row in opportunities
    }):
        raise ApplicationRecordingError("duplicate_manifest_opportunity")
    return opportunities


def prepare_records(resolved: dict, opportunities: list[dict], *,
                    applied_date: str, confirmation_message_id: str,
                    confirmation_reply_to_id: str, confirmation_text: str,
                    confirmed_at: str) -> list[dict]:
    if confirmation_reply_to_id != resolved["pdf_message_id"]:
        raise ApplicationRecordingError("confirmation_not_reply_to_delivered_pdf")
    text_sha = hashlib.sha256(confirmation_text.encode("utf-8")).hexdigest()
    records = []
    for job in opportunities:
        sig = signature(job)
        event_id = canonical_sha({
            "linkedin_id": job["linkedin_id"],
            "applied_date": applied_date,
            "confirmation_message_id": confirmation_message_id,
            "report_sha256": resolved["report_sha256"],
        })
        records.append({
            "event_id": event_id,
            "linkedin_id": str(job["linkedin_id"]),
            "applied_date": applied_date,
            "company": str(job.get("company") or ""),
            "title": str(job.get("title") or ""),
            "url": str(job.get("url") or ""),
            "signature": sig,
            "source_run_id": resolved["run_id"],
            "source_report_sha256": resolved["report_sha256"],
            "confirmation_message_id": confirmation_message_id,
            "confirmation_reply_to_id": confirmation_reply_to_id,
            "confirmation_text_sha256": text_sha,
            "confirmed_at": confirmed_at,
        })
    return records


def record_all(store: Store, records: list[dict], dry_run: bool) -> dict:
    existing = store.applied_ids()
    new_records = [
        row for row in records if str(row["linkedin_id"]) not in existing
    ]
    if not dry_run:
        for row in new_records:
            if not store.record_applied(row):
                raise ApplicationRecordingError("applied_commit_race")
    return {
        "dry_run": dry_run,
        "selected": len(records),
        "new_applied_entries": len(new_records),
        "already_applied": len(records) - len(new_records),
        "linkedin_ids": [row["linkedin_id"] for row in records],
    }
