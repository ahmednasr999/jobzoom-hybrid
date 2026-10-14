from __future__ import annotations
import json
import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def project_root() -> Path:
    return Path(os.environ.get("JOBZOOM_HOME", PACKAGE_ROOT)).expanduser().resolve()


def config_path() -> Path:
    return Path(
        os.environ.get("JOBZOOM_CONFIG", project_root() / "config/jobzoom.json")
    ).expanduser().resolve()


def load_settings(path: Path | None = None) -> dict:
    path = Path(path or config_path())
    if not path.is_file():
        raise RuntimeError(
            f"JobZoom config not found: {path}. Run 'jobzoom init' first."
        )
    cfg = json.loads(path.read_text(encoding="utf-8"))
    required = ("profile", "search", "runtime", "scoring", "telegram", "openclaw")
    missing = [key for key in required if key not in cfg]
    if missing:
        raise ValueError("missing_config_sections:" + ",".join(missing))
    if not str(cfg["profile"].get("name") or "").strip():
        raise ValueError("profile_name_required")
    titles = cfg["search"].get("titles") or []
    countries = cfg["search"].get("countries") or []
    if not titles or not countries:
        raise ValueError("search_titles_and_countries_required")
    if len(set(titles)) != len(titles) or len(set(countries)) != len(countries):
        raise ValueError("duplicate_search_titles_or_countries")
    if float(cfg["runtime"].get("minimum_request_gap_seconds", 0)) < 4:
        raise ValueError("minimum_request_gap_must_be_at_least_4_seconds")
    if not str(cfg["telegram"].get("target") or "").strip():
        raise ValueError("telegram_target_required")
    return cfg


def resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root() / path
    return path.resolve()


def data_dir(cfg: dict) -> Path:
    return resolve_path(cfg.get("paths", {}).get("data_dir", "data"))


def reports_dir(cfg: dict) -> Path:
    return resolve_path(cfg.get("paths", {}).get("reports_dir", "reports/runs"))


def cv_path(cfg: dict) -> Path:
    return resolve_path(cfg["profile"].get("cv_path", "config/cv-master.md"))


def media_root(cfg: dict) -> Path:
    raw = cfg["openclaw"].get("media_root", "~/.openclaw/media/jobzoom")
    return Path(raw).expanduser().resolve()


def lock_path(cfg: dict) -> Path:
    return data_dir(cfg) / "jobzoom.lock"


def pacer_path(cfg: dict) -> Path:
    return data_dir(cfg) / "pacer.state"
