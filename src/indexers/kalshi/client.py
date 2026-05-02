"""Kalshi Exchange API client."""

from __future__ import annotations

import os
from typing import Any

import requests

from src.common.schemas import Market

KALSHI_PROD = "https://trading-api.kalshi.com/trade-api/v2"
KALSHI_DEMO = "https://demo.kalshi.com/trade-api/v2"


def _to_float(val: str | float | None) -> float:
    if val is None:
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _parse_kalshi_market(raw: dict[str, Any]) -> Market | None:
    """Normalize a Kalshi market dict into a Market object."""
    try:
        # Kalshi prices are in cents (0-100); normalize to 0.0-1.0
        yes_price = _to_float(raw.get("yes_ask", raw.get("last_price", 0))) / 100.0

        return Market(
            market_id=raw.get("ticker", ""),
            question=raw.get("title", ""),
            platform="kalshi",
            category=raw.get("category", ""),
            current_yes_price=yes_price,
            volume_24h=_to_float(raw.get("volume", 0)),
            liquidity_usd=_to_float(raw.get("open_interest", 0)),
            active=raw.get("status", "").lower() == "active",
            closed=raw.get("status", "").lower() in ("closed", "settled"),
            raw=raw,
        )
    except Exception:
        return None


class KalshiClient:
    """Authenticated client for Kalshi Exchange API."""

    def __init__(
        self,
        base_url: str = KALSHI_PROD,
        timeout: int = 30,
        email: str | None = None,
        password: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.email = email or os.getenv("KALSHI_EMAIL")
        self.password = password or os.getenv("KALSHI_PASSWORD")
        self._token: str | None = None
        self._session = requests.Session()

    def _auth(self) -> str:
        """Login and return session token."""
        if self._token:
            return self._token
        if not self.email or not self.password:
            raise RuntimeError("Kalshi email/password required. Set KALSHI_EMAIL and KALSHI_PASSWORD.")
        url = f"{self.base_url}/login"
        resp = self._session.post(
            url,
            json={"email": self.email, "password": self.password},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        self._token = data.get("token") or data.get("session_token")
        if not self._token:
            raise RuntimeError(f"Kalshi login failed: {data}")
        return self._token

    def _headers(self) -> dict[str, str]:
        token = self._auth()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def get_markets(
        self,
        *,
        status: str = "active",
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Fetch markets page. Returns {markets, cursor}."""
        url = f"{self.base_url}/markets"
        params: dict[str, Any] = {"status": status, "limit": limit}
        if cursor:
            params["cursor"] = cursor
        resp = self._session.get(
            url, params=params, headers=self._headers(), timeout=self.timeout
        )
        resp.raise_for_status()
        return resp.json()

    def fetch_all_markets(self, max_pages: int = 20) -> list[Market]:
        """Paginate through active markets and return normalized Market objects."""
        markets: list[Market] = []
        cursor: str | None = None
        for _ in range(max_pages):
            data = self.get_markets(status="active", limit=100, cursor=cursor)
            batch = data.get("markets", [])
            if not batch:
                break
            for raw in batch:
                m = _parse_kalshi_market(raw)
                if m:
                    markets.append(m)
            cursor = data.get("cursor")
            if not cursor:
                break
        return markets

    def get_orderbook(self, ticker: str) -> dict[str, Any]:
        """Fetch orderbook for a specific market ticker."""
        url = f"{self.base_url}/markets/{ticker}/orderbook"
        resp = self._session.get(url, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()
