"""Tests for Phase 2.2: LLM Judge CoT enforcement + temperature 0.

Verifies that:
1. All LLM judge calls use temperature=0
2. Judge results without reasoning are flagged/downgraded
3. scorer.py uses temperature=0 (not 0.3)
"""

import re

from scorer_outbound import _validate_atom_result


class TestAtomReasoningValidation:
    """Judge results without reasoning should be downgraded."""

    def test_yes_without_reason_downgraded(self):
        atom = {"id": "D1.1", "status": "yes", "evidence": "Agent确认了订单信息", "reason": ""}
        result = _validate_atom_result(atom, "agent确认了订单信息")
        assert result["status"] == "partial"
        assert "推理" in result["reason"]

    def test_yes_with_short_reason_downgraded(self):
        atom = {"id": "D1.1", "status": "yes", "evidence": "Agent确认了订单信息", "reason": "ok"}
        result = _validate_atom_result(atom, "agent确认了订单信息")
        assert result["status"] == "partial"

    def test_yes_with_proper_reason_kept(self):
        atom = {
            "id": "D1.1",
            "status": "yes",
            "evidence": "Agent确认了订单信息",
            "reason": "Agent 在第2轮明确确认了订单编号和配送地址",
        }
        result = _validate_atom_result(atom, "agent确认了订单信息")
        assert result["status"] == "yes"

    def test_no_status_not_affected(self):
        atom = {"id": "D1.1", "status": "no", "evidence": "", "reason": ""}
        result = _validate_atom_result(atom, "any transcript")
        assert result["status"] == "no"

    def test_not_applicable_not_affected(self):
        atom = {"id": "D1.1", "status": "not_applicable", "evidence": "", "reason": ""}
        result = _validate_atom_result(atom, "any transcript")
        assert result["status"] == "not_applicable"

    def test_partial_without_reason_not_double_downgraded(self):
        atom = {"id": "D1.1", "status": "partial", "evidence": "Agent提到了", "reason": ""}
        result = _validate_atom_result(atom, "agent提到了")
        assert result["status"] == "partial"


class TestTemperatureZero:
    """All LLM judge calls must use temperature=0."""

    def test_scorer_outbound_all_temperature_zero(self):
        import inspect

        import scorer_outbound

        source = inspect.getsource(scorer_outbound)
        temp_calls = re.findall(r"temperature\s*=\s*([\d.]+)", source)
        for t in temp_calls:
            assert float(t) == 0.0, f"Found temperature={t}, expected 0"

    def test_scorer_temperature_zero(self):
        import inspect

        import scorer

        source = inspect.getsource(scorer)
        temp_calls = re.findall(r"temperature\s*=\s*([\d.]+)", source)
        for t in temp_calls:
            assert float(t) == 0.0, f"Found temperature={t} in scorer.py, expected 0"
