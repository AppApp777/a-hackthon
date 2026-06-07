"""Tests for Phase 2.4: scenario information leak prevention.

Verifies that answer-key fields are NOT passed to the agent.
"""

from models_outbound import (
    SCENARIO_ANSWER_KEY_FIELDS,
    CallContext,
    CanonicalRequirement,
    ForbiddenBehavior,
    InstructionStep,
    OutboundScenario,
)


def _make_scenario_with_answers() -> OutboundScenario:
    return OutboundScenario(
        id="leak_test",
        name="信息泄漏测试",
        domain="outbound_call",
        description="验证答案字段不泄漏",
        call_type="delivery_confirm",
        call_purpose="确认配送",
        instruction_steps=[
            InstructionStep(step_id="s1", order=1, instruction="确认订单"),
        ],
        call_context=CallContext(
            customer_name="测试客户",
            customer_phone="13800000000",
            order_id="ORD_LEAK",
        ),
        expected_call_result="confirmed",
        expected_steps_completed=["s1"],
        expected_branch_taken={"s1": "happy_path"},
        must_call_tools=["query_order", "log_call_result"],
        must_not_do=["承诺退款"],
        forbidden_behaviors=[
            ForbiddenBehavior(
                id="fb1",
                description="禁止透露内部信息",
                severity="critical",
                detection_keywords=["内部系统"],
            )
        ],
        callee_goal="确认配送正常",
        canonical_intent=[
            CanonicalRequirement(
                id="r1",
                content="必须确认订单",
                source="scenario_policy",
                keywords=["确认"],
            )
        ],
        expected_db_state={"orders": [{"order_id": "ORD_LEAK", "status": "confirmed"}]},
    )


class TestAgentSafeDump:
    """agent_safe_dump() must exclude all answer-key fields."""

    def test_excludes_expected_call_result(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "expected_call_result" not in safe

    def test_excludes_expected_steps_completed(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "expected_steps_completed" not in safe

    def test_excludes_expected_branch_taken(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "expected_branch_taken" not in safe

    def test_excludes_must_call_tools(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "must_call_tools" not in safe

    def test_excludes_must_not_do(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "must_not_do" not in safe

    def test_excludes_forbidden_behaviors(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "forbidden_behaviors" not in safe

    def test_excludes_callee_goal(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "callee_goal" not in safe

    def test_excludes_canonical_intent(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "canonical_intent" not in safe

    def test_excludes_expected_db_state(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert "expected_db_state" not in safe

    def test_keeps_operational_fields(self):
        scenario = _make_scenario_with_answers()
        safe = scenario.agent_safe_dump()
        assert safe["id"] == "leak_test"
        assert safe["call_purpose"] == "确认配送"
        assert safe["call_context"]["customer_name"] == "测试客户"
        assert len(safe["instruction_steps"]) == 1

    def test_answer_key_fields_constant(self):
        expected = {
            "expected_steps_completed",
            "expected_branch_taken",
            "expected_call_result",
            "must_call_tools",
            "must_not_do",
            "canonical_intent",
            "expected_db_state",
            "forbidden_behaviors",
            "callee_goal",
        }
        assert expected == SCENARIO_ANSWER_KEY_FIELDS
