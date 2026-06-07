"""Tests for causal_diagnosis module integration."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-eval"))

from causal_diagnosis import CausalDiagnosisResult, diagnose
from policy_graph import compile_policy_graph
from trace_verifier import VerificationResult


def _make_scenario(**overrides):
    from models_outbound import (
        CalleePersona,
        InstructionStep,
        OutboundScenario,
    )

    defaults = {
        "id": "test_causal",
        "name": "因果诊断测试",
        "domain": "outbound_call",
        "description": "测试因果诊断",
        "call_purpose": "测试",
        "call_type": "delivery_confirm",
        "instruction_steps": [
            InstructionStep(step_id="s1", instruction="确认身份", order=1),
            InstructionStep(step_id="s2", instruction="告知目的", order=2),
        ],
        "difficulty": "easy",
        "callee_persona": CalleePersona(
            name="测试用户", mood="neutral", cooperation_level="cooperative"
        ),
        "expected_call_result": "confirmed",
        "forbidden_behaviors": [],
    }
    defaults.update(overrides)
    return OutboundScenario(**defaults)


class TestCausalDiagnosisBasic:
    def test_no_failure_returns_empty(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        verification = VerificationResult(
            satisfied_atoms=[],
            unsatisfied_atoms=[],
            not_applicable_atoms=[],
        )
        result = diagnose(verification, graph, scenario)
        assert isinstance(result, CausalDiagnosisResult)
        assert result.failure_mode == "none"
        assert result.root_causes == []

    def test_returns_dataclass(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        verification = VerificationResult(
            satisfied_atoms=[],
            unsatisfied_atoms=[],
            not_applicable_atoms=[],
        )
        result = diagnose(verification, graph, scenario)
        assert hasattr(result, "root_causes")
        assert hasattr(result, "counterfactual_repairs")
        assert hasattr(result, "failure_mode")
