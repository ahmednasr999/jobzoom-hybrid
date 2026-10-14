import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.budget import BudgetExceeded, RunBudget
from jobzoom_v2.linkedin import PacedGuestSession
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.store import Store


def response(status=200):
    item = requests.Response()
    item.status_code = status
    item.url = "https://www.linkedin.com/jobs-guest/jobs/api/test"
    item._content = b""
    item.headers = {}
    return item


class BudgetTests(unittest.TestCase):
    def make(self, per_run=2, rolling=10):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "state.sqlite")
        self.addCleanup(store.close)
        pipeline = Pipeline(
            store,
            load_matrix(ROOT / "config/jobzoom.example.json"),
            load_runtime(ROOT / "config/jobzoom.example.json"),
        )
        pipeline.create("run", "2026-10-01")
        return store, RunBudget(store, "run", per_run, rolling)

    def test_reservation_exists_before_transport(self):
        store, budget = self.make()
        session = PacedGuestSession(4, budget, "discovery")
        prepared = session.prepare_request(
            requests.Request("GET", "https://www.linkedin.com/jobs-guest/jobs/api/test")
        )
        observed = []
        def transport(request, **kwargs):
            observed.append(store.request_count("run"))
            return response(200)
        with patch.object(requests.Session, "send", side_effect=transport):
            session.send(prepared)
        self.assertEqual(observed, [1])
        row = store.db.execute(
            "SELECT status_code,outcome FROM requests WHERE run_id='run'"
        ).fetchone()
        self.assertEqual(tuple(row), (200, "ok"))

    def test_per_run_limit_blocks_before_transport(self):
        store, budget = self.make(per_run=1)
        session = PacedGuestSession(4, budget, "discovery")
        prepared = session.prepare_request(
            requests.Request("GET", "https://www.linkedin.com/jobs-guest/jobs/api/test")
        )
        with patch.object(requests.Session, "send", return_value=response(200)) as transport:
            session.send(prepared)
            with self.assertRaises(BudgetExceeded):
                session.send(prepared)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(store.request_count("run"), 1)

    def test_transport_exception_keeps_reserved_request_counted(self):
        store, budget = self.make()
        session = PacedGuestSession(4, budget, "discovery")
        prepared = session.prepare_request(
            requests.Request("GET", "https://www.linkedin.com/jobs-guest/jobs/api/test")
        )
        with patch.object(requests.Session, "send", side_effect=OSError("offline")):
            with self.assertRaises(OSError):
                session.send(prepared)
        self.assertEqual(store.request_count("run"), 1)
        row = store.db.execute(
            "SELECT outcome FROM requests WHERE run_id='run'"
        ).fetchone()
        self.assertEqual(row[0], "reserved")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class ExternalBudgetTests(unittest.TestCase):
    def test_external_rolling_usage_is_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            store = Store(Path(td) / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("external", "2026-10-01")
            budget = RunBudget(
                store, "external", 1200, 2000,
                external_rolling_used=2000,
            )
            with self.assertRaises(BudgetExceeded):
                budget.reserve("discovery", None)
            self.assertEqual(store.request_count("external"), 0)
