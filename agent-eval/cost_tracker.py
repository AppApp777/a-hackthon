"""Token usage and cost tracking for evaluation runs.

Accumulates per-call token usage across an evaluation run, groups by
purpose (user_sim / judge / scorer / diagnosis) and model, and estimates
USD cost based on published model pricing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 主流模型定价（USD per 1M tokens）
MODEL_PRICING: dict[str, dict[str, float]] = {
    # Anthropic
    "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
    "claude-opus-4": {"input": 15.0, "output": 75.0},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    # DeepSeek
    "deepseek-chat": {"input": 0.14, "output": 0.28},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
    # 其他
    "mimo-video-latest": {"input": 0.70, "output": 2.80},
    "kimi-latest": {"input": 1.00, "output": 3.00},
    "minimax-latest": {"input": 0.50, "output": 1.50},
    "MiniMax-M2.7": {"input": 0.50, "output": 1.50},
    "LongCat-2.0-Preview": {"input": 0.50, "output": 1.50},
    # 兜底
    "_default": {"input": 1.0, "output": 3.0},
}


def _get_pricing(model: str) -> dict[str, float]:
    """Look up pricing for a model, matching by prefix if exact key not found."""
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    # Prefix match: "claude-sonnet-4-6-20250514" → "claude-sonnet-4-6"
    for key in MODEL_PRICING:
        if key != "_default" and model.startswith(key):
            return MODEL_PRICING[key]
    return MODEL_PRICING["_default"]


@dataclass
class CostTracker:
    """Accumulates token usage across an evaluation run.

    Thread-safe for append (Python GIL guarantees list.append atomicity).
    """

    calls: list[dict[str, Any]] = field(default_factory=list)

    def record(self, model: str, usage: dict[str, Any], purpose: str = "") -> None:
        """Record a single LLM call's token usage.

        Args:
            model: Model identifier (e.g., "claude-sonnet-4-6").
            usage: Dict with "input_tokens" and "output_tokens" keys.
            purpose: Caller role, e.g., "user_sim", "judge", "scorer", "diagnosis".
        """
        self.calls.append(
            {
                "model": model or "unknown",
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "purpose": purpose,
            }
        )

    @property
    def total_input_tokens(self) -> int:
        return sum(c["input_tokens"] for c in self.calls)

    @property
    def total_output_tokens(self) -> int:
        return sum(c["output_tokens"] for c in self.calls)

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    def estimated_cost_usd(self) -> float:
        """Calculate estimated cost based on model pricing."""
        total = 0.0
        for call in self.calls:
            pricing = _get_pricing(call["model"])
            input_cost = call["input_tokens"] * pricing["input"] / 1_000_000
            output_cost = call["output_tokens"] * pricing["output"] / 1_000_000
            total += input_cost + output_cost
        return total

    def _group_by(self, key: str) -> dict[str, dict[str, Any]]:
        """Group calls by a given key and aggregate."""
        groups: dict[str, dict[str, Any]] = {}
        for call in self.calls:
            k = call.get(key, "unknown") or "unknown"
            if k not in groups:
                groups[k] = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
            groups[k]["calls"] += 1
            groups[k]["input_tokens"] += call["input_tokens"]
            groups[k]["output_tokens"] += call["output_tokens"]
        # Add total_tokens to each group
        for g in groups.values():
            g["total_tokens"] = g["input_tokens"] + g["output_tokens"]
        return groups

    def _group_by_purpose(self) -> dict[str, dict[str, Any]]:
        return self._group_by("purpose")

    def _group_by_model(self) -> dict[str, dict[str, Any]]:
        return self._group_by("model")

    def summary(self) -> dict[str, Any]:
        """Return summary dict for inclusion in trace metadata."""
        return {
            "total_calls": len(self.calls),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_tokens": self.total_tokens,
            "estimated_cost_usd": round(self.estimated_cost_usd(), 6),
            "by_purpose": self._group_by_purpose(),
            "by_model": self._group_by_model(),
        }
