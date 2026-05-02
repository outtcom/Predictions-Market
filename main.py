"""
Prediction Market Trading System — Entry point.

Usage:
    python main.py --mode paper --max-pages 2

Environment:
    cp .env.example .env
    # Fill in required keys
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from dotenv import load_dotenv

from agents.orchestrator import Orchestrator
from src.common.logger import log_event


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the prediction market trading system.")
    parser.add_argument("--mode", default=os.getenv("MODE", "paper"), choices=["paper", "live"])
    parser.add_argument("--max-pages", type=int, default=2, help="Market index pages per platform")
    parser.add_argument("--bankroll", type=float, default=float(os.getenv("BANKROLL_USD", "1000")))
    args = parser.parse_args()

    # Load env
    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)

    log_event("SYSTEM_START", {"mode": args.mode, "bankroll": args.bankroll})

    orch = Orchestrator(bankroll=args.bankroll, mode=args.mode)
    decisions = orch.run(cycles=1, max_pages=args.max_pages)

    print(f"\n=== Cycle Complete ===")
    print(f"Decisions made: {len(decisions)}")
    for d in decisions:
        print(
            f"  [{d.risk_verdict:8}] {d.decision:8} | {d.kelly_size_usd:>7.2f} USD | "
            f"EV={d.expected_value:>6.2%} | {d.market_id[:40]}"
        )

    open_positions = orch.execution.get_open_positions()
    print(f"\nOpen positions: {len(open_positions)}")
    for pos in open_positions:
        print(
            f"  {pos.side} {pos.size_usd:.2f} USD @ {pos.entry_price:.3f} | "
            f"TP={pos.take_profit} SL={pos.stop_loss} | {pos.market_id[:40]}"
        )

    log_event("SYSTEM_DONE", {"decisions": len(decisions), "open_positions": len(open_positions)})


if __name__ == "__main__":
    main()
