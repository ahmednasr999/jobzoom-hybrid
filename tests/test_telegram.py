import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.package import sha256
from jobzoom_v2.pipeline import Pipeline, load_matrix, load_runtime
from jobzoom_v2.store import Store
from jobzoom_v2.telegram import TelegramDelivery, message_id


class TelegramTests(unittest.TestCase):
    def make(self, runner):
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
        artifact = root / "report.pdf"
        artifact.write_bytes(b"%PDF-test")
        return store, artifact, TelegramDelivery(store, "-1001234567890:42", root / "media", runner=runner)

    def test_receipt_parser(self):
        self.assertEqual(message_id("Sent via telegram. Message ID: 23456"), "23456")

    def test_receipt_parser_matches_native_historical_output(self):
        native = (
            "│ OpenClaw message output │\n"
            "├─────────────────────────╯\n"
            "✅ Sent via telegram. Message ID: 23139\n"
        )
        self.assertEqual(message_id(native), "23139")


    def test_confirmed_file_delivery_binds_hash(self):
        def runner(*args, **kwargs):
            return subprocess.CompletedProcess(
                args[0], 0, "Sent via telegram. Message ID: 23456\n", ""
            )
        store, artifact, delivery = self.make(runner)
        receipt = delivery.send_file("run", "pdf", artifact)
        self.assertEqual(receipt, "23456")
        row = store.db.execute(
            "SELECT status,message_id FROM delivery_attempts"
        ).fetchone()
        self.assertEqual(tuple(row), ("confirmed", "23456"))
        self.assertEqual(
            store.db.execute("SELECT COUNT(*) FROM deliveries").fetchone()[0], 1
        )

    def test_uncertain_send_cannot_be_retried(self):
        calls = []
        def runner(*args, **kwargs):
            calls.append(1)
            return subprocess.CompletedProcess(args[0], 0, "no receipt", "")
        store, artifact, delivery = self.make(runner)
        with self.assertRaises(RuntimeError):
            delivery.send_file("run", "pdf", artifact)
        with self.assertRaisesRegex(RuntimeError, "no_resend"):
            delivery.send_file("run", "pdf", artifact)
        self.assertEqual(len(calls), 1)
        status = store.db.execute(
            "SELECT status FROM delivery_attempts"
        ).fetchone()[0]
        self.assertEqual(status, "uncertain")


    def test_failed_command_is_uncertain(self):
        def runner(*args, **kwargs):
            return subprocess.CompletedProcess(args[0], 1, "", "failed")
        store, artifact, delivery = self.make(runner)
        with self.assertRaises(RuntimeError):
            delivery.send_file("run", "zip", artifact)
        status = store.db.execute(
            "SELECT status FROM delivery_attempts WHERE kind='zip'"
        ).fetchone()[0]
        self.assertEqual(status, "uncertain")

    def test_command_targets_exact_jobzoom_topic(self):
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(
                command, 0, "Sent via telegram. Message ID: 34567\n", ""
            )
        _, artifact, delivery = self.make(runner)
        delivery.send_file("run", "pdf", artifact)
        self.assertEqual(commands[0][commands[0].index("--target") + 1],
                         "-1001234567890:42")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class LocalMediaRecoveryTests(unittest.TestCase):
    def test_proven_local_path_rejection_can_reopen_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("run-local", "2026-10-01")
            artifact = root / "report.pdf"
            artifact.write_bytes(b"%PDF-local")
            def reject(command, **kwargs):
                return subprocess.CompletedProcess(
                    command, 1, "",
                    "LocalMediaAccessError: path-not-allowed"
                )
            delivery = TelegramDelivery(store, "-1001234567890:42", root / "media", runner=reject)
            with self.assertRaisesRegex(RuntimeError, "not_sent"):
                delivery.send_file("run-local", "pdf", artifact)
            self.assertEqual(
                store.delivery_attempt("run-local", "pdf")["status"],
                "local_rejected",
            )
            commands = []
            def succeed(command, **kwargs):
                commands.append(command)
                return subprocess.CompletedProcess(
                    command, 0,
                    "Sent via telegram. Message ID: 45678\n", ""
                )
            recovered = TelegramDelivery(store, "-1001234567890:42", root / "media", runner=succeed)
            self.assertEqual(
                recovered.send_file("run-local", "pdf", artifact), "45678"
            )
            self.assertEqual(len(commands), 1)
            staged = Path(commands[0][commands[0].index("--media") + 1])
            self.assertTrue(staged.is_file())
            self.assertEqual(sha256(staged), sha256(artifact))
            self.assertEqual(
                store.delivery_attempt("run-local", "pdf")["status"],
                "confirmed",
            )

    def test_uncertain_delivery_still_cannot_reopen(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("run-uncertain", "2026-10-01")
            artifact = root / "report.pdf"
            artifact.write_bytes(b"%PDF-uncertain")
            def ambiguous(command, **kwargs):
                return subprocess.CompletedProcess(command, 1, "", "unknown")
            delivery = TelegramDelivery(store, "-1001234567890:42", root / "media", runner=ambiguous)
            with self.assertRaises(RuntimeError):
                delivery.send_file("run-uncertain", "pdf", artifact)
            self.assertEqual(
                store.delivery_attempt("run-uncertain", "pdf")["status"],
                "uncertain",
            )
            with self.assertRaisesRegex(RuntimeError, "no_resend"):
                delivery.send_file("run-uncertain", "pdf", artifact)


class SummaryDeliveryTests(unittest.TestCase):
    def test_summary_receipt_is_bound_and_not_resent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("summary-run", "2026-10-02")
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(
                    command, 0,
                    "Sent via telegram. Message ID: 56789\n", ""
                )
            delivery = TelegramDelivery(store, "-1001234567890:42", root / "media", runner=runner)
            text = "🔍 JobZoom Daily: 9 matches found across 3 countries"
            self.assertEqual(
                delivery.send_text("summary-run", "summary", text), "56789"
            )
            self.assertEqual(
                delivery.send_text("summary-run", "summary", text), "56789"
            )
            self.assertEqual(len(calls), 1)
            attempt = store.delivery_attempt("summary-run", "summary")
            self.assertEqual(attempt["status"], "confirmed")
            self.assertEqual(attempt["message_id"], "56789")

    def test_uncertain_summary_is_not_resent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = Store(root / "state.sqlite")
            self.addCleanup(store.close)
            pipeline = Pipeline(
                store,
                load_matrix(ROOT / "config/jobzoom.example.json"),
                load_runtime(ROOT / "config/jobzoom.example.json"),
            )
            pipeline.create("summary-uncertain", "2026-10-02")
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                return subprocess.CompletedProcess(command, 1, "", "failed")
            delivery = TelegramDelivery(store, "-1001234567890:42", root / "media", runner=runner)
            text = "🔍 JobZoom Daily: 0 matches found across 0 countries"
            with self.assertRaises(RuntimeError):
                delivery.send_text("summary-uncertain", "summary", text)
            with self.assertRaisesRegex(RuntimeError, "no_resend"):
                delivery.send_text("summary-uncertain", "summary", text)
            self.assertEqual(len(calls), 1)
