from __future__ import annotations
import re


EXCLUDE = (
    "intern", "internship", "junior", "associate", "entry-level",
    "entry level", "part-time", "part time", "volunteer", "freelance",
    "fresh graduate", "graduate trainee", "contract", "contractor",
    "temporary", "fixed term", "consultant", "consulting",
)
SENIORITY = (
    "director", "head", "vp", "svp", "evp", "chief", "senior manager",
    "senior program manager", "senior project manager", "pmo manager",
    "program director", "transformation manager", "principal", "lead",
    "program manager", "project manager", "manager",
)
DOMAIN = (
    "program manager", "project manager", "pmo", "program director",
    "transformation manager", "operations manager", "strategy manager",
    "delivery manager", "implementation manager", "product manager",
    "portfolio manager", "chief of staff", "business manager",
    "digital transformation", "change manager", "engagement manager",
    "technology", "healthcare", "healthtech", "ai", "artificial intelligence",
)
COUNTRIES = (
    "United Arab Emirates", "Saudi Arabia", "Qatar",
    "Kuwait", "Bahrain", "Oman",
)
RESTRICTED = (
    r"\buaen\b", r"\bemiratisation\b", r"\buae nationals? only\b",
    r"\bemirati nationals? only\b", r"\bsaudi nationals? only\b",
    r"\bsaudi nationals? required\b", r"\bqatarization\b",
    r"\bqatar nationals? only\b", r"\bkuwaiti nationals? only\b",
    r"\bbahraini nationals? only\b", r"\bomani nationals? only\b",
)


def title_eligible(job: dict, target_titles=None, location_aliases=None) -> bool:
    title = " ".join(str(job.get("title") or "").lower().split())
    if not title:
        return False
    if any(re.search(r"\b" + re.escape(word) + r"\b", title) for word in EXCLUDE):
        return False
    country = str(job.get("country") or job.get("search_country") or "")
    location = " ".join(str(job.get("location") or "").lower().split())
    alias_map = location_aliases or LOCATION_ALIASES
    aliases = tuple(str(v).lower() for v in alias_map.get(country, ()))
    if not aliases and country:
        aliases = (country.lower(),)
    if not location or not any(alias in location for alias in aliases):
        return False
    configured = tuple(target_titles or ())
    if not configured:
        configured = (str(job.get("search_title") or ""),)
    exact_target = any(
        str(target).strip() and str(target).lower() in title
        for target in configured
    )
    if exact_target:
        return True
    seniority = any(
        re.search(r"\b" + re.escape(word) + r"\b", title)
        for word in SENIORITY
    )
    domain = any(
        re.search(r"\b" + re.escape(word) + r"\b", title)
        for word in DOMAIN
    )
    return seniority and domain

def complete_description(job: dict) -> bool:
    description = " ".join(str(job.get("description") or "").split())
    if len(description) < 500 or len(description.split()) < 80:
        return False
    lowered = description.lower()
    boilerplate = (
        "cookie consent", "privacy preference center",
        "strictly necessary cookies", "targeting cookies",
    )
    return not any(marker in lowered for marker in boilerplate)


def eligibility_blocked(job: dict) -> bool:
    text = (
        str(job.get("title") or "") + " " +
        str(job.get("description") or "")
    ).lower()
    return any(re.search(pattern, text) for pattern in RESTRICTED)


def scoring_candidates(jobs: list[dict], target_titles=None,
                       location_aliases=None) -> list[dict]:
    return [
        job for job in jobs
        if title_eligible(job, target_titles, location_aliases)
        and complete_description(job)
        and not eligibility_blocked(job)
    ]


def salary_floor(country: str, floors=None) -> str:
    return str((floors or {}).get(str(country or ""), "Not configured"))
LOCATION_ALIASES = {
    "United Arab Emirates": ("united arab emirates", "uae", "dubai", "abu dhabi", "sharjah", "ajman", "ras al khaimah"),
    "Saudi Arabia": ("saudi arabia", "riyadh", "jeddah", "dammam", "khobar", "mecca", "makkah", "medina"),
    "Qatar": ("qatar", "doha"),
    "Kuwait": ("kuwait",),
    "Bahrain": ("bahrain", "manama"),
    "Oman": ("oman", "muscat"),
}
