# nbm_core/state.py
"""
Manages persistent state and transaction logging for complex operations.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

import portalocker

from .config import logger


class StateManager:
    """Atomically reads and writes JSON state files using file locks."""

    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        """Reads the state file with a shared lock."""
        if not self.path.exists():
            return {}
        try:
            with portalocker.Lock(str(self.path), "r", timeout=5) as f:
                return json.load(f) if self.path.stat().st_size > 0 else {}
        except (
            portalocker.exceptions.LockException,
            OSError,
            json.JSONDecodeError,
        ) as e:
            logger.error(f"❌ Failed to read state file '{self.path}': {e}")
            return {}

    def write(self, state: dict) -> None:
        """Writes to the state file atomically using a temporary file."""
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
            with portalocker.Lock(str(self.path), "w", timeout=5):
                temp_path.replace(self.path)
        except (portalocker.exceptions.LockException, OSError) as e:
            logger.error(f"❌ Failed to write state file '{self.path}': {e}")
        finally:
            temp_path.unlink(missing_ok=True)


class TransactionLogger:
    """Logs transaction records to a file in a thread-safe manner."""

    def __init__(self, log_dir: Path):
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.log_file = self.log_dir / f"sync_log_{ts}.jsonl"
        self._thread_lock = threading.Lock()

    def log(self, record: dict) -> None:
        """Appends a single JSON record to the log file."""
        try:
            line = json.dumps(record, ensure_ascii=False)
            with self._thread_lock, self.log_file.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except (TypeError, OSError) as e:
            logger.error(f"❌ Failed to write to transaction log: {e}")
