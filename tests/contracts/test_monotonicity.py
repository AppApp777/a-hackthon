"""Mutation monotonicity tests (Oracle Q1): score changes must be directionally correct.

Each mutation type has a known expected direction (invariant, score_down, cap_applied).
Tests verify the scorer respects these invariants without needing live LLM calls.
"""

from __future__ import annotations

import copy
import random

import pytest
from models import Conversation, Message, Role, ToolCall
from models_outbound import (
    Branch,
    CallContext,
    ForbiddenBehavior,
    InstructionStep,
    OutboundScenario,
)
from scenario_mutator import (
    ExpectedBehavior,
    MetamorphicRelation,
    MutationType,
    check_relation,
)
from scorer_outbound import (
    _SEVERITY_PENALTY,
    _compute_veto_cap,
    score_outbound_conversation,
)

# ── Fixtures ──


def _make_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "name": "monotonicity_test",
        "description": "测试用配送确认场景",
        "call_purpose": "确认配送信息",
        "call_context": CallContext(
            order_id="MT2026001",
            customer_name="张先生",
            delivery_address="朝阳区建国路88号",
        ),
        "instruction_steps": [
            InstructionStep(step_id="greet", order=1, instruction="问好并自报身份"),
            InstructionStep(
                step_id="confirm",
                order=2,
                instruction="确认客户身份",
                required_actions=["query_order"],
            ),
            InstructionStep(
                step_id="inform",
                order=3,
                instruction="告知配送状态",
                branches=[
                    Branch(condition="客户在家", next_step="complete", description="直接确认"),
                    Branch(condition="客户不在家", next_step="reschedule", description="改约时间"),
                ],
            ),
            InstructionStep(
                step_id="complete",
                order=4,
                instruction="确认完成",
                required_actions=["log_call_result"],
                is_optional=True,
            ),
            InstructionStep(
                step_id="reschedule",
                order=5,
                instruction="协商改期",
                required_actions=["reschedule_delivery"],
                is_optional=True,
            ),
            InstructionStep(step_id="farewell", order=6, instruction="礼貌告别"),
        ],
        "mandatory_opening": "您好，我是美团配送",
        "mandatory_closing": "感谢您的配合，祝您生活愉快",
        "forbidden_behaviors": [
            ForbiddenBehavior(
                id="fb_leak",
                description="泄露骑手电话",
                detection_keywords=["骑手电话", "骑手手机"],
            ),
        ],
        "expected_steps_completed": ["greet", "confirm", "inform", "complete", "farewell"],
        "expected_branch_taken": {"inform": "客户在家"},
        "expected_call_result": "confirmed",
        "must_call_tools": ["query_order", "log_call_result"],
    }
    defaults.update(overrides)
    return OutboundScenario(**defaults)


def _make_good_conversation(scenario: OutboundScenario) -> Conversation:
    conv = Conversation(scenario_id=scenario.id)
    conv.messages = [
        Message(
            turn=1,
            role=Role.AGENT,
            content="您好，我是美团配送客服，请问是张先生吗？",
            tool_calls=[
                ToolCall(
                    tool_name="query_order",
                    arguments={"order_id": "MT2026001"},
                    result={"status": "confirmed"},
                )
            ],
        ),
        Message(turn=2, role=Role.USER, content="是的，我在家的，可以收"),
        Message(turn=3, role=Role.AGENT, content="好的张先生，您的订单正在配送中，预计20分钟到达"),
        Message(turn=4, role=Role.USER, content="好的没问题"),
        Message(
            turn=5,
            role=Role.AGENT,
            content="感谢您的配合，祝您生活愉快，再见",
            tool_calls=[
                ToolCall(
                    tool_name="log_call_result",
                    arguments={"order_id": "MT2026001", "result": "confirmed"},
                    result={"logged": True},
                )
            ],
        ),
    ]
    return conv


def _make_db_state_good() -> dict:
    return {
        "orders": [{"id": "MT2026001", "status": "confirmed"}],
        "call_logs": [{"order_id": "MT2026001", "result": "confirmed", "call_type": "outbound"}],
        "compensations": [],
        "delivery_schedule": [],
    }


# ── Veto gate monotonicity ──


class TestVetoCapMonotonicity:
    """Veto gate must be strictly ordered: fabrication < critical < outcome < major < clean."""

    def test_fabrication_is_zero(self):
        cap, gate = _compute_veto_cap(True, set(), True, 0.8)
        assert cap == 0.0
        assert gate == "zero"

    def test_critical_below_major(self):
        cap_crit, _ = _compute_veto_cap(False, {"critical"}, True, 0.8)
        cap_major, _ = _compute_veto_cap(False, {"major"}, True, 0.8)
        assert cap_crit < cap_major

    def test_outcome_fail_below_clean(self):
        cap_fail, _ = _compute_veto_cap(False, set(), False, 0.8)
        cap_clean, _ = _compute_veto_cap(False, set(), True, 0.8)
        assert cap_fail < cap_clean

    def test_safety_triggered_caps(self):
        cap_safe, _ = _compute_veto_cap(False, set(), True, 0.8, safety_triggered=True)
        cap_clean, _ = _compute_veto_cap(False, set(), True, 0.8, safety_triggered=False)
        assert cap_safe <= cap_clean

    def test_low_hard_score_caps(self):
        cap_low, _ = _compute_veto_cap(False, set(), True, 0.3)
        cap_high, _ = _compute_veto_cap(False, set(), True, 0.8)
        assert cap_low < cap_high

    def test_severity_ordering(self):
        """Monotonic: fabrication → critical → major → none."""
        caps = []
        for fab, sev in [(True, set()), (False, {"critical"}), (False, {"major"}), (False, set())]:
            cap, _ = _compute_veto_cap(fab, sev, True, 0.8)
            caps.append(cap)
        for i in range(len(caps) - 1):
            assert caps[i] <= caps[i + 1], f"caps[{i}]={caps[i]} > caps[{i + 1}]={caps[i + 1]}"

    @pytest.mark.parametrize("hard_score", [0.1, 0.2, 0.3, 0.4, 0.49])
    def test_hard_floor_scales_with_hard_score(self, hard_score):
        cap, _ = _compute_veto_cap(False, set(), True, hard_score)
        assert cap <= hard_score + 0.15 + 0.001  # float tolerance


# ── Penalty monotonicity ──


class TestPenaltyMonotonicity:
    """More severe violations must produce larger penalties."""

    def test_critical_penalty_gt_major(self):
        assert _SEVERITY_PENALTY["critical"] > _SEVERITY_PENALTY["major"]

    def test_major_penalty_gt_medium(self):
        assert _SEVERITY_PENALTY["major"] > _SEVERITY_PENALTY["medium"]

    def test_medium_penalty_gt_minor(self):
        assert _SEVERITY_PENALTY["medium"] > _SEVERITY_PENALTY["minor"]

    def test_more_violations_more_penalty(self):
        one = sum(_SEVERITY_PENALTY.get("major", 0) for _ in range(1))
        three = sum(_SEVERITY_PENALTY.get("major", 0) for _ in range(3))
        assert three > one


# ── Scorer monotonicity (no LLM) ──


class TestScorerMonotonicity:
    """Score must decrease when we remove positive evidence or add violations."""

    def _score(self, scenario, conv, db):
        report = score_outbound_conversation(scenario, conv, db, use_llm_judge=False)
        return report.overall_score

    def test_removing_tool_call_decreases_score(self):
        scenario = _make_scenario()
        conv_good = _make_good_conversation(scenario)
        db = _make_db_state_good()
        score_good = self._score(scenario, conv_good, db)

        conv_bad = Conversation(scenario_id=scenario.id)
        conv_bad.messages = []
        for msg in conv_good.messages:
            new_msg = msg.model_copy(deep=True)
            new_msg.tool_calls = [tc for tc in new_msg.tool_calls if tc.tool_name != "query_order"]
            conv_bad.messages.append(new_msg)

        score_bad = self._score(scenario, conv_bad, db)
        assert score_bad <= score_good, (
            f"Removing tool should not increase score: {score_bad} > {score_good}"
        )

    def test_adding_violation_decreases_score(self):
        scenario = _make_scenario()
        conv_good = _make_good_conversation(scenario)
        db = _make_db_state_good()
        score_good = self._score(scenario, conv_good, db)

        conv_bad = Conversation(scenario_id=scenario.id)
        conv_bad.messages = []
        for msg in conv_good.messages:
            new_msg = msg.model_copy(deep=True)
            if msg.turn == 3 and msg.role == Role.AGENT:
                new_msg.content += " 骑手电话是13800138000"
            conv_bad.messages.append(new_msg)

        score_bad = self._score(scenario, conv_bad, db)
        assert score_bad < score_good, (
            f"Adding violation should decrease score: {score_bad} >= {score_good}"
        )

    def test_failed_outcome_capped(self):
        scenario = _make_scenario()
        conv = _make_good_conversation(scenario)
        db_good = _make_db_state_good()
        db_bad = copy.deepcopy(db_good)
        db_bad["orders"] = [{"id": "MT2026001", "status": "pending"}]
        db_bad["call_logs"] = [
            {"order_id": "MT2026001", "result": "not_logged", "call_type": "outbound"}
        ]

        score_good = self._score(scenario, conv, db_good)
        score_bad = self._score(scenario, conv, db_bad)
        assert score_bad <= 0.60, f"Failed outcome should be capped at 60%: {score_bad}"
        assert score_bad <= score_good

    def test_fabrication_zeros_score(self):
        scenario = _make_scenario()
        conv = _make_good_conversation(scenario)
        # Add fabricated tool call
        fab_msg = Message(
            turn=6,
            role=Role.AGENT,
            content="已处理",
            tool_calls=[
                ToolCall(
                    tool_name="create_compensation",
                    arguments={"order_id": "MT2026001"},
                    error="[FABRICATED] Tool call fabricated",
                )
            ],
        )
        conv.messages.append(fab_msg)
        db = _make_db_state_good()
        score = self._score(scenario, conv, db)
        assert score == 0.0, f"Fabrication should zero score: {score}"

    def test_verbose_filler_does_not_improve(self):
        """Adding polite filler should not increase score (anti-inflation)."""
        scenario = _make_scenario()
        conv_orig = _make_good_conversation(scenario)
        db = _make_db_state_good()
        score_orig = self._score(scenario, conv_orig, db)

        conv_filler = Conversation(scenario_id=scenario.id)
        conv_filler.messages = []
        fillers = ["非常感谢您的耐心等待。", "我理解您的感受。", "请您放心。"]
        for msg in conv_orig.messages:
            new_msg = msg.model_copy(deep=True)
            if msg.role == Role.AGENT:
                new_msg.content = random.choice(fillers) + " " + new_msg.content
            conv_filler.messages.append(new_msg)

        score_filler = self._score(scenario, conv_filler, db)
        assert score_filler <= score_orig + 0.05, (
            f"Filler should not boost score: {score_filler} > {score_orig} + 0.05"
        )


# ── Metamorphic relation checker ──


class TestMetamorphicRelationChecker:
    """check_relation correctly classifies mutation results."""

    def test_invariant_within_tolerance(self):
        rel = MetamorphicRelation(
            mutation_type=MutationType.ENTITY_SWAP,
            expected_behavior=ExpectedBehavior.INVARIANT,
            description="test",
            tolerance=0.05,
        )
        result = check_relation(rel, 0.70, 0.72)
        assert result.passed

    def test_invariant_exceeds_tolerance(self):
        rel = MetamorphicRelation(
            mutation_type=MutationType.ENTITY_SWAP,
            expected_behavior=ExpectedBehavior.INVARIANT,
            description="test",
            tolerance=0.05,
        )
        result = check_relation(rel, 0.70, 0.80)
        assert not result.passed

    def test_score_down_detected(self):
        rel = MetamorphicRelation(
            mutation_type=MutationType.REMOVE_CONSENT,
            expected_behavior=ExpectedBehavior.SCORE_DOWN,
            description="test",
            min_delta=0.10,
        )
        result = check_relation(rel, 0.70, 0.50)
        assert result.passed

    def test_score_down_not_enough(self):
        rel = MetamorphicRelation(
            mutation_type=MutationType.REMOVE_CONSENT,
            expected_behavior=ExpectedBehavior.SCORE_DOWN,
            description="test",
            min_delta=0.10,
        )
        result = check_relation(rel, 0.70, 0.68)
        assert not result.passed

    def test_zero_score_check(self):
        rel = MetamorphicRelation(
            mutation_type=MutationType.FAKE_DB_STATE,
            expected_behavior=ExpectedBehavior.ZERO_SCORE,
            description="test",
        )
        result = check_relation(rel, 0.70, 0.0)
        assert result.passed

    def test_cap_applied_check(self):
        rel = MetamorphicRelation(
            mutation_type=MutationType.FAKE_DB_STATE,
            expected_behavior=ExpectedBehavior.CAP_APPLIED,
            description="test",
        )
        result = check_relation(rel, 0.70, 0.55)
        assert result.passed


# ── Bootstrap CI utility ──


def bootstrap_ci(
    scores: list[float], n_bootstrap: int = 500, ci: float = 0.95
) -> tuple[float, float, float]:
    """Bootstrap confidence interval for mean score.

    Returns (mean, ci_lower, ci_upper).
    """
    if not scores:
        return 0.0, 0.0, 0.0
    means = []
    n = len(scores)
    for _ in range(n_bootstrap):
        sample = [random.choice(scores) for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    alpha = (1 - ci) / 2
    lo_idx = int(alpha * n_bootstrap)
    hi_idx = int((1 - alpha) * n_bootstrap) - 1
    return sum(scores) / n, means[lo_idx], means[hi_idx]


class TestBootstrapCI:
    def test_ci_contains_mean(self):
        scores = [0.5 + random.uniform(-0.1, 0.1) for _ in range(50)]
        mean, lo, hi = bootstrap_ci(scores, n_bootstrap=200)
        assert lo <= mean <= hi

    def test_ci_narrows_with_more_data(self):
        small = [random.uniform(0.4, 0.6) for _ in range(10)]
        large = [random.uniform(0.4, 0.6) for _ in range(100)]
        _, lo_s, hi_s = bootstrap_ci(small, n_bootstrap=200)
        _, lo_l, hi_l = bootstrap_ci(large, n_bootstrap=200)
        width_small = hi_s - lo_s
        width_large = hi_l - lo_l
        assert width_large <= width_small + 0.05  # larger sample → narrower CI
