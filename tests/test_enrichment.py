import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.enrichment import DescriptionResult, EnrichmentEngine
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.store import Store


class FakeDescriptions:
    def __init__(self, stop_at=None):
        self.calls = 0
        self.stop_at = stop_at

    def fetch(self, linkedin_id):
        self.calls += 1
        if self.calls == self.stop_at:
            return DescriptionResult("", 1, 429, "provider_rate_limit")
        return DescriptionResult(f"Description for {linkedin_id}", 1, 200)


class EnrichmentTests(unittest.TestCase):
    def test_restriction_preserves_completed_descriptions(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("run", "2026-10-01")
            jobs = [
                {"linkedin_id": "li-1", "title": "A"},
                {"linkedin_id": "li-2", "title": "B"},
                {"linkedin_id": "li-3", "title": "C"},
            ]
            client = FakeDescriptions(stop_at=2)
            result = EnrichmentEngine(store, client).run("run", jobs)
            self.assertFalse(result["complete"])
            self.assertEqual(result["restriction"], "provider_rate_limit")
            self.assertEqual(len(result["jobs"]), 1)
            self.assertEqual(store.job_count("run"), 1)
            self.assertEqual(store.jobs("run")[0]["linkedin_id"], "li-1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
