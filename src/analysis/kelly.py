"""Kelly Criterion position sizing with fractional Kelly caps."""

from __future__ import annotations


def kelly_fraction(p: float, b: float, fraction: float = 0.25) -> float:
    """
    Compute fractional Kelly position size as a fraction of bankroll.

    f* = (p * b - q) / b
    where q = 1 - p

    Args:
        p: estimated win probability (0.0 to 1.0)
        b: net odds received on a win (payout per $1 risked, e.g. 1.0 for even money)
        fraction: fractional Kelly cap (default 0.25 as per risk parameters)

    Returns:
        Fraction of bankroll to allocate (0.0 to fraction). Never negative.
    """
    if p <= 0.0 or b <= 0.0:
        return 0.0
    q = 1.0 - p
    f_star = (p * b - q) / b
    if f_star <= 0.0:
        return 0.0
    return min(f_star * fraction, fraction)


def kelly_position_size_usd(
    p: float,
    market_price: float,
    bankroll: float,
    fraction: float = 0.25,
    max_position: float = 500.0,
) -> float:
    """
    Convert Kelly fraction to a dollar position size.

    In a binary prediction market:
    - If you buy YES at price 'market_price', you risk 'market_price' to win (1 - market_price).
    - Net odds b = (1 - market_price) / market_price
    """
    if market_price <= 0.0 or market_price >= 1.0:
        return 0.0
    b = (1.0 - market_price) / market_price
    f = kelly_fraction(p, b, fraction)
    raw_size = f * bankroll
    return min(raw_size, max_position)
