"""Tests for Phase 2.1: three-layer score structure.

Verifies that the new evidence_layer / quality_layer / safety_layer fields
are populated correctly and stay consistent with the legacy flat fields.
"""

from models import Conversation, Message, Role, ToolCall
from models_outbound import (
    CallContext,
    InstructionStep,
    ObjectiveEvidenceLayer,
    OutboundScenario,
    SafetyVetoLayer,
    SoftQualityLayer,
)
from scorer_outbound import score_outbound_conversation


def _make_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "id": "layer_test",
        "name": "层级测试场景",
        "domain": "outbound_call",
        "description": "验证三层分数结构",
        "call_type": "delivery_confirm",
        "call_purpose": "确认配送",
        "instruction_steps": [
            InstructionStep(
                step_id="step_1",
                order=1,
                instruction="确认订单信息",
                required_actions=["query_order"],
            ),
        ],
        "call_context": CallContext(
            customer_name="测试客户",
            customer_phone="13800000000",
            order_id="ORD_TEST",
            delivery_address="测试地址",
            delivery_time="12:00",
            compensation_budget=30,
        ),
        "must_call_tools": ["query_order", "log_call_result"],
        "expected_call_result": "confirmed",
        "expected_steps_completed": ["step_1"],
        "expected_branch_taken": {},
        "max_turns": 10,
    }
    defaults.update(overrides)
    return OutboundScenario(**defaults)


def _make_conv_with_tools() -> Conversation:
    """Build a conversation where the agent does the right things."""
    conv = Conversation(scenario_id="layer_test")
    conv.messages.append(
        Message(
            turn=1,
            role=Role.AGENT,
            content="您好，我是美团客服，请问是测试客户吗？我来确认您的订单 ORD_TEST 的配送信息。",
            tool_calls=[
                ToolCall(
                    tool_name="query_order",
                    arguments={"order_id": "ORD_TEST"},
                    result='{"status": "delivering", "order_id": "ORD_TEST"}',
                )
            ],
        )
    )
    conv.messages.append(
        Message(turn=2, role=Role.USER, content="是的，我是测试客户，订单没问题。")
    )
    conv.messages.append(
        Message(
            turn=3,
            role=Role.AGENT,
            content="好的，已确认您的配送信息，感谢您的配合，祝您生活愉快，再见！",
            tool_calls=[
                ToolCall(
                    tool_name="log_call_result",
                    arguments={"result": "confirmed", "order_id": "ORD_TEST"},
                    result='{"logged": true}',
                )
            ],
        )
    )
    return conv


class TestLayerStructureExists:
    """Three-layer sub-models exist and have correct fields."""

    def test_objective_evidence_layer_fields(self):
        layer = ObjectiveEvidenceLayer()
        assert hasattr(layer, "hard")
        assert hasattr(layer, "step_compliance")
        assert hasattr(layer, "branch_accuracy")
        assert hasattr(layer, "temporal_order")
        assert hasattr(layer, "path_alignment")
        assert hasattr(layer, "total")
        assert hasattr(layer, "weights")

    def test_soft_quality_layer_fields(self):
        layer = SoftQualityLayer()
        assert hasattr(layer, "raw_score")
        assert hasattr(layer, "gate_threshold")
        assert hasattr(layer, "gate_value")
        assert hasattr(layer, "gated_contribution")

    def test_safety_veto_layer_fields(self):
        layer = SafetyVetoLayer()
        assert hasattr(layer, "veto_cap")
        assert hasattr(layer, "gate_type")
        assert hasattr(layer, "has_fabrication")
        assert hasattr(layer, "violation_count")
        assert hasattr(layer, "safety_triggered")


class TestLayerConsistency:
    """New layer fields must be consistent with legacy flat fields."""

    def test_evidence_layer_matches_flat_scores(self):
        scenario = _make_scenario()
        conv = _make_conv_with_tools()
        db_state = {
            "call_logs": [{"result": "confirmed", "call_type": "outbound", "order_id": "ORD_TEST"}],
            "compensations": [],
            "orders": [{"order_id": "ORD_TEST", "status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)

        el = report.evidence_layer
        assert el.hard == report.hard_score
        assert el.step_compliance == report.step_compliance_score
        assert el.branch_accuracy == report.branch_accuracy_score
        assert el.temporal_order == report.temporal_order_score
        assert el.path_alignment == report.alignment_score
        assert el.total == report.objective_score

    def test_quality_layer_matches_flat_scores(self):
        scenario = _make_scenario()
        conv = _make_conv_with_tools()
        db_state = {
            "call_logs": [{"result": "confirmed", "call_type": "outbound", "order_id": "ORD_TEST"}],
            "compensations": [],
            "orders": [{"order_id": "ORD_TEST", "status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)

        ql = report.quality_layer
        assert ql.raw_score == report.soft_score

    def test_safety_layer_matches_flat_scores(self):
        scenario = _make_scenario()
        conv = _make_conv_with_tools()
        db_state = {
            "call_logs": [{"result": "confirmed", "call_type": "outbound", "order_id": "ORD_TEST"}],
            "compensations": [],
            "orders": [{"order_id": "ORD_TEST", "status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)

        sl = report.safety_layer
        assert sl.veto_cap == report.veto_cap
        assert sl.gate_type == report.gate_type
        assert sl.violation_count == report.forbidden_violation_count


class TestLayerSerialization:
    """Layer data survives JSON round-trip (trace file compat)."""

    def test_json_round_trip(self):
        scenario = _make_scenario()
        conv = _make_conv_with_tools()
        db_state = {
            "call_logs": [{"result": "confirmed", "call_type": "outbound", "order_id": "ORD_TEST"}],
            "compensations": [],
            "orders": [{"order_id": "ORD_TEST", "status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)

        json_str = report.model_dump_json()
        restored = report.model_validate_json(json_str)

        assert restored.evidence_layer.hard == report.evidence_layer.hard
        assert restored.evidence_layer.total == report.evidence_layer.total
        assert restored.quality_layer.raw_score == report.quality_layer.raw_score
        assert restored.safety_layer.veto_cap == report.safety_layer.veto_cap

    def test_backward_compat_missing_layers(self):
        """Old trace JSON without layer fields should still parse."""
        from models_outbound import OutboundScoreReport

        old_data = {
            "scenario_id": "old_trace",
            "hard_score": 0.8,
            "soft_score": 0.7,
            "objective_score": 0.75,
            "overall_score": 0.72,
        }
        report = OutboundScoreReport(**old_data)
        assert report.evidence_layer.hard == 0
        assert report.evidence_layer.total == 0
        assert report.quality_layer.raw_score is None
        assert report.safety_layer.veto_cap == 1.0


class TestLayerFormula:
    """Verify the three-layer formula: overall = min(evidence + gated_soft, veto_cap)."""

    def test_overall_equals_min_evidence_veto(self):
        scenario = _make_scenario()
        conv = _make_conv_with_tools()
        db_state = {
            "call_logs": [{"result": "confirmed", "call_type": "outbound", "order_id": "ORD_TEST"}],
            "compensations": [],
            "orders": [{"order_id": "ORD_TEST", "status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)

        expected_overall = min(report.evidence_score, report.safety_layer.veto_cap)
        assert abs(report.overall_score - expected_overall) < 0.001

    def test_evidence_layer_weights_sum_to_088(self):
        scenario = _make_scenario()
        conv = _make_conv_with_tools()
        db_state = {
            "call_logs": [{"result": "confirmed", "call_type": "outbound", "order_id": "ORD_TEST"}],
            "compensations": [],
            "orders": [{"order_id": "ORD_TEST", "status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)

        if report.evidence_layer.weights:
            total_weight = sum(report.evidence_layer.weights.values())
            assert abs(total_weight - 0.88) < 0.01
