"""Tests for the static scenario linter."""

from __future__ import annotations

from pathlib import Path

import pytest
from scenario_linter import lint_all_scenarios, lint_scenario


def _base_scenario(**overrides) -> dict:
    base = {
        "id": "test_01",
        "name": "test",
        "instruction_steps": [
            {"step_id": "open", "order": 1, "required_actions": ["self_identify"]},
            {"step_id": "confirm", "order": 2, "required_actions": ["confirm_name"]},
            {
                "step_id": "wrap_up",
                "order": 3,
                "required_actions": ["say_goodbye", "log_call_result"],
            },
        ],
        "forbidden_behaviors": [{"id": "fb_test", "severity": "major"}],
        "must_call_tools": ["query_order", "log_call_result"],
        "expected_call_result": "confirmed",
        "mandatory_opening": "您好，这里是美团客服",
        "call_context": {"customer_name": "王女士", "order_id": "MT001"},
    }
    base.update(overrides)
    return base


class TestStepReachability:
    def test_all_steps_reachable(self):
        findings = lint_scenario(_base_scenario())
        assert not any(f.code == "S002" for f in findings)

    def test_no_steps_is_error(self):
        findings = lint_scenario(_base_scenario(instruction_steps=[]))
        assert any(f.code == "S001" and f.level == "error" for f in findings)


class TestBranchTargets:
    def test_valid_branch_target(self):
        s = _base_scenario()
        s["instruction_steps"][1]["branches"] = [{"condition": "x", "next_step": "wrap_up"}]
        findings = lint_scenario(s)
        assert not any(f.code == "B001" for f in findings)

    def test_invalid_branch_target(self):
        s = _base_scenario()
        s["instruction_steps"][1]["branches"] = [{"condition": "x", "next_step": "nonexistent"}]
        findings = lint_scenario(s)
        assert any(f.code == "B001" and f.level == "error" for f in findings)


class TestToolReferences:
    def test_valid_tools(self):
        findings = lint_scenario(_base_scenario())
        assert not any(f.code == "T001" for f in findings)

    def test_unknown_tool(self):
        s = _base_scenario(must_call_tools=["query_order", "nonexistent_tool"])
        findings = lint_scenario(s)
        assert any(f.code == "T001" and "nonexistent_tool" in f.message for f in findings)


class TestIdentitySatisfiability:
    def test_name_available(self):
        findings = lint_scenario(_base_scenario())
        assert not any(f.code == "I001" for f in findings)

    def test_no_name_no_role_warns(self):
        s = _base_scenario(call_context={})
        s["instruction_steps"][1]["required_actions"] = ["confirm_identity"]
        findings = lint_scenario(s)
        assert any(f.code == "I001" for f in findings)


class TestOutcomeConsistency:
    def test_refunded_without_create_compensation_warns(self):
        s = _base_scenario(
            expected_call_result="refunded",
            must_call_tools=["query_order", "log_call_result"],
        )
        findings = lint_scenario(s)
        assert any(f.code == "O001" and "create_compensation" in f.message for f in findings)

    def test_refunded_with_create_compensation_ok(self):
        s = _base_scenario(
            expected_call_result="refunded",
            must_call_tools=["query_order", "create_compensation", "log_call_result"],
        )
        findings = lint_scenario(s)
        assert not any(f.code == "O001" for f in findings)


class TestDeadRubric:
    def test_ai_self_disclosure_info(self):
        s = _base_scenario(mandatory_opening="您好，我是智能助手")
        findings = lint_scenario(s)
        assert any(f.code == "R001" for f in findings)

    def test_no_forbidden_behaviors_info(self):
        s = _base_scenario(forbidden_behaviors=[])
        findings = lint_scenario(s)
        assert any(f.code == "R002" for f in findings)


class TestLintAllScenarios:
    def test_lint_real_scenarios(self):
        scenarios_dir = (
            Path(__file__).resolve().parent.parent.parent / "agent-eval" / "scenarios" / "outbound"
        )
        if not scenarios_dir.exists():
            pytest.skip("scenarios/outbound not found")
        results = lint_all_scenarios(scenarios_dir)
        errors = sum(1 for v in results.values() for f in v if f.level == "error")
        assert errors == 0, f"Found {errors} errors in real scenarios: {results}"
