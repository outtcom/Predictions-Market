"""Tests for StrategyAgent market eligibility filter."""
from __future__ import annotations

import pytest
from agents.strategy_agent import StrategyAgent
from src.common.schemas import Market


def _market(
    market_id: str = "mkt_1",
    current_yes_price: float = 0.50,
    volume_24h: float = 10_000,
    days_to_resolution: int = 14,
    active: bool = True,
    closed: bool = False,
) -> Market:
    return Market(
        market_id=market_id,
        question="Will X happen?",
        platform="polymarket",
        current_yes_price=current_yes_price,
        volume_24h=volume_24h,
        days_to_resolution=days_to_resolution,
        active=active,
        closed=closed,
    )


def test_unknown_preset_raises() -> None:
    with pytest.raises(ValueError, match="Unknown preset"):
        StrategyAgent(preset="nonexistent")


class TestStrategyAgentIsEligible:
    def setup_method(self) -> None:
        self.agent = StrategyAgent(preset="high_ev_divergence")

    def test_eligible_market_passes(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(current_yes_price=0.50))
        assert eligible is True
        assert reason == ""

    def test_price_too_low_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(current_yes_price=0.05))
        assert eligible is False
        assert "price" in reason.lower()

    def test_price_too_high_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(current_yes_price=0.95))
        assert eligible is False
        assert "price" in reason.lower()

    def test_exactly_at_min_price_passes(self) -> None:
        eligible, _ = self.agent.is_eligible(_market(current_yes_price=0.10))
        assert eligible is True

    def test_exactly_at_max_price_passes(self) -> None:
        eligible, _ = self.agent.is_eligible(_market(current_yes_price=0.90))
        assert eligible is True

    def test_low_volume_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(volume_24h=1_000))
        assert eligible is False
        assert "volume" in reason.lower()

    def test_already_closed_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(closed=True))
        assert eligible is False
        assert "closed" in reason.lower()

    def test_inactive_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(active=False))
        assert eligible is False
        assert "inactive" in reason.lower()

    def test_zero_days_to_resolution_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(days_to_resolution=0))
        assert eligible is False
        assert "days" in reason.lower()

    def test_too_many_days_to_resolution_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(days_to_resolution=61))
        assert eligible is False
        assert "days" in reason.lower()

    def test_exactly_at_max_days_passes(self) -> None:
        eligible, _ = self.agent.is_eligible(_market(days_to_resolution=60))
        assert eligible is True


class TestStrategyAgentFilter:
    def setup_method(self) -> None:
        self.agent = StrategyAgent(preset="high_ev_divergence")

    def test_filter_returns_only_eligible(self) -> None:
        markets = [
            _market("good", current_yes_price=0.50),
            _market("too_low", current_yes_price=0.02),
            _market("too_high", current_yes_price=0.98),
            _market("low_vol", volume_24h=100),
        ]
        result = self.agent.filter(markets)
        assert len(result) == 1
        assert result[0].market_id == "good"

    def test_filter_empty_list(self) -> None:
        assert self.agent.filter([]) == []

    def test_filter_all_eligible(self) -> None:
        markets = [_market(f"m{i}") for i in range(5)]
        result = self.agent.filter(markets)
        assert len(result) == 5
