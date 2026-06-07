"""Unit tests for user simulator upgrades (Phase 3.4 — VoiceAgentEval + ARTKIT borrowing).

Tests scenario_hints, pressure_escalation, and adaptive_probing in prompt construction.
"""

from models_outbound import (
    CallContext,
    CalleePersona,
    InstructionStep,
    OutboundScenario,
)
from user_sim_outbound import OutboundUserSimulator


def _make_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "id": "test_sim",
        "name": "Test",
        "description": "Test scenario",
        "call_purpose": "Test",
        "call_context": CallContext(customer_name="Test", customer_phone="13800000000"),
        "instruction_steps": [InstructionStep(step_id="s1", order=1, instruction="Greet")],
    }
    defaults.update(overrides)
    return OutboundScenario(**defaults)


class TestScenarioHints:
    def test_hints_in_persona_model(self):
        persona = CalleePersona(scenario_hints=["催收场景下会提到还款困难", "会要求延期"])
        assert len(persona.scenario_hints) == 2

    def test_empty_hints_default(self):
        persona = CalleePersona()
        assert persona.scenario_hints == []

    def test_hints_appear_in_prompt(self):
        scenario = _make_scenario(
            callee_persona=CalleePersona(scenario_hints=["会提到家里有急事", "可能要求改约时间"])
        )
        sim = OutboundUserSimulator(scenario)
        prompt = sim._build_system_prompt()
        assert "场景专属行为" in prompt
        assert "会提到家里有急事" in prompt
        assert "可能要求改约时间" in prompt

    def test_no_hints_no_block(self):
        scenario = _make_scenario()
        sim = OutboundUserSimulator(scenario)
        prompt = sim._build_system_prompt()
        assert "场景专属行为" not in prompt


class TestPressureEscalation:
    def test_no_escalation_initially(self):
        scenario = _make_scenario()
        sim = OutboundUserSimulator(scenario)
        prompt = sim._build_system_prompt()
        assert "情绪升级" not in prompt

    def test_escalation_after_pressure(self):
        scenario = _make_scenario()
        sim = OutboundUserSimulator(scenario)
        sim._pressure_counter = 2
        prompt = sim._build_system_prompt()
        assert "情绪升级" in prompt
        assert "更不耐烦" in prompt

    def test_no_escalation_at_one(self):
        scenario = _make_scenario()
        sim = OutboundUserSimulator(scenario)
        sim._pressure_counter = 1
        prompt = sim._build_system_prompt()
        assert "情绪升级" not in prompt
