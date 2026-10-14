import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.scoring import ScoreResult, ScoringEngine
from jobzoom_v2.store import Store


class FakeScorer:
    def __init__(self):
        self.calls = 0

    def score(self, job):
        self.calls += 1
        return ScoreResult(85, "qualified", {"id": job["linkedin_id"]})


class ScoringTests(unittest.TestCase):
    def test_existing_scores_are_reused(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("run", "2026-10-01")
            scorer = FakeScorer()
            engine = ScoringEngine(store, scorer)
            description = " ".join(["enterprise program transformation delivery"] * 100)
            jobs = [
                {"linkedin_id": "li-1", "title": "Director PMO", "description": description},
                {"linkedin_id": "li-2", "title": "Head of PMO", "description": description},
            ]
            first = engine.run("run", jobs)
            second = engine.run("run", jobs)
            self.assertEqual(first["new_scores"], 2)
            self.assertEqual(second["new_scores"], 0)
            self.assertEqual(second["reused_scores"], 2)
            self.assertEqual(scorer.calls, 2)
            self.assertEqual(store.score_count("run"), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
