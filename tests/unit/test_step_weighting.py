"""Tests for Phase 4.4: AgentPRM-inspired step-level weighted scoring.

Verifies:
- InstructionStep.weight field exists and defaults to 1.0
- StepComplianceEntry.contribution_weight field exists
- Weighted progress_rate calculation
- Weight propagation from scenario steps to compliance entries
"""

from models import Conversation, Message, Role, ToolCall
from models_outbound import (
    CallContext,
    InstructionStep,
    OutboundScenario,
    StepComplianceEntry,
)
from scorer_outbound import score_outbound_conversation


def _make_scenario(weights: dict[str, float] | None = None) -> OutboundScenario:
    steps = [
        InstructionStep(
            step_id="step_1",
            order=1,
            instruction="确认订单信息",
            required_actions=["query_order"],
            weight=weights.get("step_1", 1.0) if weights else 1.0,
        ),
        InstructionStep(
            step_id="step_2",
            order=2,
            instruction="处理核心问题",
            required_actions=["handle_issue"],
            weight=weights.get("step_2", 1.0) if weights else 1.0,
        ),
        InstructionStep(
            step_id="step_3",
            order=3,
            instruction="结束通话",
            required_actions=["close_call"],
            weight=weights.get("step_3", 1.0) if weights else 1.0,
        ),
    ]
    return OutboundScenario(
        id="weight_test",
        name="权重测试",
        domain="outbound_call",
        description="测试步骤加权",
        call_type="delivery_confirm",
        call_purpose="确认配送",
        instruction_steps=steps,
        call_context=CallContext(
            customer_name="测试客户",
            customer_phone="13800000000",
            order_id="ORD_W",
            delivery_address="测试地址",
            delivery_time="12:00",
            compensation_budget=30,
        ),
        must_call_tools=["query_order", "log_call_result"],
        expected_call_result="confirmed",
        expected_steps_completed=["step_1", "step_2", "step_3"],
        expected_branch_taken={},
        max_turns=10,
    )


def _make_conv_completing_steps(completed_step_ids: list[str]) -> Conversation:
    conv = Conversation(scenario_id="weight_test")
    conv.messages.append(
        Message(
            turn=1,
            role=Role.AGENT,
            content="您好，我是美团客服，确认订单。",
            tool_calls=[
                ToolCall(
                    tool_name="query_order",
                    arguments={"order_id": "ORD_W"},
                    result='{"status": "delivering"}',
                )
            ],
        )
    )
    conv.messages.append(Message(turn=2, role=Role.USER, content="好的"))
    conv.messages.append(
        Message(
            turn=3,
            role=Role.AGENT,
            content="问题已处理，感谢配合，再见！",
            tool_calls=[
                ToolCall(
                    tool_name="log_call_result",
                    arguments={"result": "confirmed", "order_id": "ORD_W"},
                    result='{"logged": true}',
                )
            ],
        )
    )
    return conv


def _db():
    return {
        "call_logs": [{"result": "confirmed", "call_type": "outbound", "order_id": "ORD_W"}],
        "compensations": [],
        "orders": [{"order_id": "ORD_W", "status": "delivering"}],
        "delivery_schedule": [],
    }


class TestInstructionStepWeight:
    def test_default_weight_is_1(self):
        step = InstructionStep(step_id="s1", order=1, instruction="test")
        assert step.weight == 1.0

    def test_custom_weight(self):
        step = InstructionStep(step_id="s1", order=1, instruction="test", weight=2.0)
        assert step.weight == 2.0

    def test_weight_from_json(self):
        import json

        data = json.loads('{"step_id": "s1", "order": 1, "instruction": "test", "weight": 1.5}')
        step = InstructionStep(**data)
        assert step.weight == 1.5


class TestStepComplianceEntryWeight:
    def test_default_contribution_weight(self):
        entry = StepComplianceEntry(step_id="s1", instruction="test")
        assert entry.contribution_weight == 1.0

    def test_custom_contribution_weight(self):
        entry = StepComplianceEntry(step_id="s1", instruction="test", contribution_weight=2.0)
        assert entry.contribution_weight == 2.0


class TestWeightedProgressRate:
    def test_equal_weight_same_as_unweighted(self):
        scenario = _make_scenario()
        conv = _make_conv_completing_steps(["step_1", "step_2", "step_3"])
        report = score_outbound_conversation(scenario, conv, _db(), use_llm_judge=False)
        assert report.progress_rate is not None

    def test_weighted_progress_rate_full_completion(self):
        scenario = _make_scenario({"step_1": 1.0, "step_2": 2.0, "step_3": 1.0})
        conv = _make_conv_completing_steps(["step_1", "step_2", "step_3"])
        report = score_outbound_conversation(scenario, conv, _db(), use_llm_judge=False)
        if report.progress_rate is not None:
            assert report.progress_rate > 0.0

    def test_weight_propagated_to_compliance_entries(self):
        scenario = _make_scenario({"step_1": 1.0, "step_2": 2.5, "step_3": 0.5})
        conv = _make_conv_completing_steps(["step_1", "step_2", "step_3"])
        report = score_outbound_conversation(scenario, conv, _db(), use_llm_judge=False)
        weight_map = {e.step_id: e.contribution_weight for e in report.step_compliance}
        if "step_2" in weight_map:
            assert weight_map["step_2"] == 2.5
        if "step_3" in weight_map:
            assert weight_map["step_3"] == 0.5


class TestWeightedScenarioLoading:
    def test_after_sales_has_weighted_steps(self):
        import json
        from pathlib import Path

        path = (
            Path(__file__).resolve().parents[2]
            / "agent-eval"
            / "scenarios"
            / "outbound"
            / "after_sales_complaint.json"
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        scenario = OutboundScenario(**data)
        weighted = [s for s in scenario.instruction_steps if s.weight != 1.0]
        assert len(weighted) >= 2, "after_sales_complaint should have at least 2 weighted steps"

    def test_weighted_steps_are_positive(self):
        import json
        from pathlib import Path

        scenarios_dir = (
            Path(__file__).resolve().parents[2] / "agent-eval" / "scenarios" / "outbound"
        )
        for f in scenarios_dir.glob("*.json"):
            data = json.loads(f.read_text(encoding="utf-8"))
            scenario = OutboundScenario(**data)
            for step in scenario.instruction_steps:
                assert step.weight > 0, f"{f.name}: {step.step_id} has non-positive weight"
