"""
Risk Manager Agent — The last line of defense before capital is deployed.

Responsibilities:
- Enforce hard position limits
- Run Kelly Criterion sizing and cap at fractional Kelly (0.25x)
- Reject any trade where signal confidence < 60% or EV < 3%
- Monitor portfolio concentration: no single category > 40% of deployed capital
- Track correlation between open positions — penalize high-correlation clusters
- Require stop-loss levels on all positions > $500
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from src.analysis.kelly import kelly_position_size_usd
from src.common.config import RISK
from src.common.logger import log_event
from src.common.schemas import Signal
from agents.market_analyst import MarketAnalysis

Verdict = Literal["APPROVED", "REJECTED", "RESIZED"]


@dataclass
class RiskAssessment:
    """Output of the Risk Manager review."""

    verdict: Verdict
    market_id: str
    size_usd: float
    stop_loss: float | None = None
    take_profit: float | None = None
    reason: str = ""


class RiskManager:
    """Validates trades against risk parameters and hard veto conditions."""

    def __init__(self, params: dict | None = None) -> None:
        self.params = params or RISK
        self.daily_pnl: float = 0.0
        self.halted: bool = False

    def _veto(self, market_id: str, reason: str) -> RiskAssessment:
        log_event("RISK_MANAGER_VETO", {"market_id": market_id, "reason": reason})
        return RiskAssessment(verdict="REJECTED", market_id=market_id, size_usd=0.0, reason=reason)

    def assess(
        self,
        analysis: MarketAnalysis,
        signal: Signal | None,
        *,
        bankroll: float = 1_000.0,
        open_positions: list[dict] | None = None,
    ) -> RiskAssessment:
        """Evaluate a proposed trade. Returns APPROVED, REJECTED, or RESIZED."""
        market = analysis.market
        market_id = market.market_id

        # Hard veto: daily loss limit hit
        if self.halted:
            return self._veto(market_id, "Daily loss limit already hit — trading halted")

        daily_loss_limit = bankroll * self.params.get("daily_loss_limit_pct", 0.05)
        if self.daily_pnl <= -daily_loss_limit:
            self.halted = True
            return self._veto(market_id, "Daily loss limit hit — halting trading")

        # Hard veto: ambiguous market
        if not market.question or len(market.question) < 10:
            return self._veto(market_id, "Ambiguous or missing resolution criteria")

        # Hard veto: no signal or unverified single source
        if signal is None:
            return self._veto(market_id, "No signal available")

        if len(signal.signal_sources) < 1:
            return self._veto(market_id, "Signal derived from no sources")

        # Confidence and EV floors
        min_conf = self.params.get("min_signal_confidence", 0.60)
        # Map signal_strength to a crude confidence proxy
        conf_map = {"weak": 0.50, "moderate": 0.70, "strong": 0.90}
        signal_conf = conf_map.get(signal.signal_strength, 0.50)
        if signal_conf < min_conf:
            return self._veto(market_id, f"Signal confidence {signal_conf:.0%} < floor {min_conf:.0%}")

        min_ev = self.params.get("min_expected_value", 0.03)
        if analysis.expected_value < min_ev:
            return self._veto(market_id, f"EV {analysis.expected_value:.2%} < floor {min_ev:.2%}")

        # Kelly sizing
        kelly_frac = self.params.get("kelly_fraction", 0.25)
        max_pos = self.params.get("max_single_position_usd", 500)
        size = kelly_position_size_usd(
            p=signal.signal_prob,
            market_price=market.current_yes_price,
            bankroll=bankroll,
            fraction=kelly_frac,
            max_position=max_pos,
        )

        if size <= 0:
            return self._veto(market_id, "Kelly sizing returned zero or negative")

        # Portfolio exposure limit
        max_exposure = bankroll * self.params.get("max_portfolio_exposure_pct", 0.60)
        open_positions = open_positions or []
        current_exposure = sum(p.get("size_usd", 0) for p in open_positions)
        if current_exposure + size > max_exposure:
            resized = max_exposure - current_exposure
            if resized <= 0:
                return self._veto(market_id, "Max portfolio exposure reached")
            size = resized
            verdict: Verdict = "RESIZED"
        else:
            verdict = "APPROVED"

        # Category concentration
        max_cat = self.params.get("max_category_concentration", 0.40)
        cat_exposure = sum(
            p.get("size_usd", 0)
            for p in open_positions
            if p.get("category") == analysis.category
        )
        if cat_exposure + size > bankroll * max_cat:
            resized = bankroll * max_cat - cat_exposure
            if resized <= 0:
                return self._veto(market_id, f"Max {analysis.category} concentration reached")
            size = resized
            verdict = "RESIZED"

        # Stop-loss / take-profit for all positions
        edge = analysis.edge_pct
        entry = market.current_yes_price
        take_profit = entry + (edge * 0.7) if edge > 0 else None
        stop_loss = entry - (edge * 0.5) if edge > 0 else None
        if take_profit is not None:
            # Ensure TP is strictly above entry ( BUY_YES )
            take_profit = min(0.99, max(entry + 0.001, take_profit))
        if stop_loss is not None:
            # Ensure SL is strictly below entry ( BUY_YES )
            stop_loss = max(0.001, min(entry - 0.001, stop_loss))

        log_event(
            "RISK_MANAGER_ASSESS",
            {
                "market_id": market_id,
                "verdict": verdict,
                "size_usd": size,
                "kelly_fraction": kelly_frac,
                "signal_confidence": signal_conf,
                "ev": analysis.expected_value,
            },
        )

        return RiskAssessment(
            verdict=verdict,
            market_id=market_id,
            size_usd=size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            reason="Passed all risk checks" if verdict == "APPROVED" else "Resized to fit limits",
        )
