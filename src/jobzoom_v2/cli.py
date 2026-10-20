from __future__ import annotations
import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .run import db_path, execute
from .settings import (
    config_path, load_settings, project_root, reports_dir,
)
from .store import Store


def init_project(args):
    root = project_root()
    cfg_path = config_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    example = root / "config/jobzoom.example.json"
    cv_example = root / "config/cv-master.example.md"
    cv_target = root / "config/cv-master.md"
    if cfg_path.exists() and not args.force:
        raise SystemExit(f"Config already exists: {cfg_path}. Use --force to replace it.")
    cfg = json.loads(example.read_text(encoding="utf-8"))
    if args.non_interactive:
        name = args.name or cfg["profile"]["name"]
        timezone = args.timezone or cfg["profile"]["timezone"]
        target = args.telegram_target or cfg["telegram"]["target"]
        model = args.model or cfg["scoring"]["model"]
    else:
        name = input("Candidate name: ").strip() or cfg["profile"]["name"]
        timezone = input(
            f"Timezone [{cfg['profile']['timezone']}]: "
        ).strip() or cfg["profile"]["timezone"]
        target = input(
            "Telegram target, for example -1001234567890:42: "
        ).strip()
        model = input(
            f"OpenClaw scoring model [{cfg['scoring']['model']}]: "
        ).strip() or cfg["scoring"]["model"]
    cfg["profile"]["name"] = name
    cfg["profile"]["timezone"] = timezone
    cfg["telegram"]["target"] = target
    cfg["scoring"]["model"] = model
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    if args.cv:
        shutil.copyfile(Path(args.cv).expanduser(), cv_target)
    elif not cv_target.exists() or args.force:
        shutil.copyfile(cv_example, cv_target)
    (root / "data").mkdir(exist_ok=True)
    (root / "reports/runs").mkdir(parents=True, exist_ok=True)
    print(f"Created {cfg_path}")
    print(f"Master CV: {cv_target}")
    print("Edit the master CV and review search titles/countries before the first live run.")


def daily(force: bool = False):
    from .run import expire_provider_holds
    cfg = load_settings()
    tz = ZoneInfo(str(cfg["profile"].get("timezone", "UTC")))
    today = datetime.now(tz).date().isoformat()
    db = db_path(cfg)
    expire_provider_holds(db)
    if not db.exists():
        result = execute(live=True)
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    store = Store(db)
    try:
        unresolved = store.db.execute(
            """SELECT run_id,run_date,stage FROM runs
               WHERE stage NOT IN ('completed','failed')
               ORDER BY created_at"""
        ).fetchall()
        if len(unresolved) > 1 and not force:
            raise RuntimeError("multiple_unresolved_runs")
        if unresolved:
            row = unresolved[0]
            if str(row["run_date"]) != today and not force:
                raise RuntimeError(
                    f"older_unresolved_run:{row['run_id']}:{row['run_date']}:{row['stage']}"
                )
            run_id = str(row["run_id"])
        else:
            completed = store.db.execute(
                "SELECT COUNT(*) FROM runs WHERE run_date=? AND stage='completed'",
                (today,),
            ).fetchone()[0]
            if completed:
                print(f"JobZoom already completed for {today}; duplicate launch suppressed.")
                return
            run_id = None
    finally:
        store.close()
    result = execute(live=True, resume_run_id=run_id)
    print(json.dumps(result, indent=2, sort_keys=True))


def recover(reason: str = "manual_recovery"):
    from .run import recover_stale_runs
    cfg = load_settings()
    db = db_path(cfg)
    if not db.exists():
        print(json.dumps({"status": "not_initialized"}))
        return
    affected = recover_stale_runs(db, reason)
    print(json.dumps({"recovered": affected, "reason": reason}))


def clear_hold():
    cfg = load_settings()
    db = db_path(cfg)
    if not db.exists():
        print(json.dumps({"status": "not_initialized"}))
        return
    store = Store(db)
    try:
        store.clear_provider_hold()
    finally:
        store.close()
    print(json.dumps({"status": "provider_hold_cleared"}))


def status():
    cfg = load_settings()
    db = db_path(cfg)
    if not db.exists():
        print(json.dumps({"status": "not_initialized"}, indent=2))
        return
    store = Store(db)
    try:
        rows = store.db.execute(
            """SELECT run_id,run_date,stage,failure_reason,created_at,updated_at
               FROM runs ORDER BY created_at DESC LIMIT 5"""
        ).fetchall()
        payload = {
            "runs": [dict(row) for row in rows],
            "applied_jobs": store.applied_count(),
            "provider_hold": store.provider_hold(),
        }
    finally:
        store.close()
    print(json.dumps(payload, indent=2, sort_keys=True))


def schedule(args):
    cfg = load_settings()
    root = project_root()
    binary = shutil.which("jobzoom") or sys.argv[0]
    log = root / "reports/jobzoom-daily.log"
    marker = "# jobzoom-v2-daily"
    line = (
        f"{args.minute} {args.hour} * * * "
        f"{binary} daily >> {log} 2>&1 {marker}"
    )
    tz_line = f"CRON_TZ={cfg['profile'].get('timezone', 'UTC')}"
    if not args.install:
        print(tz_line)
        print(line)
        return
    current = subprocess.run(
        ["crontab", "-l"], capture_output=True, text=True
    )
    existing = current.stdout if current.returncode == 0 else ""
    kept = "\n".join(
        row for row in existing.splitlines()
        if marker not in row and not row.startswith("CRON_TZ=")
    ).rstrip()
    updated = (kept + "\n" if kept else "") + tz_line + "\n" + line + "\n"
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    print("Installed JobZoom daily schedule:")
    print(tz_line)
    print(line)


def main():
    parser = argparse.ArgumentParser(prog="jobzoom")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create a local JobZoom configuration.")
    init.add_argument("--force", action="store_true")
    init.add_argument("--non-interactive", action="store_true")
    init.add_argument("--name")
    init.add_argument("--timezone")
    init.add_argument("--telegram-target")
    init.add_argument("--model")
    init.add_argument("--cv", help="Path to a prepared master-CV markdown file.")

    sub.add_parser("preflight", help="Check whether a new live run is allowed.")
    run_parser = sub.add_parser("run", help="Start one new live run.")
    run_parser.add_argument("--backend", choices=["python", "node"], default="python",
                            help="Collection backend to use")
    resume = sub.add_parser("resume", help="Resume one paused same-day run.")
    resume.add_argument("run_id")
    daily_parser = sub.add_parser("daily", help="Idempotent scheduled daily entry point.")
    daily_parser.add_argument("--force", action="store_true",
                              help="Override cross-day and multiple-unresolved blocks")
    sub.add_parser("status", help="Show recent run state.")
    sub.add_parser("clear-hold", help="Release an active provider hold so a new run can start")
    recover_parser = sub.add_parser("recover", help="Mark all unresolved runs as failed")
    recover_parser.add_argument("--reason", default="manual_recovery")
    schedule_parser = sub.add_parser("schedule", help="Print or install a cron schedule.")
    schedule_parser.add_argument("--hour", type=int, default=5)
    schedule_parser.add_argument("--minute", type=int, default=0)
    schedule_parser.add_argument("--install", action="store_true")

    args = parser.parse_args()
    if args.command == "init":
        init_project(args)
    elif args.command == "preflight":
        print(json.dumps(execute(live=False), indent=2, sort_keys=True))
    elif args.command == "run":
        print(json.dumps(execute(live=True), indent=2, sort_keys=True))
    elif args.command == "resume":
        print(json.dumps(
            execute(live=True, resume_run_id=args.run_id),
            indent=2, sort_keys=True,
        ))
    elif args.command == "daily":
        daily(force=args.force)
    elif args.command == "status":
        status()
    elif args.command == "recover":
        recover(reason=args.reason)
    elif args.command == "clear-hold":
        clear_hold()
    elif args.command == "schedule":
        schedule(args)


if __name__ == "__main__":
    main()
