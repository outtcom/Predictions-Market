# Signal Quality Fix — Design Spec

**Date:** 2026-05-24
**Problem:** Two compounding issues prevent daily profits: (1) LLM ensemble produces uncalibrated probability estimates, generating fake EV values of 900–1100% on sports markets. (2) The system bets on championship winner markets (NBA Finals, FIFA World Cup) resolving months out — no daily P&L is possible even with perfect signals.

**Root cause:** Metaculus and Manifold matching thresholds are too strict (60%/65% keyword overlap), so sports tournament markets never get a real signal and fall through to the LLM. The LLM generates probabilities in a vacuum without anchoring to the market price, producing calibration errors of 10–25x on tail events. Meanwhile, `max_days_to_resolution=60` allows markets that won't resolve for 2 months.

**Fix:** Three-layer signal defense + short-duration market filter.

---

## Files Changed

| Action | Path |
|---|---|
| Modify | `src/common/prompts.py` |
| Modify | `src/analysis/llm_forecaster.py` |
| Modify | `agents/signal_agent.py` |
| Modify | `agents/strategy_agent.py` |
| Modify | `tests/test_signal_calibration.py` |

No other files are touched. Risk manager, orchestrator, execution agent are unchanged.

---

## Layer 1: Anchored LLM Prompt

**File:** `src/common/prompts.py`

Add `market_price: float` as a required parameter to `format_binary_prompt`. Add a market-anchoring section to the prompt template — placed before the structured reasoning steps — that:

1. Shows the LLM the current market price (e.g., `1.2%`)
2. Explains that market prices aggregate real-money trader beliefs
3. Instructs the LLM to explain the *specific mechanism* behind any disagreement > 10 percentage points

New prompt section (inserted after the question/resolution block, before the reasoning steps):

```
MARKET CONTEXT:
The prediction market currently prices YES at {market_price:.1%}. This represents
the aggregate belief of real-money traders. Do not generate a probability from
scratch — instead, evaluate whether the market is systematically wrong.

If your estimate differs from the market price by more than 10 percentage points,
you MUST identify a specific, concrete mechanism (e.g., "market hasn't priced in
yesterday's news", "systematic underestimation of incumbent advantage") that
explains why sophisticated traders with real money at stake have it wrong.
Vague disagreement ("I think the probability is higher") is not sufficient.
```

**Signature change:** `format_binary_prompt(question, resolution_criteria, today, market_price)` — `market_price` defaults to `None`, in which case the anchoring section is omitted (preserves backward compatibility with any tests that don't pass a price).

**Caller change:** `llm_forecaster.forecast_ensemble` gains a `market_price: float | None = None` parameter and passes it through to `format_binary_prompt`. `SignalAgent._llm_signal` passes `market.current_yes_price`.

---

## Layer 2: EV Cap on LLM-Only Signals

**File:** `agents/signal_agent.py`, method `_llm_signal`

After computing the signal from the LLM ensemble, add a guard before returning:

```python
MAX_LLM_EV = 3.0  # 300% — real edge doesn't exceed this
ev = (signal_prob - market_price) / market_price if market_price > 0 else 0.0
if ev > MAX_LLM_EV:
    log_event("SIGNAL_AGENT_LLM_CAPPED", {
        "market_id": market.market_id,
        "signal_prob": signal_prob,
        "market_price": market_price,
        "ev": ev,
        "reason": f"LLM EV {ev:.1f}x exceeds 3x cap — likely miscalibration",
    })
    return None
```

This kills the 900–1100% EV signals outright. The threshold 3.0 (300%) is conservative — genuine edge in prediction markets rarely exceeds 50%, and 300% would represent an extraordinary edge that an LLM alone cannot reliably identify.

---

## Layer 3: Manifold Corroboration Gate

**File:** `agents/signal_agent.py`, method `generate_signals`

**Problem:** Currently, Manifold price matches are emitted as signals and added to `all_signals`, then forgotten. There is no way for the LLM gate to check "does Manifold agree with this LLM estimate?"

**Fix:** Build a `manifold_prices: dict[str, float]` lookup during the Manifold pass. Store the matched Manifold price for every Polymarket market that was successfully matched. This requires no extra API calls — it's just retaining the data that's already computed.

Then in `_llm_signal`, accept a `manifold_prices` argument (default `{}`). After the EV cap check, apply:

```python
MANIFOLD_MIN_CORROBORATION = 0.03  # Manifold must diverge from market by ≥ 3% same direction

manifold_price = manifold_prices.get(market.market_id)
has_corroboration = False
if manifold_price is not None:
    manifold_divergence = manifold_price - market.current_yes_price  # signed
    llm_divergence = signal_prob - market.current_yes_price          # signed
    # Same direction AND Manifold also shows meaningful divergence
    if manifold_divergence * llm_divergence > 0 and abs(manifold_divergence) >= MANIFOLD_MIN_CORROBORATION:
        has_corroboration = True

if not has_corroboration:
    # Log but mark as insufficient — orchestrator will skip
    log_event("SIGNAL_AGENT_NO_CORROBORATION", {
        "market_id": market.market_id,
        "signal_prob": signal_prob,
        "market_price": market.current_yes_price,
        "manifold_price": manifold_price,
    })
    # Return signal with downgraded strength so it's visible in logs but not traded
    return Signal(
        ...,
        signal_strength="insufficient_corroboration",
        notes=f"LLM-only, no Manifold corroboration. {signal.notes}",
    )
```

**Orchestrator/RiskManager change:** The orchestrator already passes signals through the risk manager. Add one check: skip any signal where `signal_strength == "insufficient_corroboration"`. This is a one-line guard in the orchestrator's cycle loop.

**When Manifold has no coverage:** If `manifold_price is None` (Manifold doesn't have this market), the signal is also downgraded to `insufficient_corroboration`. LLM-only signals are not actionable without a second market price to validate against. This means the LLM is now useful only when Manifold also covers the market — which is the correct behavior.

---

## Data Flow After Fix

```
Polymarket markets (filtered by StrategyAgent)
    │
    ├─► Metaculus signals → all_signals dict
    │
    ├─► Manifold signals → all_signals dict
    │                    + manifold_prices lookup dict
    │
    └─► LLM signals (fallback: markets not in all_signals):
            → anchored prompt (market price passed in)
            → EV cap: ev > 300%? → drop, log SIGNAL_AGENT_LLM_CAPPED
            → corroboration: manifold agrees (same direction, ≥3%)? → actionable
                           : no manifold coverage or disagrees? → insufficient_corroboration
```

---

## Tests

**File:** `tests/test_signal_calibration.py`

Add two new test cases (existing tests are not modified):

**Test 1 — EV cap drops miscalibrated signal:**
Mock a market at price 0.01 (1%). LLM returns `signal_prob=0.12` (12%). EV = (0.12 - 0.01) / 0.01 = 11x > 3x cap. Assert `_llm_signal` returns `None`.

**Test 2 — Corroboration gate blocks unconfirmed LLM signal:**
Mock a market at price 0.15. LLM returns `signal_prob=0.28` (EV = 0.87x, passes cap). `manifold_prices = {}` (no coverage). Assert returned signal has `signal_strength == "insufficient_corroboration"`.

**Test 3 — Valid signal passes all three layers:**
Mock a market at price 0.15. LLM returns `signal_prob=0.25` (EV = 0.67x). `manifold_prices = {market_id: 0.22}` (Manifold shows same direction, +7pp divergence). Assert returned signal has `signal_strength in ("moderate", "strong")`.

---

## What This Fixes

| Symptom | Fix |
|---|---|
| EV 900–1100% on sports longshots | EV cap (Layer 2) drops these outright |
| LLM anchors on round numbers (12%, 18%) | Anchored prompt (Layer 1) forces market-relative reasoning |
| Single LLM source driving all trades | Corroboration gate (Layer 3) requires Manifold agreement |
| Fake "superforecaster consensus" label | Signals correctly labeled `insufficient_corroboration` when unverified |

## Layer 4: Short-Duration Market Filter

**File:** `agents/strategy_agent.py`

**Problem:** `HIGH_EV_DIVERGENCE` preset allows `max_days_to_resolution=60`. Championship markets (NBA Finals, FIFA World Cup winners) pass this filter and consume LLM budget, but resolve months out. Daily profits require markets that resolve within 1–14 days.

**Fix:** Change `max_days_to_resolution` from `60` to `14` in the `HIGH_EV_DIVERGENCE` preset. One-line change.

```python
HIGH_EV_DIVERGENCE = StrategyPreset(
    name="high_ev_divergence",
    min_market_price=0.10,
    max_market_price=0.90,
    min_volume_24h=5_000,
    min_days_to_resolution=1,
    max_days_to_resolution=14,   # was 60
)
```

**Effect:** Markets resolving in ≤14 days include: daily economic releases (CPI, jobs, Fed), weekly political events, individual sports game outcomes, breaking news questions. These generate realized P&L on a daily-to-weekly cadence rather than waiting months.

**If eligible market count drops too low** (< 3 markets per run), raise to 21 days. Monitor `STRATEGY_AGENT_FILTER` log for `eligible` count. Target: 5–15 eligible markets per run.

---

## What This Does NOT Fix

- Position deduplication (same market re-entered on consecutive runs) — separate task
- Resolution tracking (positions never marked as won/lost) — separate task
- SmartMoney vol_z always 0.0 — separate task
- Metaculus matching for sports markets (matching threshold still 60%; sports are rarely on Metaculus anyway)
