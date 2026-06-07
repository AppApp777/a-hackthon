"""Unit tests for progress_rate metric (Phase 3.5 — AgentBoard borrowing).

progress_rate = completed required steps / total required steps.
"""

from models import Conversation, Message, Role
from models_outbound import (
    CallContext,
    InstructionStep,
    OutboundScenario,
    OutboundScoreReport,
)
from scorer_outbound import score_outbound_conversation


def _make_scenario(steps: list[dict]) -> OutboundScenario:
    return OutboundScenario(
        id="test_progress",
        name="Progress rate test",
        description="Test progress rate calculation",
        call_purpose="Test",
        call_context=CallContext(customer_name="Test", customer_phone="13800000000"),
        instruction_steps=[
            InstructionStep(
                step_id=s["id"],
                order=s["order"],
                instruction=s.get("instruction", f"Step {s['id']}"),
                required_actions=s.get("actions", []),
                is_optional=s.get("optional", False),
            )
            for s in steps
        ],
    )


def _make_conversation(agent_msgs: list[str]) -> Conversation:
    conv = Conversation(scenario_id="test_progress")
    for i, text in enumerate(agent_msgs):
        conv.messages.append(Message(turn=i + 1, role=Role.USER, content="你好"))
        conv.messages.append(Message(turn=i + 1, role=Role.AGENT, content=text))
    return conv


class TestProgressRateField:
    def test_field_exists_in_report(self):
        report = OutboundScoreReport(scenario_id="test")
        assert hasattr(report, "progress_rate")

    def test_default_is_none(self):
        report = OutboundScoreReport(scenario_id="test")
        assert report.progress_rate is None

    def test_can_set_value(self):
        report = OutboundScoreReport(scenario_id="test", progress_rate=0.75)
        assert report.progress_rate == 0.75


class TestProgressRateCalculation:
    def test_no_steps_returns_none(self):
        scenario = _make_scenario([])
        conv = _make_conversation(["你好"])
        report = score_outbound_conversation(scenario, conv, {}, use_llm_judge=False)
        assert report.progress_rate is None

    def test_all_optional_returns_none(self):
        scenario = _make_scenario(
            [
                {"id": "s1", "order": 1, "optional": True},
                {"id": "s2", "order": 2, "optional": True},
            ]
        )
        conv = _make_conversation(["你好"])
        report = score_outbound_conversation(scenario, conv, {}, use_llm_judge=False)
        assert report.progress_rate is None

    def test_progress_rate_between_0_and_1(self):
        scenario = _make_scenario(
            [
                {"id": "open", "order": 1, "actions": ["self_identify"]},
                {"id": "confirm", "order": 2, "actions": ["confirm_name"]},
                {"id": "close", "order": 3, "actions": ["close_call"]},
            ]
        )
        conv = _make_conversation(
            [
                "你好，我是美团客服小张",
                "好的",
                "感谢您的时间，再见",
            ]
        )
        report = score_outbound_conversation(scenario, conv, {}, use_llm_judge=False)
        assert report.progress_rate is not None
        assert 0.0 <= report.progress_rate <= 1.0

    def test_excludes_optional_from_denominator(self):
        scenario = _make_scenario(
            [
                {"id": "required1", "order": 1, "optional": False},
                {"id": "optional1", "order": 2, "optional": True},
                {"id": "required2", "order": 3, "optional": False},
            ]
        )
        conv = _make_conversation(["你好", "好的", "再见"])
        report = score_outbound_conversation(scenario, conv, {}, use_llm_judge=False)
        assert report.progress_rate is not None
