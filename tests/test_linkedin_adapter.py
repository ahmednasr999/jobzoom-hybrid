import hashlib
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jobzoom_v2.linkedin import PacedGuestSession


def response(status, url="https://www.linkedin.com/jobs-guest/jobs/api/test"):
    item = requests.Response()
    item.status_code = status
    item.url = url
    item._content = b""
    item.headers = {}
    return item


class LinkedInAdapterTests(unittest.TestCase):
    def test_429_is_classified_without_retry(self):
        session = PacedGuestSession(4)
        request = requests.Request("GET", "https://www.linkedin.com/jobs-guest/jobs/api/test")
        prepared = session.prepare_request(request)
        with patch.object(requests.Session, "send", return_value=response(429)) as send:
            result = session.send(prepared)
        self.assertEqual(result.status_code, 429)
        self.assertEqual(session.request_count, 1)
        self.assertEqual(session.restriction, "provider_rate_limit")
        self.assertEqual(send.call_count, 1)

    def test_credentials_are_rejected_before_transport(self):
        session = PacedGuestSession(4)
        request = requests.Request(
            "GET",
            "https://www.linkedin.com/jobs-guest/jobs/api/test",
            headers={"Authorization": "Bearer secret"},
        )
        prepared = session.prepare_request(request)
        with patch.object(requests.Session, "send") as send:
            with self.assertRaises(RuntimeError):
                session.send(prepared)
        self.assertEqual(send.call_count, 0)
        self.assertEqual(session.request_count, 0)

    def test_redirect_is_restriction(self):
        session = PacedGuestSession(4)
        request = requests.Request("GET", "https://www.linkedin.com/jobs-guest/jobs/api/test")
        prepared = session.prepare_request(request)
        item = response(302, "https://www.linkedin.com/checkpoint")
        item.headers["location"] = "/login"
        with patch.object(requests.Session, "send", return_value=item):
            session.send(prepared)
        self.assertEqual(session.restriction, "redirect_response")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class PaginationTests(unittest.TestCase):
    def card(self, job_id):
        return (
            '<div class="base-search-card">'
            '<a class="base-card__full-link" href="https://www.linkedin.com/jobs/view/role-' + str(job_id) + '"></a>'
            '<span class="sr-only">Director PMO</span>'
            '<h4 class="base-search-card__subtitle"><a>Example</a></h4>'
            '<span class="job-search-card__location">Riyadh</span>'
            '<time datetime="' + date.today().isoformat() + '"></time>'
            '</div>'
        )

    def test_page_advance_uses_current_page_card_count(self):
        from urllib.parse import parse_qs, urlsplit
        from jobzoom_v2.linkedin import LinkedInGuestClient
        from jobzoom_v2.model import QueryKey
        starts = []

        def transport(request, **kwargs):
            start = int(parse_qs(urlsplit(request.url).query)["start"][0])
            starts.append(start)
            html = "".join(self.card(1000000 + start + i) for i in range(10))
            item = response(200)
            item.url = request.url
            item._content = html.encode()
            return item

        client = LinkedInGuestClient(25, 24, 4)
        with patch("jobzoom_v2.linkedin.time.sleep"),              patch.object(requests.Session, "send", side_effect=transport):
            result = client.search(QueryKey("Director PMO", "Saudi Arabia"))
        self.assertEqual(starts, [0, 10, 20])
        self.assertEqual(len(result.jobs), 25)
        self.assertEqual(result.request_count, 3)
        self.assertIsNone(result.restriction)


class DetailAvailabilityTests(unittest.TestCase):
    def test_missing_job_detail_is_not_global_provider_hold(self):
        session = PacedGuestSession(4, phase="enrichment")
        request = requests.Request(
            "GET", "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/1234567"
        )
        prepared = session.prepare_request(request)
        with patch.object(requests.Session, "send", return_value=response(404)):
            result = session.send(prepared)
        self.assertEqual(result.status_code, 404)
        self.assertIsNone(session.restriction)
        self.assertEqual(session.events[-1]["outcome"], "resource_unavailable")


class BodyValidationTests(unittest.TestCase):
    def test_http_200_challenge_is_not_success(self):
        from jobzoom_v2.linkedin import LinkedInGuestClient
        from jobzoom_v2.model import QueryKey
        item = response(200)
        item._content = b"<html><body>Sign in to LinkedIn security verification</body></html>"
        with patch.object(requests.Session, "send", return_value=item):
            result = LinkedInGuestClient(35, 24, 4).search(
                QueryKey("Director PMO", "Saudi Arabia")
            )
        self.assertEqual(result.restriction, "provider_challenge")

    def test_nonempty_unknown_markup_is_not_zero_result_success(self):
        from jobzoom_v2.linkedin import LinkedInGuestClient
        from jobzoom_v2.model import QueryKey
        item = response(200)
        item._content = b"<html><body><div>unexpected markup</div></body></html>"
        with patch.object(requests.Session, "send", return_value=item):
            result = LinkedInGuestClient(35, 24, 4).search(
                QueryKey("Director PMO", "Saudi Arabia")
            )
        self.assertTrue(result.restriction.startswith("search_markup_unrecognized:"))

    def test_known_exhaustion_fingerprint_is_zero_result_success(self):
        import jobzoom_v2.linkedin as linkedin
        from jobzoom_v2.model import QueryKey
        item = response(200)
        item._content = b"<html>known exhausted</html>"
        fingerprint = (hashlib.sha256(item.content).hexdigest(), len(item.content))
        with patch.object(linkedin, "KNOWN_EXHAUSTION_FINGERPRINTS", {fingerprint}),              patch.object(requests.Session, "send", return_value=item):
            result = linkedin.LinkedInGuestClient(35, 24, 4).search(
                QueryKey("Director PMO", "Saudi Arabia")
            )
        self.assertIsNone(result.restriction)
        self.assertEqual(result.jobs, [])
        self.assertEqual(result.request_count, 1)


class AnchorRootCardTests(unittest.TestCase):
    def markup(self, urn_id="1234567", href_id="1234567",
               host="www.linkedin.com"):
        return (
            '<a class="base-search-card job-search-card" '
            'data-entity-urn="urn:li:jobPosting:' + urn_id + '" '
            'href="https://' + host + '/jobs/view/role-' + href_id + '">'
            '<h3 class="base-search-card__title">Head of PMO</h3>'
            '<h4 class="base-search-card__subtitle">Example Health</h4>'
            '<span class="job-search-card__location">Muscat, Oman</span>'
            '<time class="job-search-card__listdate--new" datetime="'
            + date.today().isoformat() + '"></time>'
            '</a>'
        )

    def test_strict_anchor_root_card_is_parsed(self):
        from jobzoom_v2.linkedin import LinkedInGuestClient
        from jobzoom_v2.model import QueryKey
        first = response(200)
        first._content = self.markup(host="om.linkedin.com").encode()
        exhausted = response(200)
        exhausted._content = b"<known-exhaustion>"
        fingerprint = (
            hashlib.sha256(exhausted.content).hexdigest(),
            len(exhausted.content),
        )
        with patch("jobzoom_v2.linkedin.KNOWN_EXHAUSTION_FINGERPRINTS",
                   {fingerprint}),              patch.object(requests.Session, "send",
                          side_effect=[first, exhausted]):
            result = LinkedInGuestClient(35, 24, 4).search(
                QueryKey("Head of PMO", "Oman")
            )
        self.assertIsNone(result.restriction)
        self.assertEqual(len(result.jobs), 1)
        self.assertEqual(result.jobs[0]["linkedin_id"], "li-1234567")

    def test_anchor_root_identity_mismatch_fails_closed(self):
        from jobzoom_v2.linkedin import LinkedInGuestClient
        from jobzoom_v2.model import QueryKey
        item = response(200)
        item._content = self.markup("1234567", "7654321").encode()
        with patch.object(requests.Session, "send", return_value=item):
            result = LinkedInGuestClient(35, 24, 4).search(
                QueryKey("Head of PMO", "Oman")
            )
        self.assertEqual(
            result.restriction,
            "search_card_validation_failed:anchor_identity_mismatch",
        )
