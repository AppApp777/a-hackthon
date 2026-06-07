"""Tests for cost_tracker module."""

import sys
from pathlib import Path

# Ensure agent-eval root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cost_tracker import MODEL_PRICING, CostTracker, _get_pricing

# ── _get_pricing ──


class TestGetPricing:
    def test_exact_match(self):
        p = _get_pricing("claude-sonnet-4-6")
        assert p["input"] == 3.0
        assert p["output"] == 15.0

    def test_prefix_match(self):
        """Model with version suffix should match the base model."""
        p = _get_pricing("claude-sonnet-4-6-20250514")
        assert p["input"] == 3.0
        assert p["output"] == 15.0

    def test_unknown_model_uses_default(self):
        p = _get_pricing("totally-unknown-model-v9")
        assert p == MODEL_PRICING["_default"]

    def test_deepseek_chat(self):
        p = _get_pricing("deepseek-chat")
        assert p["input"] == 0.14
        assert p["output"] == 0.28


# ── CostTracker: basic record/totals ──


class TestCostTrackerBasics:
    def test_empty_tracker(self):
        ct = CostTracker()
        assert ct.total_input_tokens == 0
        assert ct.total_output_tokens == 0
        assert ct.total_tokens == 0
        assert ct.estimated_cost_usd() == 0.0
        assert ct.calls == []

    def test_record_single_call(self):
        ct = CostTracker()
        ct.record("claude-sonnet-4-6", {"input_tokens": 1000, "output_tokens": 500}, "judge")
        assert len(ct.calls) == 1
        assert ct.total_input_tokens == 1000
        assert ct.total_output_tokens == 500
        assert ct.total_tokens == 1500

    def test_record_multiple_calls(self):
        ct = CostTracker()
        ct.record("claude-sonnet-4-6", {"input_tokens": 1000, "output_tokens": 200}, "user_sim")
        ct.record("gpt-4o", {"input_tokens": 500, "output_tokens": 300}, "judge")
        ct.record("deepseek-chat", {"input_tokens": 2000, "output_tokens": 1000}, "scorer")
        assert ct.total_input_tokens == 3500
        assert ct.total_output_tokens == 1500
        assert ct.total_tokens == 5000
        assert len(ct.calls) == 3

    def test_record_missing_usage_keys(self):
        """Usage dict without expected keys should default to 0."""
        ct = CostTracker()
        ct.record("gpt-4o", {}, "test")
        assert ct.total_input_tokens == 0
        assert ct.total_output_tokens == 0

    def test_record_none_model_becomes_unknown(self):
        ct = CostTracker()
        ct.record("", {"input_tokens": 100, "output_tokens": 50}, "test")
        assert ct.calls[0]["model"] == "unknown"


# ── CostTracker: estimated_cost_usd ──


class TestCostEstimation:
    def test_known_model_cost(self):
        ct = CostTracker()
        # claude-sonnet-4-6: $3/M input, $15/M output
        ct.record("claude-sonnet-4-6", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
        cost = ct.estimated_cost_usd()
        assert cost == 3.0 + 15.0  # $18.0

    def test_small_token_count(self):
        ct = CostTracker()
        # 1000 input tokens of claude-sonnet-4-6: 1000 * 3.0 / 1M = 0.003
        ct.record("claude-sonnet-4-6", {"input_tokens": 1000, "output_tokens": 0})
        cost = ct.estimated_cost_usd()
        assert abs(cost - 0.003) < 1e-9

    def test_unknown_model_uses_default_pricing(self):
        ct = CostTracker()
        # _default: $1/M input, $3/M output
        ct.record("some-unknown-model", {"input_tokens": 1_000_000, "output_tokens": 1_000_000})
        cost = ct.estimated_cost_usd()
        assert cost == 1.0 + 3.0  # $4.0

    def test_mixed_models_cost(self):
        ct = CostTracker()
        ct.record("deepseek-chat", {"input_tokens": 1_000_000, "output_tokens": 0})
        ct.record("gpt-4o", {"input_tokens": 0, "output_tokens": 1_000_000})
        cost = ct.estimated_cost_usd()
        # deepseek-chat input: 0.14, gpt-4o output: 10.0
        assert abs(cost - 10.14) < 1e-9

    def test_empty_calls_zero_cost(self):
        ct = CostTracker()
        assert ct.estimated_cost_usd() == 0.0


# ── CostTracker: summary ──


class TestSummary:
    def test_empty_summary(self):
        ct = CostTracker()
        s = ct.summary()
        assert s["total_calls"] == 0
        assert s["total_input_tokens"] == 0
        assert s["total_output_tokens"] == 0
        assert s["total_tokens"] == 0
        assert s["estimated_cost_usd"] == 0.0
        assert s["by_purpose"] == {}
        assert s["by_model"] == {}

    def test_summary_structure(self):
        ct = CostTracker()
        ct.record("claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50}, "user_sim")
        ct.record("claude-sonnet-4-6", {"input_tokens": 200, "output_tokens": 100}, "judge")
        ct.record("gpt-4o", {"input_tokens": 300, "output_tokens": 150}, "judge")

        s = ct.summary()
        assert s["total_calls"] == 3
        assert s["total_input_tokens"] == 600
        assert s["total_output_tokens"] == 300
        assert s["total_tokens"] == 900
        assert s["estimated_cost_usd"] > 0

    def test_by_purpose_grouping(self):
        ct = CostTracker()
        ct.record("claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50}, "user_sim")
        ct.record("claude-sonnet-4-6", {"input_tokens": 200, "output_tokens": 100}, "judge")
        ct.record("gpt-4o", {"input_tokens": 300, "output_tokens": 150}, "judge")

        s = ct.summary()
        by_purpose = s["by_purpose"]
        assert "user_sim" in by_purpose
        assert "judge" in by_purpose
        assert by_purpose["user_sim"]["calls"] == 1
        assert by_purpose["user_sim"]["input_tokens"] == 100
        assert by_purpose["user_sim"]["output_tokens"] == 50
        assert by_purpose["user_sim"]["total_tokens"] == 150
        assert by_purpose["judge"]["calls"] == 2
        assert by_purpose["judge"]["input_tokens"] == 500
        assert by_purpose["judge"]["output_tokens"] == 250
        assert by_purpose["judge"]["total_tokens"] == 750

    def test_by_model_grouping(self):
        ct = CostTracker()
        ct.record("claude-sonnet-4-6", {"input_tokens": 100, "output_tokens": 50}, "user_sim")
        ct.record("claude-sonnet-4-6", {"input_tokens": 200, "output_tokens": 100}, "judge")
        ct.record("gpt-4o", {"input_tokens": 300, "output_tokens": 150}, "judge")

        s = ct.summary()
        by_model = s["by_model"]
        assert "claude-sonnet-4-6" in by_model
        assert "gpt-4o" in by_model
        assert by_model["claude-sonnet-4-6"]["calls"] == 2
        assert by_model["claude-sonnet-4-6"]["input_tokens"] == 300
        assert by_model["gpt-4o"]["calls"] == 1
        assert by_model["gpt-4o"]["input_tokens"] == 300

    def test_empty_purpose_grouped_as_unknown(self):
        """Calls with empty purpose string should group under 'unknown'."""
        ct = CostTracker()
        ct.record("gpt-4o", {"input_tokens": 100, "output_tokens": 50}, "")
        ct.record("gpt-4o", {"input_tokens": 200, "output_tokens": 100})

        s = ct.summary()
        by_purpose = s["by_purpose"]
        # Both have empty purpose → grouped under "unknown"
        assert "unknown" in by_purpose
        assert by_purpose["unknown"]["calls"] == 2

    def test_cost_rounded(self):
        ct = CostTracker()
        ct.record("claude-sonnet-4-6", {"input_tokens": 1, "output_tokens": 1})
        s = ct.summary()
        # Rounded to 6 decimal places
        cost = s["estimated_cost_usd"]
        assert isinstance(cost, float)
        assert cost == round(cost, 6)


# ── CostTracker: llm.py callback integration ──


class TestLlmCallback:
    def test_callback_wiring(self):
        """Verify the callback signature matches what llm.py sends."""
        ct = CostTracker()
        # Simulate what llm.py does: callback(model, usage)
        ct.record("claude-sonnet-4-6", {"input_tokens": 500, "output_tokens": 200})
        assert ct.total_tokens == 700

    def test_callback_with_purpose(self):
        """Verify purpose parameter works when set."""
        ct = CostTracker()
        ct.record("gpt-4o-mini", {"input_tokens": 100, "output_tokens": 50}, purpose="diagnosis")
        assert ct.calls[0]["purpose"] == "diagnosis"
