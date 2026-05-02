"""Manifold Markets API client — public read access, no auth required."""

from __future__ import annotations

from typing import Any

import requests

from src.common.schemas import Market

MANIFOLD_BASE = "https://api.manifold.markets/v0"


def _to_float(val: str | float | None) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _parse_manifold_market(raw: dict[str, Any]) -> Market | None:
    """Normalize a Manifold market dict into a Market object."""
    try:
        # Manifold probability is already 0.0–1.0 for binary markets
        prob = _to_float(raw.get("probability", 0))
        # Manifold uses "question" for the market text
        question = raw.get("question", "")
        # Manifold volume is in mana (play money)
        volume = _to_float(raw.get("volume24Hours", raw.get("volume24hr", raw.get("volume", 0))))

        return Market(
            market_id=raw.get("id", ""),
            question=question,
            platform="manifold",
            category=raw.get("groupSlugs", [""])[0] if raw.get("groupSlugs") else "",
            current_yes_price=prob,
            volume_24h=volume,
            liquidity_usd=_to_float(raw.get("pool", {}).get("YES", 0)),  # approximate
            active=not raw.get("isResolved", False),
            closed=raw.get("closeTime", 0) < 0 or raw.get("isResolved", False),
            raw=raw,
        )
    except Exception:
        return None


class ManifoldClient:
    """Public client for Manifold Markets API."""

    def __init__(self, base_url: str = MANIFOLD_BASE, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def get_markets(
        self,
        *,
        limit: int = 100,
        before: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch markets list. Returns raw market dicts."""
        url = f"{self.base_url}/markets"
        params: dict[str, Any] = {"limit": limit}
        if before:
            params["before"] = before
        resp = self._session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_market(self, market_id: str) -> dict[str, Any]:
        """Fetch single market by ID."""
        url = f"{self.base_url}/market/{market_id}"
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_market_prob(self, market_id: str) -> float:
        """Fetch current probability for a single market."""
        url = f"{self.base_url}/market/{market_id}/prob"
        resp = self._session.get(url, timeout=self.timeout)
        resp.raise_for_status()
        data = resp.json()
        return _to_float(data.get("prob", data.get("probability", 0)))

    def fetch_all_markets(self, max_pages: int = 5) -> list[Market]:
        """Paginate through markets and return normalized Market objects."""
        markets: list[Market] = []
        before: str | None = None
        for _ in range(max_pages):
            batch_raw = self.get_markets(limit=100, before=before)
            if not batch_raw:
                break
            for raw in batch_raw:
                m = _parse_manifold_market(raw)
                if m:
                    markets.append(m)
            # Manifold pagination uses the last market ID as "before"
            if len(batch_raw) < 100:
                break
            before = batch_raw[-1].get("id")
        return markets
