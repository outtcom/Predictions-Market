# Signal Quality Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix fake EV signals and long-duration market selection so the system trades short-duration markets with calibrated, corroborated signals.

**Architecture:** Four sequential changes — (1) tighten StrategyAgent to 14-day markets, (2) anchor LLM prompt to market price, (3) cap LLM EV at 300%, (4) require Manifold corroboration for all LLM-only signals. No orchestrator or risk manager changes needed — returning `None` from `_llm_signal` is sufficient to suppress uncorroborated signals.

**Tech Stack:** Python, pytest, `uv run pytest`

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `agents/strategy_agent.py` | Change `max_days_to_resolution` 60 → 14 |
| Modify | `src/common/prompts.py` | Add `market_price` param; inject anchoring section |
| Modify | `src/analysis/llm_forecaster.py` | Thread `market_price` through to prompt call |
| Modify | `agents/signal_agent.py` | EV cap + Manifold corroboration gate in `_llm_signal`; build `manifold_prices` dict in `generate_signals` |
| Modify | `tests/test_signal_calibration.py` | Replace stub; add 5 new tests |

---

### Task 1: Short-Duration Market Filter

**Files:**
- Modify: `agents/strategy_agent.py` (line 27)
- Modify: `tests/test_signal_calibration.py`

- [ ] **Step 1: Write the failing tests**

Replace the entire contents of `tests/test_signal_calibration.py`:

```python
"""Tests for Signal Agent calibration, EV cap, and corroboration gate."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agents.strategy_agent import StrategyAgent
from agents.signal_agent import SignalAgent
from src.common.schemas import Market, Signal


def _make_market(
    *,
    market_id: str = "mkt_1",
    question: str = "Will X happen?",
    platform: str = "polymarket",
    current_yes_price: float = 0.15,
    volume_24h: float = 10_000,
    days_to_resolution: int = 7,
    active: bool = True,
    closed: bool = False,
) -> Market:
    return Market(
        market_id=market_id,
        question=question,
        platform=platform,
        current_yes_price=current_yes_price,
        volume_24h=volume_24h,
        days_to_resolution=days_to_resolution,
        active=active,
        closed=closed,
    )


class TestShortDurationFilter:
    def test_market_at_14_days_is_eligible(self) -> None:
        agent = StrategyAgent(preset="high_ev_divergence")
        market = _make_market(days_to_resolution=14)
        ok, reason = agent.is_eligible(market)
        assert ok, f"Expected eligible, got: {reason}"

    def test_market_at_15_days_is_dropped(self) -> None:
        agent = StrategyAgent(preset="high_ev_divergence")
        market = _make_market(days_to_resolution=15)
        ok, reason = agent.is_eligible(market)
        assert not ok
        assert "above max" in reason
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/test_signal_calibration.py::TestShortDurationFilter -v
```

Expected: `test_market_at_15_days_is_dropped` FAILS (currently max is 60, so 15 days passes).

- [ ] **Step 3: Change `max_days_to_resolution` to 14**

In `agents/strategy_agent.py`, change line 27:

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

- [ ] **Step 4: Run tests to confirm they pass**

```
uv run pytest tests/test_signal_calibration.py::TestShortDurationFilter -v
```

Expected: both tests PASS.

- [ ] **Step 5: Commit**

```
git add agents/strategy_agent.py tests/test_signal_calibration.py
git commit -m "feat: tighten StrategyAgent to 14-day markets for daily P&L"
```

---

### Task 2: Anchored LLM Prompt

**Files:**
- Modify: `src/common/prompts.py`
- Modify: `src/analysis/llm_forecaster.py`

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_signal_calibration.py`:

```python
class TestAnchoredPrompt:
    def test_market_price_appears_in_prompt(self) -> None:
        from src.common.prompts import format_binary_prompt
        prompt = format_binary_prompt(
            question="Will X happen?",
            resolution_criteria="Standard resolution.",
            today="2026-05-24",
            market_price=0.15,
        )
        assert "15.0%" in prompt, "Market price must appear in prompt"

    def test_no_market_price_omits_anchor_section(self) -> None:
        from src.common.prompts import format_binary_prompt
        prompt = format_binary_prompt(
            question="Will X happen?",
            resolution_criteria="",
            today="2026-05-24",
            market_price=None,
        )
        assert "MARKET CONTEXT" not in prompt
```

- [ ] **Step 2: Run tests to confirm they fail**

```
uv run pytest tests/test_signal_calibration.py::TestAnchoredPrompt -v
```

Expected: both FAIL (`format_binary_prompt` doesn't accept `market_price`).

- [ ] **Step 3: Update `src/common/prompts.py`**

Replace the entire file:

```python
"""Structured forecasting prompts adapted from Metaculus tournament best practices."""

from __future__ import annotations

MARKET_ANCHOR_SECTION = """
MARKET CONTEXT:
The prediction market currently prices YES at {market_price:.1%}. This represents
the aggregate belief of real-money traders. Do not generate a probability from
scratch — instead, evaluate whether the market is systematically wrong.

If your estimate differs from the market price by more than 10 percentage points,
you MUST identify a specific, concrete mechanism (e.g., "market hasn't priced in
yesterday's news", "systematic underestimation of incumbent advantage") that
explains why sophisticated traders with real money at stake have it wrong.
Vague disagreement ("I think the probability is higher") is not sufficient.
"""

BINARY_FORECAST_PROMPT = """You are a superforecaster participating in a prediction market. Your task is to estimate the probability that the following question resolves YES.

QUESTION: {question}

RESOLUTION CRITERIA: {resolution_criteria}
{anchor_section}
Follow this structured reasoning process:

1. BASE RATE / OUTSIDE VIEW
   - What is the historical base rate for events like this?
   - What reference class does this event belong to?
   - What would a naive frequentist estimate be?

2. INSIDE VIEW / SPECIFIC FACTORS
   - What are the key causal drivers that would make this resolve YES?
   - What are the key causal drivers that would make this resolve NO?
   - What is the current state of play? (e.g., polls, recent news, momentum)

3. UNCERTAINTY AND TIME HORIZON
   - How much could change between now and resolution?
   - What are the key upcoming dates or information releases?
   - How much of the outcome is already "locked in" vs. still uncertain?

4. SYNTHESIS
   - Combine the base rate and inside-view adjustments into a single probability.
   - Avoid anchoring too heavily on 50% or round numbers.
   - Be granular — use probabilities like 0.23, 0.67, not 0.25, 0.75.

OUTPUT FORMAT (strict JSON):
{{
  "probability_yes": float,        // 0.0 to 1.0, your best estimate
  "confidence_low": float,         // 0.0 to 1.0, lower bound of 80% CI
  "confidence_high": float,        // 0.0 to 1.0, upper bound of 80% CI
  "reasoning": str,                // 2-3 sentence summary of key factors
  "key_uncertainties": [str]       // list of unknowns that could shift probability
}}

Rules:
- probability_yes must be a single float, not a range.
- confidence_low < probability_yes < confidence_high.
- Do not hedge by outputting 0.50 unless the evidence truly warrants it.
- Today is {today}.
"""


def format_binary_prompt(
    question: str,
    resolution_criteria: str = "",
    today: str = "",
    market_price: float | None = None,
) -> str:
    """Format the binary forecasting prompt with market details."""
    anchor_section = (
        MARKET_ANCHOR_SECTION.format(market_price=market_price)
        if market_price is not None
        else ""
    )
    return BINARY_FORECAST_PROMPT.format(
        question=question,
        resolution_criteria=resolution_criteria or "Standard resolution by the market platform.",
        today=today or "the current date",
        anchor_section=anchor_section,
    )
```

- [ ] **Step 4: Thread `market_price` through `llm_forecaster.py`**

In `src/analysis/llm_forecaster.py`, update three functions. First, update each `_call_*` function signature and the `format_binary_prompt` call inside it. Then update `forecast_ensemble`.

In `_call_openai` (line 73), change the signature and prompt call:
```python
def _call_openai(question: str, resolution_criteria: str, model: str = "gpt-4o", market_price: float | None = None) -> LlmForecast | None:
    ...
    prompt = format_binary_prompt(question, resolution_criteria, today=datetime.now(timezone.utc).isoformat()[:10], market_price=market_price)
```

In `_call_anthropic` (line 108), same change:
```python
def _call_anthropic(question: str, resolution_criteria: str, model: str = "claude-3-5-sonnet-latest", market_price: float | None = None) -> LlmForecast | None:
    ...
    prompt = format_binary_prompt(question, resolution_criteria, today=datetime.now(timezone.utc).isoformat()[:10], market_price=market_price)
```

In `_call_groq` (line 146), same change:
```python
def _call_groq(question: str, resolution_criteria: str, model: str = "llama-3.3-70b-versatile", market_price: float | None = None) -> LlmForecast | None:
    ...
    prompt = format_binary_prompt(question, resolution_criteria, today=datetime.now(timezone.utc).isoformat()[:10], market_price=market_price)
```

In `forecast_ensemble` (line 187), add `market_price` param and pass it through:
```python
def forecast_ensemble(question: str, resolution_criteria: str = "", market_price: float | None = None) -> EnsembleForecast | None:
    """Query GPT-4, Claude, and Groq; return median ensemble forecast."""
    forecasts: list[LlmForecast] = []

    gpt = _call_openai(question, resolution_criteria, model="gpt-4o", market_price=market_price)
    if gpt:
        forecasts.append(gpt)

    claude = _call_anthropic(question, resolution_criteria, model="claude-sonnet-4-6", market_price=market_price)
    if claude:
        forecasts.append(claude)

    groq = _call_groq(question, resolution_criteria, model="llama-3.3-70b-versatile", market_price=market_price)
    if groq:
        forecasts.append(groq)
    ...  # rest unchanged
```

- [ ] **Step 5: Run tests**

```
uv run pytest tests/test_signal_calibration.py::TestAnchoredPrompt -v
```

Expected: both PASS.

- [ ] **Step 6: Commit**

```
git add src/common/prompts.py src/analysis/llm_forecaster.py tests/test_signal_calibration.py
git commit -m "feat: anchor LLM prompt to market price to reduce calibration errors"
```

---

### Task 3: EV Cap on LLM Signals

**Files:**
- Modify: `agents/signal_agent.py`
- Modify: `tests/test_signal_calibration.py`

- [ ] **Step 1: Write the failing test**

Add this class to `tests/test_signal_calibration.py`:

```python
class TestEvCap:
    def test_ev_above_300pct_returns_none(self) -> None:
        """LLM returning 12% on a 1% market (EV=11x) must be dropped."""
        from src.analysis.llm_forecaster import EnsembleForecast

        agent = SignalAgent(use_llm=True)
        market = _make_market(current_yes_price=0.01)

        fake_ensemble = EnsembleForecast(
            median_prob=0.12,
            mean_prob=0.12,
            confidence_low=0.06,
            confidence_high=0.20,
            model_probs={"gpt-4o": 0.12},
            reasoning_summary="Seems possible",
            sources=["gpt-4o"],
        )

        with patch("agents.signal_agent.forecast_ensemble", return_value=fake_ensemble):
            result = agent._llm_signal(market, manifold_prices={})

        assert result is None, f"Expected None (EV cap), got: {result}"

    def test_ev_below_300pct_with_corroboration_returns_signal(self) -> None:
        """LLM returning 25% on a 15% market (EV=0.67x) with Manifold agreeing returns a signal."""
        from src.analysis.llm_forecaster import EnsembleForecast

        agent = SignalAgent(use_llm=True)
        market = _make_market(current_yes_price=0.15)

        fake_ensemble = EnsembleForecast(
            median_prob=0.25,
            mean_prob=0.25,
            confidence_low=0.18,
            confidence_high=0.35,
            model_probs={"gpt-4o": 0.25},
            reasoning_summary="Underpriced",
            sources=["gpt-4o"],
        )

        with patch("agents.signal_agent.forecast_ensemble", return_value=fake_ensemble):
            result = agent._llm_signal(
                market,
                manifold_prices={market.market_id: 0.22},  # Manifold also above market
            )

        assert result is not None, "Signal below EV cap with Manifold corroboration must be returned"
        assert "manifold_corroborated" in result.signal_sources
```

> **Note:** The second test for `test_ev_below_300pct_is_not_capped` will be completed in Task 4 once the corroboration gate is in place. For now just write and run the first test.

- [ ] **Step 2: Run the first test to confirm it fails**

```
uv run pytest tests/test_signal_calibration.py::TestEvCap::test_ev_above_300pct_returns_none -v
```

Expected: FAIL — `_llm_signal` doesn't accept `manifold_prices` arg yet and doesn't have EV cap.

- [ ] **Step 3: Add `manifold_prices` param and EV cap to `_llm_signal`**

In `agents/signal_agent.py`, update the `_llm_signal` method signature and add the EV cap. The method currently ends at line 119. Replace the full method:

```python
_MAX_LLM_EV = 3.0  # 300% — real edge doesn't exceed this; higher means LLM miscalibration

def _llm_signal(self, market: Market, manifold_prices: dict[str, float] | None = None) -> Signal | None:
    """Generate a signal via LLM ensemble for a single market."""
    if manifold_prices is None:
        manifold_prices = {}

    ensemble = forecast_ensemble(
        question=market.question,
        resolution_criteria=market.raw.get("description", "") if market.raw else "",
        market_price=market.current_yes_price,
    )
    if ensemble is None:
        return None

    divergence = abs(ensemble.median_prob - market.current_yes_price)
    if divergence < self.divergence_threshold:
        return None

    # Layer 1: EV cap — reject signals where LLM disagrees by > 300x the market price
    ev = (ensemble.median_prob - market.current_yes_price) / market.current_yes_price if market.current_yes_price > 0 else 0.0
    if ev > _MAX_LLM_EV:
        log_event(
            "SIGNAL_LLM_EV_CAPPED",
            {
                "market_id": market.market_id,
                "signal_prob": ensemble.median_prob,
                "market_price": market.current_yes_price,
                "ev": round(ev, 2),
                "reason": f"LLM EV {ev:.1f}x exceeds {_MAX_LLM_EV}x cap",
            },
        )
        return None

    # Layer 2: Corroboration gate — require Manifold to agree (same direction, ≥3%)
    _MANIFOLD_MIN_CORROBORATION = 0.03
    manifold_price = manifold_prices.get(market.market_id)
    if manifold_price is not None:
        manifold_divergence = manifold_price - market.current_yes_price
        llm_divergence = ensemble.median_prob - market.current_yes_price
        same_direction = (manifold_divergence * llm_divergence) > 0
        sufficient = abs(manifold_divergence) >= _MANIFOLD_MIN_CORROBORATION
        corroborated = same_direction and sufficient
    else:
        corroborated = False

    if not corroborated:
        log_event(
            "SIGNAL_LLM_NO_CORROBORATION",
            {
                "market_id": market.market_id,
                "signal_prob": ensemble.median_prob,
                "market_price": market.current_yes_price,
                "manifold_price": manifold_price,
                "reason": "no Manifold coverage" if manifold_price is None else "Manifold disagrees or insufficient divergence",
            },
        )
        return None

    signal_strength = "strong" if divergence > 0.10 else "moderate"

    return Signal(
        market_id=market.market_id,
        signal_prob=ensemble.median_prob,
        confidence_interval=(ensemble.confidence_low, ensemble.confidence_high),
        signal_sources=ensemble.sources + ["llm_ensemble", "manifold_corroborated"],
        staleness_hours=0.0,
        signal_strength=signal_strength,
        notes=(
            f"LLM ensemble median={ensemble.median_prob:.2%} "
            f"vs market={market.current_yes_price:.2%} "
            f"(divergence={divergence:.2%}, EV={ev:.1f}x). "
            f"Manifold corroboration: {manifold_price:.2%}. "
            f"Models: {ensemble.model_probs}"
        ),
    )
```

Also add `_MAX_LLM_EV = 3.0` as a module-level constant just before the class definition.

- [ ] **Step 4: Run EV cap test**

```
uv run pytest tests/test_signal_calibration.py::TestEvCap::test_ev_above_300pct_returns_none -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```
git add agents/signal_agent.py tests/test_signal_calibration.py
git commit -m "feat: add EV cap (300%) and corroboration gate skeleton to _llm_signal"
```

---

### Task 4: Manifold Price Index + Wire Corroboration Gate

**Files:**
- Modify: `agents/signal_agent.py` (`generate_signals` method)
- Modify: `tests/test_signal_calibration.py`

- [ ] **Step 1: Write the failing tests**

Add this class to `tests/test_signal_calibration.py`:

```python
class TestCorroborationGate:
    def _agent_with_mocked_llm(self, signal_prob: float, market_price: float) -> tuple[SignalAgent, Market]:
        from src.analysis.llm_forecaster import EnsembleForecast
        from unittest.mock import patch

        market = _make_market(market_id="mkt_test", current_yes_price=market_price)
        agent = SignalAgent(use_llm=True)
        return agent, market

    def test_no_manifold_coverage_returns_none(self) -> None:
        """LLM signal with no Manifold match must be suppressed."""
        from src.analysis.llm_forecaster import EnsembleForecast

        agent = SignalAgent(use_llm=True)
        market = _make_market(current_yes_price=0.15)

        fake_ensemble = EnsembleForecast(
            median_prob=0.25,
            mean_prob=0.25,
            confidence_low=0.18,
            confidence_high=0.35,
            model_probs={"gpt-4o": 0.25},
            reasoning_summary="Underpriced",
            sources=["gpt-4o"],
        )

        with patch("agents.signal_agent.forecast_ensemble", return_value=fake_ensemble):
            result = agent._llm_signal(market, manifold_prices={})

        assert result is None, "No Manifold coverage should suppress LLM signal"

    def test_manifold_agreeing_returns_signal(self) -> None:
        """LLM + Manifold both above market → signal returned."""
        from src.analysis.llm_forecaster import EnsembleForecast

        agent = SignalAgent(use_llm=True)
        market = _make_market(market_id="mkt_test", current_yes_price=0.15)

        fake_ensemble = EnsembleForecast(
            median_prob=0.25,
            mean_prob=0.25,
            confidence_low=0.18,
            confidence_high=0.35,
            model_probs={"gpt-4o": 0.25},
            reasoning_summary="Underpriced",
            sources=["gpt-4o"],
        )

        with patch("agents.signal_agent.forecast_ensemble", return_value=fake_ensemble):
            result = agent._llm_signal(
                market,
                manifold_prices={"mkt_test": 0.22},  # Manifold at 22%, market at 15% → same direction
            )

        assert result is not None, "Corroborated LLM signal should be returned"
        assert result.signal_strength in ("moderate", "strong")
        assert "manifold_corroborated" in result.signal_sources

    def test_manifold_disagreeing_returns_none(self) -> None:
        """LLM above market but Manifold below market → signal suppressed."""
        from src.analysis.llm_forecaster import EnsembleForecast

        agent = SignalAgent(use_llm=True)
        market = _make_market(market_id="mkt_test", current_yes_price=0.15)

        fake_ensemble = EnsembleForecast(
            median_prob=0.25,
            mean_prob=0.25,
            confidence_low=0.18,
            confidence_high=0.35,
            model_probs={"gpt-4o": 0.25},
            reasoning_summary="Underpriced",
            sources=["gpt-4o"],
        )

        with patch("agents.signal_agent.forecast_ensemble", return_value=fake_ensemble):
            result = agent._llm_signal(
                market,
                manifold_prices={"mkt_test": 0.10},  # Manifold at 10%, market at 15% → opposite direction
            )

        assert result is None, "Disagreeing Manifold should suppress LLM signal"
```

- [ ] **Step 2: Run to confirm all three tests pass**

```
uv run pytest tests/test_signal_calibration.py::TestCorroborationGate -v
```

Expected: all three PASS — `_llm_signal` was fully implemented in Task 3. If any fail, fix `_llm_signal` before continuing to Step 3.

- [ ] **Step 3: Wire `manifold_prices` into `generate_signals`**

In `agents/signal_agent.py`, update `generate_signals` to build the `manifold_prices` lookup and pass it to the LLM candidates loop. The current method is at line 227. Replace it:

```python
def generate_signals(self, markets: list[Market]) -> list[Signal]:
    """Generate signals from all available sources and deduplicate by market."""
    all_signals: dict[str, Signal] = {}

    # Source 1: Metaculus superforecaster consensus
    for sig in self._metaculus_signals(markets):
        all_signals[sig.market_id] = sig

    # Source 2: Manifold cross-platform arbitrage (only for Polymarket markets)
    poly_markets = [m for m in markets if m.platform == "polymarket"]
    manifold_signals = self._manifold_arbitrage_signals(poly_markets)

    # Build Manifold price lookup for corroboration gate: {polymarket_id: manifold_price}
    manifold_prices: dict[str, float] = {
        sig.market_id: sig.signal_prob for sig in manifold_signals
    }

    for sig in manifold_signals:
        existing = all_signals.get(sig.market_id)
        if existing:
            blended = (existing.signal_prob + sig.signal_prob) / 2.0
            # find the market to get current_yes_price for signal_strength calculation
            market_price = next(
                (m.current_yes_price for m in markets if m.market_id == sig.market_id),
                0.0,
            )
            sig = Signal(
                market_id=sig.market_id,
                signal_prob=blended,
                confidence_interval=existing.confidence_interval,
                signal_sources=existing.signal_sources + sig.signal_sources,
                staleness_hours=0.0,
                signal_strength="strong" if abs(blended - market_price) > 0.10 else "moderate",
                notes=f"BLENDED: {existing.notes} | {sig.notes}",
            )
        all_signals[sig.market_id] = sig

    # Source 3: LLM ensemble (fallback for markets not covered by Metaculus or Manifold)
    if self.use_llm:
        llm_candidates = [
            m for m in markets
            if m.market_id not in all_signals
            and m.volume_24h >= 10_000
            and not m.closed
            and m.platform != "manifold"
        ]
        llm_candidates = sorted(llm_candidates, key=lambda x: x.volume_24h, reverse=True)[:10]
        for market in llm_candidates:
            sig = self._llm_signal(market, manifold_prices=manifold_prices)
            if sig:
                all_signals[sig.market_id] = sig

    return list(all_signals.values())
```

- [ ] **Step 4: Run all signal calibration tests**

```
uv run pytest tests/test_signal_calibration.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Run the full test suite to check for regressions**

```
uv run pytest tests/ -v
```

Expected: all tests pass. If `test_risk_manager.py`, `test_kelly_sizing.py`, or `test_execution_protocol.py` fail, fix them before committing.

- [ ] **Step 6: Commit**

```
git add agents/signal_agent.py tests/test_signal_calibration.py
git commit -m "feat: wire Manifold corroboration gate — LLM signals require Manifold agreement"
```

---

### Task 5: Smoke Test the Full Pipeline

**Files:** None modified — this is a dry-run verification.

- [ ] **Step 1: Run a dry run against live Polymarket data**

```
uv run python scripts/dry_run.py
```

Watch the logs output. Verify:
1. `STRATEGY_AGENT_FILTER` shows `eligible` markets with `days_to_resolution ≤ 14`
2. `SIGNAL_LLM_EV_CAPPED` events appear for any market where LLM gave extreme EV
3. `SIGNAL_LLM_NO_CORROBORATION` events appear for LLM signals without Manifold match
4. Any `TRADE_DECISION` events have `signal_sources` containing `"manifold_corroborated"`

- [ ] **Step 2: Check events log for the new event types**

```
python -c "
import json
lines = open('logs/events.jsonl').readlines()
relevant = [e for e in (json.loads(l) for l in lines[-200:])
            if e.get('event') in ('SIGNAL_LLM_EV_CAPPED', 'SIGNAL_LLM_NO_CORROBORATION',
                                   'STRATEGY_AGENT_FILTER', 'TRADE_DECISION')]
for e in relevant:
    print(json.dumps(e, indent=2))
"
```

- [ ] **Step 3: Commit final state**

If the dry run looks clean (no Python errors, new log events appear as expected):

```
git add logs/events.jsonl
git commit -m "chore: dry run log after signal quality fix"
```

---

## Notes

- If `eligible` market count after the 14-day filter drops below 3 per run consistently, raise `max_days_to_resolution` to 21 in `agents/strategy_agent.py`. Monitor `STRATEGY_AGENT_FILTER` log.
- The LLM is now only actionable when Manifold corroborates. If Manifold has zero coverage on a market type, the LLM is silent on that type — which is correct behavior.
- Metaculus signals (Source 1) are unaffected — they remain actionable without corroboration since Metaculus is already a calibrated human-forecaster source.
