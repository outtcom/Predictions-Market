"""
Data Ingester Agent — Reliable, versioned data pipeline for all market and trade data.

Responsibilities:
- Index Polymarket markets via Gamma API; index Kalshi markets via REST API
- Store all market metadata and trade history in Parquet format (matches Becker schema)
- Resume interrupted collection without data loss (checkpoint-based)
- Provide clean DataFrames to all other agents on demand
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd

from src.common.logger import log_event
from src.common.schemas import Market
from src.indexers.kalshi.client import KalshiClient, KALSHI_DEMO, KALSHI_PROD
from src.indexers.manifold.client import ManifoldClient
from src.indexers.polymarket.gamma import GammaClient

DATA_DIR = Path("data")


class DataIngester:
    """Indexes and stores market and trade data from Polymarket and Kalshi."""

    def __init__(self, data_dir: Path | str = DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.polymarket = GammaClient()
        self.kalshi = self._init_kalshi()
        self.manifold = ManifoldClient()

    def _init_kalshi(self) -> KalshiClient | None:
        """Initialize Kalshi client if credentials are available."""
        email = os.getenv("KALSHI_EMAIL")
        password = os.getenv("KALSHI_PASSWORD")
        if not email or not password:
            log_event(
                "DATA_INGESTER_INIT",
                {"warning": "Kalshi credentials missing; skipping Kalshi indexing"},
            )
            return None
        base = KALSHI_DEMO if os.getenv("MODE", "paper") == "paper" else KALSHI_PROD
        return KalshiClient(base_url=base, email=email, password=password)

    def index_polymarket(self, max_pages: int = 10) -> list[Market]:
        """Fetch and normalize all active Polymarket markets."""
        log_event("DATA_INGESTER_START", {"platform": "polymarket", "max_pages": max_pages})
        markets = self.polymarket.fetch_all_markets(max_pages=max_pages)
        log_event("DATA_INGESTER_DONE", {"platform": "polymarket", "count": len(markets)})
        return markets

    def index_kalshi(self, max_pages: int = 20) -> list[Market]:
        """Fetch and normalize all active Kalshi markets."""
        if self.kalshi is None:
            return []
        log_event("DATA_INGESTER_START", {"platform": "kalshi", "max_pages": max_pages})
        markets = self.kalshi.fetch_all_markets(max_pages=max_pages)
        log_event("DATA_INGESTER_DONE", {"platform": "kalshi", "count": len(markets)})
        return markets

    def index_manifold(self, max_pages: int = 3) -> list[Market]:
        """Fetch and normalize top Manifold markets by volume."""
        log_event("DATA_INGESTER_START", {"platform": "manifold", "max_pages": max_pages})
        markets = self.manifold.fetch_all_markets(max_pages=max_pages)
        log_event("DATA_INGESTER_DONE", {"platform": "manifold", "count": len(markets)})
        return markets

    def save_markets(self, markets: list[Market], platform: str, date: str | None = None) -> Path:
        """Persist normalized markets to Parquet."""
        if not markets:
            return self.data_dir / f"{platform}_markets.parquet"

        date = date or pd.Timestamp.utcnow().strftime("%Y%m%d")
        out_dir = self.data_dir / platform / "markets"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"markets_{date}.parquet"

        # Flatten dataclasses to dicts, dropping raw nested data
        records: list[dict[str, Any]] = []
        for m in markets:
            rec = {
                "market_id": m.market_id,
                "question": m.question,
                "platform": m.platform,
                "category": m.category,
                "current_yes_price": m.current_yes_price,
                "volume_24h": m.volume_24h,
                "liquidity_usd": m.liquidity_usd,
                "days_to_resolution": m.days_to_resolution,
                "active": m.active,
                "closed": m.closed,
            }
            records.append(rec)

        df = pd.DataFrame.from_records(records)
        df.to_parquet(path, index=False)
        log_event("DATA_INGESTER_SAVE", {"platform": platform, "path": str(path), "rows": len(df)})
        return path

    def load_latest_markets(self, platform: str) -> pd.DataFrame:
        """Load the most recent markets Parquet for a platform."""
        pattern = self.data_dir / platform / "markets" / "markets_*.parquet"
        files = sorted(pattern.parent.glob(pattern.name))
        if not files:
            return pd.DataFrame()
        return pd.read_parquet(files[-1])

    def run(self) -> dict[str, Path]:
        """Full indexing run for all configured platforms."""
        results: dict[str, Path] = {}

        poly = self.index_polymarket()
        if poly:
            results["polymarket"] = self.save_markets(poly, "polymarket")

        kalshi = self.index_kalshi()
        if kalshi:
            results["kalshi"] = self.save_markets(kalshi, "kalshi")

        manifold = self.index_manifold()
        if manifold:
            results["manifold"] = self.save_markets(manifold, "manifold")

        return results
