"""Strategy Agent — pre-filters markets to eligible candidates before signal generation."""

from __future__ import annotations

from dataclasses import dataclass

from src.common.logger import log_event
from src.common.schemas import Market


@dataclass(frozen=True)
class StrategyPreset:
    name: str
    min_market_price: float
    max_market_price: float
    min_volume_24h: float
    min_days_to_resolution: int
    max_days_to_resolution: int


HIGH_EV_DIVERGENCE = StrategyPreset(
    name="high_ev_divergence",
    min_market_price=0.10,
    max_market_price=0.90,
    min_volume_24h=5_000,
    min_days_to_resolution=1,
    max_days_to_resolution=60,
)

_PRESETS: dict[str, StrategyPreset] = {
    "high_ev_divergence": HIGH_EV_DIVERGENCE,
}


class StrategyAgent:
    """Pre-filters markets to those eligible under the active strategy preset."""

    def __init__(self, preset: str = "high_ev_divergence") -> None:
        if preset not in _PRESETS:
            raise ValueError(f"Unknown preset '{preset}'. Available: {list(_PRESETS)}")
        self.preset = _PRESETS[preset]

    def is_eligible(self, market: Market) -> tuple[bool, str]:
        """Return (True, '') if eligible, or (False, reason) if not."""
        preset = self.preset

        if market.closed:
            return False, "market is closed"
        if not market.active:
            return False, "market is inactive"
        if market.current_yes_price < preset.min_market_price:
            return False, f"price {market.current_yes_price:.2%} below min {preset.min_market_price:.2%}"
        if market.current_yes_price > preset.max_market_price:
            return False, f"price {market.current_yes_price:.2%} above max {preset.max_market_price:.2%}"
        if market.volume_24h < preset.min_volume_24h:
            return False, f"volume ${market.volume_24h:,.0f} below min ${preset.min_volume_24h:,.0f}"
        if market.days_to_resolution < preset.min_days_to_resolution:
            return False, f"days_to_resolution {market.days_to_resolution} below min {preset.min_days_to_resolution}"
        if market.days_to_resolution > preset.max_days_to_resolution:
            return False, f"days_to_resolution {market.days_to_resolution} above max {preset.max_days_to_resolution}"

        return True, ""

    def filter(self, markets: list[Market]) -> list[Market]:
        """Return only eligible markets; log dropped ones."""
        eligible: list[Market] = []
        dropped = 0
        for market in markets:
            ok, reason = self.is_eligible(market)
            if ok:
                eligible.append(market)
            else:
                dropped += 1
                log_event(
                    "STRATEGY_AGENT_DROP",
                    {"market_id": market.market_id, "reason": reason},
                )
        log_event(
            "STRATEGY_AGENT_FILTER",
            {
                "preset": self.preset.name,
                "total": len(markets),
                "eligible": len(eligible),
                "dropped": dropped,
            },
        )
        return eligible
