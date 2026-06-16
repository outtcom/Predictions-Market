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


class TestAnchoredPrompt:
    def test_market_price_appears_in_prompt(self) -> None:
        from src.common.prompts import format_binary_prompt
        prompt = format_binary_prompt(
            question="Will X happen?",
            resolution_criteria="Standard resolution.",
            today="2026-05-24",
            market_price=0.15,
        )
        assert "15.0%" in prompt, "Market price must appear in prompt"

    def test_no_market_price_omits_anchor_section(self) -> None:
        from src.common.prompts import format_binary_prompt
        prompt = format_binary_prompt(
            question="Will X happen?",
            resolution_criteria="",
            today="2026-05-24",
            market_price=None,
        )
        assert "MARKET CONTEXT" not in prompt


class TestEvCap:
    def test_ev_above_300pct_returns_none(self) -> None:
        """LLM returning 12% on a 1% market (EV=11x) must be dropped."""
        from src.analysis.llm_forecaster import EnsembleForecast

        agent = SignalAgent(use_llm=True)
        market = _make_market(current_yes_price=0.01)

        fake_ensemble = EnsembleForecast(
            median_prob=0.12,
            mean_prob=0.12,
            confidence_low=0.06,
            confidence_high=0.20,
            model_probs={"gpt-4o": 0.12},
            reasoning_summary="Seems possible",
            sources=["gpt-4o"],
        )

        with patch("agents.signal_agent.forecast_ensemble", return_value=fake_ensemble):
            result = agent._llm_signal(market, manifold_prices={})

        assert result is None, f"Expected None (EV cap), got: {result}"

    def test_ev_below_300pct_with_corroboration_returns_signal(self) -> None:
        """LLM returning 25% on a 15% market (EV=0.67x) with Manifold agreeing returns a signal."""
        from src.analysis.llm_forecaster import EnsembleForecast

        agent = SignalAgent(use_llm=True)
        market = _make_market(current_yes_price=0.15)

        fake_ensemble = EnsembleForecast(
            median_prob=0.25,
            mean_prob=0.25,
            confidence_low=0.18,
            confidence_high=0.35,
            model_probs={"gpt-4o": 0.25},
            reasoning_summary="Underpriced",
            sources=["gpt-4o"],
        )

        with patch("agents.signal_agent.forecast_ensemble", return_value=fake_ensemble):
            result = agent._llm_signal(
                market,
                manifold_prices={market.market_id: 0.22},  # Manifold also above market
            )

        assert result is not None, "Signal below EV cap with Manifold corroboration must be returned"
        assert "manifold_corroborated" in result.signal_sources


class TestSignalCalibration:
    def test_brier_score_on_held_out_data(self) -> None:
        raise NotImplementedError
