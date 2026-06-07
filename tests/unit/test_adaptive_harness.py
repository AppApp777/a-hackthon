"""Tests for adaptive harness degradation.

Core finding: one-size-fits-all Harness hurts weak models.
Haiku drops from 20→1.7 because 10-13 content injections overwhelm it.
Adaptive harness tracks effectiveness and auto-degrades.
"""

from harness import (
    AdaptiveLevel,
    HarnessConfig,
    HarnessState,
    OutboundHarness,
)
from models import Conversation, Message, Role
from models_outbound import (
    CallContext,
    InstructionStep,
    OutboundScenario,
)
from tools_outbound import OutboundToolSimulator


def _make_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "id": "test_adaptive",
        "name": "自适应测试",
        "domain": "outbound_call",
        "description": "测试自适应降级",
        "call_type": "delivery_confirm",
        "call_purpose": "确认配送",
        "instruction_steps": [
            InstructionStep(
                step_id="step_1",
                order=1,
                instruction="确认订单信息",
                required_actions=["query_order"],
            ),
            InstructionStep(
                step_id="step_2",
                order=2,
                instruction="告知配送时间",
                required_actions=["inform_delivery"],
            ),
            InstructionStep(
                step_id="step_3",
                order=3,
                instruction="结束通话",
                required_actions=["log_call_result"],
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


def _make_conversation(messages=None) -> Conversation:
    conv = Conversation(scenario_id="test_adaptive")
    if messages:
        for turn, role, content in messages:
            conv.messages.append(Message(turn=turn, role=role, content=content))
    return conv


class TestAdaptiveLevelEnum:
    def test_levels_exist(self):
        assert AdaptiveLevel.FULL == "full"
        assert AdaptiveLevel.BLOCK_ONLY == "block_only"
        assert AdaptiveLevel.LOG_ONLY == "log_only"

    def test_level_ordering(self):
        levels = [AdaptiveLevel.FULL, AdaptiveLevel.BLOCK_ONLY, AdaptiveLevel.LOG_ONLY]
        assert len(levels) == 3


class TestAdaptiveConfig:
    def test_adaptive_default_off(self):
        config = HarnessConfig()
        assert config.adaptive is False

    def test_adaptive_enable(self):
        config = HarnessConfig(adaptive=True)
        assert config.adaptive is True
        assert config.adaptive_degrade_threshold == 2

    def test_adaptive_custom_threshold(self):
        config = HarnessConfig(adaptive=True, adaptive_degrade_threshold=3)
        assert config.adaptive_degrade_threshold == 3

    def test_from_mode_preserves_adaptive(self):
        config = HarnessConfig.from_mode("guarded_eval")
        assert config.adaptive is False
        config2 = HarnessConfig(adaptive=True, mode="guarded_eval")
        assert config2.adaptive is True


class TestAdaptiveState:
    def test_initial_state(self):
        state = HarnessState()
        assert state.adaptive_level == AdaptiveLevel.FULL
        assert state.consecutive_ineffective == 0
        assert state.adaptive_transitions == []


class TestAdaptiveDegradation:
    def _make_harness(self, adaptive=True, threshold=2) -> OutboundHarness:
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            adaptive=adaptive,
            adaptive_degrade_threshold=threshold,
            step_gating=True,
            closing_injection=True,
            mode="supervised_deploy",
            step_injection_on_deviation=True,
        )
        return OutboundHarness(scenario, tool_sim, config)

    def test_starts_at_full(self):
        harness = self._make_harness()
        assert harness.state.adaptive_level == AdaptiveLevel.FULL

    def test_step_injection_at_full_level(self):
        harness = self._make_harness()
        conv = _make_conversation([(1, Role.AGENT, "你好")])
        result = harness.get_step_injection(conv)
        assert result is not None

    def test_step_injection_blocked_at_block_only(self):
        harness = self._make_harness()
        harness.state.adaptive_level = AdaptiveLevel.BLOCK_ONLY
        conv = _make_conversation([(1, Role.AGENT, "你好")])
        result = harness.get_step_injection(conv)
        assert result is None

    def test_step_injection_blocked_at_log_only(self):
        harness = self._make_harness()
        harness.state.adaptive_level = AdaptiveLevel.LOG_ONLY
        conv = _make_conversation([(1, Role.AGENT, "你好")])
        result = harness.get_step_injection(conv)
        assert result is None

    def test_blocking_still_works_at_block_only(self):
        """BLOCK_ONLY should still block forbidden words."""
        scenario = _make_scenario(
            forbidden_behaviors=[
                {
                    "id": "fb_1",
                    "description": "禁止说赔偿",
                    "severity": "high",
                    "detection_keywords": ["赔偿"],
                }
            ]
        )
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            adaptive=True,
            forbidden_word_blocking=True,
            mode="guarded_eval",
        )
        harness = OutboundHarness(scenario, tool_sim, config)
        harness.state.adaptive_level = AdaptiveLevel.BLOCK_ONLY

        conv = _make_conversation()
        _, _, blocked = harness.process_agent_output("我给你赔偿吧", [], conv, turn=1)
        assert blocked is True

    def test_log_only_manual_still_works(self):
        """LOG_ONLY can be set manually (for raw_eval), logs but doesn't block."""
        scenario = _make_scenario(
            forbidden_behaviors=[
                {
                    "id": "fb_1",
                    "description": "禁止说赔偿",
                    "severity": "high",
                    "detection_keywords": ["赔偿"],
                }
            ]
        )
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            adaptive=True,
            forbidden_word_blocking=True,
            mode="guarded_eval",
        )
        harness = OutboundHarness(scenario, tool_sim, config)
        harness.state.adaptive_level = AdaptiveLevel.LOG_ONLY

        conv = _make_conversation()
        _, _, blocked = harness.process_agent_output("我给你赔偿吧", [], conv, turn=1)
        assert blocked is False
        assert len(harness.state.interventions_log) > 0
        assert harness.state.interventions_log[-1]["type"] == "forbidden_word_log_only"

    def test_degrade_full_to_block_only(self):
        harness = self._make_harness(threshold=2)
        harness.record_intervention_outcome(effective=False)
        assert harness.state.adaptive_level == AdaptiveLevel.FULL
        harness.record_intervention_outcome(effective=False)
        assert harness.state.adaptive_level == AdaptiveLevel.BLOCK_ONLY
        assert len(harness.state.adaptive_transitions) == 1

    def test_caps_at_block_only(self):
        """Adaptive degradation caps at BLOCK_ONLY — never reaches LOG_ONLY."""
        harness = self._make_harness(threshold=2)
        harness.state.adaptive_level = AdaptiveLevel.BLOCK_ONLY
        harness.state.consecutive_ineffective = 0
        harness.record_intervention_outcome(effective=False)
        harness.record_intervention_outcome(effective=False)
        harness.record_intervention_outcome(effective=False)
        assert harness.state.adaptive_level == AdaptiveLevel.BLOCK_ONLY

    def test_effective_resets_counter(self):
        harness = self._make_harness(threshold=2)
        harness.record_intervention_outcome(effective=False)
        assert harness.state.consecutive_ineffective == 1
        harness.record_intervention_outcome(effective=True)
        assert harness.state.consecutive_ineffective == 0
        assert harness.state.adaptive_level == AdaptiveLevel.FULL

    def test_safety_checks_preserved_at_block_only(self):
        """BLOCK_ONLY still enforces step_gating and emotion_protection."""
        harness = self._make_harness()
        harness.state.adaptive_level = AdaptiveLevel.BLOCK_ONLY
        harness.config.step_gating = True
        harness.scenario.must_call_tools = ["query_order", "log_call_result"]
        conv = _make_conversation()

        _, _, blocked = harness.process_agent_output("祝您生活愉快，再见", [], conv, turn=5)
        assert blocked is True

    def test_non_adaptive_ignores_degradation(self):
        harness = self._make_harness(adaptive=False)
        harness.record_intervention_outcome(effective=False)
        harness.record_intervention_outcome(effective=False)
        harness.record_intervention_outcome(effective=False)
        assert harness.state.adaptive_level == AdaptiveLevel.FULL

    def test_closing_injection_blocked_at_block_only(self):
        """BLOCK_ONLY should not inject closing text."""
        harness = self._make_harness()
        harness.state.adaptive_level = AdaptiveLevel.BLOCK_ONLY
        harness.config.closing_injection = True
        harness.scenario.mandatory_closing = "感谢您的配合，祝您生活愉快！"

        conv = _make_conversation()
        harness.state.completed_steps = {"step_1", "step_2", "step_3", "wrap_up"}
        text, _, blocked = harness.process_agent_output("祝您生活愉快，再见", [], conv, turn=5)
        assert "感谢您的配合" not in text

    def test_summary_includes_adaptive_info(self):
        harness = self._make_harness()
        harness.record_intervention_outcome(effective=False)
        harness.record_intervention_outcome(effective=False)
        summary = harness.get_summary()
        assert "adaptive" in summary
        assert summary["adaptive"]["level"] == AdaptiveLevel.BLOCK_ONLY
        assert summary["adaptive"]["transitions"] == harness.state.adaptive_transitions

    def test_transition_log_format(self):
        harness = self._make_harness(threshold=2)
        harness.state.current_turn = 5
        harness.record_intervention_outcome(effective=False)
        harness.record_intervention_outcome(effective=False)
        assert len(harness.state.adaptive_transitions) == 1
        transition = harness.state.adaptive_transitions[0]
        assert transition["from"] == "full"
        assert transition["to"] == "block_only"
        assert transition["turn"] == 5


class TestAdaptiveWithOrchestration:
    """Integration-style tests: adaptive harness in realistic flow."""

    def test_weak_model_scenario(self):
        """Simulate weak model: interventions never help, harness degrades."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            adaptive=True,
            adaptive_degrade_threshold=2,
            step_gating=True,
            closing_injection=True,
            mode="supervised_deploy",
            step_injection_on_deviation=True,
            step_injection_periodic=True,
            step_injection_interval=1,
        )
        harness = OutboundHarness(scenario, tool_sim, config)

        conv = _make_conversation()
        harness.state.current_turn = 1
        inj = harness.get_step_injection(conv)
        assert inj is not None

        harness.record_intervention_outcome(effective=False)
        harness.record_intervention_outcome(effective=False)
        assert harness.state.adaptive_level == AdaptiveLevel.BLOCK_ONLY

        # Content injections stop, but safety checks stay
        harness.state.current_turn = 5
        inj2 = harness.get_step_injection(conv)
        assert inj2 is None

        # Safety still works
        _, _, blocked = harness.process_agent_output("祝您生活愉快，再见", [], conv, turn=6)
        assert blocked is True

    def test_strong_model_stays_full(self):
        """Simulate strong model: interventions always help, stays FULL."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            adaptive=True,
            adaptive_degrade_threshold=2,
            step_gating=True,
            mode="supervised_deploy",
        )
        harness = OutboundHarness(scenario, tool_sim, config)

        for _ in range(10):
            harness.record_intervention_outcome(effective=True)
        assert harness.state.adaptive_level == AdaptiveLevel.FULL
        assert harness.state.consecutive_ineffective == 0

    def test_gaming_attack_safety_preserved(self):
        """Even after max degradation, safety checks still block."""
        scenario = _make_scenario(
            forbidden_behaviors=[
                {
                    "id": "fb_1",
                    "description": "禁止说赔偿",
                    "severity": "high",
                    "detection_keywords": ["赔偿"],
                }
            ]
        )
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            adaptive=True,
            adaptive_degrade_threshold=2,
            forbidden_word_blocking=True,
            step_gating=True,
            mode="guarded_eval",
        )
        harness = OutboundHarness(scenario, tool_sim, config)

        for _ in range(10):
            harness.record_intervention_outcome(effective=False)

        assert harness.state.adaptive_level == AdaptiveLevel.BLOCK_ONLY

        conv = _make_conversation()
        _, _, blocked = harness.process_agent_output("赔偿你", [], conv, turn=8)
        assert blocked is True

        _, _, blocked2 = harness.process_agent_output("祝您生活愉快，再见", [], conv, turn=9)
        assert blocked2 is True
