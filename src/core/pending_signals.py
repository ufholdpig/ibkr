"""Pending signals persistence for delayed entry execution.

When a TradingSignal has entry_delay_days > 0, it is stored here
and replayed on subsequent daemon cycles after the delay expires.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from dataclasses import asdict

from src.core.logger import get_logger

logger = get_logger(__name__)

PROJECT_ROOT = Path(__file__).parent.parent.parent
DEFAULT_STORE_PATH = PROJECT_ROOT / "data" / "pending_signals.json"


class PendingSignalStore:
    """Persists signals that should execute after a delay.

    File format: JSON array of signal dicts with added metadata:
    - _created_at: ISO timestamp when signal was stored
    - _execute_after: ISO timestamp after which signal should fire
    - _expired: bool, set True after signal is consumed or expires
    """

    def __init__(self, store_path: Path = None):
        self.store_path = store_path or DEFAULT_STORE_PATH
        self._ensure_file()

    def _ensure_file(self):
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.store_path.exists():
            self.store_path.write_text("[]")

    def _load(self) -> List[dict]:
        try:
            with open(self.store_path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []

    def _save(self, data: List[dict]):
        with open(self.store_path, "w") as f:
            json.dump(data, f, indent=2, default=str)

    def add(self, signal_dict: dict, delay_days: int = 1):
        """Store a signal for delayed execution.

        Args:
            signal_dict: TradingSignal serialized as dict
            delay_days: Number of trading days to wait before executing
        """
        now = datetime.now()
        execute_after = now + timedelta(days=delay_days)

        entry = {
            **signal_dict,
            "_created_at": now.isoformat(),
            "_execute_after": execute_after.isoformat(),
            "_expired": False,
        }

        data = self._load()
        data.append(entry)
        self._save(data)
        logger.info(
            f"Pending signal stored: {signal_dict.get('symbol')} "
            f"{signal_dict.get('action')} (execute after {execute_after.date()})"
        )

    def get_ready_signals(self) -> List[dict]:
        """Return signals whose delay has expired and mark them as consumed.

        Returns list of signal dicts (without internal _ fields).
        """
        now = datetime.now()
        data = self._load()
        ready = []
        modified = False

        for entry in data:
            if entry.get("_expired"):
                continue
            execute_after = datetime.fromisoformat(entry["_execute_after"])
            if now >= execute_after:
                entry["_expired"] = True
                modified = True
                clean = {k: v for k, v in entry.items() if not k.startswith("_")}
                ready.append(clean)

        if modified:
            self._save(data)

        if ready:
            logger.info(f"Retrieved {len(ready)} ready pending signals")

        return ready

    def cleanup(self, max_age_days: int = 7):
        """Remove expired entries older than max_age_days."""
        cutoff = datetime.now() - timedelta(days=max_age_days)
        data = self._load()
        original_len = len(data)
        data = [
            entry for entry in data
            if not entry.get("_expired")
            or datetime.fromisoformat(entry["_created_at"]) > cutoff
        ]
        if len(data) < original_len:
            self._save(data)
            logger.info(f"Cleaned up {original_len - len(data)} old pending signals")

    def count_pending(self) -> int:
        """Return count of non-expired pending signals."""
        data = self._load()
        return sum(1 for entry in data if not entry.get("_expired"))
