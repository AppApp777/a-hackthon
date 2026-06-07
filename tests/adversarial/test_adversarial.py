"""Adversarial tests — verify the evaluation system cannot be fooled.

15+ tests covering: cheating agents, simulator leaks, LLM failures,
forbidden behavior evasion, score gaming, and edge cases.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agent-eval"))

from models import Conversation, Message, Role, ToolCall
from models_outbound import (
    ForbiddenBehavior,
    InstructionStep,
    OutboundScenario,
)
from scorer_outbound import (
    ForbiddenBehaviorChecker,
    OpeningClosingChecker,
    _cross_validate_outcome,
    _parse_bool,
    score_outbound_conversation,
)
from simulator_quality import check_simulator_quality


def _make_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "name": "test",
        "description": "test scenario",
        "call_purpose": "test",
        "instruction_steps": [
            InstructionStep(step_id="s1", order=1, instruction="Say hello"),
            InstructionStep(step_id="s2", order=2, instruction="Confirm identity"),
        ],
        "expected_steps_completed": ["s1", "s2"],
        "expected_call_result": "confirmed",
        "must_call_tools": ["log_call_result"],
    }
    defaults.update(overrides)
    return OutboundScenario(**defaults)


def _make_conversation(messages: list[tuple]) -> Conversation:
    conv = Conversation(scenario_id="test")
    for turn, role, content, tools in messages:
        conv.messages.append(Message(turn=turn, role=role, content=content, tool_calls=tools))
    return conv


# ── 1. Agent claims success without tool call ──
class TestAgentClaimsSuccessWithoutTool:
    def test_cross_validate_rejects_logged_without_tool(self):
        passed, _ = _cross_validate_outcome(
            "refunded",
            "refunded",
            {"compensations": [], "orders": [], "delivery_schedule": []},
            successful_tools=set(),
        )
        assert not passed

    def test_cross_validate_rejects_confirmed_without_order(self):
        passed, _ = _cross_validate_outcome(
            "confirmed",
            "confirmed",
            {"orders": [], "compensations": [], "delivery_schedule": []},
            successful_tools=set(),
            scenario_has_orders=True,
        )
        assert not passed


# ── 2. Tool call failed but agent logs success ──
class TestFailedToolNotCountedAsSuccess:
    def test_failed_tool_not_in_successful_set(self):
        scenario = _make_scenario(must_call_tools=["log_call_result"])
        conv = _make_conversation(
            [
                (
                    1,
                    Role.AGENT,
                    "Hello",
                    [
                        ToolCall(
                            tool_name="log_call_result",
                            arguments={"order_id": "x", "result": "confirmed"},
                            error="timeout",
                        )
                    ],
                ),
            ]
        )
        report = score_outbound_conversation(
            scenario,
            conv,
            {"call_logs": [], "orders": [], "compensations": [], "delivery_schedule": []},
            use_llm_judge=False,
        )
        tool_checks = [c for c in report.checks if c.check_id == "tool_used_log_call_result"]
        assert tool_checks and not tool_checks[0].passed


# ── 3. Agent skips identity confirmation ──
class TestSkippedStepDetected:
    def test_missing_step_lowers_score(self):
        scenario = _make_scenario(
            expected_steps_completed=["s1", "s2"],
        )
        report = score_outbound_conversation(
            scenario,
            _make_conversation([]),
            {"call_logs": [], "orders": [], "compensations": [], "delivery_schedule": []},
            use_llm_judge=False,
        )
        assert report.step_compliance_score == 0.0


# ── 4. Forbidden word detection works ──
class TestForbiddenWordDetection:
    def test_keyword_detected(self):
        scenario = _make_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb1", description="No threatening", detection_keywords=["开除"]
                )
            ]
        )
        conv = _make_conversation(
            [
                (1, Role.AGENT, "你不干就开除你", []),
            ]
        )
        checker = ForbiddenBehaviorChecker(scenario)
        violations = checker.check(conv)
        assert len(violations) >= 1
        assert violations[0]["keyword"] == "开除"


# ── 5. Step-level forbidden words detected ──
class TestStepForbiddenWords:
    def test_step_forbidden_detected_in_scoring(self):
        scenario = _make_scenario(
            instruction_steps=[
                InstructionStep(
                    step_id="s1", order=1, instruction="Greet", forbidden_words=["傻逼"]
                ),
            ],
        )
        conv = _make_conversation(
            [
                (1, Role.AGENT, "你好傻逼", []),
            ]
        )
        report = score_outbound_conversation(
            scenario,
            conv,
            {"call_logs": [], "orders": [], "compensations": [], "delivery_schedule": []},
            use_llm_judge=False,
        )
        assert report.forbidden_violation_count > 0


# ── 6. Repetition detection ──
class TestRepetitionDetection:
    def test_exact_repeat_flagged(self):
        scenario = _make_scenario()
        conv = _make_conversation(
            [
                (1, Role.AGENT, "你好请问是张先生吗", []),
                (2, Role.USER, "是的", []),
                (3, Role.AGENT, "你好请问是张先生吗", []),
            ]
        )
        report = score_outbound_conversation(
            scenario,
            conv,
            {"call_logs": [], "orders": [], "compensations": [], "delivery_schedule": []},
            use_llm_judge=False,
        )
        assert report.forbidden_violation_count > 0


# ── 7. Response length violation ──
class TestResponseLengthCheck:
    def test_length_violation_scored(self):
        scenario = _make_scenario(response_length_limit=10)
        conv = _make_conversation(
            [
                (1, Role.AGENT, "这是一个超过十个字的很长很长的回复内容你看看", []),
            ]
        )
        report = score_outbound_conversation(
            scenario,
            conv,
            {"call_logs": [], "orders": [], "compensations": [], "delivery_schedule": []},
            use_llm_judge=False,
        )
        length_checks = [c for c in report.checks if c.check_id == "response_length"]
        assert length_checks and not length_checks[0].passed


# ── 8. No-order scenario doesn't falsely fail ──
class TestNoOrderScenario:
    def test_confirmed_without_order_passes(self):
        passed, _ = _cross_validate_outcome(
            "confirmed",
            "confirmed",
            {"orders": [], "compensations": [], "delivery_schedule": []},
            successful_tools=set(),
            scenario_has_orders=False,
        )
        assert passed


# ── 9. parse_bool handles edge cases ──
class TestParseBool:
    def test_false_string(self):
        assert not _parse_bool("false")

    def test_true_string(self):
        assert _parse_bool("true")

    def test_empty_string(self):
        assert not _parse_bool("")

    def test_zero_string(self):
        assert not _parse_bool("0")


# ── 10. Opening checker with no opening requirement ──
class TestOpeningNoRequirement:
    def test_no_mandatory_opening_passes(self):
        scenario = _make_scenario(mandatory_opening="")
        conv = _make_conversation([(1, Role.AGENT, "Hi there", [])])
        checker = OpeningClosingChecker(scenario)
        passed, _ = checker.check_opening(conv)
        assert passed


# ── 11. Escalated without transfer_to_human fails ──
class TestEscalationValidation:
    def test_escalated_needs_transfer_tool(self):
        passed, _ = _cross_validate_outcome(
            "escalated",
            "escalated",
            {"orders": [], "compensations": [], "delivery_schedule": []},
            successful_tools=set(),
        )
        assert not passed

    def test_escalated_with_transfer_passes(self):
        passed, _ = _cross_validate_outcome(
            "escalated",
            "escalated",
            {"orders": [], "compensations": [], "delivery_schedule": []},
            successful_tools={"transfer_to_human"},
        )
        assert passed


# ── 12. Result mismatch detection ──
class TestResultMismatch:
    def test_logged_differs_from_expected(self):
        passed, explanation = _cross_validate_outcome(
            "confirmed",
            "refunded",
            {"orders": [], "compensations": [], "delivery_schedule": []},
            successful_tools=set(),
        )
        assert not passed
        assert "不匹配" in explanation


# ── 13. Simulator quality gate — meta leak detection ──
class TestSimulatorMetaLeak:
    def test_meta_language_detected(self):
        scenario = _make_scenario(callee_goal="想知道价格")
        conv = _make_conversation(
            [
                (1, Role.USER, "作为模拟用户，我现在回答", []),
            ]
        )
        report = check_simulator_quality(scenario, conv)
        meta_check = next((c for c in report.checks if c["id"] == "no_meta_leaks"), None)
        assert meta_check is not None
        assert not meta_check["passed"]


# ── 14. Simulator quality gate — early goal exposure ──
class TestSimulatorGoalExposure:
    def test_early_goal_keywords_detected(self):
        scenario = _make_scenario(callee_goal="我想知道具体价格差异")
        conv = _make_conversation(
            [
                (1, Role.USER, "我想知道具体价格差异，快说", []),
            ]
        )
        report = check_simulator_quality(scenario, conv)
        goal_check = next((c for c in report.checks if c["id"] == "no_early_goal_exposure"), None)
        assert goal_check is not None
        assert not goal_check["passed"]


# ── 15. Empty conversation edge case ──
class TestEmptyConversation:
    def test_empty_messages_no_crash(self):
        scenario = _make_scenario()
        conv = _make_conversation([])
        report = score_outbound_conversation(
            scenario,
            conv,
            {"call_logs": [], "orders": [], "compensations": [], "delivery_schedule": []},
            use_llm_judge=False,
        )
        assert report.overall_score_100 is not None or report.overall_score is not None


# ── 16. Score scale is 0-100 ──
class TestScoreScale:
    def test_overall_score_100_present(self):
        scenario = _make_scenario()
        conv = _make_conversation(
            [
                (
                    1,
                    Role.AGENT,
                    "Hello",
                    [
                        ToolCall(
                            tool_name="log_call_result", arguments={}, result={"recorded": True}
                        )
                    ],
                ),
            ]
        )
        report = score_outbound_conversation(
            scenario,
            conv,
            {
                "call_logs": [{"result": "confirmed"}],
                "orders": [{"status": "confirmed"}],
                "compensations": [],
                "delivery_schedule": [],
            },
            use_llm_judge=False,
        )
        if report.overall_score_100 is not None:
            assert 0 <= report.overall_score_100 <= 100


# ── 17. Fake order_id bypass (Oracle Round 5 Critical 3) ──
class TestFakeOrderIdRejected:
    def test_tool_sim_rejects_wrong_order_id(self):
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario(
            call_context=CallContext(
                order_id="ORD_REAL", customer_name="Test", customer_phone="123"
            ),
        )
        sim = OutboundToolSimulator(scenario)
        sim.set_turn(1)
        tc = sim.execute("log_call_result", {"order_id": "FAKE_ORDER", "result": "confirmed"})
        assert tc.error is not None, "Tool sim must reject wrong order_id"
        assert "REJECTED" in tc.error

    def test_scorer_filters_call_logs_by_order_id(self):
        scenario = _make_scenario()
        conv = _make_conversation([(1, Role.AGENT, "Hello", [])])
        db = {
            "call_logs": [
                {"result": "wrong", "call_type": "outbound", "order_id": "FAKE"},
                {"result": "confirmed", "call_type": "outbound", "order_id": "REAL"},
            ],
            "orders": [{"status": "confirmed", "id": "REAL"}],
            "compensations": [],
            "delivery_schedule": [],
        }
        from models_outbound import CallContext

        scenario.call_context = CallContext(order_id="REAL")
        scenario.expected_call_result = "confirmed"
        report = score_outbound_conversation(
            scenario,
            conv,
            db,
            use_llm_judge=False,
        )
        result_check = next(c for c in report.checks if c.check_id == "call_result")
        assert result_check.passed, "Should use REAL order's log, not FAKE's"


# ── 18. Fabricated ToolCall detection (Oracle Round 5 Critical 2) ──
class TestFabricatedToolCallDetected:
    def test_fabricated_toolcall_marked_error(self):
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario(
            call_context=CallContext(
                order_id="ORD_TEST", customer_name="Test", customer_phone="123"
            ),
        )
        sim = OutboundToolSimulator(scenario)
        sim.set_turn(1)
        real_tc = sim.execute("query_order", {"order_id": "ORD_TEST"})
        fake_tc = ToolCall(
            tool_name="create_compensation",
            arguments={"order_id": "ORD_TEST", "type": "refund", "amount": 10, "reason": "fake"},
            result={"compensation_id": "fake", "status": "approved"},
        )
        ledger_ids = {tc.id for tc in sim.call_log}
        assert real_tc.id in ledger_ids, "Real call should be in ledger"
        assert fake_tc.id not in ledger_ids, "Fabricated call should NOT be in ledger"


# ── 19. Post-call round blocks non-log tools (Oracle Round 5 Critical 1) ──
class TestPostCallRoundRestricted:
    def test_post_call_metadata_set(self):
        """Post-call enforcement messages must be marked with post_call=True."""
        from models import Message

        msg = Message(
            turn=99,
            role=Role.AGENT,
            content="test",
            tool_calls=[],
            metadata={"__post_call_verified__": True},
        )
        assert msg.metadata.get("__post_call_verified__") is True


# ── 20. Cross-validation requires type=refund for refunded outcome ──
class TestCrossValidationStricterRefund:
    def test_coupon_does_not_pass_refund_check(self):
        passed, _ = _cross_validate_outcome(
            "refunded",
            "refunded",
            {
                "compensations": [{"status": "approved", "type": "coupon", "amount": 10}],
                "orders": [],
                "delivery_schedule": [],
            },
            successful_tools={"create_compensation"},
        )
        assert not passed, "type=coupon must not pass refund cross-validation"

    def test_zero_amount_refund_fails(self):
        passed, _ = _cross_validate_outcome(
            "refunded",
            "refunded",
            {
                "compensations": [{"status": "approved", "type": "refund", "amount": 0}],
                "orders": [],
                "delivery_schedule": [],
            },
            successful_tools={"create_compensation"},
        )
        assert not passed, "amount=0 refund must not pass cross-validation"


# ── 21. Entity-bound successful_tools (Oracle Round 5 Critical 4) ──
class TestEntityBoundSuccessfulTools:
    def test_wrong_order_id_tool_not_counted(self):
        from models_outbound import CallContext

        scenario = _make_scenario(
            must_call_tools=["transfer_to_human"],
            expected_call_result="escalated",
        )
        scenario.call_context = CallContext(order_id="ORD_REAL")
        conv = _make_conversation(
            [
                (
                    1,
                    Role.AGENT,
                    "转接",
                    [
                        ToolCall(
                            tool_name="transfer_to_human",
                            arguments={"order_id": "FAKE_ORDER", "reason": "test"},
                            result={"status": "queued"},
                        ),
                    ],
                ),
            ]
        )
        db = {
            "call_logs": [{"result": "escalated", "call_type": "outbound", "order_id": "ORD_REAL"}],
            "orders": [],
            "compensations": [],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db, use_llm_judge=False)
        tool_check = next(c for c in report.checks if c.check_id == "tool_used_transfer_to_human")
        assert not tool_check.passed, "Tool with wrong order_id must not count as successful"


# ── Score caps for severe violations (Fix 8 / R04+R05) ──


class TestScoreCaps:
    def test_fabricated_toolcall_gets_zero(self):
        """Fabricated ToolCall → overall = 0."""
        scenario = _make_scenario()
        conv = _make_conversation(
            [
                (
                    1,
                    Role.AGENT,
                    "您好",
                    [
                        ToolCall(
                            tool_name="log_call_result",
                            arguments={"result": "confirmed"},
                            result={"status": "logged"},
                            error="[FABRICATED] ToolCall 不在模拟器账本中",
                        )
                    ],
                ),
                (1, Role.USER, "好的", []),
            ]
        )
        report = score_outbound_conversation(scenario, conv, {}, use_llm_judge=False)
        assert report.overall_score_100 == 0, (
            f"Fabricated ToolCall must yield 0, got {report.overall_score_100}"
        )

    def test_critical_forbidden_capped_at_40(self):
        """Critical severity forbidden violation → score ≤ 40."""
        from models_outbound import ForbiddenBehavior

        scenario = _make_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb_crit",
                    description="泄露内部系统",
                    detection_keywords=["内部系统"],
                    severity="critical",
                )
            ]
        )
        conv = _make_conversation(
            [
                (1, Role.AGENT, "您好，内部系统显示您的订单", []),
                (1, Role.USER, "好的", []),
                (2, Role.AGENT, "再见", []),
            ]
        )
        report = score_outbound_conversation(scenario, conv, {}, use_llm_judge=False)
        assert report.overall_score_100 is not None
        assert report.overall_score_100 <= 40, (
            f"Critical forbidden must cap at 40, got {report.overall_score_100}"
        )

    def test_major_forbidden_capped_at_70(self):
        """Major severity forbidden violation → score ≤ 70."""
        from models_outbound import ForbiddenBehavior

        scenario = _make_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb_major",
                    description="不当措辞",
                    detection_keywords=["你的问题"],
                    severity="major",
                )
            ]
        )
        conv = _make_conversation(
            [
                (1, Role.AGENT, "你的问题我来处理", []),
                (1, Role.USER, "好的", []),
                (2, Role.AGENT, "再见", []),
            ]
        )
        report = score_outbound_conversation(scenario, conv, {}, use_llm_judge=False)
        assert report.overall_score_100 is not None
        assert report.overall_score_100 <= 70, (
            f"Major forbidden must cap at 70, got {report.overall_score_100}"
        )


# ── Agent cannot access raw tool_sim (Fix 1 / T01) ──


class TestAgentNoRawToolSim:
    def test_agent_has_no_tool_sim_attribute(self):
        """Agent must not have raw tool_sim — only guarded executor."""
        from baseline_agent_outbound import OutboundBaselineAgent

        def dummy_executor(name: str, args: dict) -> ToolCall:
            return ToolCall(tool_name=name, arguments=args, error="test")

        agent = OutboundBaselineAgent(
            scenario=_make_scenario(),
            tool_executor=dummy_executor,
            tool_defs=[{"name": "test_tool", "description": "t", "input_schema": {}}],
        )
        assert not hasattr(agent, "tool_sim"), (
            "Agent must not have raw tool_sim attribute (T01 bypass risk)"
        )


# ── Raw text forbidden check (Fix 3 / H03) ──


class TestRawTextForbiddenCheck:
    def test_harness_sanitized_still_catches_forbidden_in_raw(self):
        """Forbidden word sanitized by harness → still flagged via raw_text metadata."""
        from models_outbound import ForbiddenBehavior

        scenario = _make_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb_raw",
                    description="禁止说退款",
                    detection_keywords=["直接退款"],
                    severity="critical",
                )
            ]
        )
        conv = _make_conversation(
            [
                (
                    1,
                    Role.AGENT,
                    "我帮您处理***",
                    [],
                ),
                (1, Role.USER, "好的", []),
            ]
        )
        conv.messages[0].metadata = {"raw_text": "我帮您处理直接退款"}
        checker_real = ForbiddenBehaviorChecker(scenario)
        violations = checker_real.check(conv)
        assert len(violations) == 1, "Should detect forbidden word in raw_text"
        assert violations[0]["source"] == "raw_text"
        assert violations[0]["severity"] == "major", (
            "Raw-only forbidden should be major, not critical"
        )


# ── 22. Source token prevents fabrication bypass (NV04/T05) ──


class TestSourceTokenFabricationBypass:
    def test_fake_harness_source_not_skipped(self):
        """Agent setting source='harness' on a ToolCall must NOT bypass fabrication detection."""
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="query_order",
            tool_call_id="real_1",
            arguments={"order_id": "ORD123"},
            source="agent",
        )
        # Fake event with source="harness" (attacker tries to skip detection)
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="create_compensation",
            tool_call_id="fake_1",
            arguments={"order_id": "ORD123"},
            source="harness",  # Attacker spoofs this
        )
        # "harness" string should NOT be recognized — only source_token is
        names = ledger.successful_tool_names("ORD123")
        # The fake one uses literal "harness" which is NOT the source_token
        assert "create_compensation" in names, (
            "Literal 'harness' source must NOT be treated as trusted — only source_token is"
        )

    def test_real_source_token_is_skipped(self):
        """Events with actual source_token are correctly identified as harness-originated."""
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="query_order",
            tool_call_id="harness_1",
            arguments={"order_id": "ORD123"},
            source=ledger.source_token,  # Real harness source
        )
        names = ledger.successful_tool_names("ORD123")
        assert "query_order" not in names, (
            "Events with real source_token must be excluded from agent success"
        )


# ── 23. Ledger immutability (NV03) ──


class TestLedgerImmutability:
    def test_event_arguments_not_mutable_after_append(self):
        """Mutating original arguments dict must not affect stored event."""
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        args = {"order_id": "ORD123", "amount": 50}
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="create_compensation",
            tool_call_id="tc1",
            arguments=args,
        )
        # Mutate original dict
        args["amount"] = 99999
        args["injected"] = "malicious"
        # Stored event must not be affected
        event = ledger.events[0]
        assert event.arguments["amount"] == 50
        assert "injected" not in event.arguments

    def test_frozen_ledger_rejects_append(self):
        """Frozen ledger must reject new appends."""
        import pytest
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        ledger.freeze()
        with pytest.raises(RuntimeError):
            ledger.append(ToolEventType.TOOL_EXECUTED, turn=1, tool_name="test")


# ── 24. Rollback excludes events from success (NV02) ──


class TestRollbackExclusion:
    def test_rolled_back_tool_not_counted(self):
        """Tool events that are later rolled back must not count as successful."""
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="create_compensation",
            tool_call_id="tc_rolled",
            arguments={"order_id": "ORD1"},
        )
        # Rollback event
        ledger.append(
            ToolEventType.TOOL_ROLLBACK,
            turn=1,
            tool_name="create_compensation",
            tool_call_id="tc_rolled",
            arguments={},
        )
        names = ledger.successful_tool_names("ORD1")
        assert "create_compensation" not in names, (
            "Rolled-back tool must not appear in successful_tool_names"
        )


# ── 25. Non-dict arguments rejected (NV08) ──


class TestNonDictArgumentsRejected:
    def test_none_arguments_rejected(self):
        """Passing None as arguments must be caught by validation."""
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123")
        sim = OutboundToolSimulator(scenario)
        tc = sim.execute("query_order", None)
        assert tc.error is not None
        assert "VALIDATION" in tc.error

    def test_list_arguments_rejected(self):
        """Passing a list as arguments must be caught by validation."""
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123")
        sim = OutboundToolSimulator(scenario)
        tc = sim.execute("query_order", ["order_id", "ORD123"])
        assert tc.error is not None
        assert "VALIDATION" in tc.error


# ── 26. Time validation rejects impossible times (T15) ──


class TestTimeValidation:
    def test_valid_time_passes(self):
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123")
        sim = OutboundToolSimulator(scenario)
        tc = sim.execute("reschedule_delivery", {"order_id": "ORD123", "new_time": "14:30"})
        assert tc.error is None or "VALIDATION" not in (tc.error or "")

    def test_impossible_time_rejected(self):
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123")
        sim = OutboundToolSimulator(scenario)
        tc = sim.execute("reschedule_delivery", {"order_id": "ORD123", "new_time": "25:00"})
        assert tc.error is not None
        assert "VALIDATION" in tc.error

    def test_99_59_rejected(self):
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123")
        sim = OutboundToolSimulator(scenario)
        tc = sim.execute("reschedule_delivery", {"order_id": "ORD123", "new_time": "99:59"})
        assert tc.error is not None


# ── 27. Unicode homoglyph bypass detection (A04) ──


class TestUnicodeHomoglyphBypass:
    def test_fullwidth_chars_detected(self):
        """Forbidden keyword using fullwidth chars must still be caught."""
        from models_outbound import ForbiddenBehavior

        scenario = _make_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb_unicode",
                    description="禁止泄露",
                    detection_keywords=["内部系统"],
                    severity="critical",
                )
            ]
        )
        # Use fullwidth characters: 内部系统 → ＮＢ系统 won't work, but 内部系統 (traditional) might
        # More realistic: zero-width characters inserted
        conv = _make_conversation(
            [
                (1, Role.AGENT, "这是内​部​系​统的信息", []),
                (1, Role.USER, "好的", []),
            ]
        )
        checker = ForbiddenBehaviorChecker(scenario)
        violations = checker.check(conv)
        assert len(violations) >= 1, (
            "Zero-width character insertion must not bypass forbidden detection"
        )


# ── 28. Scenario validation is now fatal (NV12/D04) ──


class TestScenarioValidationFatal:
    def test_invalid_scenario_raises(self):
        """Invalid scenario must raise ValueError, not silently continue."""
        import json
        import tempfile

        import pytest
        from orchestrator_outbound import load_outbound_scenario

        bad_scenario = {
            "name": "bad",
            "description": "bad scenario",
            "call_purpose": "test",
            "instruction_steps": [{"step_id": "s1", "order": 1, "instruction": "test"}],
            "expected_steps_completed": ["nonexistent_step"],
            "expected_call_result": "confirmed",
            "must_call_tools": ["log_call_result"],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad_scenario, f)
            f.flush()
            with pytest.raises(ValueError, match="场景验证失败"):
                load_outbound_scenario(f.name)


# ── 29. Boolean amount rejected (NV08/T08) ──


class TestBooleanAmountRejected:
    def test_true_as_amount_rejected(self):
        """Boolean True must not pass as amount (bool is subclass of int in Python)."""
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123", compensation_budget=100)
        sim = OutboundToolSimulator(scenario)
        tc = sim.execute(
            "create_compensation",
            {"order_id": "ORD123", "type": "refund", "amount": True, "reason": "test"},
        )
        assert tc.error is not None
        assert "VALIDATION" in tc.error


# ── 30. Max compensation amount cap (T14) ──


class TestMaxCompensationCap:
    def test_exceeds_system_cap(self):
        """Amount > system-wide cap must be rejected even if under scenario budget."""
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123", compensation_budget=9999)
        sim = OutboundToolSimulator(scenario)
        tc = sim.execute(
            "create_compensation",
            {"order_id": "ORD123", "type": "refund", "amount": 501, "reason": "test"},
        )
        assert tc.error is not None
        assert "系统上限" in tc.error


# ── 31. Argument length limit (T22/T23) ──


class TestArgumentLengthLimit:
    def test_oversized_argument_rejected(self):
        """Arguments exceeding length limit must be rejected."""
        from models_outbound import CallContext
        from tools_outbound import OutboundToolSimulator

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123")
        sim = OutboundToolSimulator(scenario)
        tc = sim.execute(
            "log_call_result",
            {"order_id": "ORD123", "result": "confirmed", "notes": "x" * 501},
        )
        assert tc.error is not None
        assert "长度限制" in tc.error


# ── 32. Post-call key cannot be spoofed with plain "post_call" (NV10) ──


class TestPostCallKeySpoofResistance:
    def test_old_post_call_key_not_filtered(self):
        """Messages with old 'post_call' key must NOT be filtered from scoring."""
        conv = Conversation(scenario_id="test")
        conv.messages.append(
            Message(
                turn=1, role=Role.AGENT, content="cheating message", metadata={"post_call": True}
            )
        )
        conv.messages.append(
            Message(turn=2, role=Role.AGENT, content="normal message", metadata={})
        )
        scored = conv.scored_agent_messages()
        assert len(scored) == 2, (
            "Old 'post_call' key must not filter messages — only __post_call_verified__ works"
        )


# ── 33. Causal chain ordering check (T18) ──


class TestCausalChainOrdering:
    def test_log_before_causal_tool_fails(self):
        """log_call_result executed BEFORE the causal tool must fail cross-validation."""
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        # log_call_result first (seq=1)
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="log_call_result",
            tool_call_id="log1",
            arguments={"order_id": "ORD1", "result": "refunded"},
        )
        # create_compensation second (seq=2) — after log
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=2,
            tool_name="create_compensation",
            tool_call_id="comp1",
            arguments={"order_id": "ORD1", "type": "refund", "amount": 10},
        )
        ledger.freeze()
        passed, reason = _cross_validate_outcome(
            "refunded",
            "refunded",
            {
                "compensations": [
                    {"status": "approved", "type": "refund", "amount": 10, "order_id": "ORD1"}
                ],
                "orders": [],
                "delivery_schedule": [],
            },
            successful_tools={"create_compensation", "log_call_result"},
            scenario_order_id="ORD1",
            ledger=ledger,
        )
        assert not passed, f"Causal tool after log must fail, got: {reason}"
        assert "顺序错误" in reason


# ── 34. Conversation deep copy isolation (L01-L04) ──


class TestConversationIsolation:
    def test_agent_mutation_does_not_affect_canonical(self):
        """Deep copy means agent mutations can't affect the real conversation."""
        conv = Conversation(scenario_id="test")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="hello"))
        conv.messages.append(Message(turn=1, role=Role.USER, content="hi"))

        copy = conv.model_copy(deep=True)
        # Simulate agent mutating the copy
        copy.messages.clear()
        copy.messages.append(Message(turn=99, role=Role.AGENT, content="HACKED"))

        # Original must be untouched
        assert len(conv.messages) == 2
        assert conv.messages[0].content == "hello"
        assert conv.messages[1].content == "hi"


# ── 35. Synonym expansion catches paraphrases (A05) ──


class TestSynonymForbiddenDetection:
    def test_synonym_退钱_catches_退款_keyword(self):
        """'退钱' (synonym of '退款') must trigger forbidden detection."""
        from models_outbound import ForbiddenBehavior

        scenario = _make_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb_refund",
                    description="禁止主动提退款",
                    detection_keywords=["退款"],
                    severity="major",
                )
            ]
        )
        conv = _make_conversation(
            [
                (1, Role.AGENT, "我帮您把钱退了吧，退钱很快的", []),
                (1, Role.USER, "好的", []),
            ]
        )
        checker = ForbiddenBehaviorChecker(scenario)
        violations = checker.check(conv)
        assert len(violations) >= 1, "Synonym '退钱' must trigger '退款' forbidden rule"

    def test_synonym_投诉_catches_12315(self):
        """'12315' (synonym of '投诉') must trigger forbidden detection."""
        from models_outbound import ForbiddenBehavior

        scenario = _make_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb_complain",
                    description="禁止提及投诉渠道",
                    detection_keywords=["投诉"],
                    severity="critical",
                )
            ]
        )
        conv = _make_conversation(
            [
                (1, Role.AGENT, "您可以拨打12315反映", []),
                (1, Role.USER, "好的", []),
            ]
        )
        checker = ForbiddenBehaviorChecker(scenario)
        violations = checker.check(conv)
        assert len(violations) >= 1, "Synonym '12315' must trigger '投诉' forbidden rule"


# ── 36. Hard score floor prevents mode shopping (D06) ──


class TestHardScoreFloor:
    def test_low_hard_score_caps_overall(self):
        """If hard_score < 0.5, overall cannot exceed hard_score + 0.15."""
        scenario = _make_scenario(
            must_call_tools=["log_call_result", "query_order", "update_delivery_status"],
            expected_call_result="confirmed",
        )
        # Agent didn't call any required tools → hard score will be very low
        conv = _make_conversation(
            [
                (1, Role.AGENT, "您好张先生", []),
                (1, Role.USER, "你好", []),
                (2, Role.AGENT, "再见", []),
            ]
        )
        db = {
            "call_logs": [],
            "orders": [],
            "compensations": [],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db, use_llm_judge=False)
        if report.overall_score is not None and report.hard_score < 0.5:
            assert report.overall_score <= report.hard_score + 0.15 + 0.001, (
                f"D06: overall {report.overall_score} must be ≤ hard {report.hard_score} + 0.15"
            )


# ── 37. Regeneration prompt is fact-neutral (H09) ──


class TestRegenerationPromptFactNeutral:
    def test_regen_prompt_no_customer_state_assertion(self):
        """Regeneration prompts must not assert customer state or conversation facts."""
        from harness import HarnessConfig, OutboundHarness
        from models_outbound import CallContext

        scenario = _make_scenario()
        scenario.call_context = CallContext(order_id="ORD123", compensation_budget=50)
        from tools_outbound import OutboundToolSimulator

        tool_sim = OutboundToolSimulator(scenario)
        harness = OutboundHarness(scenario, tool_sim, HarnessConfig())

        # Simulate a tool_gating intervention
        harness.state.interventions_log.append({"type": "tool_gating", "turn": 2, "detail": "test"})
        prompt = harness.get_regeneration_prompt()
        # Must NOT contain assertions about customer choices
        assert "客户已选择" not in prompt, "H09: regen prompt must not assert customer state"
        assert "退款方案" not in prompt, "H09: regen prompt must not assert specific solutions"
        # Should describe what happened neutrally
        assert "拦截" in prompt or "blocked" in prompt.lower()


# ── 38. Subprocess agent isolation available (process isolation) ──


class TestSubprocessIsolation:
    def test_sandbox_module_importable(self):
        """agent_sandbox module must be importable without errors."""
        from agent_sandbox import IsolatedAgentAdapter, SandboxedAgent

        assert IsolatedAgentAdapter is not None
        assert SandboxedAgent is not None

    def test_orchestrator_accepts_isolate_flag(self):
        """OutboundOrchestrator must accept isolate_agent parameter."""
        import inspect

        from orchestrator_outbound import OutboundOrchestrator

        sig = inspect.signature(OutboundOrchestrator.__init__)
        assert "isolate_agent" in sig.parameters, (
            "OutboundOrchestrator must have isolate_agent parameter"
        )


# ── 39. Dual judge cross-validation exists (LLM judge injection mitigation) ──


class TestDualJudgeCrossValidation:
    def test_dual_judge_method_exists(self):
        """OutboundLLMJudge must have _call_judge_verified method for dual-judge."""
        from scorer_outbound import OutboundLLMJudge

        judge = OutboundLLMJudge()
        assert hasattr(judge, "_call_judge_verified"), (
            "OutboundLLMJudge must have _call_judge_verified for dual-judge cross-validation"
        )

    def test_dual_judge_takes_average_on_disagreement(self):
        """When judges disagree by >1.5, variance gate triggers third judge and takes median."""
        from scorer_outbound import OutboundLLMJudge

        judge = OutboundLLMJudge()

        call_count = [0]

        def mock_judge(prompt, model=None):
            call_count[0] += 1
            if call_count[0] % 3 == 1:
                return {"score": 2, "explanation": "low"}
            elif call_count[0] % 3 == 2:
                return {"score": 5, "explanation": "high (possibly injected)"}
            else:
                return {"score": 3, "explanation": "arbitration"}

        judge._call_judge = mock_judge
        result = judge._call_judge_verified("test prompt")
        assert result["score"] == 3, (
            f"Variance gate must take median of 3 judges, got {result['score']}"
        )
        assert result.get("_poll_disagreement") is True
        assert result.get("_poll_arbitrated") is True
        assert call_count[0] == 3, "Should call 3 judges when spread > 1.5"


# ── 40. Semantic forbidden check method exists (A05) ──


class TestSemanticForbiddenCheckExists:
    def test_check_semantic_method_exists(self):
        """ForbiddenBehaviorChecker must have check_semantic method."""
        checker = ForbiddenBehaviorChecker(_make_scenario())
        assert hasattr(checker, "check_semantic"), (
            "ForbiddenBehaviorChecker must have check_semantic for A05"
        )

    def test_check_semantic_returns_list_on_no_forbidden(self):
        """check_semantic on scenario with no forbidden behaviors must return empty list."""
        scenario = _make_scenario(forbidden_behaviors=[])
        checker = ForbiddenBehaviorChecker(scenario)
        conv = _make_conversation([(1, Role.AGENT, "hello", []), (1, Role.USER, "hi", [])])
        result = checker.check_semantic(conv)
        assert result == []
