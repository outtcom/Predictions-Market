"""Risk parameters and API keys loaded from environment."""

from __future__ import annotations

import os

RISK = {
    "max_single_position_usd": 500,
    "max_portfolio_exposure_pct": 0.60,
    "min_expected_value": 0.03,
    "min_signal_confidence": 0.60,
    "max_days_to_resolution": 90,
    "kelly_fraction": 0.25,
    "max_category_concentration": 0.40,
    "max_platform_concentration": 0.70,
    "daily_loss_limit_pct": 0.05,
}


def env(key: str, default: str | None = None) -> str | None:
    return os.getenv(key, default)
