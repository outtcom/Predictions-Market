# Strategy Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `StrategyAgent` that pre-filters markets to the 10–90% price range before signal generation, fixing phantom EV signals on micro-probability markets and clearing zombie positions from the ledger.

**Architecture:** A new `StrategyAgent` class holds a named preset (`high_ev_divergence`) defining market eligibility rules. The Orchestrator calls `strategy_agent.filter(markets)` immediately after indexing, before signals are generated. A separate `reset_zombie_positions()` method on `ExecutionAgent` closes all open sub-10% positions with `pnl=0` to clear the ledger.

**Tech Stack:** Python 3.11+, dataclasses, existing `Market` schema from `src/common/schemas.py`, pytest.

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `agents/strategy_agent.py` | **CREATE** | `StrategyPreset` dataclass + `StrategyAgent` class |
| `tests/test_strategy_agent.py` | **CREATE** | Unit tests for filter logic |
| `agents/execution_agent.py` | **MODIFY** | Add `reset_zombie_positions()` method |
| `tests/test_execution_agent.py` | **CREATE** | Unit test for zombie reset |
| `agents/orchestrator.py` | **MODIFY** | Instantiate `StrategyAgent`, call `.filter()` in `run_cycle` |
| `scripts/reset_zombies.py` | **CREATE** | One-shot CLI script to clear current zombie positions |

---

## Task 1: `StrategyAgent` — core filter logic (TDD)

**Files:**
- Create: `agents/strategy_agent.py`
- Create: `tests/test_strategy_agent.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_strategy_agent.py`:

```python
"""Tests for StrategyAgent market eligibility filter."""
from __future__ import annotations

import pytest
from agents.strategy_agent import StrategyAgent, StrategyPreset, HIGH_EV_DIVERGENCE
from src.common.schemas import Market


def _market(
    market_id: str = "mkt_1",
    current_yes_price: float = 0.50,
    volume_24h: float = 10_000,
    days_to_resolution: int = 14,
    active: bool = True,
    closed: bool = False,
) -> Market:
    return Market(
        market_id=market_id,
        question="Will X happen?",
        platform="polymarket",
        current_yes_price=current_yes_price,
        volume_24h=volume_24h,
        days_to_resolution=days_to_resolution,
        active=active,
        closed=closed,
    )


class TestStrategyPreset:
    def test_high_ev_divergence_preset_exists(self) -> None:
        assert HIGH_EV_DIVERGENCE.name == "high_ev_divergence"
        assert HIGH_EV_DIVERGENCE.min_market_price == 0.10
        assert HIGH_EV_DIVERGENCE.max_market_price == 0.90
        assert HIGH_EV_DIVERGENCE.min_volume_24h == 5_000
        assert HIGH_EV_DIVERGENCE.min_days_to_resolution == 1
        assert HIGH_EV_DIVERGENCE.max_days_to_resolution == 60
        assert HIGH_EV_DIVERGENCE.min_divergence_threshold == 0.07


class TestStrategyAgentIsEligible:
    def setup_method(self) -> None:
        self.agent = StrategyAgent(preset="high_ev_divergence")

    def test_eligible_market_passes(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(current_yes_price=0.50))
        assert eligible is True
        assert reason == ""

    def test_price_too_low_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(current_yes_price=0.05))
        assert eligible is False
        assert "price" in reason.lower()

    def test_price_too_high_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(current_yes_price=0.95))
        assert eligible is False
        assert "price" in reason.lower()

    def test_exactly_at_min_price_passes(self) -> None:
        eligible, _ = self.agent.is_eligible(_market(current_yes_price=0.10))
        assert eligible is True

    def test_exactly_at_max_price_passes(self) -> None:
        eligible, _ = self.agent.is_eligible(_market(current_yes_price=0.90))
        assert eligible is True

    def test_low_volume_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(volume_24h=1_000))
        assert eligible is False
        assert "volume" in reason.lower()

    def test_already_closed_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(closed=True))
        assert eligible is False
        assert "closed" in reason.lower()

    def test_inactive_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(active=False))
        assert eligible is False

    def test_zero_days_to_resolution_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(days_to_resolution=0))
        assert eligible is False
        assert "days" in reason.lower()

    def test_too_many_days_to_resolution_rejected(self) -> None:
        eligible, reason = self.agent.is_eligible(_market(days_to_resolution=61))
        assert eligible is False
        assert "days" in reason.lower()

    def test_exactly_at_max_days_passes(self) -> None:
        eligible, _ = self.agent.is_eligible(_market(days_to_resolution=60))
        assert eligible is True


class TestStrategyAgentFilter:
    def setup_method(self) -> None:
        self.agent = StrategyAgent(preset="high_ev_divergence")

    def test_filter_returns_only_eligible(self) -> None:
        markets = [
            _market("good", current_yes_price=0.50),
            _market("too_low", current_yes_price=0.02),
            _market("too_high", current_yes_price=0.98),
            _market("low_vol", volume_24h=100),
        ]
        result = self.agent.filter(markets)
        assert len(result) == 1
        assert result[0].market_id == "good"

    def test_filter_empty_list(self) -> None:
        assert self.agent.filter([]) == []

    def test_filter_all_eligible(self) -> None:
        markets = [_market(f"m{i}") for i in range(5)]
        result = self.agent.filter(markets)
        assert len(result) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

```
uv run pytest tests/test_strategy_agent.py -v
```

Expected: `ModuleNotFoundError: No module named 'agents.strategy_agent'`

- [ ] **Step 3: Create `agents/strategy_agent.py`**

```python
"""Strategy Agent — pre-filters markets to eligible candidates before signal generation."""

from __future__ import annotations

from dataclasses import dataclass

from src.common.logger import log_event
from src.common.schemas import Market


@dataclass(frozen=True)
class StrategyPreset:
    name: str
    min_market_price: float
    max_market_price: float
    min_volume_24h: float
    min_days_to_resolution: int
    max_days_to_resolution: int
    min_divergence_threshold: float


HIGH_EV_DIVERGENCE = StrategyPreset(
    name="high_ev_divergence",
    min_market_price=0.10,
    max_market_price=0.90,
    min_volume_24h=5_000,
    min_days_to_resolution=1,
    max_days_to_resolution=60,
    min_divergence_threshold=0.07,
)

_PRESETS: dict[str, StrategyPreset] = {
    "high_ev_divergence": HIGH_EV_DIVERGENCE,
}


class StrategyAgent:
    """Pre-filters markets to those eligible under the active strategy preset."""

    def __init__(self, preset: str = "high_ev_divergence") -> None:
        if preset not in _PRESETS:
            raise ValueError(f"Unknown preset '{preset}'. Available: {list(_PRESETS)}")
        self.preset = _PRESETS[preset]

    def is_eligible(self, market: Market) -> tuple[bool, str]:
        """Return (True, '') if eligible, or (False, reason) if not."""
        p = self.preset

        if market.closed:
            return False, "market is closed"
        if not market.active:
            return False, "market is inactive"
        if market.current_yes_price < p.min_market_price:
            return False, f"price {market.current_yes_price:.2%} below min {p.min_market_price:.2%}"
        if market.current_yes_price > p.max_market_price:
            return False, f"price {market.current_yes_price:.2%} above max {p.max_market_price:.2%}"
        if market.volume_24h < p.min_volume_24h:
            return False, f"volume ${market.volume_24h:,.0f} below min ${p.min_volume_24h:,.0f}"
        if market.days_to_resolution < p.min_days_to_resolution:
            return False, f"days_to_resolution {market.days_to_resolution} below min {p.min_days_to_resolution}"
        if market.days_to_resolution > p.max_days_to_resolution:
            return False, f"days_to_resolution {market.days_to_resolution} above max {p.max_days_to_resolution}"

        return True, ""

    def filter(self, markets: list[Market]) -> list[Market]:
        """Return only eligible markets; log dropped ones."""
        eligible: list[Market] = []
        dropped = 0
        for market in markets:
            ok, reason = self.is_eligible(market)
            if ok:
                eligible.append(market)
            else:
                dropped += 1
                log_event(
                    "STRATEGY_AGENT_DROP",
                    {"market_id": market.market_id, "reason": reason},
                )
        log_event(
            "STRATEGY_AGENT_FILTER",
            {
                "preset": self.preset.name,
                "total": len(markets),
                "eligible": len(eligible),
                "dropped": dropped,
            },
        )
        return eligible
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_strategy_agent.py -v
```

Expected: All tests PASS.

- [ ] **Step 5: Commit**

```
git add agents/strategy_agent.py tests/test_strategy_agent.py
git commit -m "feat: add StrategyAgent with high_ev_divergence preset"
```

---

## Task 2: `ExecutionAgent.reset_zombie_positions()` (TDD)

**Files:**
- Modify: `agents/execution_agent.py`
- Create: `tests/test_execution_agent.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_execution_agent.py`:

```python
"""Tests for ExecutionAgent zombie position reset."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agents.execution_agent import ExecutionAgent
from src.common.schemas import Market


def _make_agent_with_positions(tmp_path: Path) -> ExecutionAgent:
    """Write a ledger with 3 open positions and return a loaded agent."""
    ledger = tmp_path / "positions.jsonl"
    positions = [
        {
            "event": "POSITION_OPENED",
            "position_id": "pos_low_1",
            "market_id": "mkt_low_1",
            "platform": "polymarket",
            "side": "BUY_YES",
            "entry_price": 0.012,   # sub-10% — zombie
            "size_usd": 25.0,
            "opened_at": "2026-05-01T00:00:00+00:00",
            "take_profit": None,
            "stop_loss": None,
            "closed_at": None,
            "exit_price": None,
            "pnl_usd": None,
        },
        {
            "event": "POSITION_OPENED",
            "position_id": "pos_low_2",
            "market_id": "mkt_low_2",
            "platform": "polymarket",
            "side": "BUY_YES",
            "entry_price": 0.045,   # sub-10% — zombie
            "size_usd": 18.0,
            "opened_at": "2026-05-02T00:00:00+00:00",
            "take_profit": None,
            "stop_loss": None,
            "closed_at": None,
            "exit_price": None,
            "pnl_usd": None,
        },
        {
            "event": "POSITION_OPENED",
            "position_id": "pos_healthy",
            "market_id": "mkt_healthy",
            "platform": "polymarket",
            "side": "BUY_YES",
            "entry_price": 0.55,    # healthy — should NOT be reset
            "size_usd": 30.0,
            "opened_at": "2026-05-03T00:00:00+00:00",
            "take_profit": None,
            "stop_loss": None,
            "closed_at": None,
            "exit_price": None,
            "pnl_usd": None,
        },
    ]
    with ledger.open("w") as f:
        for p in positions:
            f.write(json.dumps(p) + "\n")
    return ExecutionAgent(mode="paper", ledger_path=ledger)


class TestResetZombiePositions:
    def test_closes_sub_threshold_positions(self, tmp_path: Path) -> None:
        agent = _make_agent_with_positions(tmp_path)
        count = agent.reset_zombie_positions(max_entry_price=0.10)
        assert count == 2

    def test_healthy_position_stays_open(self, tmp_path: Path) -> None:
        agent = _make_agent_with_positions(tmp_path)
        agent.reset_zombie_positions(max_entry_price=0.10)
        open_positions = agent.get_open_positions()
        assert len(open_positions) == 1
        assert open_positions[0].position_id == "pos_healthy"

    def test_closed_positions_have_pnl_zero(self, tmp_path: Path) -> None:
        agent = _make_agent_with_positions(tmp_path)
        agent.reset_zombie_positions(max_entry_price=0.10)
        ledger_lines = (tmp_path / "positions.jsonl").read_text().splitlines()
        close_records = [
            json.loads(l) for l in ledger_lines
            if json.loads(l).get("event") == "POSITION_CLOSED"
        ]
        assert len(close_records) == 2
        for r in close_records:
            assert r["pnl_usd"] == 0.0

    def test_no_zombies_returns_zero(self, tmp_path: Path) -> None:
        ledger = tmp_path / "positions.jsonl"
        ledger.write_text("")
        agent = ExecutionAgent(mode="paper", ledger_path=ledger)
        count = agent.reset_zombie_positions(max_entry_price=0.10)
        assert count == 0
```

- [ ] **Step 2: Run test to verify it fails**

```
uv run pytest tests/test_execution_agent.py -v
```

Expected: `AttributeError: 'ExecutionAgent' object has no attribute 'reset_zombie_positions'`

- [ ] **Step 3: Add `reset_zombie_positions()` to `agents/execution_agent.py`**

Add the following method to the `ExecutionAgent` class, after `get_open_positions()` at line 253:

```python
def reset_zombie_positions(self, max_entry_price: float = 0.10) -> int:
    """Close all open positions with entry_price below max_entry_price with pnl=0.

    Used to clear legacy sub-threshold positions that can never trigger exits.
    Returns the count of positions closed.
    """
    closed_count = 0
    for pos in list(self._positions.values()):
        if pos.closed_at is not None:
            continue
        if pos.entry_price < max_entry_price:
            self._close_position(pos, exit_price=pos.entry_price)
            pos.pnl_usd = 0.0
            closed_count += 1
            log_event(
                "EXECUTION_ZOMBIE_RESET",
                {
                    "position_id": pos.position_id,
                    "market_id": pos.market_id,
                    "entry_price": pos.entry_price,
                },
            )
    return closed_count
```

- [ ] **Step 4: Run tests to verify they pass**

```
uv run pytest tests/test_execution_agent.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```
git add agents/execution_agent.py tests/test_execution_agent.py
git commit -m "feat: add reset_zombie_positions to ExecutionAgent"
```

---

## Task 3: Orchestrator integration

**Files:**
- Modify: `agents/orchestrator.py`

- [ ] **Step 1: Add import at top of `agents/orchestrator.py`**

After the existing imports (around line 36), add:

```python
from agents.strategy_agent import StrategyAgent
```

- [ ] **Step 2: Instantiate `StrategyAgent` in `Orchestrator.__init__`**

In `__init__`, after `self.execution = ExecutionAgent(mode=self.mode)` (line 71), add:

```python
self.strategy_agent = StrategyAgent(preset="high_ev_divergence")
```

- [ ] **Step 3: Call `.filter()` in `run_cycle`**

In `run_cycle`, find the lines (around line 79–84):

```python
        # 1. Index markets
        poly_markets = self.ingester.index_polymarket(max_pages=max_pages)
        # Kalshi markets skipped if credentials missing
        all_markets = poly_markets
        if not all_markets:
```

Replace `all_markets = poly_markets` with:

```python
        # 1. Index markets
        poly_markets = self.ingester.index_polymarket(max_pages=max_pages)
        # Kalshi markets skipped if credentials missing
        all_markets = self.strategy_agent.filter(poly_markets)
        if not all_markets:
```

- [ ] **Step 4: Verify no import errors**

```
uv run python -c "from agents.orchestrator import Orchestrator; print('OK')"
```

Expected output: `OK`

- [ ] **Step 5: Commit**

```
git add agents/orchestrator.py
git commit -m "feat: wire StrategyAgent filter into Orchestrator run_cycle"
```

---

## Task 4: One-shot zombie reset script

**Files:**
- Create: `scripts/reset_zombies.py`

- [ ] **Step 1: Create `scripts/reset_zombies.py`**

```python
"""One-shot script to close all open zombie positions (entry_price < 10%).

Run once to clear the legacy sub-threshold position ledger:
    uv run python -m scripts.reset_zombies
"""

from __future__ import annotations

from agents.execution_agent import ExecutionAgent


def main() -> None:
    agent = ExecutionAgent()
    open_before = len(agent.get_open_positions())
    closed = agent.reset_zombie_positions(max_entry_price=0.10)
    open_after = len(agent.get_open_positions())
    print(f"Positions before: {open_before}")
    print(f"Zombie positions closed: {closed}")
    print(f"Positions remaining: {open_after}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the reset against the real ledger**

```
uv run python -m scripts.reset_zombies
```

Expected output:
```
Positions before: 22
Zombie positions closed: 22
Positions remaining: 0
```

(Numbers may vary if the ledger has changed, but zombie count should drop to 0.)

- [ ] **Step 3: Commit**

```
git add scripts/reset_zombies.py
git commit -m "feat: add reset_zombies one-shot script"
```

---

## Task 5: Full test suite and smoke check

- [ ] **Step 1: Run the full test suite**

```
uv run pytest tests/ -v
```

Expected: All tests PASS. (Pre-existing `test_risk_manager.py`, `test_kelly_sizing.py` etc. that have `raise NotImplementedError` will still fail — those are pre-existing stubs, not regressions. Only new tests must pass.)

- [ ] **Step 2: Smoke-check the filter with real data**

```
uv run python -c "
from agents.data_ingester import DataIngester
from agents.strategy_agent import StrategyAgent

ingester = DataIngester()
markets = ingester.index_polymarket(max_pages=1)
agent = StrategyAgent()
eligible = agent.filter(markets)
print(f'Total markets: {len(markets)}')
print(f'Eligible (10-90% price, vol>5k, 1-60 days): {len(eligible)}')
if eligible:
    import statistics
    prices = [m.current_yes_price for m in eligible]
    print(f'Price range: {min(prices):.1%} – {max(prices):.1%}')
    print(f'Price median: {statistics.median(prices):.1%}')
"
```

Expected: `Eligible` count is non-zero; all prices are between 10% and 90%.

- [ ] **Step 3: Final commit**

```
git add .
git commit -m "chore: verify strategy agent filter on live data"
```

---

## Success Criteria Checklist

- [ ] `uv run pytest tests/test_strategy_agent.py tests/test_execution_agent.py -v` — all pass
- [ ] After running `reset_zombies.py`: `get_open_positions()` returns 0
- [ ] Smoke check: all eligible markets have `current_yes_price` between 10% and 90%
- [ ] No `EV > 10.0` (1000%) appears in trade decisions after the first live cycle
