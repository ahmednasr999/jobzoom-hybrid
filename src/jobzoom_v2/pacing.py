from __future__ import annotations
import fcntl
import os
import time
from pathlib import Path


class SharedPacer:
    def __init__(self, path: Path, minimum_gap: float,
                 clock=time.time, sleep=time.sleep):
        if minimum_gap < 4:
            raise ValueError("minimum_gap_too_small")
        self.path = Path(path)
        self.minimum_gap = float(minimum_gap)
        self.clock = clock
        self.sleep = sleep

    def wait(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            handle.seek(0)
            raw = handle.read().strip()
            last = float(raw) if raw else 0.0
            now = self.clock()
            delay = self.minimum_gap - (now - last)
            if delay > 0:
                self.sleep(delay)
                now = self.clock()
            handle.seek(0)
            handle.truncate()
            handle.write(f"{now:.6f}\n")
            handle.flush()
            os.fsync(handle.fileno())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return now
