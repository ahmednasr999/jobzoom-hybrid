import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.cv import parse_master, render


class CVTests(unittest.TestCase):
    def test_cv_uses_only_master_highlights(self):
        master = ROOT / "config/cv-master.example.md"
        sections = parse_master(master)
        highlight = next(
            line[2:].strip()
            for line in sections["Professional Experience"]
            if line.startswith("- ")
        )
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "cv.pdf"
            render(master, "Sample Candidate", "Director PMO", [highlight], output)
            self.assertTrue(output.is_file())
            self.assertEqual(output.read_bytes()[:4], b"%PDF")
            self.assertGreater(output.stat().st_size, 2000)

    def test_fabricated_highlight_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            with self.assertRaises(ValueError):
                render(
                    ROOT / "config/cv-master.example.md",
                    "Sample Candidate",
                    "Director PMO",
                    ["Fabricated achievement not in master CV"],
                    Path(td) / "cv.pdf",
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
