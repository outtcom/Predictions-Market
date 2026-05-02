# CLAUDE.md — Prediction Market Trading System

Behavioral guidelines and agent architecture for a multi-agent prediction market trading system.
Inspired by Karpathy's LLM coding guidelines. Bias toward caution over speed — especially with capital.

---

## Karpathy Core Principles

### 1. Think Before Coding
**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First
**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes
**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution
**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add signal" → "Write backtests that confirm edge, then integrate"
- "Fix position sizing" → "Write a test that reproduces the bug, then fix it"
- "Optimize strategy" → "Ensure Sharpe/ROI metrics pass thresholds before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

---

## System Overview

This system trades prediction markets on **Polymarket** and **Kalshi** using a team of specialized AI agents coordinated by an Orchestrator. The architecture is inspired by Jon Becker's prediction-market-analysis framework and extends it with active trading, signal generation, and risk management layers.

```
┌─────────────────────────────────────────────────────────────────┐
│                        ORCHESTRATOR                             │
│         Coordinates agents · Allocates capital · Logs          │
└────────────┬────────────┬────────────┬────────────┬────────────┘
             │            │            │            │
     ┌───────▼──┐  ┌──────▼──┐  ┌────▼──────┐  ┌──▼──────────┐
     │  MARKET  │  │ SIGNAL  │  │   RISK    │  │  EXECUTION  │
     │ ANALYST  │  │  AGENT  │  │  MANAGER  │  │    AGENT    │
     └──────────┘  └─────────┘  └───────────┘  └─────────────┘
             │            │
     ┌───────▼──┐  ┌──────▼──┐
     │  DATA    │  │  NEWS & │
     │ INGESTER │  │  INTEL  │
     └──────────┘  └─────────┘
```

---

## Agent Roster

### 🎯 Orchestrator Agent
**Role:** Central coordinator. The only agent that issues trade instructions.

**Responsibilities:**
- Receive signals from all sub-agents and synthesize into a position decision
- Enforce portfolio-level exposure limits before any order is placed
- Log every decision with rationale, agent votes, and confidence scores
- Gate trades through the Risk Manager before sending to Execution Agent
- Run a daily P&L and attribution report across all open and closed positions

**Decision Protocol:**
```
1. Collect signals from Market Analyst + Signal Agent
2. Pass proposed trade to Risk Manager → receive approve/reject/resize
3. If approved, send to Execution Agent with limit price + size
4. Record outcome; feed back into Signal Agent's calibration loop
```

**Success Criteria:** Sharpe ratio > 1.5 on 30-day rolling window. Win rate > 55% on directional trades.

---

### 📊 Market Analyst Agent
**Role:** Deep understanding of each market's structure, history, and microstructure.

**Responsibilities:**
- Parse Polymarket and Kalshi market metadata (question, resolution criteria, end date, liquidity)
- Identify markets with mispriced probabilities using reference base rates
- Classify markets by category: political, economic, sports, crypto, science, geopolitical
- Compute implied probability vs. historical base rate divergence
- Flag markets with thin order books (spread > 3%) as high-slippage risk
- Maintain a watchlist of markets where edge > 3% EV

**Key Metrics to Track per Market:**
```python
{
  "market_id": str,
  "question": str,
  "platform": "polymarket" | "kalshi",
  "current_yes_price": float,       # 0.0 to 1.0
  "estimated_true_prob": float,     # from Signal Agent
  "expected_value": float,          # (true_prob - price) / price
  "liquidity_usd": float,
  "volume_24h": float,
  "days_to_resolution": int,
  "category": str,
  "confidence": float               # 0.0 to 1.0
}
```

**Data Sources:** Polymarket CLOB API, Kalshi REST API, Jon Becker's parquet datasets for historical base rates.

---

### 📡 Signal Agent
**Role:** Generate probability estimates independent of market price.

**Responsibilities:**
- Synthesize signals from multiple independent sources into a single probability estimate
- Maintain calibration log: compare predicted probabilities to outcomes
- Flag overconfidence when model uncertainty is high
- Output signal with confidence interval, not just point estimate

**Signal Sources (in priority order):**
1. **Superforecaster consensus** — Metaculus community median, Good Judgment forecasts
2. **Prediction market arbitrage** — Cross-platform divergences (Polymarket vs. Kalshi vs. Manifold)
3. **Statistical base rates** — Historical frequency of similar events (use Becker dataset)
4. **News sentiment** — Weighted aggregation of recent headlines (recency-decayed)
5. **Polymarket whale tracking** — Large orders (>$5K) that may carry information
6. **Implied probability from adjacent markets** — Use correlated markets as priors

**Output Schema:**
```python
{
  "market_id": str,
  "signal_prob": float,             # your best estimate
  "confidence_interval": [float, float],  # 80% CI
  "signal_sources": list[str],
  "staleness_hours": float,         # how old is newest data
  "signal_strength": "weak" | "moderate" | "strong",
  "notes": str
}
```

**Calibration Rule:** After 50+ resolved markets, Brier score must be < 0.15. If not, retrain priors.

---

### 📰 News & Intel Agent
**Role:** Real-time monitoring of information that moves probabilities.

**Responsibilities:**
- Monitor RSS feeds, Twitter/X, government releases, and official sources for relevant events
- Map breaking news to open market positions — flag for Orchestrator immediately
- Detect information asymmetry windows (before market price updates)
- Score news relevance [0–1] and estimated probability impact [delta]
- Maintain an event calendar for scheduled releases (Fed meetings, election dates, earnings)

**Alert Protocol:**
```
HIGH: P(delta) > 10% — ping Orchestrator immediately, suggest position review
MED:  P(delta) 3–10% — queue for next 15-min sync
LOW:  P(delta) < 3%  — log only
```

**Do NOT:**
- Act on unverified social media rumors without a second source
- Assume insider information is legal to trade on (flag and escalate)

---

### 🛡️ Risk Manager Agent
**Role:** The last line of defense before capital is deployed.

**Responsibilities:**
- Enforce hard position limits (see Risk Parameters below)
- Run Kelly Criterion sizing and cap at fractional Kelly (0.25x)
- Reject any trade where signal confidence < 60% or EV < 3%
- Monitor portfolio concentration: no single category > 40% of deployed capital
- Track correlation between open positions — penalize high-correlation clusters
- Require stop-loss levels on all positions > $500

**Risk Parameters (defaults — override in config):**
```python
RISK = {
  "max_single_position_usd": 500,
  "max_portfolio_exposure_pct": 0.60,    # max 60% of bankroll deployed
  "min_expected_value": 0.03,            # 3% EV floor
  "min_signal_confidence": 0.60,
  "max_days_to_resolution": 90,          # avoid long-duration illiquid markets
  "kelly_fraction": 0.25,                # fractional Kelly
  "max_category_concentration": 0.40,
  "max_platform_concentration": 0.70,   # don't over-concentrate on one platform
  "daily_loss_limit_pct": 0.05,         # halt if down 5% in a day
}
```

**Kelly Formula:**
```
f* = (p * b - q) / b
where:
  p = estimated win probability (from Signal Agent)
  q = 1 - p
  b = net odds (payout per $1 risked)
  position_size = f* * 0.25 * bankroll
```

**Hard Veto Conditions (no override):**
- Market resolution criteria are ambiguous or disputed
- Signal is derived solely from a single unverified source
- Platform has unresolved smart contract / operational issues
- Daily loss limit has been hit

---

### ⚙️ Execution Agent
**Role:** Optimal order placement and position lifecycle management.

**Responsibilities:**
- Place limit orders on Polymarket (CLOB) and Kalshi (REST API)
- Monitor fill status; cancel and reprice unfilled orders after timeout
- Track open positions in a local ledger (market_id, entry_price, size, timestamp)
- Trigger exits when: (a) target price reached, (b) signal reverses, (c) stop-loss hit
- Avoid market orders except in time-sensitive information events

**Order Protocol:**
```
1. Receive instruction from Orchestrator: {market_id, side, size_usd, max_price}
2. Check current orderbook spread
3. Place limit order at mid + 0.5% (aggressive enough to fill, not market order)
4. If unfilled after 10 min, reprice to mid + 1%
5. If unfilled after 20 min, cancel and report back to Orchestrator
6. On fill, record in ledger and set exit targets
```

**Exit Strategy per Position:**
```python
{
  "take_profit": entry_price + (edge * 0.7),   # take 70% of expected edge
  "stop_loss": entry_price - (edge * 0.5),      # lose no more than 50% of edge
  "time_stop": resolution_date - 2_days         # exit 2 days before resolution if no conviction
}
```

---

### 🗄️ Data Ingester Agent
**Role:** Reliable, versioned data pipeline for all market and trade data.

**Responsibilities:**
- Index Polymarket markets via CLOB API; index Kalshi markets via REST API
- Store all market metadata and trade history in Parquet format (matches Becker schema)
- Resume interrupted collection without data loss (checkpoint-based)
- Provide clean DataFrames to all other agents on demand
- Maintain a `data/` directory structure identical to Becker's layout:

```
data/
├── kalshi/
│   ├── markets/          # Parquet: market metadata
│   └── trades/           # Parquet: trade history
└── polymarket/
    ├── markets/
    ├── trades/
    └── blocks/           # On-chain block data
```

**Data Quality Rules:**
- Flag and quarantine any market with missing resolution criteria
- Deduplicate trades by (market_id, trade_id) before storage
- Assert price columns are in [0.0, 1.0] range; reject out-of-range rows

---

## Project Structure

```
prediction-market-trading/
├── CLAUDE.md                     ← this file
├── agents/
│   ├── orchestrator.py           ← coordinator + decision loop
│   ├── market_analyst.py
│   ├── signal_agent.py
│   ├── news_intel_agent.py
│   ├── risk_manager.py
│   ├── execution_agent.py
│   └── data_ingester.py
├── src/
│   ├── indexers/
│   │   ├── kalshi/               ← Kalshi API client
│   │   └── polymarket/           ← Polymarket CLOB + blockchain
│   ├── analysis/
│   │   ├── base_rates.py         ← historical frequency analysis
│   │   ├── calibration.py        ← Brier score, reliability diagrams
│   │   ├── microstructure.py     ← spread, depth, whale detection
│   │   └── kelly.py              ← position sizing
│   └── common/
│       ├── schemas.py            ← shared data models
│       ├── logger.py             ← structured JSON logging
│       └── config.py             ← risk params + API keys
├── data/                         ← Parquet datasets (gitignored)
├── logs/                         ← decision logs + trade records
├── tests/
│   ├── test_signal_calibration.py
│   ├── test_risk_manager.py
│   ├── test_kelly_sizing.py
│   └── test_execution_protocol.py
├── scripts/
│   ├── backtest.py               ← historical strategy simulation
│   ├── analyze.py                ← run analysis suite
│   └── dashboard.py             ← P&L + open positions viewer
├── pyproject.toml
└── .env.example
```

---

## Strategies to Maximize Returns

These are the highest-conviction edges in prediction markets, ranked by risk-adjusted return:

### 1. Cross-Platform Arbitrage (Lowest Risk)
- The same event trades on both Polymarket and Kalshi at different prices
- When |P_poly - P_kalshi| > 3% after fees, the spread is free money
- Requires capital on both platforms simultaneously
- **Expected edge:** 2–5% per trade, very high win rate (~90%)

### 2. Superforecaster Divergence (Core Strategy)
- Monitor Metaculus and Good Judgment for community medians
- When market price diverges from superforecaster consensus by >5%, bet the consensus
- Superforecasters historically outperform prediction market prices on long-duration markets
- **Expected edge:** 5–15% EV on qualifying markets

### 3. Microstructure / Whale Following
- Large trades (>$5K) on Polymarket are visible on-chain
- Informed traders move prices; follow direction within 30 min of large trades
- Filter out noise by requiring: volume spike + price move in same direction
- **Expected edge:** Variable, but sharp when signal is clean

### 4. Late-Resolution Mean Reversion
- In the final 48 hours before resolution, prices often drift toward 0 or 1 too fast
- Fade over-confident pricing (e.g., "YES at 0.97" before a non-certain event)
- **Expected edge:** 2–4%, requires tight stop-losses

### 5. Base Rate Anchoring on Political Markets
- Political outcomes have well-studied historical base rates (incumbency, polling error distributions)
- When market prices ignore base rates (e.g., polling panic), revert to base rate + model blend
- Use FiveThirtyEight / Nate Silver methodologies as prior
- **Expected edge:** High on specific market types; requires domain expertise

### 6. Event-Driven Volatility Capture
- Around scheduled announcements (Fed, elections, court decisions), markets become mispriced
- Position before the event in the direction implied by adjacent correlated markets
- Exit at resolution or at first 50% profit — don't hold through ambiguity
- **Expected edge:** 8–20% per event, but higher variance

---

## Running the System

```bash
# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env: POLYMARKET_API_KEY, KALSHI_API_KEY, etc.

# Index fresh data
python -m scripts.index

# Run backtests on historical data
python -m scripts.backtest --strategy superforecaster_divergence --from 2024-01-01

# Start live trading (paper mode default)
python -m agents.orchestrator --mode paper

# Launch dashboard
python -m scripts.dashboard
```

---

## Logging & Auditability

Every trade decision must be logged in structured JSON:

```json
{
  "timestamp": "ISO8601",
  "event": "TRADE_DECISION",
  "market_id": "...",
  "decision": "BUY_YES | BUY_NO | PASS",
  "signal_prob": 0.72,
  "market_price": 0.61,
  "expected_value": 0.115,
  "kelly_size_usd": 87.50,
  "risk_verdict": "APPROVED",
  "agent_votes": {
    "market_analyst": "BUY",
    "signal_agent": "BUY",
    "risk_manager": "APPROVED"
  },
  "rationale": "Superforecaster consensus at 0.73 vs market 0.61; Becker base rate 0.68"
}
```

Never delete log entries. Append-only. All logs are the ground truth for calibration and attribution.

---

## Testing Standards

Before merging any agent change:

- `test_risk_manager.py` must pass — especially hard veto conditions
- `test_kelly_sizing.py` must verify fractional Kelly never exceeds 0.25x
- `test_signal_calibration.py` must confirm Brier score on held-out data < 0.15
- `test_execution_protocol.py` must verify order repricing and cancellation logic

Run tests:
```bash
uv run pytest tests/ -v
```

---

## Environment Variables

```bash
# .env.example
POLYMARKET_API_KEY=
POLYMARKET_PRIVATE_KEY=       # for on-chain execution
KALSHI_API_KEY=
KALSHI_API_SECRET=
METACULUS_API_KEY=             # for superforecaster signals
NEWS_API_KEY=                  # for News & Intel Agent
OPENAI_API_KEY=                # optional: for NLP signal extraction
ANTHROPIC_API_KEY=             # for agent LLM calls
MODE=paper                     # paper | live
BANKROLL_USD=1000              # starting capital
LOG_LEVEL=INFO
```

---

## Key References

- **Becker Dataset & Framework:** https://github.com/Jon-Becker/prediction-market-analysis
- **Microstructure Research:** https://jbecker.dev/research/prediction-market-microstructure
- **Polymarket CLOB API:** https://docs.polymarket.com
- **Kalshi API:** https://trading-api.readme.io/reference
- **Metaculus API:** https://www.metaculus.com/api2/
- **Superforecasting:** Tetlock & Gardner (2015) — calibration principles apply directly

---

*These guidelines are working if: trades have documented rationale, risk limits are never silently bypassed, signal calibration improves over time, and P&L attribution is explainable per agent.*
