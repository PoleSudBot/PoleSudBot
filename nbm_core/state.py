# nbm_core/state.py
"""
Manages persistent state and transaction logging for complex operations.
"""

import json
import threading
from datetime import datetime
from pathlib import Path

from .config import logger


class StateManager:
    """Atomically reads and writes JSON state files."""

    def __init__(self, path: Path):
        self.path = path
        self._thread_lock = threading.Lock()

    def read(self) -> dict:
        """Reads the state file."""
        if not self.path.exists():
            return {}
        try:
            with self._thread_lock, self.path.open("r", encoding="utf-8") as f:
                return json.load(f) if self.path.stat().st_size > 0 else {}
        except (OSError, json.JSONDecodeError) as e:
            logger.error(f"❌ Failed to read state file '{self.path}': {e}")
            return {}

    def write(self, state: dict) -> None:
        """Writes to the state file atomically."""
        temp_path = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self._thread_lock:
                with temp_path.open("w", encoding="utf-8") as f:
                    json.dump(state, f, indent=2, ensure_ascii=False)
                temp_path.replace(self.path)
        except OSError as e:
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
