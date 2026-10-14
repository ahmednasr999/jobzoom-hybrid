"""Node.js collection adapter for JobZoom Hybrid.

Implements the same QueryClient protocol as the Python LinkedIn backend,
allowing the Node.js scripts under legacy/node/ to be used as an
alternative collection backend.
"""
from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Protocol

from .discovery import QueryResult
from .model import QueryKey


class NodeCollectionAdapter:
    """Wraps legacy/node/scrape_searches.js as a QueryClient.

    Usage:
        adapter = NodeCollectionAdapter(script_path=Path("legacy/node/scrape_searches.js"))
        result = adapter.search(QueryKey(title="CEO", country="Qatar"))
    """

    def __init__(self, script_path: Path, node_binary: str = "node",
                 timeout_seconds: int = 300):
        self.script_path = Path(script_path)
        self.node_binary = node_binary
        self.timeout_seconds = timeout_seconds
        if not self.script_path.is_file():
            raise FileNotFoundError(f"Node collection script not found: {self.script_path}")

    def search(self, query: QueryKey) -> QueryResult:
        """Execute a single search query via the Node.js script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            query_csv = tmpdir / "query.csv"
            output_csv = tmpdir / "output.csv"

            # Write single-query CSV
            with query_csv.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.writer(fh)
                writer.writerow(["Country", "Job Title", "LinkedIn Guest Search URL"])
                writer.writerow([
                    query.country,
                    query.title,
                    f"https://www.linkedin.com/jobs/search/?keywords={query.title.replace(' ', '%20')}&location={query.country.replace(' ', '%20')}&f_TPR=r86400",
                ])

            # Run Node script
            cmd = [self.node_binary, str(self.script_path), "--input", str(query_csv), "--output", str(output_csv)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                return QueryResult(
                    jobs=[], request_count=1, status_code=0,
                    restriction="node_timeout",
                    request_events=({"status_code": 0, "outcome": "node_timeout"},),
                )

            if proc.returncode != 0:
                return QueryResult(
                    jobs=[], request_count=1, status_code=0,
                    restriction="node_error:" + proc.stderr[:200],
                    request_events=({"status_code": 0, "outcome": "node_error"},),
                )

            # Parse output CSV
            rows = []
            if output_csv.is_file():
                with output_csv.open("r", encoding="utf-8") as fh:
                    reader = csv.DictReader(fh)
                    for row in reader:
                        href = row.get("Job Link", "")
                        # Extract job ID from URL
                        job_id = ""
                        if href:
                            import re
                            m = re.search(r"-(\d{7,})$", href.rstrip("/"))
                            if m:
                                job_id = m.group(1)
                        rows.append({
                            "linkedin_id": f"li-{job_id}" if job_id else "",
                            "title": row.get("Job Title", ""),
                            "company": row.get("Company", ""),
                            "location": row.get("Location", ""),
                            "country": query.country,
                            "url": href,
                            "date_posted": row.get("Date Posted", ""),
                            "search_title": query.title,
                            "search_country": query.country,
                        })

            events = ({"status_code": 200, "outcome": "ok"},) if rows else ()
            return QueryResult(
                jobs=rows,
                request_count=1,
                status_code=200 if rows else 204,
                restriction=None if rows else "no_results",
                request_events=events,
            )
