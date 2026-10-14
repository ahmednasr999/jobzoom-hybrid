import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.applications import (
    ApplicationRecordingError, prepare_records, record_all,
    report_opportunities, resolve_report,
)
from jobzoom_v2.dedup import AppliedIndex
from jobzoom_v2.model import QueryKey
from jobzoom_v2.package import sha256
from jobzoom_v2.store import Store


class ApplicationRecordingTests(unittest.TestCase):
    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = Store(root / "data/jobzoom-v2.sqlite")
        self.addCleanup(store.close)
        run_id = "20261002-app-test"
        store.create_run(run_id, "2026-10-02", [QueryKey("Director PMO", "Saudi Arabia")])
        with store.db:
            store.db.execute(
                "UPDATE runs SET stage='completed' WHERE run_id=?", (run_id,)
            )
        job = {
            "linkedin_id": "li-123",
            "title": "Director PMO",
            "company": "Example",
            "country": "Saudi Arabia",
            "url": "https://www.linkedin.com/jobs/view/123",
            "description_sha256": "a" * 64,
        }
        store.upsert_job(run_id, job)
        store.record_score(
            run_id, "li-123", 85, "readiness_review",
            "b" * 64, "c" * 64, "model", {"rationale": "fit"},
        )
        out = root / "reports/runs" / run_id
        out.mkdir(parents=True)
        report = out / "jobzoom-20261002.pdf"
        archive = out / "jobzoom-20261002.zip"
        report.write_bytes(b"%PDF-test")
        archive.write_bytes(b"PK-test")
        manifest = {
            "version": 2,
            "run_id": run_id,
            "bindings": {
                "01-Sample-Candidate-Director-PMO.pdf": {
                    "linkedin_id": "li-123",
                },
                "jobzoom-20261002.pdf": {"run_id": run_id},
            },
            "files": [],
        }
        (out / "manifest.json").write_text(json.dumps(manifest))
        store.add_artifact(run_id, "pdf", str(report), sha256(report))
        store.add_artifact(run_id, "zip", str(archive), sha256(archive))
        store.add_delivery(
            run_id, "pdf", "700", sha256(report), "-1001234567890:42"
        )
        return root, store


    def test_dry_run_commit_and_idempotency(self):
        root, store = self.make()
        resolved = resolve_report(store, root, "2026-10-02", "-1001234567890:42")
        jobs = report_opportunities(store, resolved)
        records = prepare_records(
            resolved, jobs,
            applied_date="2026-10-02",
            confirmation_message_id="701",
            confirmation_reply_to_id="700",
            confirmation_text="applied to all 1 jobs",
            confirmed_at="2026-10-02T01:00:00+03:00",
        )
        dry = record_all(store, records, True)
        self.assertEqual(dry["new_applied_entries"], 1)
        self.assertEqual(store.applied_count(), 0)
        committed = record_all(store, records, False)
        self.assertEqual(committed["new_applied_entries"], 1)
        self.assertEqual(store.applied_count(), 1)
        repeat = record_all(store, records, True)
        self.assertEqual(repeat["new_applied_entries"], 0)
        self.assertEqual(repeat["already_applied"], 1)

    def test_confirmation_must_reply_to_delivered_pdf(self):
        root, store = self.make()
        resolved = resolve_report(store, root, "2026-10-02", "-1001234567890:42")
        jobs = report_opportunities(store, resolved)
        with self.assertRaisesRegex(ApplicationRecordingError, "reply"):
            prepare_records(
                resolved, jobs,
                applied_date="2026-10-02",
                confirmation_message_id="701",
                confirmation_reply_to_id="999",
                confirmation_text="applied",
                confirmed_at="2026-10-02T01:00:00+03:00",
            )

    def test_native_v2_applied_state_suppresses_job(self):
        root, store = self.make()
        resolved = resolve_report(store, root, "2026-10-02", "-1001234567890:42")
        jobs = report_opportunities(store, resolved)
        records = prepare_records(
            resolved, jobs,
            applied_date="2026-10-02",
            confirmation_message_id="701",
            confirmation_reply_to_id="700",
            confirmation_text="applied",
            confirmed_at="2026-10-02T01:00:00+03:00",
        )
        record_all(store, records, False)
        legacy = root / "legacy.jsonl"
        legacy.write_text("")
        index = AppliedIndex(legacy, store=store)
        self.assertTrue(index.applied(jobs[0]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
