"""Tests for Signal Agent calibration, EV cap, and corroboration gate."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.strategy_agent import StrategyAgent
from agents.signal_agent import SignalAgent
from src.common.schemas import Market, Signal


def _make_market(
    *,
    market_id: str = "mkt_1",
    question: str = "Will X happen?",
    platform: str = "polymarket",
    current_yes_price: float = 0.15,
    volume_24h: float = 10_000,
    days_to_resolution: int = 7,
    active: bool = True,
    closed: bool = False,
) -> Market:
    return Market(
        market_id=market_id,
        question=question,
        platform=platform,
        current_yes_price=current_yes_price,
        volume_24h=volume_24h,
        days_to_resolution=days_to_resolution,
        active=active,
        closed=closed,
    )


class TestShortDurationFilter:
    def test_market_at_14_days_is_eligible(self) -> None:
        agent = StrategyAgent(preset="high_ev_divergence")
        market = _make_market(days_to_resolution=14)
        ok, reason = agent.is_eligible(market)
        assert ok, f"Expected eligible, got: {reason}"

    def test_market_at_15_days_is_dropped(self) -> None:
        agent = StrategyAgent(preset="high_ev_divergence")
        market = _make_market(days_to_resolution=15)
        ok, reason = agent.is_eligible(market)
        assert not ok
        assert "above max" in reason


class TestSignalCalibration:
    def test_brier_score_on_held_out_data(self) -> None:
        raise NotImplementedError
