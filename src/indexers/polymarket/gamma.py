"""Polymarket Gamma API client — market discovery (public, no auth)."""

from __future__ import annotations

from typing import Any

import requests

from src.common.schemas import Market

GAMMA_BASE = "https://gamma-api.polymarket.com"


def _to_float(val: str | float | None) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _parse_gamma_market(raw: dict[str, Any]) -> Market | None:
    """Normalize a single Polymarket market dict into a Market object."""
    import json

    try:
        prices = raw.get("outcomePrices", [])
        # Polymarket sometimes returns outcomePrices as a JSON-encoded string
        if isinstance(prices, str):
            try:
                prices = json.loads(prices)
            except json.JSONDecodeError:
                prices = []

        yes_price = 0.0
        if isinstance(prices, list) and len(prices) >= 1:
            yes_price = _to_float(prices[0])
        elif isinstance(prices, dict):
            yes_price = _to_float(prices.get("Yes", prices.get("yes", 0)))

        # Guard against Polymarket returning prices as strings like "0.6500"
        if yes_price > 1.0:
            yes_price = yes_price / 100.0

        return Market(
            market_id=raw.get("conditionId") or raw.get("id", ""),
            question=raw.get("question", ""),
            platform="polymarket",
            category=raw.get("category", ""),
            current_yes_price=yes_price,
            volume_24h=_to_float(raw.get("volume24hr", 0)),
            liquidity_usd=_to_float(raw.get("liquidity", 0)),
            active=raw.get("active", True),
            closed=raw.get("closed", False),
            raw=raw,
        )
    except Exception:
        return None


class GammaClient:
    """Public client for Polymarket Gamma API."""

    def __init__(self, base_url: str = GAMMA_BASE, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def get_events(
        self,
        *,
        active: bool = True,
        closed: bool = False,
        limit: int = 100,
        offset: int = 0,
        order: str = "volume24hr",
        ascending: bool = False,
    ) -> list[dict[str, Any]]:
        """Fetch events from /events endpoint."""
        url = f"{self.base_url}/events"
        params = {
            "active": str(active).lower(),
            "closed": str(closed).lower(),
            "limit": limit,
            "offset": offset,
            "order": order,
            "ascending": str(ascending).lower(),
        }
        resp = self._session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_markets(
        self,
        *,
        active: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Fetch flat markets list from /markets endpoint."""
        url = f"{self.base_url}/markets"
        params = {
            "active": str(active).lower(),
            "limit": limit,
            "offset": offset,
        }
        resp = self._session.get(url, params=params, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fetch_all_markets(self, max_pages: int = 10) -> list[Market]:
        """Paginate through active markets and return normalized Market objects."""
        markets: list[Market] = []
        for page in range(max_pages):
            offset = page * 100
            batch = self.get_markets(active=True, limit=100, offset=offset)
            if not batch:
                break
            for raw in batch:
                m = _parse_gamma_market(raw)
                if m:
                    markets.append(m)
            if len(batch) < 100:
                break
        return markets
