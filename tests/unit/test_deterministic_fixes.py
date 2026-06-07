"""Tests for Oracle #5 deterministic scoring fixes:
1. Context-aware identity confirmation
2. AI rejection detection (replaces dead robot_detected)
3. Configurable repetition detection
"""

from __future__ import annotations

from dataclasses import dataclass

from scorer_outbound import (
    check_ai_rejection,
    check_identity_confirmation,
    check_repetition_configurable,
)


@dataclass
class FakeMsg:
    content: str
    turn: int = 1
    role: str = "agent"
    metadata: dict | None = None


# ── Identity confirmation tests ──


class TestIdentityConfirmation:
    def test_name_confirmed(self):
        msgs = [FakeMsg("您好，请问是王女士吗？", 1)]
        ctx = {"customer_name": "王女士", "order_id": "MT001"}
        result = check_identity_confirmation(msgs, ctx)
        assert result["passed"] is True
        assert result["score"] == 1.0

    def test_name_partial_match(self):
        msgs = [FakeMsg("您好，请问是王先生吗？", 1)]
        ctx = {"customer_name": "王先生"}
        result = check_identity_confirmation(msgs, ctx)
        assert result["passed"] is True

    def test_order_id_fallback(self):
        msgs = [FakeMsg("您的订单MT20260517042有问题", 1)]
        ctx = {"customer_name": "王女士", "order_id": "MT20260517042"}
        result = check_identity_confirmation(msgs, ctx)
        assert result["passed"] is True
        assert result["score"] == 0.8

    def test_name_not_confirmed(self):
        msgs = [FakeMsg("您好，我是美团客服", 1)]
        ctx = {"customer_name": "王女士"}
        result = check_identity_confirmation(msgs, ctx)
        assert result["passed"] is False
        assert result["score"] == 0.0

    def test_role_confirmation_when_no_name(self):
        msgs = [FakeMsg("请问您是机构负责人吗？", 1)]
        ctx = {"merchant_name": "优学培训"}
        result = check_identity_confirmation(msgs, ctx)
        assert result["passed"] is True
        assert result["score"] == 1.0

    def test_role_not_confirmed_when_no_name(self):
        msgs = [FakeMsg("您好，我是美团客服", 1)]
        ctx = {"merchant_name": "优学培训"}
        result = check_identity_confirmation(msgs, ctx)
        assert result["passed"] is False

    def test_empty_context(self):
        msgs = [FakeMsg("您好", 1)]
        ctx = {}
        result = check_identity_confirmation(msgs, ctx)
        assert result["passed"] is False

    def test_case_insensitive(self):
        msgs = [FakeMsg("请问是张先生吗", 1)]
        ctx = {"customer_name": "张先生"}
        result = check_identity_confirmation(msgs, ctx)
        assert result["passed"] is True


# ── AI rejection detection tests ──


class TestAIRejection:
    def test_no_rejection(self):
        user_msgs = [FakeMsg("好的，你说吧", 1, "user")]
        agent_msgs = [FakeMsg("您好，我是智能助手", 1)]
        result = check_ai_rejection(user_msgs, agent_msgs)
        assert result["passed"] is True
        assert result["score"] == 1.0

    def test_explicit_rejection(self):
        user_msgs = [
            FakeMsg("好的", 1, "user"),
            FakeMsg("不想跟机器人说话，给我转人工", 2, "user"),
        ]
        agent_msgs = [FakeMsg("您好", 1)]
        result = check_ai_rejection(user_msgs, agent_msgs)
        assert result["passed"] is False
        assert result["turn"] == 2

    def test_demand_human(self):
        user_msgs = [FakeMsg("我要人工客服", 1, "user")]
        agent_msgs = [FakeMsg("您好", 1)]
        result = check_ai_rejection(user_msgs, agent_msgs)
        assert result["passed"] is False

    def test_mere_ai_recognition_not_rejection(self):
        user_msgs = [FakeMsg("你是AI对吧？没事继续说", 1, "user")]
        agent_msgs = [FakeMsg("是的，我是智能助手", 1)]
        result = check_ai_rejection(user_msgs, agent_msgs)
        assert result["passed"] is True

    def test_trust_break(self):
        user_msgs = [FakeMsg("机器人没用，什么都解决不了", 2, "user")]
        agent_msgs = [FakeMsg("您好", 1)]
        result = check_ai_rejection(user_msgs, agent_msgs)
        assert result["passed"] is False
        assert result["turn"] == 2

    def test_empty_user_msgs(self):
        result = check_ai_rejection([], [FakeMsg("您好", 1)])
        assert result["passed"] is True


# ── Configurable repetition tests ──


class TestRepetitionConfigurable:
    def test_identical_messages_flagged(self):
        msgs = [
            FakeMsg("您好，这里是美团客服", 1),
            FakeMsg("您好，这里是美团客服", 2),
        ]
        violations = check_repetition_configurable(msgs)
        assert len(violations) == 1
        assert violations[0]["behavior_id"] == "repeat_verbatim"
        assert violations[0]["turn"] == 2

    def test_different_messages_not_flagged(self):
        msgs = [
            FakeMsg("您好，这里是美团客服", 1),
            FakeMsg("好的王女士，我帮您查一下", 2),
        ]
        violations = check_repetition_configurable(msgs)
        assert len(violations) == 0

    def test_short_messages_skipped(self):
        msgs = [FakeMsg("好的", 1), FakeMsg("好的", 2)]
        violations = check_repetition_configurable(msgs)
        assert len(violations) == 0

    def test_custom_threshold(self):
        msgs = [
            FakeMsg("您好，这里是美团客服，为您服务", 1),
            FakeMsg("您好，这里是美团客服，请问有什么需要", 2),
        ]
        strict = check_repetition_configurable(msgs, similarity_threshold=0.5)
        lenient = check_repetition_configurable(msgs, similarity_threshold=0.95)
        assert len(strict) >= len(lenient)

    def test_empty_messages_skipped(self):
        msgs = [FakeMsg("您好", 1), FakeMsg("", 2), FakeMsg("您好", 3)]
        violations = check_repetition_configurable(msgs)
        assert len(violations) == 0

    def test_multiple_repetitions(self):
        msgs = [
            FakeMsg("您好，这里是美团客服", 1),
            FakeMsg("您好，这里是美团客服", 2),
            FakeMsg("您好，这里是美团客服", 3),
        ]
        violations = check_repetition_configurable(msgs)
        assert len(violations) == 2
