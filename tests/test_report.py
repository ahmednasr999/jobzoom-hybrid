import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.report import generate


class ReportTests(unittest.TestCase):
    def test_standard_report_generates(self):
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "jobzoom.pdf"
            run = {
                "run_id": "20261001-test",
                "run_date": "2026-10-01",
                "planned_searches": 150,
                "completed_searches": 150,
                "failed_searches": 0,
                "raw_hits": 400,
                "unique_jobs": 300,
                "eligible_for_description": 20,
                "descriptions_complete": 20,
                "scored_count": 20,
                "qualified_count": 1,
            }
            jobs = [{
                "linkedin_id": "li-1",
                "title": "Director PMO",
                "company": "Example",
                "country": "Saudi Arabia",
                "score": 88,
                "verdict": "readiness review",
                "rationale": "Strong PMO leadership alignment.",
                "url": "https://www.linkedin.com/jobs/view/1",
            }]
            cvs = [{
                "linkedin_id": "li-1",
                "filename": "CV-Sample-Candidate-Director-PMO.pdf",
                "sha256": "a" * 64,
            }]
            generate(run, jobs, cvs, output)
            self.assertTrue(output.is_file())
            self.assertGreater(output.stat().st_size, 1000)
            self.assertEqual(output.read_bytes()[:4], b"%PDF")


if __name__ == "__main__":
    unittest.main(verbosity=2)
