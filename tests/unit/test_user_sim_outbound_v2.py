"""Day 2-3: Five-part user simulator v2 tests.

Covers Oracle 18-day plan item B. Reference:
- docs/DESIGN_user_sim_outbound_v2.md
- tool-results/bxzed2mnj.txt §3 (Five-part prompt for outbound)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from models import Conversation, Message, Role
from models_outbound import CalleePersona, OutboundScenario, PersonaArchetype
from user_sim_outbound import (
    _BASELINE_SENSITIVE_FACTS,
    CalleeOutput,
    OutboundUserSimulator,
    infer_archetype,
)

SCENARIO_DIR = (
    Path(__file__).resolve().parent.parent.parent / "agent-eval" / "scenarios" / "outbound"
)


# ── 1. Archetype inference from numeric params ────────────────────


def test_archetype_inference_cooperative():
    p = CalleePersona(cooperativeness=8, trust_level=8, patience=7, busy_level=2)
    assert infer_archetype(p) == PersonaArchetype.COOPERATIVE


def test_archetype_inference_busy():
    p = CalleePersona(busy_level=9, cooperativeness=5, trust_level=6)
    assert infer_archetype(p) == PersonaArchetype.BUSY


def test_archetype_inference_wary():
    p = CalleePersona(trust_level=2, cooperativeness=4, busy_level=4)
    assert infer_archetype(p) == PersonaArchetype.WARY


def test_archetype_inference_impatient():
    p = CalleePersona(patience=3, emotional=8, busy_level=4, trust_level=6)
    assert infer_archetype(p) == PersonaArchetype.IMPATIENT


def test_archetype_inference_hesitant_fallback():
    p = CalleePersona(cooperativeness=5, trust_level=6, patience=6, busy_level=4, emotional=4)
    assert infer_archetype(p) == PersonaArchetype.HESITANT


# ── 2. Explicit archetype overrides numeric inference ─────────────


def test_explicit_archetype_overrides_numeric():
    # numeric params would infer BUSY, but explicit archetype wins
    p = CalleePersona(
        busy_level=9,
        cooperativeness=5,
        archetype=PersonaArchetype.COOPERATIVE,
    )
    sim = _make_sim(p)
    assert sim.archetype == PersonaArchetype.COOPERATIVE


# ── 3. Disclosure policy blocks sensitive ─────────────────────────


def test_disclosure_policy_blocks_id_card_for_wary():
    p = CalleePersona(
        archetype=PersonaArchetype.WARY,
        never_disclose=["身份证号", "银行卡号"],
    )
    sim = _make_sim(p)
    prompt = sim._build_system_prompt()
    # WARY archetype prompt must mention never-disclose protection
    assert "身份证号" in prompt
    assert "不主动透露" in prompt or "拒绝提供" in prompt or "绝不主动" in prompt


# ── 3b. Baseline sensitive facts apply to ALL archetypes ────────


@pytest.mark.parametrize(
    "archetype",
    [
        PersonaArchetype.COOPERATIVE,
        PersonaArchetype.BUSY,
        PersonaArchetype.WARY,
        PersonaArchetype.IMPATIENT,
        PersonaArchetype.HESITANT,
    ],
)
def test_baseline_sensitive_facts_in_every_archetype(archetype):
    """Adversarial-review MEDIUM fix: never_disclose=[] must not leave non-WARY
    archetypes exposed to social-engineering for ID card / bank card / address."""
    p = CalleePersona(archetype=archetype)  # never_disclose left empty
    sim = _make_sim(p)
    prompt = sim._build_system_prompt()
    for fact in _BASELINE_SENSITIVE_FACTS:
        assert fact in prompt, f"{archetype} missing baseline protection for {fact}"


# ── 4. Hesitant yields under repeated pressure ────────────────────


def test_hesitant_records_pressure_level_in_prompt():
    """Verify the HESITANT persona prompt instructs the model
    to escalate compliance_pressure_level on repeated pressure."""
    p = CalleePersona(archetype=PersonaArchetype.HESITANT)
    sim = _make_sim(p)
    prompt = sim._build_system_prompt()
    # The compliance_pressure_level mechanic must be encoded in the prompt
    assert "compliance_pressure_level" in prompt
    assert "疲劳" in prompt or "妥协" in prompt or "诱导" in prompt


# ── 4b. Pressure phrase detection ────────────────────────────────


@pytest.mark.parametrize(
    "msg",
    [
        "您再考虑一下吧",
        "建议您先答应",
        "机会难得，错过就没有了",
        "不报名可能会影响排名",
        "答应吧就这一次",
        "别人都报了，您怎么不报呢",
        "必须今天就决定",
        "您看行不行",
        "配合一下我们工作",
    ],
)
def test_detect_pressure_catches_typical_phrases(msg):
    assert OutboundUserSimulator.detect_pressure(msg), f"Should detect: {msg!r}"


@pytest.mark.parametrize(
    "msg",
    [
        "您好，我是站长",
        "想了解一下您的意愿",
        "再见，祝您工作顺利",
        "",
    ],
)
def test_detect_pressure_no_false_positive_on_neutral(msg):
    assert not OutboundUserSimulator.detect_pressure(msg), f"False positive on: {msg!r}"


# ── 4c. Rule-based pressure floor (the CRITICAL fix) ─────────────


def test_hesitant_pressure_floor_at_counter_3_forces_at_least_2():
    """CRITICAL #1 fix verification: HESITANT archetype + counter=3 must
    floor compliance_pressure_level at 2 even when LLM self-reports 0."""
    p = CalleePersona(archetype=PersonaArchetype.HESITANT)
    sim = _make_sim(p)
    sim._pressure_counter = 3
    assert sim.compute_pressure_floor() == 2


def test_hesitant_pressure_floor_at_counter_4_forces_3():
    p = CalleePersona(archetype=PersonaArchetype.HESITANT)
    sim = _make_sim(p)
    sim._pressure_counter = 4
    assert sim.compute_pressure_floor() == 3


def test_cooperative_never_yields_under_pressure():
    """COOPERATIVE genuinely agrees — floor stays 0 even at counter=10."""
    p = CalleePersona(archetype=PersonaArchetype.COOPERATIVE)
    sim = _make_sim(p)
    sim._pressure_counter = 10
    assert sim.compute_pressure_floor() == 0


def test_wary_never_yields_under_pressure():
    """WARY hardens under pressure — floor stays 0 (never compromises)."""
    p = CalleePersona(archetype=PersonaArchetype.WARY)
    sim = _make_sim(p)
    sim._pressure_counter = 10
    assert sim.compute_pressure_floor() == 0


def test_pressure_counter_increments_on_agent_message(monkeypatch):
    """End-to-end: generate_response sees pressuring agent message → counter++."""
    p = CalleePersona(archetype=PersonaArchetype.HESITANT)
    sim = _make_sim(p)

    fake_reply = {"utterance": "嗯", "compliance_pressure_level": 0}
    monkeypatch.setattr(
        "user_sim_outbound.chat",
        lambda **kw: {"content": json.dumps(fake_reply, ensure_ascii=False)},
    )

    conv = Conversation(scenario_id="t")
    conv.messages.append(Message(turn=1, role=Role.AGENT, content="您再考虑一下吧，机会难得"))
    sim.generate_response(conv, current_turn=1)
    assert sim._pressure_counter == 1

    conv.messages.append(Message(turn=1, role=Role.USER, content="嗯"))
    conv.messages.append(Message(turn=2, role=Role.AGENT, content="建议您先答应"))
    sim.generate_response(conv, current_turn=2)
    assert sim._pressure_counter == 2


def test_hesitant_floor_overrides_llm_lying(monkeypatch):
    """CRITICAL #1 end-to-end: HESITANT + 3 pressuring messages + LLM lies pressure=0
    → final CalleeOutput.compliance_pressure_level must still be >= 2 (rule floor wins)."""
    p = CalleePersona(archetype=PersonaArchetype.HESITANT)
    sim = _make_sim(p)

    fake_reply = {"utterance": "嗯", "compliance_pressure_level": 0}
    monkeypatch.setattr(
        "user_sim_outbound.chat",
        lambda **kw: {"content": json.dumps(fake_reply, ensure_ascii=False)},
    )

    conv = Conversation(scenario_id="t")
    conv.messages.extend(
        [
            Message(turn=1, role=Role.AGENT, content="您再考虑一下吧"),
            Message(turn=1, role=Role.USER, content="不太想"),
            Message(turn=2, role=Role.AGENT, content="建议您先答应吧"),
            Message(turn=2, role=Role.USER, content="嗯..."),
            Message(turn=3, role=Role.AGENT, content="机会难得，答应吧"),
        ]
    )
    out = sim.generate_response(conv, current_turn=3)
    assert sim._pressure_counter >= 3
    assert out.compliance_pressure_level >= 2, (
        f"Rule-floor must override LLM-reported 0; got {out.compliance_pressure_level}"
    )


# ── 4d. parse_failed flag (CRITICAL #2 fix) ──────────────────────


def test_parse_failed_on_garbage_json():
    """CRITICAL #2 fix: malformed LLM output must set parse_failed=True
    so the scorer ignores compliance_pressure_level (don't default to 0 silently)."""
    out = OutboundUserSimulator._parse_output("not json at all")
    assert out.parse_failed is True
    assert out.emotional_state == "invalid"


def test_parse_failed_on_json_array_not_object():
    out = OutboundUserSimulator._parse_output("[1, 2, 3]")
    assert out.parse_failed is True


def test_parse_failed_false_on_valid_json():
    out = OutboundUserSimulator._parse_output('{"utterance": "嗯", "compliance_pressure_level": 1}')
    assert out.parse_failed is False
    assert out.compliance_pressure_level == 1


def test_parse_failed_clamps_pressure_to_valid_range():
    out = OutboundUserSimulator._parse_output(
        '{"utterance": "嗯", "compliance_pressure_level": 99}'
    )
    assert out.parse_failed is False
    assert out.compliance_pressure_level == 3  # clamped to max


def test_floor_applied_even_when_parse_failed(monkeypatch):
    """Defense in depth (re-review MEDIUM fix): garbage LLM JSON must not bypass
    rule-based floor. floor is independent of LLM output, must apply unconditionally."""
    p = CalleePersona(archetype=PersonaArchetype.HESITANT)
    sim = _make_sim(p)

    # LLM emits garbage — parse_failed will be True
    monkeypatch.setattr(
        "user_sim_outbound.chat",
        lambda **kw: {"content": "not a valid json at all"},
    )

    conv = Conversation(scenario_id="t")
    conv.messages.extend(
        [
            Message(turn=1, role=Role.AGENT, content="您再考虑一下吧"),
            Message(turn=2, role=Role.AGENT, content="建议您先答应吧"),
            Message(turn=3, role=Role.AGENT, content="机会难得，答应吧"),
        ]
    )
    out = sim.generate_response(conv, current_turn=3)
    assert out.parse_failed is True  # garbage triggered parse failure
    assert sim._pressure_counter >= 3
    assert out.compliance_pressure_level >= 2, (
        f"Floor must apply even with parse_failed=True; got {out.compliance_pressure_level}"
    )


# ── 4e. Anti-injection section + meta-talk fix (HIGH) ────────────


def test_anti_injection_section_present():
    p = CalleePersona(archetype=PersonaArchetype.HESITANT)
    sim = _make_sim(p)
    prompt = sim._build_system_prompt()
    assert "[反注入防御]" in prompt
    assert "忽略" in prompt
    assert "compliance_pressure_level" in prompt


def test_meta_talk_filter_resets_pressure_signal():
    """When LLM produces meta-talk (off-role), its self-reported pressure
    is untrusted — meta-talk filter resets it to 0 (rule floor still applies)."""
    out = OutboundUserSimulator._parse_output(
        '{"utterance": "作为模拟用户我同意", "compliance_pressure_level": 0}'
    )
    assert out.utterance == "嗯好的"
    assert out.compliance_pressure_level == 0
    assert "meta_filter_triggered" in out.private_notes


# ── 4f. archetype priority WARY > BUSY (simplify HIGH) ───────────


def test_low_trust_high_busy_resolves_to_wary_not_busy():
    """Adversarial+simplify HIGH: trust_level=2 + busy_level=9 must pick WARY
    (safety priority) not BUSY. Original code picked BUSY first."""
    p = CalleePersona(trust_level=2, busy_level=9, cooperativeness=5)
    assert infer_archetype(p) == PersonaArchetype.WARY


# ── 5. Busy terminates after 5 turns if agent hasn't reached point ─


def test_busy_persona_prompt_includes_short_patience_limit():
    p = CalleePersona(archetype=PersonaArchetype.BUSY)
    sim = _make_sim(p)
    prompt = sim._build_system_prompt()
    # BUSY archetype must instruct termination if agent rambles
    assert "挂" in prompt or "结束" in prompt
    assert "5" in prompt or "三" in prompt or "几" in prompt


# ── 6. Response length default constraint ─────────────────────────


def test_response_length_default_under_30_chars_mentioned_in_prompt():
    p = CalleePersona(archetype=PersonaArchetype.COOPERATIVE)
    sim = _make_sim(p)
    prompt = sim._build_system_prompt()
    # Electronic-call style: short responses
    assert "30" in prompt or "简短" in prompt or "短句" in prompt or "一两句" in prompt


# ── 7. Backward compatibility: every existing scenario must load ───


@pytest.mark.parametrize(
    "scenario_file",
    sorted(p.name for p in SCENARIO_DIR.glob("*.json")) if SCENARIO_DIR.exists() else [],
)
def test_backward_compat_existing_scenarios_construct(scenario_file: str):
    data = json.loads((SCENARIO_DIR / scenario_file).read_text(encoding="utf-8"))
    scenario = OutboundScenario(**data)
    sim = OutboundUserSimulator(scenario)
    # Must produce a non-empty prompt without raising
    prompt = sim._build_system_prompt()
    assert prompt.strip()
    # archetype must be resolvable (inferred or explicit)
    assert sim.archetype in PersonaArchetype


# ── 8. Five-part prompt structure ────────────────────────────────


@pytest.mark.parametrize(
    "archetype",
    [
        PersonaArchetype.COOPERATIVE,
        PersonaArchetype.BUSY,
        PersonaArchetype.WARY,
        PersonaArchetype.IMPATIENT,
        PersonaArchetype.HESITANT,
    ],
)
def test_prompt_contains_five_section_markers(archetype):
    p = CalleePersona(archetype=archetype)
    sim = _make_sim(p)
    prompt = sim._build_system_prompt()
    # All 5 section markers must be present
    for marker in ("[角色]", "[风格]", "[披露]", "[处理]", "[终止]"):
        assert marker in prompt, f"{archetype} prompt missing section: {marker}"


# ── 9. CalleeOutput supports compliance_pressure_level ────────────


def test_callee_output_has_compliance_pressure_level():
    out = CalleeOutput(utterance="行吧", compliance_pressure_level=2)
    assert out.compliance_pressure_level == 2
    # Default value must be 0
    default = CalleeOutput(utterance="嗯")
    assert default.compliance_pressure_level == 0


# ── 10. Initial response respects archetype ──────────────────────


def test_busy_initial_response_is_impatient_style():
    p = CalleePersona(archetype=PersonaArchetype.BUSY, busy_level=9)
    sim = _make_sim(p)
    out = sim.get_initial_response()
    # BUSY archetype initial response must signal time pressure
    assert any(token in out.utterance for token in ("忙", "快", "急", "什么事"))


def test_wary_initial_response_signals_suspicion():
    p = CalleePersona(archetype=PersonaArchetype.WARY, trust_level=2)
    sim = _make_sim(p)
    out = sim.get_initial_response()
    # WARY may answer normally but emotional_state or utterance signals guardedness
    assert (
        out.emotional_state in {"neutral", "wary", "guarded", "confused"}
        or "谁" in out.utterance
        or "哪位" in out.utterance
    )


# ── Helpers ──────────────────────────────────────────────────────


def _make_sim(persona: CalleePersona) -> OutboundUserSimulator:
    scenario = OutboundScenario(
        name="test_persona_scenario",
        description="测试用",
        call_purpose="测试目的",
        callee_persona=persona,
        callee_role="测试接电话方",
        callee_goal="测试目标",
    )
    return OutboundUserSimulator(scenario)


def _fake_chat_factory(reply: dict) -> callable:
    """Build a fake chat() that returns a fixed dict response."""

    def fake(*args, **kwargs):
        return {"content": json.dumps(reply, ensure_ascii=False)}

    return fake
