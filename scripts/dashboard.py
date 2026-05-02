"""Streamlit dashboard for prediction market trading system."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Prediction Market Bot", layout="wide")

LOGS_DIR = Path("logs")
DATA_DIR = Path("data/polymarket/markets")


# ── Data loaders ──────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_positions() -> pd.DataFrame:
    """Replay positions.jsonl into a DataFrame."""
    path = LOGS_DIR / "positions.jsonl"
    records: list[dict[str, Any]] = []
    if not path.exists():
        return pd.DataFrame()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    df = pd.DataFrame(records)
    if df.empty:
        return df
    # Normalize event types
    df["event"] = df.get("event", "")
    return df


@st.cache_data(ttl=60)
def load_events() -> pd.DataFrame:
    """Load structured events from events.jsonl."""
    path = LOGS_DIR / "events.jsonl"
    records: list[dict[str, Any]] = []
    if not path.exists():
        return pd.DataFrame()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return pd.DataFrame(records)


@st.cache_data(ttl=300)
def load_latest_markets() -> pd.DataFrame:
    """Load the most recent daily Parquet snapshot."""
    if not DATA_DIR.exists():
        return pd.DataFrame()
    files = sorted(DATA_DIR.glob("*.parquet"))
    if not files:
        return pd.DataFrame()
    return pd.read_parquet(files[-1])


# ── Derived tables ────────────────────────────────────────────────────────────

def get_open_positions(pos_df: pd.DataFrame) -> pd.DataFrame:
    """Reconstruct open positions from ledger events."""
    if pos_df.empty:
        return pd.DataFrame()
    opened = pos_df[pos_df["event"] == "POSITION_OPENED"].copy()
    closed = pos_df[pos_df["event"] == "POSITION_CLOSED"].copy()
    if opened.empty:
        return pd.DataFrame()
    # Drop null columns from opened to avoid merge suffix conflicts
    for col in ["closed_at", "exit_price", "pnl_usd"]:
        if col in opened.columns:
            opened.drop(columns=[col], inplace=True)
    # Merge closed info
    if not closed.empty:
        merge_cols = [c for c in ["position_id", "closed_at", "exit_price", "pnl_usd"] if c in closed.columns]
        if merge_cols:
            opened = opened.merge(
                closed[merge_cols],
                on="position_id",
                how="left",
            )
    # Ensure columns exist after merge
    for col in ["closed_at", "exit_price", "pnl_usd"]:
        if col not in opened.columns:
            opened[col] = None
    # Filter to still-open
    open_df = opened[opened["closed_at"].isna()].copy()
    return open_df


def get_closed_positions(pos_df: pd.DataFrame) -> pd.DataFrame:
    """Return only closed positions."""
    if pos_df.empty:
        return pd.DataFrame()
    opened = pos_df[pos_df["event"] == "POSITION_OPENED"].copy()
    closed = pos_df[pos_df["event"] == "POSITION_CLOSED"].copy()
    if opened.empty or closed.empty:
        return pd.DataFrame()
    # Drop null columns from opened to avoid merge suffix conflicts
    for col in ["closed_at", "exit_price", "pnl_usd"]:
        if col in opened.columns:
            opened.drop(columns=[col], inplace=True)
    merged = opened.merge(
        closed[["position_id", "closed_at", "exit_price", "pnl_usd"]],
        on="position_id",
        how="inner",
    )
    return merged


def get_trade_decisions(events_df: pd.DataFrame) -> pd.DataFrame:
    """Extract TRADE_DECISION events."""
    if events_df.empty or "event" not in events_df.columns:
        return pd.DataFrame()
    df = events_df[events_df["event"] == "TRADE_DECISION"].copy()
    if df.empty:
        return df
    df["timestamp"] = pd.to_datetime(df.get("timestamp", datetime.now(timezone.utc)))
    return df


# ── Page ──────────────────────────────────────────────────────────────────────

st.title("📈 Prediction Market Trading Dashboard")

pos_df = load_positions()
events_df = load_events()
markets_df = load_latest_markets()
open_df = get_open_positions(pos_df)
closed_df = get_closed_positions(pos_df)
decisions_df = get_trade_decisions(events_df)

# Summary metrics
col1, col2, col3, col4, col5 = st.columns(5)

open_count = len(open_df)
closed_count = len(closed_df)
realized_pnl = closed_df["pnl_usd"].sum() if not closed_df.empty else 0.0
win_rate = (
    (closed_df["pnl_usd"] > 0).sum() / closed_count if closed_count > 0 else 0.0
)
avg_ev = decisions_df["expected_value"].mean() if not decisions_df.empty else 0.0

col1.metric("Open Positions", open_count)
col2.metric("Closed Positions", closed_count)
col3.metric("Realized P&L", f"${realized_pnl:+.2f}")
col4.metric("Win Rate", f"{win_rate:.0%}")
col5.metric("Avg EV", f"{avg_ev:.1%}")

# Tabs
tab_open, tab_history, tab_perf, tab_signals, tab_exposure = st.tabs(
    ["Open Positions", "Trade History", "Performance", "Signal Quality", "Exposure"]
)

# ── Tab 1: Open Positions ─────────────────────────────────────────────────────
with tab_open:
    if open_df.empty:
        st.info("No open positions.")
    else:
        display = open_df[
            ["market_id", "side", "entry_price", "size_usd", "take_profit", "stop_loss", "opened_at"]
        ].copy()
        # Add current price from latest markets snapshot
        if not markets_df.empty and "market_id" in markets_df.columns:
            price_map = markets_df.set_index("market_id")["current_yes_price"].to_dict()
            display["current_price"] = display["market_id"].map(price_map)
            display["unrealized_pnl"] = display.apply(
                lambda r: (r["current_price"] - r["entry_price"]) * r["size_usd"]
                if r["side"] == "BUY_YES" and pd.notna(r["current_price"])
                else ((1 - r["current_price"]) - r["entry_price"]) * r["size_usd"]
                if pd.notna(r["current_price"])
                else None,
                axis=1,
            )
        st.dataframe(display, width='stretch')

# ── Tab 2: Trade History ──────────────────────────────────────────────────────
with tab_history:
    if decisions_df.empty:
        st.info("No trade decisions recorded yet.")
    else:
        filt = st.multiselect(
            "Filter by verdict",
            options=decisions_df["risk_verdict"].dropna().unique().tolist(),
            default=["APPROVED"],
        )
        filtered = decisions_df[decisions_df["risk_verdict"].isin(filt)] if filt else decisions_df
        st.dataframe(
            filtered[["timestamp", "market_id", "decision", "signal_prob", "market_price", "expected_value", "kelly_size_usd", "risk_verdict", "rationale"]],
            width='stretch',
        )

# ── Tab 3: Performance ────────────────────────────────────────────────────────
with tab_perf:
    if closed_df.empty:
        st.info("No closed positions yet — performance charts will appear after exits trigger.")
    else:
        closed_df["closed_at"] = pd.to_datetime(closed_df["closed_at"])
        closed_df = closed_df.sort_values("closed_at")
        closed_df["cumulative_pnl"] = closed_df["pnl_usd"].cumsum()

        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=closed_df["closed_at"],
                y=closed_df["cumulative_pnl"],
                mode="lines+markers",
                name="Cumulative P&L",
            )
        )
        fig.update_layout(title="Cumulative Realized P&L", xaxis_title="Date", yaxis_title="USD")
        st.plotly_chart(fig, width='stretch')

        # Win / loss distribution
        wins = (closed_df["pnl_usd"] > 0).sum()
        losses = (closed_df["pnl_usd"] <= 0).sum()
        fig2 = px.pie(
            names=["Wins", "Losses"],
            values=[wins, losses],
            title="Win / Loss Distribution",
        )
        st.plotly_chart(fig2, width='stretch')

# ── Tab 4: Signal Quality ─────────────────────────────────────────────────────
with tab_signals:
    if decisions_df.empty:
        st.info("No signal data yet.")
    else:
        # Scatter: signal_prob vs market_price colored by verdict
        fig = px.scatter(
            decisions_df,
            x="market_price",
            y="signal_prob",
            color="risk_verdict",
            size="expected_value",
            hover_data=["market_id", "kelly_size_usd"],
            title="Signal Probability vs Market Price",
        )
        fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1, line=dict(dash="dash"))
        st.plotly_chart(fig, width='stretch')

        # EV distribution
        fig2 = px.histogram(
            decisions_df,
            x="expected_value",
            color="risk_verdict",
            nbins=20,
            title="Expected Value Distribution",
        )
        st.plotly_chart(fig2, width='stretch')

# ── Tab 5: Exposure ───────────────────────────────────────────────────────────
with tab_exposure:
    if open_df.empty:
        st.info("No open positions.")
    else:
        total_exposure = open_df["size_usd"].sum()
        st.metric("Total Exposure", f"${total_exposure:.2f}")
        side_counts = open_df["side"].value_counts().reset_index()
        side_counts.columns = ["side", "count"]
        fig = px.pie(side_counts, names="side", values="count", title="Position Side Breakdown")
        st.plotly_chart(fig, width='stretch')

        # Entry price distribution
        fig2 = px.histogram(open_df, x="entry_price", nbins=20, title="Entry Price Distribution")
        st.plotly_chart(fig2, width='stretch')
