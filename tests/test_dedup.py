import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.dedup import AppliedIndex


class DedupTests(unittest.TestCase):
    def test_legacy_applied_id_is_excluded_when_imported(self):
        with tempfile.TemporaryDirectory() as td:
            ledger = Path(td) / "legacy.jsonl"
            ledger.write_text(json.dumps({
                "linkedin_id": "li-123",
                "signature": "Example|Program Manager",
            }) + "\n")
            index = AppliedIndex(ledger)
            self.assertTrue(index.applied({
                "linkedin_id": "li-123",
                "company": "Example",
                "title": "Program Manager",
            }))

    def test_unknown_role_is_not_excluded(self):
        index = AppliedIndex()
        self.assertFalse(index.applied({
            "linkedin_id": "li-999",
            "company": "New Company",
            "title": "Director PMO",
        }))


if __name__ == "__main__":
    unittest.main(verbosity=2)
