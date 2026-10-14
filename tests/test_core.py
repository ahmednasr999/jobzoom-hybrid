import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.model import Stage
from jobzoom_v2.package import build_manifest, build_zip, sha256, verify_delivery_binding
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.store import Store


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "jobzoom-v2.sqlite")
        self.addCleanup(self.store.close)
        self.matrix = load_matrix(ROOT / "config/jobzoom.example.json")
        self.runtime = load_runtime(ROOT / "config/jobzoom.example.json")
        self.pipeline = Pipeline(self.store, self.matrix, self.runtime)
        self.run_id = "20261001-test"
        self.pipeline.create(self.run_id, "2026-10-01")

    def complete_discovery(self):
        for seq in range(1, 151):
            self.store.start_query(self.run_id, seq)
            self.store.complete_query(self.run_id, seq, 0, "a" * 64)

    def test_matrix_is_exactly_150(self):
        self.assertEqual(len(self.matrix), 150)
        self.assertEqual(len({q.identity for q in self.matrix}), 150)

    def test_discovery_requires_all_queries(self):
        self.store.start_query(self.run_id, 1)
        self.store.complete_query(self.run_id, 1, 2, "a" * 64)
        with self.assertRaises(ValueError):
            self.pipeline.assert_discovery_complete(self.run_id)
        self.assertEqual(self.store.run(self.run_id)["stage"], Stage.DISCOVERY.value)

    def test_pause_preserves_completed_queries(self):
        for seq in range(1, 11):
            self.store.start_query(self.run_id, seq)
            self.store.complete_query(self.run_id, seq, seq, "b" * 64)
        self.store.start_query(self.run_id, 11)
        self.store.fail_query(self.run_id, 11, "rate_limited", deferred=True)
        self.pipeline.pause(self.run_id, "provider_rate_limit")
        counts = self.store.query_counts(self.run_id)
        self.assertEqual(counts["completed"], 10)
        self.assertEqual(counts["deferred"], 1)
        self.assertEqual(self.store.run(self.run_id)["stage"], Stage.PAUSED.value)


    def test_request_ledger_is_durable(self):
        self.store.record_request(self.run_id, "r1", "discovery", 1, 200, "ok")
        self.store.record_request(self.run_id, "r2", "discovery", 1, 429, "rate_limited")
        self.assertEqual(self.store.request_count(self.run_id), 2)
        with self.assertRaises(Exception):
            self.store.record_request(self.run_id, "r2", "discovery", 1, 429, "duplicate")

    def test_delivery_requires_hash_binding(self):
        self.complete_discovery()
        self.pipeline.assert_discovery_complete(self.run_id)
        self.pipeline.finish_enrichment(self.run_id)
        self.pipeline.finish_scoring(self.run_id)
        self.pipeline.finish_cvs(self.run_id)
        self.pipeline.finish_report(self.run_id)
        summary = self.root / "summary.txt"
        pdf = self.root / "report.pdf"
        zip_path = self.root / "package.zip"
        summary.write_text("🔍 JobZoom Daily: 1 matches found across 1 country")
        pdf.write_bytes(b"pdf")
        zip_path.write_bytes(b"zip")
        self.store.add_artifact(self.run_id, "summary", str(summary), sha256(summary))
        self.store.add_artifact(self.run_id, "pdf", str(pdf), sha256(pdf))
        self.store.add_artifact(self.run_id, "zip", str(zip_path), sha256(zip_path))
        target = "-1001234567890:42"
        self.store.begin_delivery_attempt(self.run_id, "summary", sha256(summary), target)
        self.store.add_delivery(self.run_id, "summary", "99", sha256(summary), target)
        self.store.close_delivery_attempt(self.run_id, "summary", "confirmed", "99")
        self.store.begin_delivery_attempt(self.run_id, "pdf", sha256(pdf), target)
        self.store.add_delivery(self.run_id, "pdf", "100", sha256(pdf), target)
        self.store.close_delivery_attempt(self.run_id, "pdf", "confirmed", "100")
        with self.assertRaises(ValueError):
            self.pipeline.finish_delivery(self.run_id)
        self.store.begin_delivery_attempt(self.run_id, "zip", sha256(zip_path), target)
        self.store.add_delivery(self.run_id, "zip", "101", sha256(zip_path), target)
        self.store.close_delivery_attempt(self.run_id, "zip", "confirmed", "101")
        self.pipeline.finish_delivery(self.run_id)
        self.assertEqual(self.store.run(self.run_id)["stage"], Stage.COMPLETED.value)


    def test_manifest_and_zip_are_immutable_bound(self):
        pdf = self.root / "JobZoom-Report.pdf"
        cv = self.root / "CV-Sample.pdf"
        pdf.write_bytes(b"report")
        cv.write_bytes(b"cv")
        manifest = self.root / "manifest.json"
        package = self.root / "JobZoom-Package.zip"
        build_manifest(self.run_id, [pdf, cv], manifest)
        digest = build_zip(manifest, [pdf, cv], package)
        self.assertEqual(len(digest), 64)
        receipts = {
            "pdf": {"message_id": "1", "artifact_sha256": sha256(pdf)},
            "zip": {"message_id": "2", "artifact_sha256": sha256(package)},
        }
        self.assertTrue(verify_delivery_binding(pdf, package, receipts))
        receipts["zip"]["artifact_sha256"] = "0" * 64
        self.assertFalse(verify_delivery_binding(pdf, package, receipts))


if __name__ == "__main__":
    unittest.main(verbosity=2)
