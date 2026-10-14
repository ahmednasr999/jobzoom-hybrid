#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json

from jobzoom_v2.applications import (
    prepare_records, record_all, report_opportunities, resolve_report,
)
from jobzoom_v2.run import db_path
from jobzoom_v2.settings import load_settings, project_root
from jobzoom_v2.store import Store


def main():
    parser = argparse.ArgumentParser(
        description="Record owner-confirmed applications from one delivered JobZoom report."
    )
    parser.add_argument("report_date")
    parser.add_argument("--applied-date", required=True)
    parser.add_argument("--confirmation-message-id", required=True)
    parser.add_argument("--confirmation-reply-to-id", required=True)
    parser.add_argument("--confirmation-text", required=True)
    parser.add_argument("--confirmed-at", required=True)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if not args.all:
        raise SystemExit("--all is required by this command")
    cfg = load_settings()
    store = Store(db_path(cfg))
    try:
        resolved = resolve_report(
            store, project_root(), args.report_date,
            str(cfg["telegram"]["target"]),
        )
        opportunities = report_opportunities(store, resolved)
        records = prepare_records(
            resolved, opportunities,
            applied_date=args.applied_date,
            confirmation_message_id=args.confirmation_message_id,
            confirmation_reply_to_id=args.confirmation_reply_to_id,
            confirmation_text=args.confirmation_text,
            confirmed_at=args.confirmed_at,
        )
        result = record_all(store, records, args.dry_run)
        result.update({
            "run_id": resolved["run_id"],
            "report_sha256": resolved["report_sha256"],
            "pdf_message_id": resolved["pdf_message_id"],
            "matched_jobs_found": len(opportunities),
        })
        print(json.dumps(result, indent=2, sort_keys=True))
    finally:
        store.close()


if __name__ == "__main__":
    main()
