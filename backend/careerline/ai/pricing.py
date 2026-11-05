# SPDX-License-Identifier: Apache-2.0
"""Per-model prices for cost estimates (USD per million tokens, first-party API rates).

Estimates only: they ignore prompt caching discounts and long-context tiers. Models
not listed (OpenAI-compatible endpoints) show tokens without a cost; Ollama runs locally
and is free.
"""

from __future__ import annotations

# (input $/MTok, output $/MTok). Source: Anthropic's published pricing (2026-10).
PRICES: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
}


def cost_micros(provider: str, model: str, input_tokens: int, output_tokens: int) -> int | None:
    """Estimated cost in micro-dollars (1e-6 USD), or None when unknown."""
    if provider == "ollama":
        return 0
    price = PRICES.get(model)
    if price is None:
        return None
    return round(input_tokens * price[0] + output_tokens * price[1])


def format_usd(micros: int | None) -> str:
    if micros is None:
        return "unknown"
    return f"${micros / 1_000_000:,.4f}"
