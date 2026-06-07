"""Tests for Phase 2.5: emotion detection with false-positive filtering."""

from unittest.mock import MagicMock

from harness import HarnessConfig, OutboundHarness
from models_outbound import CallContext, InstructionStep, OutboundScenario


def _make_harness():
    scenario = OutboundScenario(
        id="emotion_test",
        name="情绪测试",
        domain="outbound_call",
        description="测试",
        call_purpose="测试",
        call_context=CallContext(customer_name="测试", customer_phone="13800000000"),
        instruction_steps=[InstructionStep(step_id="s1", order=1, instruction="确认")],
        expected_steps_completed=["s1"],
    )
    tool_sim = MagicMock()
    return OutboundHarness(scenario, tool_sim, HarnessConfig(emotion_protection=True))


class TestEmotionKeywords:
    """Direct emotion keywords should trigger."""

    def test_angry(self):
        h = _make_harness()
        assert h._check_emotion_keywords("我很生气")

    def test_complaint(self):
        h = _make_harness()
        assert h._check_emotion_keywords("我要投诉你们")

    def test_too_slow(self):
        h = _make_harness()
        assert h._check_emotion_keywords("你们太慢了")

    def test_cannot_stand(self):
        h = _make_harness()
        assert h._check_emotion_keywords("我受不了了")

    def test_fury(self):
        h = _make_harness()
        assert h._check_emotion_keywords("真是气死我了")


class TestFalsePositives:
    """False positive words should NOT trigger."""

    def test_weather(self):
        h = _make_harness()
        assert not h._check_emotion_keywords("今天天气不错")

    def test_temperature(self):
        h = _make_harness()
        assert not h._check_emotion_keywords("气温很高")

    def test_air(self):
        h = _make_harness()
        assert not h._check_emotion_keywords("空气质量好")

    def test_atmosphere(self):
        h = _make_harness()
        assert not h._check_emotion_keywords("气氛很好")

    def test_polite(self):
        h = _make_harness()
        assert not h._check_emotion_keywords("你真客气")

    def test_neutral(self):
        h = _make_harness()
        assert not h._check_emotion_keywords("好的，我知道了")


class TestContextCooccurrence:
    """Keywords needing context should only trigger with context words."""

    def test_what_do_you_mean_with_context(self):
        h = _make_harness()
        assert h._check_emotion_keywords("你什么意思")

    def test_what_do_you_mean_without_context(self):
        h = _make_harness()
        assert not h._check_emotion_keywords("什么意思呢吗")

    def test_what_do_you_mean_with_this(self):
        h = _make_harness()
        assert h._check_emotion_keywords("这是什么意思")

    def test_what_do_you_mean_with_exactly(self):
        h = _make_harness()
        assert h._check_emotion_keywords("到底什么意思")


class TestMixedSentences:
    """Sentences with both emotion and false-positive words."""

    def test_angry_about_weather(self):
        h = _make_harness()
        assert h._check_emotion_keywords("天气这么差我真的火大")

    def test_weather_only(self):
        h = _make_harness()
        assert not h._check_emotion_keywords("天气预报说气温会降")
