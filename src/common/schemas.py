"""Shared data models and type definitions."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass(frozen=True)
class Signal:
    """Output from the Signal Agent."""

    market_id: str
    signal_prob: float
    confidence_interval: tuple[float, float]
    signal_sources: list[str]
    staleness_hours: float
    signal_strength: Literal["weak", "moderate", "strong"]
    notes: str


@dataclass
class Market:
    """Normalized market metadata across Polymarket and Kalshi."""

    market_id: str
    question: str
    platform: Literal["polymarket", "kalshi"]
    category: str = ""
    current_yes_price: float = 0.0  # 0.0 to 1.0
    volume_24h: float = 0.0
    liquidity_usd: float = 0.0
    days_to_resolution: int = 0
    active: bool = True
    closed: bool = False
    resolution_date: datetime | None = None
    raw: dict = field(default_factory=dict, repr=False)
