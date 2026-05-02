"""
News & Intel Agent — Real-time monitoring of information that moves probabilities.

Responsibilities:
- Monitor RSS feeds, Twitter/X, government releases, and official sources for relevant events
- Map breaking news to open market positions — flag for Orchestrator immediately
- Detect information asymmetry windows (before market price updates)
- Score news relevance [0–1] and estimated probability impact [delta]
- Maintain an event calendar for scheduled releases (Fed meetings, election dates, earnings)

Alert Protocol:
HIGH: P(delta) > 10% — ping Orchestrator immediately, suggest position review
MED:  P(delta) 3–10% — queue for next 15-min sync
LOW:  P(delta) < 3%  — log only
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import requests

from src.common.logger import log_event

NEWSAPI_BASE = "https://newsapi.org/v2"


@dataclass
class NewsAlert:
    headline: str
    source: str
    url: str
    published_at: str
    relevance_score: float  # 0.0–1.0
    estimated_delta: float  # estimated probability impact
    severity: str  # HIGH | MED | LOW
    matched_keywords: list[str]


class NewsIntelAgent:
    """Monitors news and events for probability-moving information."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("NEWS_API_KEY")
        self._session = requests.Session()

    def fetch_headlines(self, query: str, from_date: str | None = None, page_size: int = 20) -> list[dict[str, Any]]:
        """Fetch news via NewsAPI everything endpoint."""
        if not self.api_key:
            log_event("NEWS_INTEL_ERROR", {"error": "NEWS_API_KEY missing"})
            return []

        url = f"{NEWSAPI_BASE}/everything"
        params: dict[str, Any] = {
            "q": query,
            "sortBy": "publishedAt",
            "pageSize": page_size,
            "apiKey": self.api_key,
        }
        if from_date:
            params["from"] = from_date

        try:
            resp = self._session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json().get("articles", [])
        except Exception as exc:
            log_event("NEWS_INTEL_ERROR", {"error": str(exc), "query": query})
            return []

    @staticmethod
    def score_article(article: dict[str, Any], keywords: list[str]) -> NewsAlert | None:
        """Score a single article for relevance and estimated probability impact."""
        title = (article.get("title") or "").lower()
        description = (article.get("description") or "").lower()
        text = title + " " + description

        matched = [kw for kw in keywords if kw.lower() in text]
        if not matched:
            return None

        # Simple heuristic: more matched keywords = higher relevance
        relevance = min(1.0, len(matched) / 3.0)

        # Estimate delta based on keyword intensity
        high_impact_words = ["breaking", "exclusive", "official", "announces", "declares", "emergency", "war", "ceasefire", "signed", "agreement"]
        impact_count = sum(1 for w in high_impact_words if w in text)
        estimated_delta = min(0.30, 0.02 + impact_count * 0.03)

        if estimated_delta > 0.10:
            severity = "HIGH"
        elif estimated_delta > 0.03:
            severity = "MED"
        else:
            severity = "LOW"

        return NewsAlert(
            headline=article.get("title", ""),
            source=article.get("source", {}).get("name", "unknown"),
            url=article.get("url", ""),
            published_at=article.get("publishedAt", ""),
            relevance_score=relevance,
            estimated_delta=estimated_delta,
            severity=severity,
            matched_keywords=matched,
        )

    def scan_for_market(self, query: str, keywords: list[str]) -> list[NewsAlert]:
        """Fetch and score news for a specific market topic."""
        articles = self.fetch_headlines(query)
        alerts: list[NewsAlert] = []
        for art in articles:
            alert = self.score_article(art, keywords)
            if alert:
                alerts.append(alert)
                log_event(
                    "NEWS_INTEL_ALERT",
                    {
                        "severity": alert.severity,
                        "headline": alert.headline[:80],
                        "delta": alert.estimated_delta,
                        "relevance": alert.relevance_score,
                    },
                )
        # Sort by severity then delta
        severity_order = {"HIGH": 0, "MED": 1, "LOW": 2}
        alerts.sort(key=lambda a: (severity_order[a.severity], -a.estimated_delta))
        return alerts
