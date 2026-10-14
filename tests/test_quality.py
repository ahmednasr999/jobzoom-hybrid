import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.quality import complete_description, eligibility_blocked, title_eligible


class QualityTests(unittest.TestCase):
    def test_target_senior_title_is_eligible(self):
        self.assertTrue(title_eligible({
            "title": "Director Digital Transformation",
            "country": "United Arab Emirates",
            "location": "Dubai, United Arab Emirates",
        }))

    def test_junior_title_is_rejected(self):
        self.assertFalse(title_eligible({
            "title": "Junior Digital Transformation Manager",
            "country": "United Arab Emirates",
            "location": "Dubai, United Arab Emirates",
        }))

    def test_short_description_is_not_scoreable(self):
        self.assertFalse(complete_description({
            "description": "Lead PMO transformation."
        }))

    def test_complete_description_is_scoreable(self):
        text = " ".join(["lead enterprise transformation and program delivery"] * 90)
        self.assertTrue(complete_description({"description": text}))


    def test_nationality_restriction_blocks(self):
        text = " ".join(["lead transformation program"] * 90)
        text += " Saudi nationals only."
        self.assertTrue(eligibility_blocked({
            "title": "Director Transformation",
            "description": text,
        }))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TargetCoverageTests(unittest.TestCase):
    def test_chief_operating_officer_target_passes(self):
        self.assertTrue(title_eligible({
            "title": "Chief Operating Officer",
            "search_title": "Chief Operating Officer",
            "country": "Saudi Arabia",
            "location": "Riyadh, Saudi Arabia",
        }))

    def test_director_strategy_target_passes(self):
        self.assertTrue(title_eligible({
            "title": "Director Strategy",
            "search_title": "Director Strategy",
            "country": "Qatar",
            "location": "Doha, Qatar",
        }))

    def test_international_does_not_trigger_intern_exclusion(self):
        self.assertTrue(title_eligible({
            "title": "Director International Digital Transformation",
            "country": "United Arab Emirates",
            "location": "Dubai, United Arab Emirates",
        }))
