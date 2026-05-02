"""Structured forecasting prompts adapted from Metaculus tournament best practices."""

from __future__ import annotations


BINARY_FORECAST_PROMPT = """You are a superforecaster participating in a prediction market. Your task is to estimate the probability that the following question resolves YES.

QUESTION: {question}

RESOLUTION CRITERIA: {resolution_criteria}

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


def format_binary_prompt(question: str, resolution_criteria: str = "", today: str = "") -> str:
    """Format the binary forecasting prompt with market details."""
    return BINARY_FORECAST_PROMPT.format(
        question=question,
        resolution_criteria=resolution_criteria or "Standard resolution by the market platform.",
        today=today or "the current date",
    )
