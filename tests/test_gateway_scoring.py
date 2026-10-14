import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.gateway_scoring import GatewayScorer


def response(status, payload):
    item = requests.Response()
    item.status_code = status
    item._content = json.dumps(payload).encode()
    item.headers["Content-Type"] = "application/json"
    item.url = "http://127.0.0.1:18789/v1/chat/completions"
    return item


class GatewayScoringTests(unittest.TestCase):
    def test_valid_score_is_accepted(self):
        payload = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "score": 88,
                        "verdict": "readiness_review",
                        "rationale": "Strong PMO fit",
                        "provisional": False,
                    })
                }
            }]
        }
        scorer = GatewayScorer("Master CV", "openai/gpt-6.1-sol", "Sample Candidate")
        with patch("jobzoom_v2.gateway_scoring.load_token", return_value="test"),              patch("jobzoom_v2.gateway_scoring.requests.post",
                   return_value=response(200, payload)) as call:
            result = scorer.score({
                "title": "Director PMO",
                "company": "Example",
                "country": "Saudi Arabia",
                "description": "Lead enterprise PMO.",
            })
        self.assertEqual(result.score, 88)
        self.assertEqual(result.verdict, "readiness_review")
        self.assertEqual(result.rationale["rationale"], "Strong PMO fit")
        self.assertEqual(call.call_count, 1)

    def test_missing_description_never_calls_model(self):
        scorer = GatewayScorer("Master CV", "openai/gpt-6.1-sol", "Sample Candidate")
        with patch("jobzoom_v2.gateway_scoring.requests.post") as call:
            with self.assertRaises(ValueError):
                scorer.score({"title": "Director PMO", "description": ""})
        self.assertEqual(call.call_count, 0)

    def test_quota_response_is_not_retried(self):
        scorer = GatewayScorer("Master CV", "openai/gpt-6.1-sol", "Sample Candidate")
        with patch("jobzoom_v2.gateway_scoring.load_token", return_value="test"),              patch("jobzoom_v2.gateway_scoring.requests.post",
                   return_value=response(429, {})) as call:
            with self.assertRaisesRegex(RuntimeError, "quota"):
                scorer.score({"title": "Director PMO", "description": "Complete JD"})
        self.assertEqual(call.call_count, 1)


    def test_provisional_response_is_rejected(self):
        payload = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "score": 80,
                        "verdict": "watchlist",
                        "rationale": "Incomplete evidence",
                        "provisional": True,
                    })
                }
            }]
        }
        scorer = GatewayScorer("Master CV", "openai/gpt-6.1-sol", "Sample Candidate")
        with patch("jobzoom_v2.gateway_scoring.load_token", return_value="test"),              patch("jobzoom_v2.gateway_scoring.requests.post",
                   return_value=response(200, payload)):
            with self.assertRaises(ValueError):
                scorer.score({"title": "Director PMO", "description": "Complete JD"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
