"""Structured JSON logging for trade decisions and system events."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOG_DIR = Path("logs")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_EVENTS_FILE = _LOG_DIR / "events.jsonl"


def log_event(event: str, payload: dict[str, Any]) -> None:
    """Append-only structured JSON log to stdout and logs/events.jsonl."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **payload,
    }
    line = json.dumps(record) + "\n"
    sys.stdout.write(line)
    with _EVENTS_FILE.open("a", encoding="utf-8") as f:
        f.write(line)
