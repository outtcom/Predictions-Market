"""
Orchestrator Agent — Central coordinator for the prediction market trading system.

Responsibilities:
- Receive signals from all sub-agents and synthesize into a position decision
- Enforce portfolio-level exposure limits before any order is placed
- Log every decision with rationale, agent votes, and confidence scores
- Gate trades through the Risk Manager before sending to Execution Agent
- Run a daily P&L and attribution report across all open and closed positions

Decision Protocol:
1. Collect signals from Market Analyst + Signal Agent
2. Pass proposed trade to Risk Manager → receive approve/reject/resize
3. If approved, send to Execution Agent with limit price + size
4. Record outcome; feed back into Signal Agent's calibration loop
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from src.common.logger import log_event
from src.common.notifier import TelegramNotifier
from src.common.schemas import Market, Signal

from agents.data_ingester import DataIngester
from agents.execution_agent import ExecutionAgent, OrderSide
from agents.market_analyst import MarketAnalyst, MarketAnalysis
from agents.news_intel_agent import NewsIntelAgent
from agents.risk_manager import RiskAssessment, RiskManager
from agents.signal_agent import SignalAgent


@dataclass
class TradeDecision:
    """Structured decision log for every trade."""

    timestamp: str
    market_id: str
    decision: str  # BUY_YES | BUY_NO | PASS
    signal_prob: float
    market_price: float
    expected_value: float
    kelly_size_usd: float
    risk_verdict: str
    agent_votes: dict[str, str]
    rationale: str


class Orchestrator:
    """Central coordinator. The only agent that issues trade instructions."""

    def __init__(
        self,
        bankroll: float | None = None,
        mode: str | None = None,
    ) -> None:
        self.bankroll = bankroll or float(os.getenv("BANKROLL_USD", "1000"))
        self.mode = (mode or os.getenv("MODE", "paper")).lower()

        self.ingester = DataIngester()
        self.signal_agent = SignalAgent()
        self.analyst = MarketAnalyst()
        self.news_intel = NewsIntelAgent()
        self.risk_manager = RiskManager()
        self.execution = ExecutionAgent(mode=self.mode)
        self.notifier = TelegramNotifier()

    def run_cycle(self, max_pages: int = 2) -> list[TradeDecision]:
        """Run one full decision cycle: index → signal → analyze → risk → execute."""
        decisions: list[TradeDecision] = []

        # 1. Index markets
        poly_markets = self.ingester.index_polymarket(max_pages=max_pages)
        # Kalshi markets skipped if credentials missing
        all_markets = poly_markets
        if not all_markets:
            log_event("ORCHESTRATOR_CYCLE", {"status": "no_markets"})
            return decisions

        # 2. Generate signals
        signals = self.signal_agent.generate_signals(all_markets)

        # 3. Analyze markets
        analyses = self.analyst.analyze_batch(all_markets, signals)

        # 3b. News & Intel scan for watchlist markets
        news_context: dict[str, list[str]] = {}
        for analysis in analyses:
            if analysis.on_watchlist:
                query = analysis.market.question[:100]
                keywords = [w.lower() for w in query.split() if len(w) > 3]
                alerts = self.news_intel.scan_for_market(query, keywords)
                high_med = [a for a in alerts if a.severity in ("HIGH", "MED")]
                if high_med:
                    news_context[analysis.market.market_id] = [
                        f"[{a.severity}] {a.headline[:60]} (Δ{a.estimated_delta:.0%})"
                        for a in high_med[:3]
                    ]
                    log_event(
                        "ORCHESTRATOR_NEWS_CONTEXT",
                        {
                            "market_id": analysis.market.market_id,
                            "alerts": len(high_med),
                            "top_alert": high_med[0].headline[:80],
                        },
                    )

        # 4. For each watchlist candidate, run risk → execute
        open_positions = [
            {
                "market_id": p.market_id,
                "size_usd": p.size_usd,
                "category": self.analyst.classify(
                    next((m.question for m in all_markets if m.market_id == p.market_id), "")
                ),
            }
            for p in self.execution.get_open_positions()
        ]

        for analysis in analyses:
            if not analysis.on_watchlist:
                continue

            market = analysis.market
            signal = next((s for s in signals if s.market_id == market.market_id), None)
            if signal is None:
                continue

            # Risk gate
            assessment = self.risk_manager.assess(
                analysis,
                signal,
                bankroll=self.bankroll,
                open_positions=open_positions,
            )

            if assessment.verdict == "REJECTED":
                decision = TradeDecision(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    market_id=market.market_id,
                    decision="PASS",
                    signal_prob=signal.signal_prob,
                    market_price=market.current_yes_price,
                    expected_value=analysis.expected_value,
                    kelly_size_usd=0.0,
                    risk_verdict="REJECTED",
                    agent_votes={
                        "market_analyst": "BUY" if analysis.on_watchlist else "PASS",
                        "signal_agent": "BUY",
                        "risk_manager": "REJECTED",
                    },
                    rationale=assessment.reason,
                )
                decisions.append(decision)
                self.notifier.send_trade_alert(
                    decision, market.question, signal.signal_sources
                )
                continue

            # Determine side
            side: OrderSide = "BUY_YES" if signal.signal_prob > market.current_yes_price else "BUY_NO"

            # Execution
            order = self.execution.place_limit_order(
                market=market,
                side=side,
                size_usd=assessment.size_usd,
                limit_price=market.current_yes_price,
            )

            # Attach exit levels to all filled positions
            if order.status == "FILLED":
                for pos in self.execution.get_open_positions():
                    if pos.market_id == market.market_id and pos.opened_at >= order.created_at:
                        self.execution.set_exit_levels(
                            pos.position_id,
                            take_profit=assessment.take_profit,
                            stop_loss=assessment.stop_loss,
                        )
                        break

            # Update open_positions tracker for subsequent trades in this cycle
            if order.status == "FILLED":
                open_positions.append(
                    {
                        "market_id": market.market_id,
                        "size_usd": assessment.size_usd,
                        "category": analysis.category,
                    }
                )

            news_str = ""
            if market.market_id in news_context:
                news_str = " NEWS: " + " | ".join(news_context[market.market_id])
            decision = TradeDecision(
                timestamp=datetime.now(timezone.utc).isoformat(),
                market_id=market.market_id,
                decision=side,
                signal_prob=signal.signal_prob,
                market_price=market.current_yes_price,
                expected_value=analysis.expected_value,
                kelly_size_usd=assessment.size_usd,
                risk_verdict=assessment.verdict,
                agent_votes={
                    "market_analyst": "BUY",
                    "signal_agent": "BUY",
                    "risk_manager": assessment.verdict,
                },
                rationale=(
                    f"Superforecaster consensus at {signal.signal_prob:.0%} vs "
                    f"market {market.current_yes_price:.0%}; EV={analysis.expected_value:.2%}; "
                    f"{assessment.reason}{news_str}"
                ),
            )
            decisions.append(decision)
            self.notifier.send_trade_alert(
                decision, market.question, signal.signal_sources
            )

            log_event(
                "TRADE_DECISION",
                {
                    "market_id": market.market_id,
                    "decision": side,
                    "signal_prob": signal.signal_prob,
                    "market_price": market.current_yes_price,
                    "expected_value": analysis.expected_value,
                    "kelly_size_usd": assessment.size_usd,
                    "risk_verdict": assessment.verdict,
                    "rationale": decisions[-1].rationale,
                },
            )

        # Check exits on all open positions against latest prices
        closed_today: list[Any] = []
        for market in all_markets:
            closed = self.execution.check_exits(market)
            closed_today.extend(closed)

        self.notifier.send_daily_summary(
            open_positions=self.execution.get_open_positions(),
            closed_today=closed_today,
            bankroll=self.bankroll,
        )

        log_event("ORCHESTRATOR_CYCLE", {"status": "complete", "decisions": len(decisions)})
        return decisions

    def run(self, cycles: int = 1, max_pages: int = 2) -> list[TradeDecision]:
        """Main decision loop. Run N cycles (useful for backtesting or scheduled runs)."""
        all_decisions: list[TradeDecision] = []
        for _ in range(cycles):
            batch = self.run_cycle(max_pages=max_pages)
            all_decisions.extend(batch)
        return all_decisions
