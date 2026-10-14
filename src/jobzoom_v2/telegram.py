from __future__ import annotations
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

from .package import sha256
from .store import Store




def stage_media(run_id: str, kind: str, source: Path,
                media_root: Path) -> Path:
    source = Path(source)
    digest = sha256(source)
    target_dir = Path(media_root) / run_id
    target_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix or ".bin"
    target = target_dir / f"{kind}-{digest[:16]}{suffix}"
    if target.exists():
        if target.is_symlink() or not target.is_file() or sha256(target) != digest:
            raise RuntimeError("staged_media_collision")
        return target
    with source.open("rb") as src, target.open("xb") as dst:
        shutil.copyfileobj(src, dst)
    if sha256(target) != digest:
        raise RuntimeError("staged_media_hash_mismatch")
    return target


def message_id(stdout: str) -> str:
    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", stdout or "")
    ids = re.findall(r"Sent via telegram\. Message ID: ([1-9][0-9]*)", clean)
    if len(ids) == 1:
        return ids[0]
    try:
        envelope = json.loads(clean)
    except (ValueError, TypeError):
        envelope = {}
    value = envelope.get("messageId") if isinstance(envelope, dict) else None
    if envelope.get("ok") is not False and envelope.get("dryRun") is not True:
        if re.fullmatch(r"[1-9][0-9]*", str(value or "")):
            return str(value)
    raise RuntimeError("telegram_receipt_missing_no_resend")


class TelegramDelivery:
    def __init__(self, store: Store, target: str, media_root: Path,
                 openclaw_binary: str = "openclaw", runner=subprocess.run):
        self.store = store
        self.target = str(target).strip()
        if not self.target:
            raise ValueError("telegram_target_required")
        self.runner = runner
        self.media_root = Path(media_root).expanduser().resolve()
        self.openclaw_binary = str(openclaw_binary or "openclaw")

    def send_file(self, run_id: str, kind: str, path: Path) -> str:
        path = Path(path)
        digest = sha256(path)
        staged = stage_media(run_id, kind, path, self.media_root)
        if sha256(staged) != digest:
            raise RuntimeError("staged_media_hash_mismatch")
        existing = self.store.delivery_attempt(run_id, kind)
        if existing is not None:
            if (existing["status"] == "confirmed"
                    and existing["artifact_sha256"] == digest
                    and existing["target"] == self.target
                    and existing.get("message_id")):
                return str(existing["message_id"])
            if (existing["status"] == "local_rejected"
                    and existing["artifact_sha256"] == digest
                    and existing["target"] == self.target):
                self.store.reopen_local_rejected_delivery(
                    run_id, kind, digest, self.target
                )
            else:
                raise RuntimeError("delivery_attempt_exists_no_resend")
        else:
            self.store.begin_delivery_attempt(run_id, kind, digest, self.target)
        command = [
            self.openclaw_binary, "message", "send",
            "--channel", "telegram",
            "--target", self.target,
            "--media", str(staged),
        ]
        try:
            result = self.runner(
                command, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                combined = (result.stdout or "") + "\n" + (result.stderr or "")
                if ("LocalMediaAccessError" in combined
                        or "path-not-allowed" in combined
                        or "not under an allowed directory" in combined):
                    self.store.close_delivery_attempt(
                        run_id, kind, "local_rejected"
                    )
                    raise RuntimeError("telegram_local_media_rejected_not_sent")
                raise RuntimeError("telegram_command_failed_no_resend")
            receipt = message_id(result.stdout)
            if sha256(path) != digest or sha256(staged) != digest:
                raise RuntimeError("artifact_changed_during_delivery")
        except RuntimeError as exc:
            current = self.store.delivery_attempt(run_id, kind)
            if current and current["status"] == "local_rejected":
                raise
            self.store.close_delivery_attempt(run_id, kind, "uncertain")
            raise
        except Exception:
            self.store.close_delivery_attempt(run_id, kind, "uncertain")
            raise
        self.store.add_delivery(run_id, kind, receipt, digest, self.target)
        self.store.close_delivery_attempt(run_id, kind, "confirmed", receipt)
        return receipt

    def send_text(self, run_id: str, kind: str, text: str) -> str:
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        existing = self.store.delivery_attempt(run_id, kind)
        if existing is not None:
            if (existing["status"] == "confirmed"
                    and existing["artifact_sha256"] == digest
                    and existing["target"] == self.target
                    and existing.get("message_id")):
                return str(existing["message_id"])
            raise RuntimeError("delivery_attempt_exists_no_resend")
        self.store.begin_delivery_attempt(run_id, kind, digest, self.target)
        command = [
            self.openclaw_binary, "message", "send",
            "--channel", "telegram",
            "--target", self.target,
            "--message", text,
        ]
        try:
            result = self.runner(
                command, capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                raise RuntimeError("telegram_summary_failed_no_resend")
            receipt = message_id(result.stdout)
        except Exception:
            self.store.close_delivery_attempt(run_id, kind, "uncertain")
            raise
        self.store.add_delivery(run_id, kind, receipt, digest, self.target)
        self.store.close_delivery_attempt(run_id, kind, "confirmed", receipt)
        return receipt

    def send_message(self, text: str):
        command = [
            self.openclaw_binary, "message", "send",
            "--channel", "telegram",
            "--target", self.target,
            "--message", text,
        ]
        result = self.runner(
            command, capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0:
            raise RuntimeError("telegram_summary_failed")
        return message_id(result.stdout)
