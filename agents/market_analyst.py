"""
Market Analyst Agent — Deep understanding of each market's structure, history, and microstructure.

Responsibilities:
- Parse Polymarket and Kalshi market metadata (question, resolution criteria, end date, liquidity)
- Identify markets with mispriced probabilities using reference base rates
- Classify markets by category: political, economic, sports, crypto, science, geopolitical
- Compute implied probability vs. historical base rate divergence
- Flag markets with thin order books (spread > 3%) as high-slippage risk
- Maintain a watchlist of markets where edge > 3% EV
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from src.common.logger import log_event
from src.common.schemas import Market, Signal

Category = Literal["political", "economic", "sports", "crypto", "science", "geopolitical", "other"]

CATEGORY_KEYWORDS: dict[Category, list[str]] = {
    "political": ["election", "president", "trump", "biden", "congress", "senate", "vote", "ballot", "party", "gop", "democrat", "republican", "midterm", "primary"],
    "economic": ["fed", "inflation", "recession", "gdp", "unemployment", "interest rate", "treasury", "economy", "nasdaq", "sp500", "dow", "stock market", "cpi", "jobs report"],
    "sports": ["nba", "nfl", "mlb", "nhl", "fifa", "world cup", "olympics", "super bowl", "championship", "team", "player", "game", "season", "tournament"],
    "crypto": ["bitcoin", "ethereum", "btc", "eth", "crypto", "blockchain", "etf", "sec", "coinbase", "binance", "altcoin", "token"],
    "science": ["climate", "co2", "temperature", "vaccine", "pandemic", "space", "nasa", "spacex", "mars", "ai", "artificial intelligence", "fusion", "crispr"],
    "geopolitical": ["war", "ukraine", "russia", "china", "israel", "iran", "nato", "ceasefire", "invasion", "sanctions", "treaty", "conflict", "diplomatic"],
}


@dataclass
class MarketAnalysis:
    """Enriched market view with analyst metrics."""

    market: Market
    category: Category = "other"
    expected_value: float = 0.0
    edge_pct: float = 0.0
    liquidity_score: float = 0.0  # 0 = terrible, 1 = excellent
    slippage_risk: bool = False
    on_watchlist: bool = False
    notes: str = ""


class MarketAnalyst:
    """Analyzes market structure and identifies mispriced probabilities."""

    def __init__(self, ev_threshold: float = 0.03, spread_threshold: float = 0.03) -> None:
        self.ev_threshold = ev_threshold
        self.spread_threshold = spread_threshold

    @staticmethod
    def classify(question: str) -> Category:
        """Keyword-based category classification."""
        q = question.lower()
        scores: dict[Category, int] = {cat: 0 for cat in CATEGORY_KEYWORDS}
        for cat, keywords in CATEGORY_KEYWORDS.items():
            for kw in keywords:
                if kw in q:
                    scores[cat] += 1
        best = max(scores, key=lambda c: scores[c])  # type: ignore[arg-type]
        return best if scores[best] > 0 else "other"

    def analyze(self, market: Market, signal: Signal | None = None) -> MarketAnalysis:
        """Run full analysis on a single market."""
        category = self.classify(market.question)

        # EV = (true_prob - price) / price  (per CLAUDE.md schema)
        # Floor price at 1% to avoid extreme EV on micro-priced noise markets
        MIN_PRICE = 0.01
        if signal:
            true_prob = signal.signal_prob
            price = max(market.current_yes_price, MIN_PRICE)
            ev = (true_prob - price) / price if price > 0 else 0.0
            edge = true_prob - market.current_yes_price
        else:
            ev = 0.0
            edge = 0.0

        # Simple liquidity score: log-scaled volume vs arbitrary reference
        # $1M daily = score 1.0, $1K daily = score 0.3
        vol = market.volume_24h
        liquidity_score = min(1.0, max(0.0, 0.3 + 0.7 * (vol / (vol + 500_000))))

        # Thin book flag (we don't have spread directly from Gamma, proxy via volume)
        slippage_risk = vol < 1_000  # Less than $1K daily volume

        on_watchlist = ev > self.ev_threshold and not slippage_risk

        notes = f"cat={category}, ev={ev:.2%}, edge={edge:.2%}, liq_score={liquidity_score:.2f}"
        if on_watchlist:
            notes += " | WATCHLIST"
        if slippage_risk:
            notes += " | SLIPPAGE_RISK"

        return MarketAnalysis(
            market=market,
            category=category,
            expected_value=ev,
            edge_pct=edge,
            liquidity_score=liquidity_score,
            slippage_risk=slippage_risk,
            on_watchlist=on_watchlist,
            notes=notes,
        )

    def analyze_batch(
        self, markets: list[Market], signals: list[Signal]
    ) -> list[MarketAnalysis]:
        """Analyze all markets, attaching signals where matched."""
        signal_by_market = {s.market_id: s for s in signals}
        analyses: list[MarketAnalysis] = []
        for m in markets:
            sig = signal_by_market.get(m.market_id)
            analysis = self.analyze(m, sig)
            analyses.append(analysis)
            if analysis.on_watchlist:
                log_event(
                    "MARKET_ANALYST_WATCHLIST",
                    {
                        "market_id": m.market_id,
                        "question": m.question,
                        "ev": analysis.expected_value,
                        "edge": analysis.edge_pct,
                    },
                )
        return analyses
