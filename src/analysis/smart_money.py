"""Smart-money proxy analysis using volume, liquidity, and momentum heuristics.

Since Polymarket does not expose individual trader P&L via public APIs,
we treat markets with unusually high relative volume + liquidity inflows
as "smart-money interest" and boost conviction when our signal aligns.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from src.common.logger import log_event

DATA_DIR = Path("data/polymarket/markets")


@dataclass
class SmartMoneyScore:
    market_id: str
    volume_percentile: float  # 0.0–1.0
    liquidity_percentile: float  # 0.0–1.0
    volume_zscore: float  # vs historical mean; inf if no history
    conviction: str  # strong | moderate | weak
    notes: str


class SmartMoneyAnalyzer:
    """Scores markets for smart-money footprint using public snapshot data."""

    def __init__(self, data_dir: Path = DATA_DIR) -> None:
        self.data_dir = data_dir
        self._history: pd.DataFrame | None = None

    def _load_history(self) -> pd.DataFrame:
        """Load all historical daily snapshots."""
        if self._history is not None:
            return self._history
        files = sorted(self.data_dir.glob("*.parquet"))
        dfs = [pd.read_parquet(f) for f in files]
        self._history = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
        return self._history

    def analyze_markets(self, markets: list[Any]) -> dict[str, SmartMoneyScore]:
        """Score each market for smart-money interest."""
        history = self._load_history()
        if history.empty:
            log_event("SMART_MONEY", {"status": "no_history", "markets": len(markets)})
            return {}

        # Build current snapshot
        current = pd.DataFrame([{
            "market_id": m.market_id,
            "volume_24h": getattr(m, "volume_24h", 0),
            "liquidity_usd": getattr(m, "liquidity_usd", 0),
            "current_yes_price": getattr(m, "current_yes_price", 0),
        } for m in markets])

        # Compute percentiles across ALL historical observations
        vol_pcts = current["volume_24h"].rank(pct=True)
        liq_pcts = current["liquidity_usd"].rank(pct=True)

        # Volume Z-score (current vs historical mean for that market)
        scores: dict[str, SmartMoneyScore] = {}
        for idx, row in current.iterrows():
            mid = row["market_id"]
            hist = history[history["market_id"] == mid]

            if len(hist) >= 2:
                mean_vol = hist["volume_24h"].mean()
                std_vol = hist["volume_24h"].std() or 1.0
                zscore = (row["volume_24h"] - mean_vol) / std_vol
            else:
                zscore = 0.0

            vol_pct = vol_pcts.iloc[idx] if idx < len(vol_pcts) else 0.0
            liq_pct = liq_pcts.iloc[idx] if idx < len(liq_pcts) else 0.0

            # Conviction heuristic
            if vol_pct > 0.85 and zscore > 1.5:
                conviction = "strong"
            elif vol_pct > 0.70 or zscore > 1.0:
                conviction = "moderate"
            else:
                conviction = "weak"

            scores[mid] = SmartMoneyScore(
                market_id=mid,
                volume_percentile=vol_pct,
                liquidity_percentile=liq_pct,
                volume_zscore=zscore,
                conviction=conviction,
                notes=(
                    f"vol_pct={vol_pct:.0%} liq_pct={liq_pct:.0%} "
                    f"vol_z={zscore:.2f}"
                ),
            )

        log_event("SMART_MONEY", {"scored": len(scores)})
        return scores
