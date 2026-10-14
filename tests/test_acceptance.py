import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.discovery import DiscoveryEngine, QueryResult
from jobzoom_v2.enrichment import DescriptionResult, EnrichmentEngine
from jobzoom_v2.package import build_manifest, build_zip, sha256
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.report import generate
from jobzoom_v2.scoring import ScoreResult, ScoringEngine
from jobzoom_v2.store import Store
from jobzoom_v2.telegram import TelegramDelivery


class Search:
    def __init__(self):
        self.calls = 0
    def search(self, query):
        self.calls += 1
        return QueryResult([{
            "linkedin_id": f"li-{1000000 + self.calls}",
            "title": query.title,
            "company": f"Company {self.calls}",
            "country": query.country,
            "url": f"https://www.linkedin.com/jobs/view/{1000000 + self.calls}",
        }], 1, 200)


class Descriptions:
    def fetch(self, linkedin_id):
        text = " ".join(["enterprise program transformation delivery"] * 100)
        return DescriptionResult(text + " " + linkedin_id, 1, 200)


class Scores:
    def score(self, job):
        number = int(job["linkedin_id"].split("-")[-1])
        score = 88 if number == 1000001 else 60
        verdict = "readiness review" if score >= 82 else "below threshold"
        return ScoreResult(score, verdict, {"linkedin_id": job["linkedin_id"]})


class AcceptanceTests(unittest.TestCase):
    def test_full_150_to_verified_delivery_closeout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            run_id = "20261002-acceptance"
            pipeline.create(run_id, "2026-10-02")
            search = Search()
            discovery = DiscoveryEngine(store, pipeline, search).run(run_id)
            self.assertEqual(discovery["queries"], {"completed": 150})
            self.assertEqual(discovery["unique_jobs"], 150)
            jobs = store.jobs(run_id)
            enriched = EnrichmentEngine(store, Descriptions()).run(run_id, jobs)
            self.assertTrue(enriched["complete"])
            pipeline.finish_enrichment(run_id)
            scored = ScoringEngine(store, Scores()).run(run_id, enriched["jobs"])
            self.assertEqual(scored["total_scores"], 150)
            pipeline.finish_scoring(run_id)
            qualified = [{
                **enriched["jobs"][0],
                "score": 88,
                "verdict": "readiness review",
                "rationale": "Synthetic acceptance role.",
            }]
            cv = root / "CV-Sample-Candidate-Test.pdf"
            cv.write_bytes(b"%PDF-1.4\nsynthetic acceptance cv\n%%EOF\n")
            cv_rows = [{
                "linkedin_id": qualified[0]["linkedin_id"],
                "filename": cv.name,
                "sha256": sha256(cv),
            }]
            pipeline.finish_cvs(run_id)
            report = root / "JobZoom-Report.pdf"
            generate({
                "run_id": run_id,
                "run_date": "2026-10-02",
                "planned_searches": 150,
                "completed_searches": 150,
                "failed_searches": 0,
                "raw_hits": 150,
                "unique_jobs": 150,
                "eligible_for_description": 150,
                "descriptions_complete": 150,
                "scored_count": 150,
                "qualified_count": 1,
            }, qualified, cv_rows, report)
            pipeline.finish_report(run_id)
            manifest = root / "manifest.json"
            build_manifest(run_id, [report, cv], manifest)
            package = root / "JobZoom-Package.zip"
            build_zip(manifest, [report, cv], package)
            summary = root / "summary.txt"
            summary_text = "🔍 JobZoom Daily: 1 matches found across 1 country"
            summary.write_text(summary_text)
            store.add_artifact(run_id, "summary", str(summary), sha256(summary))
            store.add_artifact(run_id, "pdf", str(report), sha256(report))
            store.add_artifact(run_id, "zip", str(package), sha256(package))
            ids = iter(("9000", "9001", "9002"))
            def runner(command, **kwargs):
                receipt = next(ids)
                return subprocess.CompletedProcess(
                    command, 0, f"Sent via telegram. Message ID: {receipt}\n", ""
                )
            delivery = TelegramDelivery(store, "-1001234567890:42", root / "media", runner=runner)
            self.assertEqual(
                delivery.send_text(run_id, "summary", summary_text), "9000"
            )
            self.assertEqual(delivery.send_file(run_id, "pdf", report), "9001")
            self.assertEqual(delivery.send_file(run_id, "zip", package), "9002")
            pipeline.finish_delivery(run_id)
            self.assertEqual(store.run(run_id)["stage"], "completed")
            self.assertTrue(store.completion_ready(run_id))
            self.assertEqual(search.calls, 150)


if __name__ == "__main__":
    unittest.main(verbosity=2)
