# Strategy Agent — Design Spec
**Date:** 2026-05-18
**Status:** Approved

## Problem

The current system trades exclusively on micro-probability markets (0.45–6% YES price). LLMs systematically over-estimate these events, producing phantom EV signals (900%+) with no real edge. Exit levels are never triggered because the price movement required is impossible (entry 1.2% → take-profit 8.7%). All 22 open positions are zombies. Zero realized PnL.

Root cause: no market eligibility filter upstream of signal generation.

## Solution

Add a `StrategyAgent` that pre-filters markets before signals are generated. Only eligible markets enter the pipeline. Ineligible markets are dropped silently.

Also: one-time zombie position reset to clear the ledger.

---

## Architecture

```
markets (all)
    → StrategyAgent.filter()       # NEW — drops ineligible markets
    → SignalAgent.generate_signals()
    → MarketAnalyst.analyze_batch()
    → RiskManager.assess()
    → ExecutionAgent.place_limit_order()
```

No other layer changes. Orchestrator integration is a 3-line addition.

---

## New File: `agents/strategy_agent.py`

### Preset dataclass

```python
@dataclass
class StrategyPreset:
    name: str
    min_market_price: float       # 0.10
    max_market_price: float       # 0.90
    min_volume_24h: float         # 5_000
    min_days_to_resolution: int   # 1
    max_days_to_resolution: int   # 60
    min_divergence_threshold: float  # 0.07
```

### `StrategyAgent` class

```python
class StrategyAgent:
    def __init__(self, preset: str = "high_ev_divergence") -> None: ...
    def filter(self, markets: list[Market]) -> list[Market]: ...
    def is_eligible(self, market: Market) -> tuple[bool, str]: ...
```

`is_eligible` returns `(True, "")` or `(False, reason)` for logging.

`filter` calls `is_eligible` on each market, logs dropped markets at DEBUG level, returns the passing list.

### Built-in preset: `high_ev_divergence`

| Parameter | Value |
|---|---|
| `min_market_price` | 0.10 |
| `max_market_price` | 0.90 |
| `min_volume_24h` | 5,000 |
| `min_days_to_resolution` | 1 |
| `max_days_to_resolution` | 60 |
| `min_divergence_threshold` | 0.07 |

Note: `required_signal_strength` is NOT enforced here — signal strength is only known after `SignalAgent` runs (which comes after filtering). Signal strength enforcement remains in `RiskManager` via `min_signal_confidence`.

Rationale for 10% floor: LLM calibration degrades sharply below 10%. Historical Brier scores on sub-10% markets are ~2–3× worse than on 10–90% markets. The 7% divergence threshold (up from 5%) compensates for slightly higher noise in the 10–20% range.

---

## Zombie Position Reset

New method on `ExecutionAgent`:

```python
def reset_zombie_positions(self, max_entry_price: float = 0.10) -> int:
    """Close all open positions below max_entry_price with pnl=0.
    Returns count of positions closed."""
```

Writes `POSITION_CLOSED` records to the ledger with `pnl_usd=0.0` and `exit_price=entry_price`. This is honest — no fabricated profit, just clearing dead weight. Called once from a `scripts/reset_zombies.py` helper, not from the main cycle.

---

## Orchestrator Changes (`agents/orchestrator.py`)

```python
# __init__: add
from agents.strategy_agent import StrategyAgent
self.strategy_agent = StrategyAgent(preset="high_ev_divergence")

# run_cycle: replace
all_markets = poly_markets
# with:
all_markets = self.strategy_agent.filter(poly_markets)
```

That's the full diff to orchestrator.py.

---

## New Script: `scripts/reset_zombies.py`

One-shot script. Loads the execution agent, calls `reset_zombie_positions()`, prints count. Run once manually, then delete or ignore.

---

## Files Changed

| File | Change |
|---|---|
| `agents/strategy_agent.py` | **NEW** — StrategyAgent + preset dataclass |
| `agents/execution_agent.py` | Add `reset_zombie_positions()` method |
| `agents/orchestrator.py` | Instantiate StrategyAgent, call `.filter()` in `run_cycle` |
| `scripts/reset_zombies.py` | **NEW** — one-shot zombie clearer |

No changes to: SignalAgent, MarketAnalyst, RiskManager, config.py, schemas.py.

---

## Success Criteria

1. After reset: `get_open_positions()` returns 0 open positions
2. First post-fix cycle: all traded markets have `current_yes_price` between 10% and 90%
3. No `EV > 10.0` (1000%) in trade decisions — these were the junk signals
4. At least one position closes within 7 days (take-profit or stop-loss triggered at realistic price levels)
