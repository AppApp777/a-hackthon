"""Tests for τ-bench inspired features (2026-05-23).

Covers:
- DB state hash comparison (expected_db_state)
- Explicit termination signals (###STOP###, ###TRANSFER###, ###OUT_OF_SCOPE###)
- Adversarial scenario loading
"""

import json
from pathlib import Path

from models_outbound import OutboundScenario
from scorer_outbound import _check_db_state_match


class TestDBStateMatch:
    """DB state comparison: expected vs actual."""

    def test_exact_match(self):
        actual = {
            "orders": [{"id": "O1", "status": "confirmed", "amount": 50}],
            "compensations": [],
        }
        expected = {
            "orders": [{"status": "confirmed"}],
        }
        passed, score, explanation = _check_db_state_match(actual, expected)
        assert passed
        assert score == 1.0

    def test_mismatch(self):
        actual = {
            "orders": [{"id": "O1", "status": "delivering", "amount": 50}],
        }
        expected = {
            "orders": [{"status": "confirmed"}],
        }
        passed, score, explanation = _check_db_state_match(actual, expected)
        assert not passed
        assert score == 0.0
        assert "未找到匹配行" in explanation

    def test_empty_expected(self):
        actual = {"orders": [{"id": "O1", "status": "confirmed"}]}
        passed, score, explanation = _check_db_state_match(actual, {})
        assert passed
        assert score == 1.0

    def test_ignores_timestamp_fields(self):
        actual = {
            "compensations": [
                {
                    "id": 1,
                    "order_id": "O1",
                    "type": "refund",
                    "status": "approved",
                    "created_at": "2026-05-23 12:00:00",
                }
            ],
        }
        expected = {
            "compensations": [{"order_id": "O1", "type": "refund", "status": "approved"}],
        }
        passed, score, explanation = _check_db_state_match(actual, expected)
        assert passed
        assert score == 1.0

    def test_partial_match(self):
        actual = {
            "orders": [
                {"id": "O1", "status": "confirmed"},
                {"id": "O2", "status": "delivering"},
            ],
        }
        expected = {
            "orders": [
                {"status": "confirmed"},
                {"status": "cancelled"},
            ],
        }
        passed, score, explanation = _check_db_state_match(actual, expected)
        assert score == 0.5
        assert not passed

    def test_multiple_tables(self):
        actual = {
            "orders": [{"id": "O1", "status": "confirmed"}],
            "call_logs": [{"order_id": "O1", "call_type": "outbound", "result": "confirmed"}],
        }
        expected = {
            "orders": [{"status": "confirmed"}],
            "call_logs": [{"order_id": "O1", "result": "confirmed"}],
        }
        passed, score, explanation = _check_db_state_match(actual, expected)
        assert passed
        assert score == 1.0


class TestExplicitTerminationSignals:
    """Explicit termination signals: ###STOP###, ###TRANSFER###, ###OUT_OF_SCOPE###."""

    def _make_orchestrator(self):
        from orchestrator_outbound import OutboundOrchestrator

        scenario = OutboundScenario(
            id="test_signal",
            name="信号测试",
            domain="outbound_call",
            description="测试显式终止信号",
            call_purpose="测试",
            call_context={"order_id": "T1", "customer_name": "测试"},
        )
        return OutboundOrchestrator.__new__(OutboundOrchestrator), scenario

    def test_stop_signal(self):
        orch, _ = self._make_orchestrator()
        assert orch._agent_ended_call("好的，祝您生活愉快！###STOP###")

    def test_transfer_signal(self):
        orch, _ = self._make_orchestrator()
        assert orch._agent_ended_call("我帮您转接人工客服 ###TRANSFER###")

    def test_out_of_scope_signal(self):
        orch, _ = self._make_orchestrator()
        assert orch._agent_ended_call("这个问题超出我的处理范围 ###OUT_OF_SCOPE###")

    def test_signal_alone(self):
        orch, _ = self._make_orchestrator()
        assert orch._agent_ended_call("###STOP###")

    def test_no_signal_no_farewell(self):
        orch, _ = self._make_orchestrator()
        assert not orch._agent_ended_call("好的，我帮您查一下")

    def test_keyword_fallback_still_works(self):
        orch, _ = self._make_orchestrator()
        assert orch._agent_ended_call("感谢您的配合，再见！")

    def test_signal_constants_defined(self):
        from orchestrator_outbound import OutboundOrchestrator

        assert OutboundOrchestrator.SIGNAL_STOP == "###STOP###"
        assert OutboundOrchestrator.SIGNAL_TRANSFER == "###TRANSFER###"
        assert OutboundOrchestrator.SIGNAL_OUT_OF_SCOPE == "###OUT_OF_SCOPE###"


class TestAdversarialScenarioLoading:
    """Verify adversarial scenarios load and validate correctly."""

    SCENARIO_DIR = (
        Path(__file__).resolve().parent.parent.parent / "agent-eval" / "scenarios" / "outbound"
    )

    def test_false_approval_loads(self):
        path = self.SCENARIO_DIR / "adversarial_false_approval.json"
        assert path.exists(), f"Missing: {path}"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        scenario = OutboundScenario(**data)
        assert scenario.difficulty == "hard"
        assert "trust_false_claim" in [fb.id for fb in scenario.forbidden_behaviors]
        assert scenario.expected_db_state

    def test_identity_fake_loads(self):
        path = self.SCENARIO_DIR / "adversarial_identity_fake.json"
        assert path.exists(), f"Missing: {path}"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        scenario = OutboundScenario(**data)
        assert scenario.difficulty == "hard"
        assert scenario.expected_call_result == "escalated"

    def test_order_switch_loads(self):
        path = self.SCENARIO_DIR / "adversarial_order_switch.json"
        assert path.exists(), f"Missing: {path}"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        scenario = OutboundScenario(**data)
        assert scenario.difficulty == "extreme"
        assert len(data["world_seed"]["orders"]) == 2
        assert scenario.expected_db_state

    def test_all_adversarial_have_forbidden_behaviors(self):
        for name in [
            "adversarial_false_approval.json",
            "adversarial_identity_fake.json",
            "adversarial_order_switch.json",
        ]:
            path = self.SCENARIO_DIR / name
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            scenario = OutboundScenario(**data)
            assert len(scenario.forbidden_behaviors) >= 2, f"{name}: needs ≥2 forbidden behaviors"


class TestExpectedDBStateInScenario:
    """Verify expected_db_state field in OutboundScenario model."""

    def test_model_accepts_expected_db_state(self):
        s = OutboundScenario(
            name="test",
            domain="outbound_call",
            description="test",
            call_purpose="test",
            expected_db_state={
                "orders": [{"status": "confirmed"}],
            },
        )
        assert s.expected_db_state["orders"][0]["status"] == "confirmed"

    def test_model_defaults_to_empty(self):
        s = OutboundScenario(
            name="test",
            domain="outbound_call",
            description="test",
            call_purpose="test",
        )
        assert s.expected_db_state == {}
