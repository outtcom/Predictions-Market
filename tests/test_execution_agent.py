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
            "entry_price": 0.012,
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
            "entry_price": 0.045,
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
            "entry_price": 0.55,
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
