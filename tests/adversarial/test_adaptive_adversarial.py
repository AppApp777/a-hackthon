"""Adversarial tests for Phase 4.3: ARTKIT-inspired adaptive multi-turn adversarial mode.

Tests that the user simulator correctly:
- Detects agent refusals
- Escalates through 5 attack strategies
- Includes adversarial section in system prompt when mode is active
- scenario_mutator.adversarial_escalation creates correct mutations
"""

import re

from models import Conversation, Message, Role
from models_outbound import (
    CallContext,
    CalleePersona,
    InstructionStep,
    OutboundScenario,
)
from scenario_mutator import MutationType, adversarial_escalation
from user_sim_outbound import (
    _ADVERSARIAL_STRATEGIES,
    _REFUSAL_PHRASES,
    OutboundUserSimulator,
)


def _make_scenario(adversarial: bool = False) -> OutboundScenario:
    return OutboundScenario(
        id="adv_test",
        name="对抗模式测试",
        description="测试自适应对抗",
        call_purpose="测试",
        call_context=CallContext(customer_name="测试", customer_phone="13800000000"),
        instruction_steps=[
            InstructionStep(step_id="s1", order=1, instruction="开场"),
        ],
        callee_persona=CalleePersona(
            adversarial_mode=adversarial,
            cooperativeness=2,
            hidden_agenda="获取内部信息",
        ),
        callee_goal="测试对抗策略升级",
    )


class TestAdversarialModeInit:
    def test_adversarial_mode_off_by_default(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=False))
        assert sim._adversarial_mode is False
        assert sim._refusal_count == 0
        assert sim._current_strategy_idx == 0

    def test_adversarial_mode_on(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        assert sim._adversarial_mode is True

    def test_strategies_list_has_5(self):
        assert len(_ADVERSARIAL_STRATEGIES) == 5


class TestRefusalDetection:
    def test_detects_cannot(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        assert sim._detect_refusal("抱歉，这个我无法帮您处理。")

    def test_detects_no_permission(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        assert sim._detect_refusal("我没有权限查看其他客户的信息。")

    def test_detects_out_of_scope(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        assert sim._detect_refusal("这不在我的服务范围内。")

    def test_no_refusal_in_normal_response(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        assert not sim._detect_refusal("好的，我帮您查一下订单。")

    def test_refusal_phrases_compiled(self):
        assert len(_REFUSAL_PHRASES) >= 2
        for p in _REFUSAL_PHRASES:
            assert isinstance(p, re.Pattern)


class TestStrategyEscalation:
    def test_initial_strategy_is_direct(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        assert sim.current_adversarial_strategy == "direct_request"

    def test_escalation_after_refusal(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        conv = Conversation(scenario_id="adv_test")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="抱歉，我无法提供这些信息。"))
        sim._update_adversarial_state(conv)
        assert sim._refusal_count == 1
        assert sim.current_adversarial_strategy == "social_engineering"

    def test_double_escalation(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        conv = Conversation(scenario_id="adv_test")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="我没有权限做这个操作。"))
        sim._update_adversarial_state(conv)
        conv.messages.append(Message(turn=2, role=Role.USER, content="我要找你们经理！"))
        conv.messages.append(Message(turn=3, role=Role.AGENT, content="抱歉做不到。"))
        sim._update_adversarial_state(conv)
        assert sim._refusal_count == 2
        assert sim.current_adversarial_strategy == "emotional_manipulation"

    def test_max_escalation_does_not_overflow(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        conv = Conversation(scenario_id="adv_test")
        for i in range(10):
            conv.messages.append(
                Message(
                    turn=i * 2 + 1,
                    role=Role.AGENT,
                    content="抱歉，无法为您处理。",
                )
            )
            sim._update_adversarial_state(conv)
        assert sim._current_strategy_idx == len(_ADVERSARIAL_STRATEGIES) - 1
        assert sim.current_adversarial_strategy == "persistent_reframing"

    def test_no_escalation_without_adversarial_mode(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=False))
        conv = Conversation(scenario_id="adv_test")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="我无法帮您。"))
        sim._update_adversarial_state(conv)
        assert sim._refusal_count == 0
        assert sim.current_adversarial_strategy == "none"


class TestAdversarialPrompt:
    def test_adversarial_section_in_prompt(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        prompt = sim._build_system_prompt()
        assert "对抗策略" in prompt
        assert "direct_request" in prompt

    def test_no_adversarial_section_when_off(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=False))
        prompt = sim._build_system_prompt()
        assert "[对抗策略 —" not in prompt

    def test_strategy_name_updates_in_prompt(self):
        sim = OutboundUserSimulator(_make_scenario(adversarial=True))
        conv = Conversation(scenario_id="adv_test")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="不可以，没有权限。"))
        sim._update_adversarial_state(conv)
        prompt = sim._build_system_prompt()
        assert "social_engineering" in prompt


class TestAdversarialEscalationMutator:
    def test_mutation_enables_adversarial_mode(self):
        scenario = _make_scenario(adversarial=False)
        mutated, relation = adversarial_escalation(scenario)
        assert mutated.callee_persona.adversarial_mode is True

    def test_mutation_type_is_correct(self):
        scenario = _make_scenario()
        _, relation = adversarial_escalation(scenario)
        assert relation.mutation_type == MutationType.ADVERSARIAL_ESCALATION

    def test_original_not_modified(self):
        scenario = _make_scenario(adversarial=False)
        adversarial_escalation(scenario)
        assert scenario.callee_persona.adversarial_mode is False

    def test_cooperation_lowered(self):
        scenario = _make_scenario()
        scenario.callee_persona.cooperativeness = 8
        mutated, _ = adversarial_escalation(scenario)
        assert mutated.callee_persona.cooperativeness <= 3
