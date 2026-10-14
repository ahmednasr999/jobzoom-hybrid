from __future__ import annotations
import json
import re
from pathlib import Path


def normalize(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", " ", str(value).lower())
    return " ".join(value.split())


def signature(job: dict) -> str:
    return normalize(job.get("company", "")) + "|" + normalize(job.get("title", ""))


class AppliedIndex:
    def __init__(self, path: Path | None = None, store=None):
        self.ids = set()
        self.signatures = set()
        if path is not None and Path(path).is_file():
            for line in Path(path).read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                linkedin_id = str(row.get("linkedin_id") or "")
                if linkedin_id:
                    self.ids.add(linkedin_id)
                sig = str(row.get("signature") or "")
                if sig:
                    self.signatures.add(normalize(sig.replace("|", " ")))
        if store is not None:
            self.ids.update(store.applied_ids())
            self.signatures.update(
                normalize(sig.replace("|", " "))
                for sig in store.applied_signatures()
            )


    def applied(self, job: dict) -> bool:
        linkedin_id = str(job.get("linkedin_id") or "")
        if linkedin_id and linkedin_id in self.ids:
            return True
        return normalize(signature(job).replace("|", " ")) in self.signatures
