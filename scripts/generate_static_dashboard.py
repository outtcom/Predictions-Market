"""Generate a static HTML dashboard for GitHub Pages from local data."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

LOGS_DIR = Path("logs")
DOCS_DIR = Path("docs")
DATA_DIR = Path("data/polymarket/markets")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    if not path.exists():
        return records
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


def build_dashboard() -> str:
    positions = load_jsonl(LOGS_DIR / "positions.jsonl")
    events = load_jsonl(LOGS_DIR / "events.jsonl")

    pos_df = pd.DataFrame(positions)
    events_df = pd.DataFrame(events)

    # Open / closed positions
    opened = pos_df[pos_df.get("event", "") == "POSITION_OPENED"] if not pos_df.empty else pd.DataFrame()
    closed = pos_df[pos_df.get("event", "") == "POSITION_CLOSED"] if not pos_df.empty else pd.DataFrame()

    open_count = len(opened)
    closed_count = len(closed)

    realized_pnl = 0.0
    win_rate = 0.0
    if not closed.empty and "pnl_usd" in closed.columns:
        realized_pnl = closed["pnl_usd"].sum()
        wins = (closed["pnl_usd"] > 0).sum()
        win_rate = wins / closed_count if closed_count > 0 else 0.0

    # Trade decisions
    decisions = events_df[events_df.get("event", "") == "TRADE_DECISION"] if not events_df.empty else pd.DataFrame()
    approved = len(decisions[decisions.get("risk_verdict", "") == "APPROVED"]) if not decisions.empty else 0
    rejected = len(decisions[decisions.get("risk_verdict", "") == "REJECTED"]) if not decisions.empty else 0

    # Open positions table data
    open_positions_data = []
    if not opened.empty:
        for _, row in opened.iterrows():
            open_positions_data.append({
                "market_id": row.get("market_id", "")[:16] + "...",
                "side": row.get("side", ""),
                "entry": f"{row.get('entry_price', 0):.3f}",
                "size": f"${row.get('size_usd', 0):.2f}",
                "tp": row.get("take_profit", "—"),
                "sl": row.get("stop_loss", "—"),
            })

    # Signal audit data
    audits = events_df[events_df.get("event", "") == "SIGNAL_AUDIT"] if not events_df.empty else pd.DataFrame()
    audit_data = []
    if not audits.empty:
        for _, row in audits.tail(20).iterrows():
            audit_data.append({
                "question": row.get("question", "")[:60] + "..." if len(str(row.get("question", ""))) > 60 else row.get("question", ""),
                "signal": f"{row.get('signal_prob', 0):.1%}",
                "market": f"{row.get('market_price', 0):.1%}",
                "ev": f"{row.get('expected_value', 0):.1%}",
                "verdict": row.get("risk_verdict", ""),
            })

    # Decision scatter data
    scatter_data = []
    if not decisions.empty and "signal_prob" in decisions.columns and "market_price" in decisions.columns:
        for _, row in decisions.iterrows():
            scatter_data.append({
                "x": round(row.get("market_price", 0), 3),
                "y": round(row.get("signal_prob", 0), 3),
                "verdict": row.get("risk_verdict", ""),
                "ev": round(row.get("expected_value", 0), 3),
            })

    generated_at = datetime.now(timezone.utc).isoformat()

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Prediction Market Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0f172a; color: #e2e8f0; padding: 20px; }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        h1 {{ font-size: 1.8rem; margin-bottom: 8px; color: #38bdf8; }}
        .subtitle {{ color: #94a3b8; margin-bottom: 24px; font-size: 0.9rem; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .card {{ background: #1e293b; border-radius: 12px; padding: 20px; border: 1px solid #334155; }}
        .card h3 {{ font-size: 0.85rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; margin-bottom: 8px; }}
        .card .value {{ font-size: 2rem; font-weight: 700; color: #f8fafc; }}
        .card .value.positive {{ color: #4ade80; }}
        .card .value.negative {{ color: #f87171; }}
        .section {{ background: #1e293b; border-radius: 12px; padding: 20px; margin-bottom: 24px; border: 1px solid #334155; }}
        .section h2 {{ font-size: 1.2rem; margin-bottom: 16px; color: #38bdf8; }}
        table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
        th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #334155; }}
        th {{ color: #94a3b8; font-weight: 600; text-transform: uppercase; font-size: 0.75rem; letter-spacing: 0.05em; }}
        tr:hover {{ background: #27354f; }}
        .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }}
        .badge-approved {{ background: #14532d; color: #4ade80; }}
        .badge-rejected {{ background: #450a0a; color: #f87171; }}
        .chart-container {{ position: relative; height: 300px; }}
        .two-col {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
        @media (max-width: 768px) {{ .two-col {{ grid-template-columns: 1fr; }} .grid {{ grid-template-columns: 1fr 1fr; }} }}
    </style>
</head>
<body>
    <div class="container">
        <h1>📈 Prediction Market Dashboard</h1>
        <p class="subtitle">Generated at {generated_at}</p>

        <div class="grid">
            <div class="card">
                <h3>Open Positions</h3>
                <div class="value">{open_count}</div>
            </div>
            <div class="card">
                <h3>Closed Positions</h3>
                <div class="value">{closed_count}</div>
            </div>
            <div class="card">
                <h3>Realized P&L</h3>
                <div class="value {'positive' if realized_pnl >= 0 else 'negative'}">${realized_pnl:+.2f}</div>
            </div>
            <div class="card">
                <h3>Win Rate</h3>
                <div class="value">{win_rate:.0%}</div>
            </div>
            <div class="card">
                <h3>Approved Signals</h3>
                <div class="value">{approved}</div>
            </div>
            <div class="card">
                <h3>Rejected Signals</h3>
                <div class="value">{rejected}</div>
            </div>
        </div>

        <div class="two-col">
            <div class="section">
                <h2>Signal Probability vs Market Price</h2>
                <div class="chart-container">
                    <canvas id="scatterChart"></canvas>
                </div>
            </div>
            <div class="section">
                <h2>Win / Loss</h2>
                <div class="chart-container">
                    <canvas id="pieChart"></canvas>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>Open Positions</h2>
            <table>
                <thead><tr><th>Market</th><th>Side</th><th>Entry</th><th>Size</th><th>TP</th><th>SL</th></tr></thead>
                <tbody>
                    {''.join(f'<tr><td>{p["market_id"]}</td><td>{p["side"]}</td><td>{p["entry"]}</td><td>{p["size"]}</td><td>{p["tp"]}</td><td>{p["sl"]}</td></tr>' for p in open_positions_data) if open_positions_data else '<tr><td colspan="6" style="text-align:center;color:#64748b;">No open positions</td></tr>'}
                </tbody>
            </table>
        </div>

        <div class="section">
            <h2>Recent Signal Audits</h2>
            <table>
                <thead><tr><th>Market</th><th>Signal</th><th>Market</th><th>EV</th><th>Verdict</th></tr></thead>
                <tbody>
                    {''.join(f'<tr><td>{a["question"]}</td><td>{a["signal"]}</td><td>{a["market"]}</td><td>{a["ev"]}</td><td><span class="badge badge-{a["verdict"].lower()}">{a["verdict"]}</span></td></tr>' for a in audit_data) if audit_data else '<tr><td colspan="5" style="text-align:center;color:#64748b;">No signal audits yet</td></tr>'}
                </tbody>
            </table>
        </div>
    </div>

    <script>
        const scatterData = {json.dumps(scatter_data)};
        const wins = {int(win_rate * closed_count) if closed_count > 0 else 0};
        const losses = {closed_count - int(win_rate * closed_count) if closed_count > 0 else 0};

        new Chart(document.getElementById('scatterChart'), {{
            type: 'scatter',
            data: {{
                datasets: [
                    {{
                        label: 'APPROVED',
                        data: scatterData.filter(d => d.verdict === 'APPROVED'),
                        backgroundColor: '#4ade80',
                        pointRadius: 6
                    }},
                    {{
                        label: 'REJECTED',
                        data: scatterData.filter(d => d.verdict === 'REJECTED'),
                        backgroundColor: '#f87171',
                        pointRadius: 5
                    }}
                ]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                scales: {{
                    x: {{ title: {{ display: true, text: 'Market Price', color: '#94a3b8' }}, grid: {{ color: '#334155' }}, ticks: {{ color: '#94a3b8' }} }},
                    y: {{ title: {{ display: true, text: 'Signal Probability', color: '#94a3b8' }}, grid: {{ color: '#334155' }}, ticks: {{ color: '#94a3b8' }} }}
                }},
                plugins: {{
                    legend: {{ labels: {{ color: '#e2e8f0' }} }},
                    tooltip: {{ callbacks: {{ label: ctx => `EV: ${{ctx.raw.ev}}` }} }}
                }}
            }}
        }});

        new Chart(document.getElementById('pieChart'), {{
            type: 'doughnut',
            data: {{
                labels: ['Wins', 'Losses'],
                datasets: [{{ data: [wins, losses], backgroundColor: ['#4ade80', '#f87171'], borderWidth: 0 }}]
            }},
            options: {{
                responsive: true,
                maintainAspectRatio: false,
                plugins: {{
                    legend: {{ position: 'bottom', labels: {{ color: '#e2e8f0' }} }}
                }}
            }}
        }});
    </script>
</body>
</html>'''

    return html


def main() -> None:
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    html = build_dashboard()
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    print(f"Dashboard generated: {DOCS_DIR / 'index.html'}")


if __name__ == "__main__":
    main()
