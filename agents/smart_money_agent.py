"""Smart Money Agent — proxy copy-trading via volume + liquidity heuristics.

Emits a SMART_MONEY signal when a market shows unusually high relative
volume and liquidity, indicating concentrated capital flow.
"""

from __future__ import annotations

from src.analysis.smart_money import SmartMoneyAnalyzer
from src.common.logger import log_event
from src.common.schemas import Market, Signal


class SmartMoneyAgent:
    """Generates smart-money divergence signals."""

    def __init__(self, min_volume_percentile: float = 0.75) -> None:
        self.analyzer = SmartMoneyAnalyzer()
        self.min_volume_percentile = min_volume_percentile

    def generate_signals(self, markets: list[Market]) -> list[Signal]:
        """Emit signals for markets with strong smart-money footprints."""
        scores = self.analyzer.analyze_markets(markets)
        signals: list[Signal] = []

        for mid, score in scores.items():
            if score.conviction == "weak":
                continue

            # Find current market price
            market = next((m for m in markets if m.market_id == mid), None)
            if not market:
                continue

            # Smart money signal = market price itself (we copy the flow)
            signal_prob = market.current_yes_price
            ci_low = max(0.0, signal_prob - 0.10)
            ci_high = min(1.0, signal_prob + 0.10)

            signal = Signal(
                market_id=mid,
                signal_prob=signal_prob,
                confidence_interval=(ci_low, ci_high),
                signal_sources=["smart_money_volume"],
                staleness_hours=0.0,
                signal_strength=score.conviction,
                notes=(
                    f"SmartMoney {score.conviction}: {score.notes}"
                ),
            )
            signals.append(signal)
            log_event(
                "SMART_MONEY_EMIT",
                {
                    "market_id": mid,
                    "conviction": score.conviction,
                    "vol_pct": score.volume_percentile,
                    "liq_pct": score.liquidity_percentile,
                    "vol_z": score.volume_zscore,
                },
            )

        return signals
