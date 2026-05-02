"""
Signal Agent — Generate probability estimates independent of market price.

Core strategy: Superforecaster Divergence + Cross-Platform Arbitrage
- Monitor Metaculus community medians + LLM ensemble forecasts + Manifold prices
- When market price diverges from consensus by >5%, flag signal
- Cross-platform: when |P_manifold - P_polymarket| > 3%, flag arbitrage signal
- Output confidence interval and source attribution

Responsibilities:
- Synthesize signals from multiple independent sources into a single probability estimate
- Maintain calibration log: compare predicted probabilities to outcomes
- Flag overconfidence when model uncertainty is high
- Output signal with confidence interval, not just point estimate
"""

from __future__ import annotations

import re
from typing import Any

from src.analysis.llm_forecaster import EnsembleForecast, forecast_ensemble
from src.common.logger import log_event
from src.common.schemas import Market, Signal
from src.indexers.manifold.client import ManifoldClient
from src.indexers.metaculus.client import MetaculusClient


class SignalAgent:
    """Generates independent probability estimates for markets."""

    def __init__(
        self,
        divergence_threshold: float = 0.05,
        arbitrage_threshold: float = 0.03,
        metaculus: MetaculusClient | None = None,
        manifold: ManifoldClient | None = None,
        use_llm: bool = True,
    ) -> None:
        self.divergence_threshold = divergence_threshold
        self.arbitrage_threshold = arbitrage_threshold
        self.metaculus = metaculus or MetaculusClient()
        self.manifold = manifold or ManifoldClient()
        self.use_llm = use_llm

    @staticmethod
    def _normalize(text: str) -> str:
        """Lowercase, remove punctuation, collapse whitespace."""
        text = text.lower()
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _keyword_overlap(a: str, b: str) -> float:
        """Simple Jaccard-ish overlap of keyword sets."""
        words_a = set(SignalAgent._normalize(a).split())
        words_b = set(SignalAgent._normalize(b).split())
        if not words_a or not words_b:
            return 0.0
        return len(words_a & words_b) / len(words_a | words_b)

    def match_metaculus_to_market(
        self, post: dict[str, Any], markets: list[Market], min_overlap: float = 0.6
    ) -> Market | None:
        """Heuristic: match a Metaculus question to the best-fitting market by text overlap."""
        meta_title = post.get("title", "")
        best: Market | None = None
        best_score = min_overlap
        for m in markets:
            score = self._keyword_overlap(meta_title, m.question)
            if score > best_score:
                best_score = score
                best = m
        return best

    def match_manifold_to_market(
        self, m_manifold: Market, markets: list[Market], min_overlap: float = 0.65
    ) -> Market | None:
        """Heuristic: match a Manifold market to the best-fitting real-money market."""
        best: Market | None = None
        best_score = min_overlap
        for m in markets:
            if m.platform == "manifold":
                continue
            score = self._keyword_overlap(m_manifold.question, m.question)
            if score > best_score:
                best_score = score
                best = m
        return best

    def _llm_signal(self, market: Market) -> Signal | None:
        """Generate a signal via LLM ensemble for a single market."""
        ensemble = forecast_ensemble(
            question=market.question,
            resolution_criteria=market.raw.get("description", "") if market.raw else "",
        )
        if ensemble is None:
            return None

        divergence = abs(ensemble.median_prob - market.current_yes_price)
        if divergence < self.divergence_threshold:
            return None

        signal_strength = "strong" if divergence > 0.10 else "moderate"

        return Signal(
            market_id=market.market_id,
            signal_prob=ensemble.median_prob,
            confidence_interval=(ensemble.confidence_low, ensemble.confidence_high),
            signal_sources=ensemble.sources + ["llm_ensemble"],
            staleness_hours=0.0,
            signal_strength=signal_strength,
            notes=(
                f"LLM ensemble median={ensemble.median_prob:.2%} "
                f"vs market={market.current_yes_price:.2%} "
                f"(divergence={divergence:.2%}). Models: {ensemble.model_probs}"
            ),
        )

    def _metaculus_signals(self, markets: list[Market]) -> list[Signal]:
        """Fetch Metaculus forecasts, match to markets, and emit divergence signals."""
        signals: list[Signal] = []
        try:
            posts = self.metaculus.fetch_open_binary_questions(max_pages=3)
        except Exception as exc:
            log_event("SIGNAL_AGENT_ERROR", {"error": str(exc), "stage": "fetch_metaculus"})
            return signals

        log_event("SIGNAL_AGENT_FETCH", {"metaculus_posts": len(posts), "markets": len(markets)})

        for post in posts:
            cp = MetaculusClient.extract_community_probability(post)
            if cp is None:
                continue

            matched = self.match_metaculus_to_market(post, markets)
            if matched is None:
                continue

            divergence = abs(cp - matched.current_yes_price)
            if divergence < self.divergence_threshold:
                continue

            ci_low = max(0.0, cp - 0.10)
            ci_high = min(1.0, cp + 0.10)
            signal_strength = "strong" if divergence > 0.10 else "moderate"

            signal = Signal(
                market_id=matched.market_id,
                signal_prob=cp,
                confidence_interval=(ci_low, ci_high),
                signal_sources=["metaculus_recency_weighted"],
                staleness_hours=0.0,
                signal_strength=signal_strength,
                notes=(
                    f"Metaculus CP={cp:.2%} vs Market={matched.current_yes_price:.2%} "
                    f"(divergence={divergence:.2%})"
                ),
            )
            signals.append(signal)
            log_event(
                "SIGNAL_AGENT_EMIT",
                {
                    "market_id": matched.market_id,
                    "signal_prob": cp,
                    "market_price": matched.current_yes_price,
                    "divergence": divergence,
                    "source": "metaculus",
                },
            )

        return signals

    def _manifold_arbitrage_signals(self, poly_markets: list[Market]) -> list[Signal]:
        """Fetch Manifold markets, match to Polymarket, and emit cross-platform divergence signals."""
        signals: list[Signal] = []
        try:
            manifold_markets = self.manifold.fetch_all_markets(max_pages=3)
        except Exception as exc:
            log_event("SIGNAL_AGENT_ERROR", {"error": str(exc), "stage": "fetch_manifold"})
            return signals

        log_event("SIGNAL_AGENT_FETCH", {"manifold_markets": len(manifold_markets)})

        for mm in manifold_markets:
            matched = self.match_manifold_to_market(mm, poly_markets)
            if matched is None:
                continue

            divergence = abs(mm.current_yes_price - matched.current_yes_price)
            if divergence < self.arbitrage_threshold:
                continue

            # Use Manifold price as the signal (the "free" consensus)
            signal_prob = mm.current_yes_price
            ci_low = max(0.0, signal_prob - 0.10)
            ci_high = min(1.0, signal_prob + 0.10)
            signal_strength = "strong" if divergence > 0.10 else "moderate"

            signal = Signal(
                market_id=matched.market_id,
                signal_prob=signal_prob,
                confidence_interval=(ci_low, ci_high),
                signal_sources=["manifold_cross_platform"],
                staleness_hours=0.0,
                signal_strength=signal_strength,
                notes=(
                    f"Manifold={mm.current_yes_price:.2%} vs Polymarket={matched.current_yes_price:.2%} "
                    f"(divergence={divergence:.2%}) | {mm.question[:60]}"
                ),
            )
            signals.append(signal)
            log_event(
                "SIGNAL_AGENT_EMIT",
                {
                    "market_id": matched.market_id,
                    "signal_prob": signal_prob,
                    "market_price": matched.current_yes_price,
                    "divergence": divergence,
                    "source": "manifold_arbitrage",
                },
            )

        return signals

    def generate_signals(self, markets: list[Market]) -> list[Signal]:
        """Generate signals from all available sources and deduplicate by market."""
        all_signals: dict[str, Signal] = {}

        # Source 1: Metaculus superforecaster consensus
        for sig in self._metaculus_signals(markets):
            all_signals[sig.market_id] = sig

        # Source 2: Manifold cross-platform arbitrage (only for Polymarket markets)
        poly_markets = [m for m in markets if m.platform == "polymarket"]
        for sig in self._manifold_arbitrage_signals(poly_markets):
            # If Metaculus already has a signal for this market, blend probabilities
            existing = all_signals.get(sig.market_id)
            if existing:
                blended = (existing.signal_prob + sig.signal_prob) / 2.0
                sig = Signal(
                    market_id=sig.market_id,
                    signal_prob=blended,
                    confidence_interval=existing.confidence_interval,
                    signal_sources=existing.signal_sources + sig.signal_sources,
                    staleness_hours=0.0,
                    signal_strength="strong" if abs(blended - markets[0].current_yes_price) > 0.10 else "moderate",
                    notes=f"BLENDED: {existing.notes} | {sig.notes}",
                )
            all_signals[sig.market_id] = sig

        # Source 3: LLM ensemble (only for high-value markets without existing signal)
        if self.use_llm:
            llm_candidates = [
                m for m in markets
                if m.market_id not in all_signals
                and m.volume_24h >= 10_000
                and not m.closed
                and m.platform != "manifold"
            ]
            llm_candidates = sorted(llm_candidates, key=lambda x: x.volume_24h, reverse=True)[:10]
            for market in llm_candidates:
                sig = self._llm_signal(market)
                if sig:
                    all_signals[sig.market_id] = sig

        return list(all_signals.values())
