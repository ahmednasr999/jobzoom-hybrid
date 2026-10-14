from __future__ import annotations
import hashlib
import json
import logging
import random
import re
import time
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter

from .discovery import QueryResult
from .model import QueryKey

logger = logging.getLogger(__name__)


BASE = "https://www.linkedin.com"
SEARCH_PATH = "/jobs-guest/jobs/api/seeMoreJobPostings/search"
KNOWN_EXHAUSTION_FINGERPRINTS = {
    ("ea12acf875ac1dffa5de9dd8f5c5a54a41cf50370705c4694aef841aa2b28a36", 26),
}
CAIRO = ZoneInfo("Africa/Cairo")


class PacedGuestSession(requests.Session):
    def __init__(self, minimum_gap: float, budget=None, phase="provider", pacer=None,
                 backoff_base: float = 60.0, backoff_max: float = 1800.0):
        super().__init__()
        if minimum_gap < 4:
            raise ValueError("minimum_gap_too_small")
        self.minimum_gap = minimum_gap
        self.budget = budget
        self.phase = phase
        self.pacer = pacer
        self.backoff_base = backoff_base
        self.backoff_max = backoff_max
        self.last_dispatch = None
        self.request_count = 0
        self.last_status = None
        self.restriction = None
        self.events = []
        self.trust_env = False
        self.headers.clear()
        self.headers.update({
            "User-Agent": "JobZoom/2.0 (public-job-reader)",
            "Accept": "text/html,application/xhtml+xml",
        })
        self.mount("https://", HTTPAdapter(max_retries=0))

    def _apply_backoff(self, status_code: int, attempt: int) -> None:
        """Apply exponential backoff with jitter for rate-limit responses."""
        if status_code != 429 or attempt <= 0:
            return
        delay = min(self.backoff_base * (2 ** (attempt - 1)), self.backoff_max)
        jitter = random.uniform(0, delay * 0.1)
        total = delay + jitter
        logger.warning("Rate limited (429). Backing off %.1fs before retry %d", total, attempt)
        time.sleep(total)

    def get(self, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().get(url, **kwargs)

    def send(self, request, **kwargs):
        if request.headers.get("Authorization") or request.headers.get("Cookie"):
            raise RuntimeError("credentials_not_allowed")

        max_retries = 3
        for attempt in range(max_retries + 1):
            if self.pacer is not None:
                self.pacer.wait()
            else:
                now = time.monotonic()
                if self.last_dispatch is not None:
                    wait = self.minimum_gap - (now - self.last_dispatch)
                    if wait > 0:
                        time.sleep(wait)
                self.last_dispatch = time.monotonic()
            request_id = self.budget.reserve(self.phase, None) if self.budget else None
            self.request_count += 1
            try:
                response = super().send(request, **kwargs)
            except Exception:
                if request_id:
                    self.events.append({
                        "request_id": request_id,
                        "status_code": 0,
                        "outcome": "transport_exception_reserved",
                    })
                raise
            self.last_status = response.status_code
            self.cookies.clear()

            if response.status_code == 429 and attempt < max_retries:
                self._apply_backoff(response.status_code, attempt + 1)
                if request_id:
                    self.events.append({
                        "request_id": request_id,
                        "status_code": 429,
                        "outcome": "rate_limited_retry",
                    })
                continue

            if request_id:
                final_outcome = "provider_rate_limit" if (response.status_code == 429 and attempt == max_retries) else "ok"
                self.events.append({
                    "request_id": request_id,
                    "status_code": response.status_code,
                    "outcome": final_outcome,
                })
            break

        if response.is_redirect or response.is_permanent_redirect:
            self.restriction = "redirect_response"
        elif response.status_code == 429:
            self.restriction = "provider_rate_limit"
        elif response.status_code in (401, 403, 999):
            self.restriction = "provider_access_restriction"
        elif response.status_code >= 500:
            self.restriction = "provider_server_error"
        elif response.status_code in (404, 410) and self.phase == "enrichment":
            pass
        elif response.status_code >= 400:
            self.restriction = "provider_http_error"
        outcome = self.restriction or (
            "resource_unavailable" if response.status_code in (404, 410) else "ok"
        )
        if request_id:
            self.budget.complete(request_id, response.status_code, outcome)
        self.events.append({
            "request_id": request_id,
            "status_code": response.status_code,
            "outcome": outcome,
        })
        return response


def numeric_job_id(href: str) -> str:
    parsed = urlparse(str(href or ""))
    host = parsed.hostname or ""
    if (
        host not in {"www.linkedin.com", "linkedin.com"}
        and re.fullmatch(r"[a-z]{2}\.linkedin\.com", host) is None
    ):
        return ""
    if not parsed.path.startswith("/jobs/view/"):
        return ""
    match = re.search(r"(?:-|/)([0-9]{7,})$", parsed.path.rstrip("/"))
    return match.group(1) if match else ""


def challenge_body(html: str) -> bool:
    lowered = str(html or "").lower()
    markers = (
        "linkedin.com/checkpoint", "authwall", "captcha",
        "security verification", "sign in to linkedin",
        "join linkedin", "challenge-page",
    )
    return any(marker in lowered for marker in markers)


def parse_page(html: str, query: QueryKey):
    soup = BeautifulSoup(html, "html.parser")
    pairs = []
    selector = (
        "div.base-card, div.base-search-card, "
        "a.base-search-card.job-search-card"
    )
    for li in soup.find_all("li"):
        card = li.select_one(selector)
        if card is not None:
            pairs.append((li, card))
    if not pairs:
        pairs = [(card, card) for card in soup.select(selector)]
    rows = []
    invalid = 0
    invalid_reasons = []
    for container, card in pairs:
        anchor_root = (
            card.name == "a"
            and "base-search-card" in card.get("class", [])
            and "job-search-card" in card.get("class", [])
        )
        if anchor_root:
            if card.select("a,script,form,iframe,object,embed"):
                invalid += 1
                invalid_reasons.append("anchor_nested_interactive")
                continue
            anchor = card
        else:
            anchor = card.find("a", class_="base-card__full-link")
        href = anchor.get("href", "") if anchor else ""
        urn = str(container.get("data-entity-urn") or card.get("data-entity-urn") or "")
        urn_match = re.fullmatch(r"urn:li:jobPosting:([0-9]{7,})", urn)
        href_id = numeric_job_id(href)
        if anchor_root and (not urn_match or not href_id or urn_match.group(1) != href_id):
            invalid += 1
            invalid_reasons.append("anchor_identity_mismatch")
            continue
        job_id = urn_match.group(1) if urn_match else href_id
        title_tag = card.select_one(
            "h3.base-search-card__title, .base-search-card__title, span.sr-only"
        )
        company_tag = card.select_one(
            "h4.base-search-card__subtitle, .base-search-card__subtitle"
        )
        company_anchor = company_tag.find("a") if company_tag else None
        location_tag = card.select_one(
            "span.job-search-card__location, .job-search-card__location"
        )
        time_tag = card.find("time")
        title = title_tag.get_text(" ", strip=True) if title_tag else ""
        company = (
            company_anchor.get_text(" ", strip=True)
            if company_anchor else company_tag.get_text(" ", strip=True)
            if company_tag else ""
        )
        location = location_tag.get_text(" ", strip=True) if location_tag else ""
        posted = str(time_tag.get("datetime", "")) if time_tag else ""
        if not all((job_id, title, company, location, posted)):
            invalid += 1
            missing = [
                name for name, value in (
                    ("job_id", job_id), ("title", title), ("company", company),
                    ("location", location), ("posted", posted),
                ) if not value
            ]
            invalid_reasons.append("missing_" + "_".join(missing))
            continue
        rows.append({
            "linkedin_id": "li-" + job_id,
            "title": title,
            "company": company,
            "location": location,
            "country": query.country,
            "url": BASE + "/jobs/view/" + job_id,
            "date_posted": posted,
            "search_title": query.title,
            "search_country": query.country,
        })
    return rows, len(pairs), invalid, tuple(invalid_reasons)


def posted_date_valid(value: str, today: date | None = None) -> bool:
    today = today or datetime.now(CAIRO).date()
    try:
        posted = datetime.strptime(str(value), "%Y-%m-%d").date()
    except ValueError:
        return False
    return today - timedelta(days=1) <= posted <= today


class LinkedInGuestClient:
    def __init__(self, results_per_query: int | None, hours_old: int, minimum_gap: float,
                 budget=None, pacer=None):
        self.results_per_query = results_per_query
        self.hours_old = hours_old
        self.minimum_gap = minimum_gap
        self.budget = budget
        self.pacer = pacer

    def search(self, query: QueryKey) -> QueryResult:
        session = PacedGuestSession(
            self.minimum_gap, self.budget, "discovery", self.pacer
        )
        jobs = []
        seen = set()
        page_signatures = set()
        start = 0
        validated_exhaustion = False
        limit = self.results_per_query if self.results_per_query is not None else float("inf")
        while len(jobs) < limit and start < 1000:
            response = session.get(
                BASE + SEARCH_PATH,
                params={
                    "keywords": query.title,
                    "location": query.country,
                    "distance": "50",
                    "pageNum": "0",
                    "start": str(start),
                    "f_TPR": "r" + str(self.hours_old * 3600),
                },
                timeout=10,
            )
            if session.restriction:
                break
            body = response.text or ""
            if challenge_body(body):
                mark_body_restriction(session, "provider_challenge")
                break
            if not body.strip():
                validated_exhaustion = True
                break
            cards, raw_count, invalid, invalid_reasons = parse_page(body, query)
            if raw_count == 0:
                raw_body = response.content or b""
                fingerprint = (hashlib.sha256(raw_body).hexdigest(), len(raw_body))
                if fingerprint in KNOWN_EXHAUSTION_FINGERPRINTS:
                    validated_exhaustion = True
                    break
                mark_body_restriction(
                    session,
                    "search_markup_unrecognized:"
                    + fingerprint[0] + ":" + str(fingerprint[1]),
                )
                break
            if invalid:
                reason = (
                    invalid_reasons[0] if invalid_reasons
                    else "unspecified"
                )
                mark_body_restriction(
                    session, "search_card_validation_failed:" + reason
                )
                break
            if any(not posted_date_valid(job["date_posted"]) for job in cards):
                mark_body_restriction(session, "search_window_validation_failed")
                break
            signature = tuple(job["linkedin_id"] for job in cards)
            if signature in page_signatures:
                mark_body_restriction(session, "pagination_repeated_page")
                break
            page_signatures.add(signature)
            for job in cards:
                linkedin_id = job["linkedin_id"]
                if linkedin_id in seen:
                    continue
                seen.add(linkedin_id)
                jobs.append(job)
                if len(jobs) >= limit:
                    break
            start += raw_count
        if (not session.restriction and len(jobs) < limit
                and not validated_exhaustion and start >= 1000):
            mark_body_restriction(session, "pagination_limit_unresolved")
        capped = jobs if self.results_per_query is None else jobs[:self.results_per_query]
        return QueryResult(
            jobs=capped,
            request_count=session.request_count,
            status_code=session.last_status or 0,
            restriction=session.restriction,
            request_events=tuple(session.events),
        )


class LinkedInDescriptionClient:
    def __init__(self, minimum_gap: float, budget=None, pacer=None):
        self.minimum_gap = minimum_gap
        self.budget = budget
        self.pacer = pacer

    def fetch(self, linkedin_id: str):
        from .enrichment import DescriptionResult
        raw_id = linkedin_id.removeprefix("li-")
        if not raw_id.isdigit():
            raise ValueError("invalid_linkedin_id")
        session = PacedGuestSession(
            self.minimum_gap, self.budget, "enrichment", self.pacer
        )
        response = session.get(
            BASE + "/jobs-guest/jobs/api/jobPosting/" + raw_id,
            timeout=10,
        )
        description = ""
        if not session.restriction and challenge_body(response.text):
            mark_body_restriction(session, "provider_challenge")
        if not session.restriction:
            soup = BeautifulSoup(response.text, "html.parser")
            node = soup.find(
                "div",
                class_=lambda value: value and "show-more-less-html__markup" in value,
            )
            if node is not None:
                description = " ".join(node.get_text(" ", strip=True).split())
        return DescriptionResult(
            description=description,
            request_count=session.request_count,
            status_code=session.last_status or 0,
            restriction=session.restriction,
            request_events=tuple(session.events),
        )


def mark_body_restriction(session: PacedGuestSession, reason: str):
    session.restriction = reason
    if session.events:
        event = session.events[-1]
        event["outcome"] = reason
        request_id = event.get("request_id")
        if request_id and session.budget:
            session.budget.reclassify(request_id, reason)
