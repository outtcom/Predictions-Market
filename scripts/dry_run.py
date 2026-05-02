"""Dry-run signal audit — full cycle without placing orders.

Usage:
    python scripts/dry_run.py --max-pages 2
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is on path for imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from agents.data_ingester import DataIngester
from agents.market_analyst import MarketAnalyst
from agents.news_intel_agent import NewsIntelAgent
from agents.risk_manager import RiskManager
from agents.signal_agent import SignalAgent
from src.common.logger import log_event
from src.common.schemas import Market


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run signal audit.")
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--bankroll", type=float, default=float(os.getenv("BANKROLL_USD", "1000")))
    args = parser.parse_args()

    env_path = Path(".env")
    if env_path.exists():
        load_dotenv(env_path)

    log_event("DRY_RUN_START", {"max_pages": args.max_pages, "bankroll": args.bankroll})

    ingester = DataIngester()
    signal_agent = SignalAgent()
    analyst = MarketAnalyst()
    news_intel = NewsIntelAgent()
    risk_manager = RiskManager()

    # 1. Index markets
    poly_markets = ingester.index_polymarket(max_pages=args.max_pages)
    all_markets = poly_markets
    if not all_markets:
        log_event("DRY_RUN_END", {"status": "no_markets"})
        return

    # 2. Generate signals
    signals = signal_agent.generate_signals(all_markets)
    signal_map = {s.market_id: s for s in signals}

    # 3. Analyze markets
    analyses = analyst.analyze_batch(all_markets, signals)

    # 4. Run risk assessment on every watchlist candidate (no execution)
    audited = 0
    for analysis in analyses:
        if not analysis.on_watchlist:
            continue

        market = analysis.market
        signal = signal_map.get(market.market_id)
        if signal is None:
            continue

        assessment = risk_manager.assess(
            analysis,
            signal,
            bankroll=args.bankroll,
            open_positions=[],  # dry-run assumes no existing exposure
        )

        log_event(
            "SIGNAL_AUDIT",
            {
                "market_id": market.market_id,
                "question": market.question,
                "signal_prob": signal.signal_prob,
                "market_price": market.current_yes_price,
                "expected_value": analysis.expected_value,
                "risk_verdict": assessment.verdict,
                "kelly_size_usd": assessment.size_usd if hasattr(assessment, "size_usd") else 0.0,
                "reason": getattr(assessment, "reason", ""),
                "signal_sources": signal.signal_sources,
                "signal_strength": signal.signal_strength,
            },
        )
        audited += 1

    log_event("DRY_RUN_END", {"status": "complete", "audited": audited})
    print(f"Dry-run complete: {audited} watchlist candidates audited.")


if __name__ == "__main__":
    main()
