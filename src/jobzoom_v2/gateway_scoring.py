from __future__ import annotations
import hashlib
import json
import os
import uuid
from pathlib import Path

import requests

from .scoring import ScoreResult


PROMPT_VERSION = "jobzoom-v2-score-v1"
DEFAULT_SECRETS = Path.home() / ".openclaw/config/secrets.json"


def load_token(path: Path = DEFAULT_SECRETS) -> str:
    env = os.environ.get("JOBZOOM_GATEWAY_TOKEN", "").strip()
    if env:
        return env
    data = json.loads(Path(path).read_text())
    token = (
        data.get("openclaw", {})
        .get("gateway", {})
        .get("auth", {})
        .get("token", "")
    )
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError("gateway_token_missing")
    return token.strip()


class GatewayScorer:
    def __init__(self, cv_text: str, model: str, candidate_name: str,
                 gateway_url: str = "http://127.0.0.1:18789",
                 timeout: int = 300, store=None,
                 run_id: str | None = None, daily_limit: int = 80):
        self.cv_text = cv_text
        self.model = model
        self.candidate_name = str(candidate_name).strip()
        if not self.candidate_name:
            raise ValueError("candidate_name_required")
        self.gateway_url = str(gateway_url).rstrip("/")
        self.timeout = timeout
        self.store = store
        self.run_id = run_id
        self.daily_limit = int(daily_limit)
        self.cv_sha256 = hashlib.sha256(cv_text.encode()).hexdigest()

    def input_binding(self, job: dict) -> str:
        payload = {
            "prompt_version": PROMPT_VERSION,
            "model": self.model,
            "cv_sha256": self.cv_sha256,
            "linkedin_id": str(job.get("linkedin_id") or ""),
            "title": str(job.get("title") or ""),
            "company": str(job.get("company") or ""),
            "country": str(job.get("country") or ""),
            "description_sha256": hashlib.sha256(
                str(job.get("description") or "").encode()
            ).hexdigest(),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(raw).hexdigest()

    def score(self, job: dict) -> ScoreResult:
        description = str(job.get("description") or "")
        if not description.strip():
            raise ValueError("complete_description_required")
        prompt = f"""Score this job for {self.candidate_name} using only the CV and job data below.

CV:
{self.cv_text}

Job Title: {job.get('title', '')}
Company: {job.get('company', '')}
Country: {job.get('country', '')}
Job Description:
{description}

Score 0-100:
Title alignment 0-25
Industry fit 0-25
Skills match 0-25
Seniority alignment 0-15
GCC location 0-10

Do not infer unlisted candidate experience.
Return only JSON:
{{"score": 0, "verdict": "watchlist|readiness_review|below_threshold", "rationale": "one-line evidence-based explanation", "provisional": false}}
"""
        body = {
            "model": "openclaw/jobzoom",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 300,
            "response_format": {"type": "json_object"},
        }
        call_id = None
        if self.store is not None:
            if not self.run_id:
                raise RuntimeError("scoring_run_id_missing")
            call_id = uuid.uuid4().hex
            self.store.reserve_scoring_call(
                self.run_id, str(job.get("linkedin_id") or ""),
                call_id, self.daily_limit,
            )
        response = requests.post(
            self.gateway_url + "/v1/chat/completions",
            headers={
                "Authorization": "Bearer " + load_token(),
                "Content-Type": "application/json",
                "x-openclaw-model": self.model,
            },
            json=body,
            timeout=self.timeout,
        )
        if call_id:
            self.store.complete_scoring_call(
                call_id, "http_" + str(response.status_code)
            )
        if response.status_code in (401, 403):
            raise RuntimeError("scoring_auth_rejected")
        if response.status_code == 429:
            raise RuntimeError("scoring_quota_unavailable")
        response.raise_for_status()
        envelope = response.json()
        choices = envelope.get("choices")
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("scoring_choices_invalid")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("scoring_content_missing")
        parsed = json.loads(content)
        score = parsed.get("score")
        verdict = parsed.get("verdict")
        rationale = parsed.get("rationale")
        provisional = parsed.get("provisional")
        if isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100:
            raise ValueError("scoring_score_invalid")
        if verdict not in {"watchlist", "readiness_review", "below_threshold"}:
            raise ValueError("scoring_verdict_invalid")
        if not isinstance(rationale, str) or not rationale.strip():
            raise ValueError("scoring_rationale_missing")
        if provisional is not False:
            raise ValueError("scoring_provisional_not_allowed")
        served_model = str(envelope.get("model") or self.model)
        return ScoreResult(
            score, verdict,
            {
                "rationale": rationale.strip(),
                "requested_model": self.model,
                "served_model": served_model,
                "prompt_version": PROMPT_VERSION,
            },
        )
