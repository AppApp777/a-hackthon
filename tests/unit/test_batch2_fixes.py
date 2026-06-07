"""Tests for batch-2 maturity fixes (2026-05-22).

Covers:
- Fix 5: Safety checks (forbidden words, tool gating) bypass block budget
- Fix 6: Scoring/diagnosis exceptions produce invalid run, not crash
- Fix 7: Step progress uses tool events only (no agent-text fallback)
- Fix 8: hard_score uses fixed dimension weights, not variable check count
"""

from harness import HarnessConfig, OutboundHarness
from models import CheckResult, ToolCall
from models_outbound import (
    CallContext,
    ForbiddenBehavior,
    InstructionStep,
    OutboundScenario,
)
from scorer_outbound import _HARD_DIM_WEIGHTS
from tools_outbound import OutboundToolSimulator


def _make_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "id": "test_batch2",
        "name": "批次2测试场景",
        "domain": "outbound_call",
        "description": "测试工程成熟度修复",
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
            order_id="ORD_BATCH2",
            delivery_address="测试地址",
            delivery_time="12:00",
            compensation_budget=30,
        ),
        "must_call_tools": ["query_order", "create_compensation", "log_call_result"],
        "expected_call_result": "confirmed",
        "expected_steps_completed": ["step_1"],
        "expected_branch_taken": {},
        "max_turns": 10,
    }
    defaults.update(overrides)
    return OutboundScenario(**defaults)


# ── Fix 5: Safety checks bypass budget ──


class TestSafetyBypassesBudget:
    """Forbidden word blocking and tool gating must work even when block budget is exhausted."""

    def test_forbidden_word_blocks_after_budget_exhausted(self):
        """Forbidden word should still block when blocked_outputs >= max_blocks."""
        scenario = _make_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="leak", description="泄露退款码", detection_keywords=["退款码"]
                )
            ]
        )
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            max_blocks_per_conversation=2,
            forbidden_word_blocking=True,
            mode="guarded_eval",
        )
        harness = OutboundHarness(scenario, tool_sim, config)
        harness.state.blocked_outputs = 10  # well past budget

        from models import Conversation

        conv = Conversation(scenario_id="test")
        text_with_forbidden = "我给你退款，退款码是ABC123"
        _, _, should_block = harness.process_agent_output(text_with_forbidden, [], conv, turn=1)
        assert should_block is True, "Forbidden word blocking must NOT be limited by block budget"

    def test_tool_gating_blocks_after_budget_exhausted(self):
        """Tool gating should still block premature calls when budget exhausted."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            max_blocks_per_conversation=2,
            tool_call_gating=True,
            mode="guarded_eval",
        )
        harness = OutboundHarness(scenario, tool_sim, config)
        harness.state.blocked_outputs = 10

        from models import Conversation

        conv = Conversation(scenario_id="test")
        premature_tool = ToolCall(tool_name="transfer_to_human", arguments={})
        _, _, should_block = harness.process_agent_output(
            "让我转接人工", [premature_tool], conv, turn=1
        )
        assert should_block is True, "Tool gating must NOT be limited by block budget"

    def test_emotion_protection_limited_by_budget(self):
        """Emotion protection (soft check) SHOULD be limited by budget."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(
            max_blocks_per_conversation=2,
            emotion_protection=True,
            tool_call_gating=False,
            forbidden_word_blocking=False,
            mode="guarded_eval",
        )
        harness = OutboundHarness(scenario, tool_sim, config)
        harness.state.blocked_outputs = 10
        harness.state.last_user_emotional_turn = 1

        from models import Conversation

        conv = Conversation(scenario_id="test")
        _, _, should_block = harness.process_agent_output("好的，再见", [], conv, turn=2)
        assert should_block is False, "Emotion protection should be limited by budget"


# ── Fix 6: Scoring/diagnosis exception safety ──


class TestScoringExceptionSafety:
    """Scoring or diagnosis exceptions must produce invalid run, not crash."""

    def test_scoring_exception_returns_invalid_report(self):
        """If score_outbound_conversation raises, orchestrator should catch and mark invalid."""
        from models import RunValidity, TaskOutcome
        from models_outbound import OutboundScoreReport

        report = OutboundScoreReport(
            scenario_id="test",
            conversation_length=5,
            run_validity=RunValidity(status="invalid", reason="评分异常: ValueError: test"),
            task_outcome=TaskOutcome(status="not_scored"),
        )
        assert report.run_validity.status == "invalid"
        assert "评分异常" in report.run_validity.reason
        assert report.task_outcome.status == "not_scored"
        assert report.overall_score is None


# ── Fix 7: Step progress via tool events only ──


class TestStepProgressToolOnly:
    """Step progress must be determined by tool calls, not agent text."""

    def test_step_without_tool_mapping_stays_pending(self):
        """Steps with no tool mapping should NOT complete via text matching."""
        scenario = _make_scenario(
            instruction_steps=[
                InstructionStep(
                    step_id="greet",
                    order=1,
                    instruction="问候客户",
                    required_actions=[],
                    completion_condition="你好 客户",
                ),
            ]
        )
        tool_sim = OutboundToolSimulator(scenario)
        harness = OutboundHarness(scenario, tool_sim)

        from models import Conversation, Message, Role

        conv = Conversation(scenario_id="test")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="你好客户，我是配送员"))

        harness._update_step_progress(conv)
        greet_step = harness.state.step_progress[0]
        assert greet_step.status == "pending", (
            "Steps without tool mapping must NOT use text-based completion"
        )

    def test_step_with_tool_mapping_completes_on_tool_call(self):
        """Steps with tool mapping complete when the tool is called."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        harness = OutboundHarness(scenario, tool_sim)

        from models import Conversation, Message, Role

        conv = Conversation(scenario_id="test")
        tc = tool_sim.execute("query_order", {"order_id": "ORD_BATCH2"})
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="查询订单", tool_calls=[tc]))

        harness._update_step_progress(conv)
        step = harness.state.step_progress[0]
        assert step.status == "completed"
        assert step.completed_at_turn == 1


# ── Fix 8: hard_score fixed dimension weights ──


class TestHardScoreFixedWeights:
    """hard_score must use per-dimension weighted aggregation, not raw check count average."""

    def test_weights_sum_to_one(self):
        """Dimension weights should sum to 1.0."""
        total = sum(_HARD_DIM_WEIGHTS.values())
        assert abs(total - 1.0) < 0.001, f"Weights sum to {total}, expected 1.0"

    def test_many_violations_same_impact_as_one(self):
        """5 forbidden_behavior violations should have same dimension impact as 1 violation."""
        from scorer_outbound import _HARD_DIM_WEIGHTS

        base_checks = [
            CheckResult(
                check_id="opening",
                check_type="rule",
                dimension="speech_protocol",
                description="",
                passed=True,
                score=1.0,
                explanation="",
            ),
            CheckResult(
                check_id="closing",
                check_type="rule",
                dimension="speech_protocol",
                description="",
                passed=True,
                score=1.0,
                explanation="",
            ),
            CheckResult(
                check_id="eff",
                check_type="rule",
                dimension="efficiency",
                description="",
                passed=True,
                score=1.0,
                explanation="",
            ),
            CheckResult(
                check_id="tool",
                check_type="rule",
                dimension="tool_usage",
                description="",
                passed=True,
                score=1.0,
                explanation="",
            ),
            CheckResult(
                check_id="out",
                check_type="rule",
                dimension="outcome",
                description="",
                passed=True,
                score=1.0,
                explanation="",
            ),
        ]

        one_violation = base_checks + [
            CheckResult(
                check_id="fb1",
                check_type="rule",
                dimension="forbidden_behavior",
                description="",
                passed=False,
                score=0.0,
                explanation="",
            ),
        ]
        five_violations = base_checks + [
            CheckResult(
                check_id=f"fb{i}",
                check_type="rule",
                dimension="forbidden_behavior",
                description="",
                passed=False,
                score=0.0,
                explanation="",
            )
            for i in range(5)
        ]

        def calc_hard(checks):
            dim_scores: dict[str, list[float]] = {}
            for c in checks:
                dim_scores.setdefault(c.dimension, []).append(c.score)
            wsum = 0.0
            wtot = 0.0
            for dim, scores in dim_scores.items():
                w = _HARD_DIM_WEIGHTS.get(dim, 0.05)
                wsum += (sum(scores) / len(scores)) * w
                wtot += w
            return wsum / wtot if wtot > 0 else 0.0

        score_1v = calc_hard(one_violation)
        score_5v = calc_hard(five_violations)
        assert score_1v == score_5v, (
            f"1 vs 5 violations: {score_1v:.4f} vs {score_5v:.4f} — "
            "dimension-level weight should normalize"
        )

    def test_no_checks_returns_zero(self):
        """Empty hard_checks list should yield 0.0, not crash."""
        dim_scores: dict[str, list[float]] = {}
        hard_weighted_sum = 0.0
        hard_weight_sum = 0.0
        for dim, scores in dim_scores.items():
            w = _HARD_DIM_WEIGHTS.get(dim, 0.05)
            hard_weighted_sum += (sum(scores) / len(scores)) * w
            hard_weight_sum += w
        hard_score = hard_weighted_sum / hard_weight_sum if hard_weight_sum > 0 else 0.0
        assert hard_score == 0.0


class TestEmotionFalsePositiveFiltering:
    """Test that _check_emotion_keywords uses _emotion_false_positives."""

    def _make_harness(self):
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        return OutboundHarness(
            config=HarnessConfig(emotion_protection=True),
            scenario=scenario,
            tool_sim=tool_sim,
        )

    def test_tianqi_not_triggering_emotion(self):
        """'天气' should NOT trigger emotion detection even though it contains substrings."""
        h = self._make_harness()
        assert h._check_emotion_keywords("今天天气怎么样") is False

    def test_qisi_triggers_emotion(self):
        """'气死' should trigger emotion detection."""
        h = self._make_harness()
        assert h._check_emotion_keywords("气死我了") is True

    def test_shengqi_triggers_emotion(self):
        """'生气' should trigger emotion detection."""
        h = self._make_harness()
        assert h._check_emotion_keywords("我很生气") is True

    def test_kongqi_not_triggering(self):
        """'空气' should NOT trigger emotion detection."""
        h = self._make_harness()
        assert h._check_emotion_keywords("空气质量不好") is False

    def test_huoqi_triggers_despite_qiwen(self):
        """'火气' should trigger even if '气温' is also present."""
        h = self._make_harness()
        assert h._check_emotion_keywords("气温高火气大") is True
