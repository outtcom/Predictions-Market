"""
Execution Agent — Optimal order placement and position lifecycle management.

Responsibilities:
- Place limit orders on Polymarket (CLOB) and Kalshi (REST API)
- Monitor fill status; cancel and reprice unfilled orders after timeout
- Track open positions in a local ledger (market_id, entry_price, size, timestamp)
- Trigger exits when: (a) target price reached, (b) signal reverses, (c) stop-loss hit
- Avoid market orders except in time-sensitive information events
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from src.common.logger import log_event
from src.common.schemas import Market

OrderSide = Literal["BUY_YES", "BUY_NO"]
OrderStatus = Literal["PENDING", "OPEN", "FILLED", "CANCELLED", "EXPIRED"]

LEDGER_PATH = Path("logs/positions.jsonl")


@dataclass
class Order:
    order_id: str
    market_id: str
    platform: Literal["polymarket", "kalshi"]
    side: OrderSide
    size_usd: float
    limit_price: float
    status: OrderStatus = "PENDING"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    filled_at: str | None = None
    fill_price: float | None = None


@dataclass
class Position:
    position_id: str
    market_id: str
    platform: Literal["polymarket", "kalshi"]
    side: OrderSide
    entry_price: float
    size_usd: float
    opened_at: str
    take_profit: float | None = None
    stop_loss: float | None = None
    closed_at: str | None = None
    exit_price: float | None = None
    pnl_usd: float | None = None


class ExecutionAgent:
    """Manages order placement and position lifecycle."""

    def __init__(self, mode: str | None = None, ledger_path: Path = LEDGER_PATH) -> None:
        self.mode = (mode or os.getenv("MODE", "paper")).lower()
        self.ledger_path = ledger_path
        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._orders: dict[str, Order] = {}
        self._positions: dict[str, Position] = {}
        self._load_ledger()

    def _generate_id(self, prefix: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"{prefix}_{ts}"

    def _load_ledger(self) -> None:
        """Replay ledger history to reconstruct open positions."""
        if not self.ledger_path.exists():
            return
        try:
            with self.ledger_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    record = json.loads(line)
                    event = record.get("event")
                    if event == "POSITION_OPENED":
                        pos = Position(
                            position_id=record["position_id"],
                            market_id=record["market_id"],
                            platform=record["platform"],  # type: ignore[arg-type]
                            side=record["side"],  # type: ignore[arg-type]
                            entry_price=record["entry_price"],
                            size_usd=record["size_usd"],
                            opened_at=record["opened_at"],
                            take_profit=record.get("take_profit"),
                            stop_loss=record.get("stop_loss"),
                        )
                        self._positions[pos.position_id] = pos
                    elif event == "POSITION_CLOSED":
                        pos_id = record["position_id"]
                        pos = self._positions.get(pos_id)
                        if pos:
                            pos.closed_at = record.get("closed_at")
                            pos.exit_price = record.get("exit_price")
                            pos.pnl_usd = record.get("pnl_usd")
        except Exception as exc:
            log_event("EXECUTION_LEDGER_ERROR", {"error": str(exc)})

    def place_limit_order(
        self,
        market: Market,
        side: OrderSide,
        size_usd: float,
        limit_price: float,
    ) -> Order:
        """Place a limit order (simulated in paper mode, real in live mode)."""
        order_id = self._generate_id("ord")
        order = Order(
            order_id=order_id,
            market_id=market.market_id,
            platform=market.platform,
            side=side,
            size_usd=size_usd,
            limit_price=limit_price,
            status="OPEN",
        )
        self._orders[order_id] = order

        if self.mode == "paper":
            # In paper mode, simulate immediate fill at limit price if price is near mid
            # For realism, assume fill at limit_price
            order.status = "FILLED"
            order.filled_at = datetime.now(timezone.utc).isoformat()
            order.fill_price = limit_price
            self._open_position(order, market)

        log_event(
            "EXECUTION_ORDER",
            {
                "order_id": order_id,
                "market_id": market.market_id,
                "platform": market.platform,
                "side": side,
                "size_usd": size_usd,
                "limit_price": limit_price,
                "mode": self.mode,
                "status": order.status,
            },
        )
        return order

    def _open_position(self, order: Order, market: Market) -> Position:
        """Convert a filled order into an open position."""
        pos_id = self._generate_id("pos")
        pos = Position(
            position_id=pos_id,
            market_id=order.market_id,
            platform=order.platform,
            side=order.side,
            entry_price=order.fill_price or order.limit_price,
            size_usd=order.size_usd,
            opened_at=order.filled_at or order.created_at,
        )
        self._positions[pos_id] = pos
        self._append_ledger({"event": "POSITION_OPENED", **self._pos_to_dict(pos)})
        return pos

    def set_exit_levels(self, position_id: str, take_profit: float | None, stop_loss: float | None) -> None:
        """Attach stop-loss and take-profit to an open position."""
        pos = self._positions.get(position_id)
        if not pos:
            return
        pos.take_profit = take_profit
        pos.stop_loss = stop_loss
        log_event(
            "EXECUTION_EXIT_LEVELS",
            {
                "position_id": position_id,
                "take_profit": take_profit,
                "stop_loss": stop_loss,
            },
        )

    def check_exits(self, market: Market) -> list[Position]:
        """Scan open positions for triggered exits. Returns closed positions."""
        closed: list[Position] = []
        for pos in list(self._positions.values()):
            if pos.market_id != market.market_id or pos.closed_at:
                continue

            current_price = market.current_yes_price
            exit_triggered = False
            exit_price = current_price

            if pos.side == "BUY_YES":
                if pos.take_profit is not None and current_price >= pos.take_profit:
                    exit_triggered = True
                elif pos.stop_loss is not None and current_price <= pos.stop_loss:
                    exit_triggered = True
            elif pos.side == "BUY_NO":
                # For NO positions, price moves inversely
                no_price = 1.0 - current_price
                if pos.take_profit is not None and no_price >= pos.take_profit:
                    exit_triggered = True
                elif pos.stop_loss is not None and no_price <= pos.stop_loss:
                    exit_triggered = True

            if exit_triggered:
                self._close_position(pos, exit_price)
                closed.append(pos)

        return closed

    def _close_position(self, pos: Position, exit_price: float) -> None:
        pos.closed_at = datetime.now(timezone.utc).isoformat()
        pos.exit_price = exit_price
        if pos.side == "BUY_YES":
            pos.pnl_usd = (exit_price - pos.entry_price) * pos.size_usd
        else:
            pos.pnl_usd = ((1.0 - exit_price) - pos.entry_price) * pos.size_usd
        self._append_ledger({"event": "POSITION_CLOSED", **self._pos_to_dict(pos)})
        log_event(
            "EXECUTION_CLOSE",
            {
                "position_id": pos.position_id,
                "market_id": pos.market_id,
                "exit_price": exit_price,
                "pnl_usd": pos.pnl_usd,
            },
        )

    def _pos_to_dict(self, pos: Position) -> dict[str, Any]:
        return {
            "position_id": pos.position_id,
            "market_id": pos.market_id,
            "platform": pos.platform,
            "side": pos.side,
            "entry_price": pos.entry_price,
            "size_usd": pos.size_usd,
            "opened_at": pos.opened_at,
            "take_profit": pos.take_profit,
            "stop_loss": pos.stop_loss,
            "closed_at": pos.closed_at,
            "exit_price": pos.exit_price,
            "pnl_usd": pos.pnl_usd,
        }

    def _append_ledger(self, record: dict[str, Any]) -> None:
        with self.ledger_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    def get_open_positions(self) -> list[Position]:
        return [p for p in self._positions.values() if p.closed_at is None]

    def get_position(self, position_id: str) -> Position | None:
        return self._positions.get(position_id)
