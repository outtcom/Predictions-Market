"""LLM-based probability forecasting using OpenAI and Anthropic ensembles."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from openai import OpenAI

from src.common.logger import log_event
from src.common.prompts import format_binary_prompt


@dataclass
class LlmForecast:
    """Single forecast from one LLM."""

    model: str
    probability_yes: float
    confidence_low: float
    confidence_high: float
    reasoning: str
    key_uncertainties: list[str]
    raw_response: str = ""


@dataclass
class EnsembleForecast:
    """Aggregated forecast from multiple LLMs."""

    median_prob: float
    mean_prob: float
    confidence_low: float
    confidence_high: float
    model_probs: dict[str, float]
    reasoning_summary: str
    sources: list[str]


def _extract_json(text: str) -> dict[str, Any] | None:
    """Extract JSON object from markdown-wrapped or plain text."""
    pattern = r"```(?:json)?\s*([\s\S]*?)```"
    matches = re.findall(pattern, text)
    for candidate in matches:
        try:
            return json.loads(candidate.strip())
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _safe_float(val: Any) -> float:
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.5


def _call_openai(question: str, resolution_criteria: str, model: str = "gpt-4o") -> LlmForecast | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None
    client = OpenAI(api_key=api_key)
    prompt = format_binary_prompt(question, resolution_criteria, today=datetime.now(timezone.utc).isoformat()[:10])

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a calibrated superforecaster. Respond only in the requested JSON format."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        content = resp.choices[0].message.content or ""
        data = _extract_json(content)
        if not data:
            return None
        return LlmForecast(
            model=model,
            probability_yes=_safe_float(data.get("probability_yes")),
            confidence_low=_safe_float(data.get("confidence_low")),
            confidence_high=_safe_float(data.get("confidence_high")),
            reasoning=str(data.get("reasoning", "")),
            key_uncertainties=list(data.get("key_uncertainties", [])),
            raw_response=content,
        )
    except Exception as exc:
        log_event("LLM_FORECAST_ERROR", {"model": model, "error": str(exc)})
        return None


def _call_anthropic(question: str, resolution_criteria: str, model: str = "claude-3-5-sonnet-latest") -> LlmForecast | None:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    try:
        from anthropic import Anthropic
    except ImportError:
        return None

    client = Anthropic(api_key=api_key)
    prompt = format_binary_prompt(question, resolution_criteria, today=datetime.now(timezone.utc).isoformat()[:10])

    try:
        resp = client.messages.create(
            model=model,
            max_tokens=800,
            temperature=0.3,
            system="You are a calibrated superforecaster. Respond only in the requested JSON format.",
            messages=[{"role": "user", "content": prompt}],
        )
        content = resp.content[0].text if resp.content else ""
        data = _extract_json(content)
        if not data:
            return None
        return LlmForecast(
            model=model,
            probability_yes=_safe_float(data.get("probability_yes")),
            confidence_low=_safe_float(data.get("confidence_low")),
            confidence_high=_safe_float(data.get("confidence_high")),
            reasoning=str(data.get("reasoning", "")),
            key_uncertainties=list(data.get("key_uncertainties", [])),
            raw_response=content,
        )
    except Exception as exc:
        log_event("LLM_FORECAST_ERROR", {"model": model, "error": str(exc)})
        return None


def _call_groq(question: str, resolution_criteria: str, model: str = "llama-3.3-70b-versatile") -> LlmForecast | None:
    """Query Groq (fast Llama inference) as a third ensemble member."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return None
    try:
        from openai import OpenAI
    except ImportError:
        return None

    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")
    prompt = format_binary_prompt(question, resolution_criteria, today=datetime.now(timezone.utc).isoformat()[:10])

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a calibrated superforecaster. Respond only in the requested JSON format."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        content = resp.choices[0].message.content or ""
        data = _extract_json(content)
        if not data:
            return None
        return LlmForecast(
            model=f"groq-{model}",
            probability_yes=_safe_float(data.get("probability_yes")),
            confidence_low=_safe_float(data.get("confidence_low")),
            confidence_high=_safe_float(data.get("confidence_high")),
            reasoning=str(data.get("reasoning", "")),
            key_uncertainties=list(data.get("key_uncertainties", [])),
            raw_response=content,
        )
    except Exception as exc:
        log_event("LLM_FORECAST_ERROR", {"model": f"groq-{model}", "error": str(exc)})
        return None


def forecast_ensemble(question: str, resolution_criteria: str = "") -> EnsembleForecast | None:
    """Query GPT-4, Claude, and Groq; return median ensemble forecast."""
    forecasts: list[LlmForecast] = []

    gpt = _call_openai(question, resolution_criteria, model="gpt-4o")
    if gpt:
        forecasts.append(gpt)

    claude = _call_anthropic(question, resolution_criteria, model="claude-sonnet-4-6")
    if claude:
        forecasts.append(claude)

    groq = _call_groq(question, resolution_criteria, model="llama-3.3-70b-versatile")
    if groq:
        forecasts.append(groq)

    if not forecasts:
        return None

    probs = [f.probability_yes for f in forecasts]
    lows = [f.confidence_low for f in forecasts]
    highs = [f.confidence_high for f in forecasts]

    median_prob = sorted(probs)[len(probs) // 2]
    mean_prob = sum(probs) / len(probs)
    ci_low = min(lows)
    ci_high = max(highs)

    model_probs = {f.model: f.probability_yes for f in forecasts}
    reasoning = " | ".join(f"{f.model}: {f.reasoning[:60]}" for f in forecasts)
    sources = [f.model for f in forecasts]

    log_event(
        "LLM_FORECAST_ENSEMBLE",
        {
            "question": question[:60],
            "median_prob": median_prob,
            "model_probs": model_probs,
        },
    )

    return EnsembleForecast(
        median_prob=median_prob,
        mean_prob=mean_prob,
        confidence_low=ci_low,
        confidence_high=ci_high,
        model_probs=model_probs,
        reasoning_summary=reasoning,
        sources=sources,
    )
