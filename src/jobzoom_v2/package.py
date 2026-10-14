from __future__ import annotations
import hashlib
import json
import zipfile
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def build_manifest(run_id: str, files: list[Path], output: Path,
                   bindings: dict | None = None) -> dict:
    rows = []
    seen = set()
    for item in files:
        item = Path(item)
        if not item.is_file() or item.is_symlink():
            raise ValueError(f"invalid_artifact:{item}")
        if item.name in seen:
            raise ValueError("duplicate_artifact_name")
        seen.add(item.name)
        rows.append({
            "name": item.name,
            "bytes": item.stat().st_size,
            "sha256": sha256(item),
        })
    bindings = bindings or {}
    if any(name not in seen for name in bindings):
        raise ValueError("binding_references_unknown_artifact")
    manifest = {
        "version": 2,
        "run_id": run_id,
        "files": rows,
        "bindings": bindings,
    }
    output = Path(output)
    if output.exists():
        raise RuntimeError("refusing_to_overwrite_manifest")
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    if json.loads(output.read_text()) != manifest:
        raise ValueError("manifest_readback_failed")
    return manifest


def build_zip(manifest_path: Path, files: list[Path], output: Path) -> str:
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    expected = {row["name"]: row["sha256"] for row in manifest["files"]}
    actual = {Path(p).name: sha256(Path(p)) for p in files}
    if actual != expected:
        raise ValueError("artifact_manifest_mismatch")
    output = Path(output)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest_path, "manifest.json")
        for item in files:
            archive.write(item, Path(item).name)
    with zipfile.ZipFile(output, "r") as archive:
        if archive.testzip() is not None:
            raise ValueError("zip_integrity_failed")
        archived_manifest = json.loads(archive.read("manifest.json"))
        if archived_manifest != manifest:
            raise ValueError("archived_manifest_mismatch")
        for name, digest in expected.items():
            archived = hashlib.sha256(archive.read(name)).hexdigest()
            if archived != digest:
                raise ValueError("archived_artifact_hash_mismatch:" + name)
    return sha256(output)


def verify_delivery_binding(pdf: Path, zip_path: Path, receipts: dict) -> bool:
    expected = {"pdf": sha256(pdf), "zip": sha256(zip_path)}
    for kind, digest in expected.items():
        row = receipts.get(kind)
        if not isinstance(row, dict):
            return False
        if not row.get("message_id") or row.get("artifact_sha256") != digest:
            return False
    return True
