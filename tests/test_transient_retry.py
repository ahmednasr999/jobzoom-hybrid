import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import jobzoom_v2.enrichment as enrichment_mod
from jobzoom_v2.discovery import backoff_delay, is_transient_restriction
from jobzoom_v2.enrichment import DescriptionResult, EnrichmentEngine
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.store import Store


class FlakyDescriptions:
    def __init__(self, fail_times):
        self.calls = 0
        self.fail_times = fail_times

    def fetch(self, linkedin_id):
        self.calls += 1
        if self.calls in self.fail_times:
            return DescriptionResult("", 1, 0, "transport_or_server_error")
        return DescriptionResult(f"Description for {linkedin_id}", 1, 200)


class TransientRetryTests(unittest.TestCase):
    def test_transient_restriction_detection(self):
        self.assertTrue(is_transient_restriction("transport_or_server_error"))
        self.assertTrue(is_transient_restriction("transport_exception:TimeoutError"))
        self.assertTrue(is_transient_restriction("node_timeout"))
        self.assertFalse(is_transient_restriction("provider_rate_limit"))
        self.assertFalse(is_transient_restriction(None))

    def test_backoff_grows_and_caps(self):
        self.assertEqual(backoff_delay(0), 30.0)
        self.assertEqual(backoff_delay(1), 60.0)
        self.assertEqual(backoff_delay(2), 120.0)
        self.assertLessEqual(backoff_delay(99), 600.0)

    def test_transient_restriction_is_retried(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("run", "2026-10-01")
            jobs = [{"linkedin_id": "li-1", "title": "A"}]
            client = FlakyDescriptions(fail_times={1})
            original = enrichment_mod.backoff_delay
            enrichment_mod.backoff_delay = lambda attempt: 0.0
            self.addCleanup(setattr, enrichment_mod, "backoff_delay", original)
            result = EnrichmentEngine(store, client).run("run", jobs)
            self.assertTrue(result["complete"])
            self.assertEqual(client.calls, 2)
            self.assertEqual(store.jobs("run")[0]["description"], "Description for li-1")

    def test_transient_restriction_pauses_after_exhaustion(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("run", "2026-10-01")
            jobs = [{"linkedin_id": "li-1", "title": "A"}]
            client = FlakyDescriptions(fail_times={1, 2, 3})
            original = enrichment_mod.backoff_delay
            enrichment_mod.backoff_delay = lambda attempt: 0.0
            self.addCleanup(setattr, enrichment_mod, "backoff_delay", original)
            result = EnrichmentEngine(store, client).run("run", jobs)
            self.assertFalse(result["complete"])
            self.assertEqual(result["restriction"], "transport_or_server_error")
            self.assertEqual(client.calls, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
