"""Metaculus API client — fetches community predictions and question metadata."""

from __future__ import annotations

import os
from typing import Any

import requests

METACULUS_BASE = "https://www.metaculus.com/api"


class MetaculusClient:
    """Authenticated client for Metaculus API."""

    def __init__(self, token: str | None = None, timeout: int = 30) -> None:
        self.token = token or os.getenv("METACULUS_API_KEY")
        self.timeout = timeout
        self._session = requests.Session()

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError("METACULUS_API_KEY not set.")
        return {"Authorization": f"Token {self.token}"}

    def get_posts(
        self,
        *,
        statuses: list[str] | None = None,
        forecast_type: str = "binary",
        with_cp: bool = True,
        include_cp_history: bool = False,
        order_by: str = "-published_at",
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Fetch paginated posts (questions) from Metaculus."""
        url = f"{METACULUS_BASE}/posts/"
        params: dict[str, Any] = {
            "forecast_type": forecast_type,
            "with_cp": str(with_cp).lower(),
            "order_by": order_by,
            "limit": limit,
            "offset": offset,
        }
        if statuses:
            params["statuses"] = ",".join(statuses)
        if include_cp_history:
            params["include_cp_history"] = "true"
        resp = self._session.get(url, params=params, headers=self._headers(), timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def fetch_open_binary_questions(self, max_pages: int = 5) -> list[dict[str, Any]]:
        """Fetch all open binary questions with community predictions."""
        results: list[dict[str, Any]] = []
        for page in range(max_pages):
            offset = page * 20
            data = self.get_posts(
                statuses=["open"],
                forecast_type="binary",
                with_cp=True,
                limit=20,
                offset=offset,
            )
            batch = data.get("results", [])
            if not batch:
                break
            results.extend(batch)
            if data.get("next") is None:
                break
        return results

    @staticmethod
    def extract_community_probability(post: dict[str, Any]) -> float | None:
        """Extract the recency-weighted community median probability for YES.

        Returns None if unavailable.
        """
        try:
            question = post.get("question")
            if not question:
                return None
            aggregations = question.get("aggregations", {})
            cp = aggregations.get("recency_weighted", aggregations.get("unweighted"))
            if not cp:
                return None
            # For binary questions, CP is typically a dict with forecast values
            forecast = cp.get("forecast", {})
            # Try common paths
            prob = forecast.get("y")
            if prob is None:
                prob = forecast.get("yes")
            if prob is None and isinstance(forecast, list) and len(forecast) > 0:
                prob = forecast[-1]
            if prob is None:
                # Some responses have probability_yes directly
                prob = question.get("community_prediction", {}).get("full", {}).get("y")
            if prob is None:
                return None
            return float(prob)
        except Exception:
            return None
