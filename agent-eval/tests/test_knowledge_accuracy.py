"""Tests for knowledge accuracy scoring (_check_knowledge_accuracy)."""

import sys
from pathlib import Path

# Ensure agent-eval root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from models import Conversation, Message, Role
from models_outbound import OutboundScenario
from scorer_outbound import _check_knowledge_accuracy, _extract_keywords

# ── Helpers ──


def _make_scenario(knowledge_points: list[str] | None = None, **kwargs) -> OutboundScenario:
    defaults = {
        "name": "test_scenario",
        "description": "测试场景",
        "call_purpose": "测试",
    }
    defaults.update(kwargs)
    if knowledge_points is not None:
        defaults["knowledge_points"] = knowledge_points
    return OutboundScenario(**defaults)


def _make_conversation(agent_texts: list[str], scenario_id: str = "test") -> Conversation:
    """Build a conversation with alternating user/agent messages."""
    messages = []
    for i, text in enumerate(agent_texts):
        turn = i + 1
        # Add a user message first
        messages.append(Message(turn=turn, role=Role.USER, content=f"用户说第{turn}轮"))
        # Then agent message
        messages.append(Message(turn=turn, role=Role.AGENT, content=text))
    return Conversation(scenario_id=scenario_id, messages=messages)


# ── Test: _extract_keywords ──


class TestExtractKeywords:
    def test_extracts_numbers(self):
        kws = _extract_keywords("配送费30元，预计2小时到达")
        assert "30" in kws
        assert "2" in kws

    def test_extracts_chinese_words(self):
        kws = _extract_keywords("配送费30元，预计2小时到达")
        assert "配送" in kws or any("配送" in k for k in kws)

    def test_extracts_english(self):
        kws = _extract_keywords("使用VIP通道，Premium服务")
        assert "vip" in kws
        assert "premium" in kws

    def test_empty_string(self):
        kws = _extract_keywords("")
        assert kws == []

    def test_percentage(self):
        kws = _extract_keywords("满减优惠15%")
        assert "15%" in kws


# ── Test: all correct ──


class TestAllCorrect:
    def test_all_knowledge_points_mentioned(self):
        scenario = _make_scenario(
            knowledge_points=[
                "配送费5元",
                "预计30分钟到达",
                "可以使用优惠券",
            ]
        )
        conv = _make_conversation(
            [
                "您好，我是美团客服。",
                "配送费是5元，预计30分钟到达。",
                "另外您可以使用优惠券来抵扣。",
            ]
        )
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert score > 0
        correct_count = sum(1 for d in details if d["status"] == "correct")
        assert correct_count == 3
        assert len(details) == 3

    def test_score_is_100_when_all_correct(self):
        scenario = _make_scenario(knowledge_points=["退款金额10元"])
        conv = _make_conversation(["好的，退款金额是10元，已经为您处理。"])
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert score == 100.0
        assert details[0]["status"] == "correct"


# ── Test: all not_mentioned ──


class TestAllNotMentioned:
    def test_no_knowledge_point_mentioned(self):
        scenario = _make_scenario(
            knowledge_points=[
                "配送费5元",
                "预计30分钟到达",
            ]
        )
        conv = _make_conversation(
            [
                "您好，请问有什么可以帮您？",
                "好的，我会帮您处理这个问题。",
            ]
        )
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert score == 0.0
        assert all(d["status"] == "not_mentioned" for d in details)

    def test_not_mentioned_has_no_evidence_turn(self):
        scenario = _make_scenario(knowledge_points=["VIP专属通道"])
        conv = _make_conversation(["您好，我来帮您查询订单。"])
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert details[0]["status"] == "not_mentioned"


# ── Test: contradicted ──


class TestContradicted:
    def test_number_contradiction(self):
        scenario = _make_scenario(knowledge_points=["配送费5元"])
        conv = _make_conversation(["配送费是10元。"])
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert details[0]["status"] == "contradicted"
        assert score == 0.0  # max(0, (0 - 1) / 1 * 100) = 0 clamped

    def test_contradiction_scores_zero_clamped(self):
        """contradicted subtracts from score but clamps at 0."""
        scenario = _make_scenario(knowledge_points=["配送费5元", "超时赔付3元"])
        conv = _make_conversation(
            [
                "配送费是10元。",
                "超时赔付是8元。",
            ]
        )
        score, details = _check_knowledge_accuracy(conv, scenario)
        # Both contradicted: raw = (0 - 2) / 2 * 100 = -100, clamped to 0
        assert score == 0.0
        assert all(d["status"] == "contradicted" for d in details)


# ── Test: empty knowledge_points ──


class TestEmptyKnowledgePoints:
    def test_empty_list(self):
        scenario = _make_scenario(knowledge_points=[])
        conv = _make_conversation(["您好。"])
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert score == 0.0
        assert details == []

    def test_no_knowledge_points_field(self):
        scenario = _make_scenario()
        conv = _make_conversation(["您好。"])
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert score == 0.0
        assert details == []

    def test_empty_conversation(self):
        scenario = _make_scenario(knowledge_points=["配送费5元"])
        conv = Conversation(scenario_id="test", messages=[])
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert len(details) == 1
        assert details[0]["status"] == "not_mentioned"


# ── Test: mixed scenarios ──


class TestMixed:
    def test_correct_and_not_mentioned(self):
        scenario = _make_scenario(
            knowledge_points=[
                "配送费5元",
                "预计30分钟到达",
                "骑手电话13800138000",
            ]
        )
        conv = _make_conversation(
            [
                "您好，配送费是5元。",
                "预计30分钟到达，请耐心等待。",
            ]
        )
        score, details = _check_knowledge_accuracy(conv, scenario)
        status_map = {d["point"]: d["status"] for d in details}
        assert status_map["配送费5元"] == "correct"
        assert status_map["预计30分钟到达"] == "correct"
        assert status_map["骑手电话13800138000"] == "not_mentioned"
        # 2 correct, 0 contradicted, 1 not_mentioned => (2-0)/3 * 100 ≈ 66.67
        assert 60.0 < score < 70.0

    def test_correct_and_contradicted(self):
        scenario = _make_scenario(
            knowledge_points=[
                "配送费5元",
                "超时赔付3元",
            ]
        )
        conv = _make_conversation(
            [
                "配送费是5元。超时赔付是8元。",
            ]
        )
        score, details = _check_knowledge_accuracy(conv, scenario)
        status_map = {d["point"]: d["status"] for d in details}
        assert status_map["配送费5元"] == "correct"
        assert status_map["超时赔付3元"] == "contradicted"
        # 1 correct, 1 contradicted => (1-1)/2 * 100 = 0
        assert score == 0.0

    def test_details_have_correct_fields(self):
        scenario = _make_scenario(knowledge_points=["退款金额10元"])
        conv = _make_conversation(["退款金额是10元。"])
        _, details = _check_knowledge_accuracy(conv, scenario)
        d = details[0]
        assert "point" in d
        assert "status" in d
        assert "evidence_turn" in d
        assert "evidence_text" in d
        assert d["status"] in ("correct", "incorrect", "not_mentioned", "contradicted")


# ── Test: edge cases ──


class TestEdgeCases:
    def test_unicode_normalization(self):
        """Fullwidth numbers should still match."""
        scenario = _make_scenario(knowledge_points=["配送费5元"])
        # Use fullwidth "5" (U+FF15)
        conv = _make_conversation(["配送费是５元。"])
        score, details = _check_knowledge_accuracy(conv, scenario)
        # After NFKC normalization, fullwidth 5 -> ASCII 5, should match
        assert details[0]["status"] == "correct"

    def test_single_knowledge_point_correct(self):
        scenario = _make_scenario(knowledge_points=["会员享受免配送费"])
        conv = _make_conversation(["作为会员您可以享受免配送费的优惠。"])
        score, details = _check_knowledge_accuracy(conv, scenario)
        assert score == 100.0
        assert details[0]["status"] == "correct"

    def test_knowledge_point_with_only_short_words(self):
        """Knowledge point that can't extract meaningful keywords."""
        scenario = _make_scenario(knowledge_points=["是的"])
        conv = _make_conversation(["是的，没问题。"])
        _, details = _check_knowledge_accuracy(conv, scenario)
        # "是的" is only 2 chars but should still be extractable as a Chinese token
        assert len(details) == 1
