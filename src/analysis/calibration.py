"""Signal calibration tracker — compare predicted probabilities to actual outcomes.

Outputs logs/calibration.jsonl with Brier-score-ready records.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

LOGS_DIR = Path("logs")
DATA_DIR = Path("data/polymarket/markets")


def load_events(event_type: str) -> list[dict[str, Any]]:
    """Load specific event types from events.jsonl."""
    path = LOGS_DIR / "events.jsonl"
    records: list[dict[str, Any]] = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("event") == event_type:
                    records.append(rec)
            except json.JSONDecodeError:
                continue
    return records


def load_latest_market_statuses() -> dict[str, dict[str, Any]]:
    """Load the most recent market snapshot to check closures."""
    if not DATA_DIR.exists():
        return {}
    files = sorted(DATA_DIR.glob("*.parquet"))
    if not files:
        return {}
    df = pd.read_parquet(files[-1])
    if "market_id" not in df.columns:
        return {}
    return df.set_index("market_id").to_dict("index")


def brier_score(prob: float, outcome: int) -> float:
    """Calculate Brier score for a binary outcome."""
    return (prob - outcome) ** 2


def run_calibration_update() -> list[dict[str, Any]]:
    """Scan for resolved markets and write calibration records."""
    audits = load_events("SIGNAL_AUDIT")
    trades = load_events("TRADE_DECISION")
    all_signals = {rec["market_id"]: rec for rec in audits + trades if "market_id" in rec}

    markets = load_latest_market_statuses()
    calibration_path = LOGS_DIR / "calibration.jsonl"
    existing_ids: set[str] = set()
    if calibration_path.exists():
        with calibration_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        existing_ids.add(json.loads(line)["market_id"])
                    except (json.JSONDecodeError, KeyError):
                        continue

    new_records: list[dict[str, Any]] = []
    for market_id, signal in all_signals.items():
        if market_id in existing_ids:
            continue
        market_info = markets.get(market_id)
        if market_info is None:
            continue  # Market not in latest snapshot — may be closed or removed

        # Determine if market is closed/resolved
        is_closed = market_info.get("closed", False) or market_info.get("active", True) is False
        if not is_closed:
            continue

        outcome = market_info.get("resolution", None)
        if outcome is None:
            continue

        # Normalize outcome to 0/1
        if isinstance(outcome, str):
            outcome_int = 1 if outcome.lower() in ("yes", "true", "1") else 0
        else:
            outcome_int = int(outcome)

        prob = signal.get("signal_prob", signal.get("market_price", 0.5))
        bs = brier_score(prob, outcome_int)

        record = {
            "market_id": market_id,
            "question": signal.get("question", ""),
            "predicted_prob": prob,
            "outcome": outcome_int,
            "brier_score": bs,
            "source": ", ".join(signal.get("signal_sources", [])),
            "signal_strength": signal.get("signal_strength", "unknown"),
            "timestamp": signal.get("timestamp", ""),
        }
        new_records.append(record)

    if new_records:
        with calibration_path.open("a", encoding="utf-8") as f:
            for rec in new_records:
                f.write(json.dumps(rec) + "\n")

    return new_records


if __name__ == "__main__":
    updated = run_calibration_update()
    print(f"Calibration update complete: {len(updated)} newly resolved markets recorded.")
