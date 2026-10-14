import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.discovery import DiscoveryEngine, QueryResult
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.store import Store


class FakeClient:
    def __init__(self, stop_at=None):
        self.calls = 0
        self.stop_at = stop_at

    def search(self, query):
        self.calls += 1
        if self.stop_at == self.calls:
            return QueryResult([], 1, 429, "provider_rate_limit")
        return QueryResult(
            [{"linkedin_id": f"li-{self.calls}", "title": query.title}],
            1,
        )


class DiscoveryTests(unittest.TestCase):
    def make(self, stop_at=None):
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
        client = FakeClient(stop_at)
        engine = DiscoveryEngine(store, pipeline, client)
        return store, pipeline, client, engine

    def test_all_150_queries_complete(self):
        store, _, client, engine = self.make()
        summary = engine.run("run")
        self.assertEqual(client.calls, 150)
        self.assertEqual(summary["queries"], {"completed": 150})
        self.assertEqual(summary["requests"], 150)
        self.assertEqual(summary["stage"], "enrichment")

    def test_rate_limit_stops_without_erasing_progress(self):
        store, _, client, engine = self.make(stop_at=11)
        summary = engine.run("run")
        self.assertEqual(client.calls, 11)
        self.assertEqual(summary["queries"]["completed"], 10)
        self.assertEqual(summary["queries"]["deferred"], 1)
        self.assertEqual(summary["queries"]["pending"], 139)
        self.assertEqual(summary["stage"], "paused")
        self.assertEqual(summary["requests"], 11)


    def test_no_automatic_retry_after_rate_limit(self):
        _, _, client, engine = self.make(stop_at=1)
        summary = engine.run("run")
        self.assertEqual(client.calls, 1)
        self.assertEqual(summary["queries"]["deferred"], 1)
        self.assertEqual(summary["requests"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class MultiPageEvidenceTests(unittest.TestCase):
    def test_each_http_outcome_is_recorded_exactly(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("run2", "2026-10-01")

            class Client:
                def search(self, query):
                    return QueryResult(
                        [], 3, 429, "provider_rate_limit",
                        ({"status_code": 200, "outcome": "ok"},
                         {"status_code": 200, "outcome": "ok"},
                         {"status_code": 429, "outcome": "provider_rate_limit"}),
                    )

            DiscoveryEngine(store, pipeline, Client()).run("run2")
            statuses = [row[0] for row in store.db.execute(
                "SELECT status_code FROM requests WHERE run_id=? ORDER BY rowid",
                ("run2",),
            )]
            self.assertEqual(statuses, [200, 200, 429])


class HoldClassificationTests(unittest.TestCase):
    def test_local_parser_uncertainty_pauses_without_provider_hold(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("local", "2026-10-01")

            class Client:
                def search(self, query):
                    return QueryResult(
                        [], 1, 200,
                        "search_markup_unrecognized:" + "a" * 64 + ":26",
                    )

            summary = DiscoveryEngine(store, pipeline, Client()).run("local")
            self.assertEqual(summary["stage"], "paused")
            self.assertIsNone(store.provider_hold())

    def test_rate_limit_activates_provider_hold(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("provider", "2026-10-01")

            class Client:
                def search(self, query):
                    return QueryResult([], 1, 429, "provider_rate_limit")

            DiscoveryEngine(store, pipeline, Client()).run("provider")
            hold = store.provider_hold()
            self.assertEqual(hold["active"], 1)
            self.assertEqual(hold["reason"], "provider_rate_limit")
