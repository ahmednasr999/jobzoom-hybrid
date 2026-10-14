import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.discovery import DiscoveryEngine, QueryResult
from jobzoom_v2.enrichment import DescriptionResult, EnrichmentEngine
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.store import Store


class StopOnce:
    def __init__(self):
        self.calls = 0
    def search(self, query):
        self.calls += 1
        if self.calls == 11:
            return QueryResult([], 1, 429, "provider_rate_limit")
        return QueryResult([{
            "linkedin_id": f"li-{self.calls}",
            "title": query.title,
            "country": query.country,
        }], 1, 200)


class AlwaysOK:
    def __init__(self):
        self.calls = 0
    def search(self, query):
        self.calls += 1
        return QueryResult([], 1, 200)


class ResumeTests(unittest.TestCase):
    def make(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        store = Store(root / "state.sqlite")
        self.addCleanup(store.close)
        pipeline = Pipeline(
            store,
            load_matrix(ROOT / "config/jobzoom.example.json"),
            load_runtime(ROOT / "config/jobzoom.example.json"),
        )
        pipeline.create("run", "2026-10-01")
        return store, pipeline

    def test_discovery_resume_keeps_first_ten(self):
        store, pipeline = self.make()
        first = StopOnce()
        summary = DiscoveryEngine(store, pipeline, first).run("run")
        self.assertEqual(summary["queries"]["completed"], 10)
        self.assertEqual(pipeline.resume_discovery("run"), 11)
        second = AlwaysOK()
        final = DiscoveryEngine(store, pipeline, second).run("run")
        self.assertEqual(final["queries"], {"completed": 150})
        self.assertEqual(second.calls, 140)

    def test_enrichment_resume_reuses_saved_descriptions(self):
        store, pipeline = self.make()
        for seq in range(1, 151):
            store.start_query("run", seq)
            store.complete_query("run", seq, 0, "a" * 64)
        pipeline.assert_discovery_complete("run")
        jobs = [
            {"linkedin_id": "li-1", "title": "Director PMO"},
            {"linkedin_id": "li-2", "title": "Head of PMO"},
        ]

        class First:
            def __init__(self): self.calls = 0
            def fetch(self, linkedin_id):
                self.calls += 1
                if self.calls == 2:
                    return DescriptionResult("", 1, 429, "provider_rate_limit")
                return DescriptionResult("x " * 600, 1, 200)

        first = First()
        result = EnrichmentEngine(store, first).run("run", jobs)
        self.assertFalse(result["complete"])
        pipeline.pause("run", "provider_rate_limit")
        pipeline.resume_enrichment("run")

        class Second:
            def __init__(self): self.calls = 0
            def fetch(self, linkedin_id):
                self.calls += 1
                return DescriptionResult("y " * 600, 1, 200)

        second = Second()
        result = EnrichmentEngine(store, second).run("run", jobs)
        self.assertTrue(result["complete"])
        self.assertEqual(second.calls, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
