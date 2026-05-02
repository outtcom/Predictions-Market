"""
Historical strategy simulation for prediction market trading.

Usage:
    python -m scripts.backtest --data-dir data/polymarket/markets --bankroll 1000 --max-files 30

Loads daily market snapshots from Parquet, runs the decision pipeline,
simulates fills at market prices, and reports P&L metrics.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import pandas as pd

# Allow imports from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.market_analyst import MarketAnalyst
from agents.risk_manager import RiskManager
from src.common.schemas import Market, Signal


def row_to_market(row: pd.Series) -> Market:
    """Convert a DataFrame row back into a Market object."""
    return Market(
        market_id=str(row["market_id"]),
        question=str(row["question"]),
        platform="polymarket",  # backtest currently Polymarket-only
        category=str(row.get("category", "")),
        current_yes_price=float(row["current_yes_price"]),
        volume_24h=float(row["volume_24h"]),
        liquidity_usd=float(row.get("liquidity_usd", 0)),
        days_to_resolution=int(row.get("days_to_resolution", 0)),
        active=bool(row.get("active", True)),
        closed=bool(row.get("closed", False)),
    )


@dataclass
class BacktestTrade:
    date: str
    market_id: str
    question: str
    side: str
    entry_price: float
    size_usd: float
    signal_prob: float
    exit_price: float | None = None
    pnl_usd: float | None = None
    closed: bool = False


@dataclass
class BacktestResult:
    trades: list[BacktestTrade] = field(default_factory=list)
    daily_values: list[tuple[str, float]] = field(default_factory=list)

    def closed_trades(self) -> list[BacktestTrade]:
        return [t for t in self.trades if t.closed]

    def total_return_pct(self, bankroll: float) -> float:
        if not self.daily_values:
            return 0.0
        final = self.daily_values[-1][1]
        return (final - bankroll) / bankroll * 100

    def win_rate(self) -> float:
        closed = self.closed_trades()
        if not closed:
            return 0.0
        winners = sum(1 for t in closed if (t.pnl_usd or 0) > 0)
        return winners / len(closed)

    def max_drawdown_pct(self) -> float:
        if not self.daily_values:
            return 0.0
        peak = self.daily_values[0][1]
        max_dd = 0.0
        for _, val in self.daily_values:
            if val > peak:
                peak = val
            dd = (peak - val) / peak
            if dd > max_dd:
                max_dd = dd
        return max_dd * 100

    def sharpe_ratio(self, risk_free_rate: float = 0.0) -> float:
        if len(self.daily_values) < 2:
            return 0.0
        returns = []
        for i in range(1, len(self.daily_values)):
            prev = self.daily_values[i - 1][1]
            curr = self.daily_values[i][1]
            returns.append((curr - prev) / prev if prev > 0 else 0)
        if not returns:
            return 0.0
        mean_ret = sum(returns) / len(returns)
        std_ret = (sum((r - mean_ret) ** 2 for r in returns) / len(returns)) ** 0.5
        if std_ret == 0:
            return 0.0
        return (mean_ret - risk_free_rate / 252) / std_ret * (252 ** 0.5)


def load_market_snapshots(data_dir: Path, max_files: int | None = None) -> list[tuple[str, list[Market]]]:
    """Load chronological market snapshots from Parquet files."""
    files = sorted(data_dir.glob("markets_*.parquet"))
    if max_files:
        files = files[-max_files:]
    snapshots: list[tuple[str, list[Market]]] = []
    for f in files:
        date_str = f.stem.replace("markets_", "")
        df = pd.read_parquet(f)
        markets = [row_to_market(row) for _, row in df.iterrows()]
        snapshots.append((date_str, markets))
    return snapshots


def run_backtest(
    snapshots: list[tuple[str, list[Market]]],
    signal_fn: Callable[[list[Market]], list[Signal]],
    bankroll: float = 1000.0,
) -> BacktestResult:
    """Run a full backtest across daily market snapshots."""
    analyst = MarketAnalyst()
    risk_mgr = RiskManager()
    result = BacktestResult()
    portfolio_value = bankroll
    open_trades: dict[str, BacktestTrade] = {}

    for date_str, markets in snapshots:
        # 1. Generate signals for this day
        signals = signal_fn(markets)
        signal_map = {s.market_id: s for s in signals}

        # 2. Analyze markets
        analyses = analyst.analyze_batch(markets, signals)

        # 3. For each watchlist candidate, simulate risk → execution
        for analysis in analyses:
            if not analysis.on_watchlist:
                continue

            market = analysis.market
            signal = signal_map.get(market.market_id)
            if signal is None:
                continue

            # Simulate risk assessment
            assessment = risk_mgr.assess(
                analysis,
                signal,
                bankroll=portfolio_value,
                open_positions=[],
            )
            if assessment.verdict == "REJECTED":
                continue

            # Simulate fill at market price
            trade = BacktestTrade(
                date=date_str,
                market_id=market.market_id,
                question=market.question,
                side="BUY_YES" if signal.signal_prob > market.current_yes_price else "BUY_NO",
                entry_price=market.current_yes_price,
                size_usd=assessment.size_usd,
                signal_prob=signal.signal_prob,
            )
            result.trades.append(trade)
            open_trades[market.market_id] = trade
            portfolio_value -= assessment.size_usd  # capital reserved

        # 4. Close trades that hit SL/TP (simplified: resolve at next day's price)
        # In a real backtest we'd need daily price history per market.
        # Here we close any trade whose market appears again with a price move.
        next_day_markets = {m.market_id: m for m in markets}
        for market_id, trade in list(open_trades.items()):
            m = next_day_markets.get(market_id)
            if m is None:
                continue
            current_price = m.current_yes_price
            exit_triggered = False
            exit_price = current_price

            if trade.side == "BUY_YES":
                if trade.entry_price > 0.01 and current_price <= trade.entry_price * 0.5:
                    exit_triggered = True  # simplified 50% drawdown stop
                elif current_price >= trade.signal_prob * 0.9:
                    exit_triggered = True  # take profit near signal
            elif trade.side == "BUY_NO":
                no_price = 1.0 - current_price
                entry_no = 1.0 - trade.entry_price
                if entry_no > 0.01 and no_price <= entry_no * 0.5:
                    exit_triggered = True
                elif no_price >= trade.signal_prob * 0.9:
                    exit_triggered = True

            if exit_triggered:
                trade.exit_price = exit_price
                if trade.side == "BUY_YES":
                    trade.pnl_usd = (exit_price - trade.entry_price) * trade.size_usd
                else:
                    trade.pnl_usd = ((1.0 - exit_price) - trade.entry_price) * trade.size_usd
                trade.closed = True
                portfolio_value += trade.size_usd + trade.pnl_usd
                del open_trades[market_id]

        # Mark portfolio value for the day
        # Add back reserved capital from still-open trades
        reserved = sum(t.size_usd for t in open_trades.values())
        result.daily_values.append((date_str, portfolio_value + reserved))

    return result


def print_report(result: BacktestResult, bankroll: float) -> None:
    """Print formatted backtest report."""
    closed = result.closed_trades()
    print("\n" + "=" * 60)
    print("BACKTEST REPORT")
    print("=" * 60)
    print(f"Starting bankroll:    ${bankroll:,.2f}")
    print(f"Final value:          ${result.daily_values[-1][1]:,.2f}" if result.daily_values else "Final value:          N/A")
    print(f"Total return:         {result.total_return_pct(bankroll):+.2f}%")
    print(f"Total trades:         {len(result.trades)}")
    print(f"Closed trades:        {len(closed)}")
    print(f"Win rate:             {result.win_rate()*100:.1f}%")
    print(f"Max drawdown:         {result.max_drawdown_pct():.2f}%")
    print(f"Sharpe (ann):         {result.sharpe_ratio():.2f}")
    print("-" * 60)
    if closed:
        print("\nLast 10 closed trades:")
        for t in closed[-10:]:
            print(
                f"  {t.date} | {t.side:6} | ${t.size_usd:>7.2f} | "
                f"entry={t.entry_price:.3f} exit={t.exit_price:.3f} | "
                f"P&L=${t.pnl_usd:>+8.2f} | {t.question[:50]}"
            )
    print("=" * 60 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest prediction market strategies.")
    parser.add_argument("--data-dir", default="data/polymarket/markets", help="Directory with markets_*.parquet files")
    parser.add_argument("--bankroll", type=float, default=1000.0, help="Starting capital")
    parser.add_argument("--max-files", type=int, default=None, help="Max number of daily snapshots to load")
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"Data directory not found: {data_dir}")
        sys.exit(1)

    snapshots = load_market_snapshots(data_dir, max_files=args.max_files)
    if not snapshots:
        print("No market snapshots found. Run the data ingester first.")
        sys.exit(1)

    print(f"Loaded {len(snapshots)} daily snapshots ({sum(len(m) for _, m in snapshots)} total markets)")

    # Example signal function: random synthetic signals for framework testing
    # In production, replace this with actual SignalAgent.generate_signals()
    def synthetic_signal_fn(markets: list[Market]) -> list[Signal]:
        """Synthetic strategy for framework validation: buy markets priced < 5% with 15% prob."""
        import random
        random.seed(42)
        signals: list[Signal] = []
        for m in markets:
            if m.current_yes_price < 0.05 and m.volume_24h > 5000 and not m.closed:
                sig_prob = 0.15
                signals.append(
                    Signal(
                        market_id=m.market_id,
                        signal_prob=sig_prob,
                        confidence_interval=(max(0.0, sig_prob - 0.10), min(1.0, sig_prob + 0.10)),
                        signal_sources=["synthetic_backtest"],
                        staleness_hours=0.0,
                        signal_strength="moderate",
                        notes="Synthetic signal for backtest framework validation",
                    )
                )
        return signals

    result = run_backtest(snapshots, synthetic_signal_fn, bankroll=args.bankroll)
    print_report(result, args.bankroll)

    # Save results to JSON for dashboard consumption
    out_path = Path("logs/backtest_result.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(
            {
                "bankroll": args.bankroll,
                "total_return_pct": result.total_return_pct(args.bankroll),
                "win_rate": result.win_rate(),
                "max_drawdown_pct": result.max_drawdown_pct(),
                "sharpe_ratio": result.sharpe_ratio(),
                "trade_count": len(result.trades),
                "daily_values": result.daily_values,
            },
            f,
            indent=2,
        )
    print(f"Backtest metrics saved to {out_path}")


if __name__ == "__main__":
    main()
