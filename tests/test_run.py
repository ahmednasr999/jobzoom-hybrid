import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import jobzoom_v2.run as run
from jobzoom_v2.model import QueryKey
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.scoring import ScoreResult
from jobzoom_v2.store import Store, canonical_sha


class RunTests(unittest.TestCase):
    def test_daily_summary(self):
        text = run.daily_summary([
            {"country": "Saudi Arabia"},
            {"country": "Saudi Arabia"},
            {"country": "Qatar"},
        ])
        self.assertEqual(
            text,
            "🔍 JobZoom Daily: 3 matches found across 2 countries",
        )

    def test_preflight_reports_dynamic_matrix_size(self):
        cfg = {
            "profile": {"name": "Sample Candidate", "timezone": "UTC"},
            "runtime": {
                "max_requests_per_run": 100,
                "max_requests_rolling_24h": 200,
            },
        }
        with patch.object(run, "runtime_config", return_value=cfg),              patch.object(run, "load_matrix", return_value=[
                 QueryKey("A", "X"), QueryKey("B", "X")
             ]),              patch.object(run, "v2_state_gate", return_value={
                 "provider_hold": None, "unresolved_runs": []
             }):
            result = run.preflight()
        self.assertEqual(result["matrix_queries"], 2)
        self.assertTrue(result["live_allowed"])

    def test_score_and_package_uses_configured_identity_and_threshold(self):
        class FakeGatewayScorer:
            def __init__(self, cv_text, model, candidate_name, **kwargs):
                self.model = model
                self.candidate_name = candidate_name
            def input_binding(self, job):
                return canonical_sha({"linkedin_id": job["linkedin_id"]})
            def score(self, job):
                return ScoreResult(
                    88, "readiness_review",
                    {"rationale": "Strong evidence."},
                )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            master = root / "master.md"
            master.write_text((ROOT / "config/cv-master.example.md").read_text())
            cfg = {
                "profile": {
                    "name": "Sample Candidate",
                    "timezone": "UTC",
                    "cv_path": str(master),
                },
                "search": {
                    "titles": ["Director PMO"],
                    "countries": ["Saudi Arabia"],
                    "hours_old": 24,
                },
                "runtime": {
                    "max_requests_per_run": 100,
                    "max_requests_rolling_24h": 200,
                    "minimum_request_gap_seconds": 4,
                    "query_checkpoint_ttl_seconds": 21600,
                    "description_ttl_seconds": 86400,
                },
                "scoring": {
                    "model": "test-model",
                    "max_calls_per_day": 10,
                    "report_threshold": 70,
                    "readiness_threshold": 82,
                },
                "openclaw": {"gateway_url": "http://127.0.0.1:18789"},
                "telegram": {"target": "-1001234567890:42"},
                "compensation_targets": {},
            }
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json")[:1],
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            run_id = "portable-test"
            store.create_run(run_id, "2026-10-02", pipeline.matrix)
            store.transition(run_id, __import__(
                "jobzoom_v2.model", fromlist=["Stage"]
            ).Stage.DISCOVERY)
            store.start_query(run_id, 1)
            store.complete_query(run_id, 1, 1, "a" * 64)
            pipeline.assert_discovery_complete(run_id)
            pipeline.finish_enrichment(run_id)
            description = " ".join([
                "enterprise PMO transformation healthcare technology delivery governance"
            ] * 90)
            job = {
                "linkedin_id": "li-999",
                "title": "Director PMO",
                "search_title": "Director PMO",
                "company": "Example Health",
                "country": "Saudi Arabia",
                "search_country": "Saudi Arabia",
                "location": "Riyadh, Saudi Arabia",
                "url": "https://www.linkedin.com/jobs/view/999",
                "description": description,
                "description_sha256": "b" * 64,
            }
            store.upsert_job(run_id, job)
            with patch.object(run, "GatewayScorer", FakeGatewayScorer):
                summary, report, archive, opportunities, cvs = run.score_and_package(
                    store, pipeline, run_id, "2026-10-02", cfg, [job], root / "out"
                )
            self.assertEqual(len(opportunities), 1)
            self.assertEqual(len(cvs), 1)
            self.assertIn("Sample-Candidate", cvs[0]["filename"])
            self.assertEqual(report.name, "jobzoom-20261002.pdf")
            self.assertEqual(archive.name, "jobzoom-20261002.zip")
            self.assertIn("1 matches", summary.read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
