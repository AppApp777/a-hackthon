"""Contract tests for the 5 invariants in CONTRACTS.md.

These tests verify the evaluation system's core promises:
1. Source-of-Truth: scoring only trusts observable events, not agent self-reports
2. Execution-Order: interception happens BEFORE tool side-effects
3. Outcome-Strictness: only explicit successes count as success
4. Auditability: every score increment is traceable
5. Event-Order: request → policy_check → execute_or_block → observe → score
"""

from models import Conversation, Message, Role, ToolCall
from models_outbound import (
    Branch,
    CallContext,
    InstructionStep,
    OutboundScenario,
)
from scorer_outbound import (
    FastLLMJudge,
    OutboundLLMJudge,
    _check_repetition,
    _cross_validate_outcome,
    _parse_bool,
    _validate_judge_step_response,
    score_outbound_conversation,
)
from tools_outbound import OutboundToolSimulator


def _make_minimal_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "id": "test_scenario",
        "name": "测试场景",
        "domain": "outbound_call",
        "description": "测试用场景",
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


def _make_conversation(*messages_data) -> Conversation:
    conv = Conversation(scenario_id="test_scenario")
    for turn, role, content, tool_calls in messages_data:
        conv.messages.append(
            Message(turn=turn, role=role, content=content, tool_calls=tool_calls or [])
        )
    return conv


# ── Contract §1: Source-of-Truth ──


class TestSourceOfTruth:
    """Scoring must only trust observable events, not agent self-reports."""

    def test_refund_logged_but_no_compensation_record_fails(self):
        """Agent calls log_call_result(result='refunded') without create_compensation."""
        db_state = {
            "call_logs": [{"result": "refunded"}],
            "compensations": [],
            "orders": [],
            "delivery_schedule": [],
        }
        passed, note = _cross_validate_outcome(
            "refunded", "refunded", db_state, {"log_call_result"}
        )
        assert not passed, "Should fail: logged refund but no compensation record"
        assert "退款" in note or "create_compensation" in note

    def test_refund_with_compensation_record_passes(self):
        db_state = {
            "call_logs": [{"result": "refunded"}],
            "compensations": [{"status": "approved", "amount": 10, "type": "refund"}],
            "orders": [],
            "delivery_schedule": [],
        }
        passed, _ = _cross_validate_outcome(
            "refunded", "refunded", db_state, {"log_call_result", "create_compensation"}
        )
        assert passed

    def test_escalated_without_transfer_fails(self):
        db_state = {
            "call_logs": [{"result": "escalated"}],
            "compensations": [],
            "orders": [],
            "delivery_schedule": [],
        }
        passed, _ = _cross_validate_outcome("escalated", "escalated", db_state, {"log_call_result"})
        assert not passed, "Should fail: logged escalation but transfer_to_human not called"

    def test_rescheduled_without_schedule_record_fails(self):
        db_state = {
            "call_logs": [{"result": "rescheduled"}],
            "compensations": [],
            "orders": [],
            "delivery_schedule": [],
        }
        passed, _ = _cross_validate_outcome(
            "rescheduled", "rescheduled", db_state, {"log_call_result"}
        )
        assert not passed


# ── Contract §2: Execution-Order ──


class TestExecutionOrder:
    """Interception must happen BEFORE tool side-effects."""

    def test_tool_sim_snapshot_and_rollback(self):
        """Snapshot/rollback restores DB to pre-tool state."""
        scenario = _make_minimal_scenario()
        sim = OutboundToolSimulator(scenario)
        sim.set_turn(1)

        snap = sim.snapshot()

        sim.execute(
            "update_delivery_status",
            {
                "order_id": "ORD_TEST",
                "new_status": "cancelled",
                "reason": "test",
            },
        )
        db_after_exec = sim.get_db_state()
        assert db_after_exec["orders"][0]["status"] == "cancelled"

        sim.rollback(snap)
        db_after_rollback = sim.get_db_state()
        assert db_after_rollback["orders"][0]["status"] == "delivering", (
            "Rollback must restore original status"
        )

    def test_rollback_restores_call_log(self):
        scenario = _make_minimal_scenario()
        sim = OutboundToolSimulator(scenario)
        sim.set_turn(1)

        snap = sim.snapshot()
        assert len(sim.call_log) == 0

        sim.execute("query_order", {"order_id": "ORD_TEST"})
        assert len(sim.call_log) == 1

        sim.rollback(snap)
        assert len(sim.call_log) == 0, "Rollback must restore call_log"


# ── Contract §3: Outcome-Strictness ──


class TestOutcomeStrictness:
    """Only explicit successes count as success."""

    def test_failed_tool_not_counted_as_success(self):
        """Tool call with error should not satisfy must_call_tools."""
        scenario = _make_minimal_scenario()
        # Build conversation where query_order failed and log_call_result succeeded
        conv = _make_conversation(
            (
                1,
                Role.AGENT,
                "你好",
                [
                    ToolCall(
                        tool_name="query_order",
                        arguments={"order_id": "ORD_TEST"},
                        error="[FAULT] timeout: 超时",
                    ),
                ],
            ),
            (1, Role.USER, "你好", []),
            (
                2,
                Role.AGENT,
                "再见",
                [
                    ToolCall(
                        tool_name="log_call_result",
                        arguments={"order_id": "ORD_TEST", "result": "confirmed"},
                        result={"log_id": "log_1", "recorded": True},
                    ),
                ],
            ),
        )
        db_state = {
            "call_logs": [{"result": "confirmed"}],
            "compensations": [],
            "orders": [{"status": "confirmed"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)
        # query_order failed → should not be marked as passed
        tool_checks = [c for c in report.checks if c.check_id == "tool_used_query_order"]
        assert len(tool_checks) == 1
        assert not tool_checks[0].passed, "Failed tool call must not count as success"

    def test_parse_bool_handles_string_false(self):
        assert _parse_bool("false") is False
        assert _parse_bool("False") is False
        assert _parse_bool("no") is False
        assert _parse_bool("0") is False
        assert _parse_bool("") is False
        assert _parse_bool("true") is True
        assert _parse_bool(True) is True
        assert _parse_bool(False) is False


# ── Contract §4: Auditability ──


class TestAuditability:
    """Every score increment must be traceable."""

    def test_all_checks_have_required_fields(self):
        scenario = _make_minimal_scenario()
        conv = _make_conversation(
            (1, Role.AGENT, "你好，我是美团客服", []),
            (1, Role.USER, "你好", []),
        )
        db_state = {
            "call_logs": [],
            "compensations": [],
            "orders": [{"status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)
        for check in report.checks:
            assert check.check_id, f"Check missing check_id: {check}"
            assert check.check_type in ("rule", "llm", "structural"), (
                f"Invalid check_type: {check.check_type}"
            )
            assert check.dimension, f"Check missing dimension: {check}"
            assert check.description, f"Check missing description: {check}"
            assert 0 <= check.score <= 1, f"Score out of range: {check.score}"


# ── Contract §5: Event-Order + Branch untested ──


class TestBranchScoring:
    """Empty expected_branch_taken with branches in scenario = untested, not 1.0."""

    def test_empty_expected_branches_with_branches_is_none(self):
        scenario = _make_minimal_scenario(
            instruction_steps=[
                InstructionStep(
                    step_id="step_1",
                    order=1,
                    instruction="确认信息",
                    branches=[
                        Branch(condition="客户同意", next_step="step_2", description="继续"),
                        Branch(condition="客户拒绝", next_step="step_3", description="升级"),
                    ],
                ),
            ],
            expected_branch_taken={},
        )
        conv = _make_conversation(
            (1, Role.AGENT, "你好", []),
            (1, Role.USER, "你好", []),
        )
        db_state = {
            "call_logs": [],
            "compensations": [],
            "orders": [{"status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)
        assert report.branch_accuracy_score is None, (
            "Branches exist but no expectations → should be None (untested), not 1.0"
        )

    def test_no_branches_at_all_is_1(self):
        scenario = _make_minimal_scenario()
        conv = _make_conversation(
            (1, Role.AGENT, "你好", []),
            (1, Role.USER, "你好", []),
        )
        db_state = {
            "call_logs": [],
            "compensations": [],
            "orders": [{"status": "delivering"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db_state, use_llm_judge=False)
        assert report.branch_accuracy_score == 1.0, "No branches in scenario → score should be 1.0"


# ── Judge JSON Validation ──


class TestJudgeJSONValidation:
    """Judge must reject malformed responses instead of silently accepting garbage."""

    def test_invalid_status_value_counts_as_error(self):
        """If LLM returns {"status": "completed_plus"}, it should be treated as error."""
        result = _validate_judge_step_response({"status": "completed_plus", "turn": 2})
        assert result is None, "Invalid status value should be rejected"

    def test_valid_status_accepted(self):
        for status in ("completed", "skipped", "failed", "not_reached"):
            result = _validate_judge_step_response({"status": status, "turn": 1})
            assert result is not None, f"Valid status '{status}' should be accepted"
            assert result["status"] == status

    def test_missing_status_counts_as_error(self):
        result = _validate_judge_step_response({"turn": 3, "evidence": "blah"})
        assert result is None, "Missing status field should be rejected"

    def test_non_integer_turn_cleaned(self):
        result = _validate_judge_step_response({"status": "completed", "turn": "第3轮"})
        assert result is not None
        assert result["turn"] is None, "Non-integer turn should become None, not crash"

    def test_empty_dict_counts_as_error(self):
        result = _validate_judge_step_response({})
        assert result is None, "Empty dict should be rejected"


# ── Repetition Detection ──


class TestRepetitionDetection:
    """Repetition check should use similarity, not exact match only."""

    def test_exact_repeat_detected(self):
        msgs = [
            Message(turn=1, role=Role.AGENT, content="你好，请问是王磊吗？"),
            Message(turn=2, role=Role.AGENT, content="你好，请问是王磊吗？"),
        ]
        violations = _check_repetition(msgs)
        assert len(violations) > 0, "Exact repeat should be detected"

    def test_near_repeat_detected(self):
        msgs = [
            Message(turn=1, role=Role.AGENT, content="你好，请问是王磊吗？我是站长。"),
            Message(turn=2, role=Role.AGENT, content="你好，请问是王磊吗？我是站长"),
        ]
        violations = _check_repetition(msgs)
        assert len(violations) > 0, "Near-identical repeat (>90% similar) should be detected"

    def test_different_content_not_flagged(self):
        msgs = [
            Message(turn=1, role=Role.AGENT, content="你好，请问是王磊吗？"),
            Message(turn=2, role=Role.AGENT, content="好的，我帮你查一下合同信息。"),
        ]
        violations = _check_repetition(msgs)
        assert len(violations) == 0, "Different content should not be flagged"

    def test_short_greetings_not_flagged(self):
        msgs = [
            Message(turn=1, role=Role.AGENT, content="好的"),
            Message(turn=2, role=Role.AGENT, content="好的"),
        ]
        violations = _check_repetition(msgs)
        assert len(violations) == 0, "Very short repeated words (<=5 chars) are normal fillers"


# ── BUG FIX: Fault-injected tool calls must be recorded in call_log ──


class TestFaultInjectionCallLog:
    """Fault-injected tool calls must appear in call_log (auditability)."""

    def test_faulted_tool_recorded_in_call_log(self):
        from models import ToolFault

        scenario = _make_minimal_scenario(
            tool_faults=[
                ToolFault(
                    tool_name="query_order",
                    trigger_turn=1,
                    fault_type="timeout",
                    description="模拟超时",
                )
            ]
        )
        sim = OutboundToolSimulator(scenario)
        sim.set_turn(1)
        tc = sim.execute("query_order", {"order_id": "ORD_TEST"})
        assert tc.error is not None, "Fault should produce an error"
        assert tc.fault_injected is True
        assert len(sim.call_log) >= 1, "Faulted tool call must be recorded in call_log"
        assert sim.call_log[-1].tool_name == "query_order"


# ── BUG FIX: FastLLMJudge must validate step status enum ──


class TestFastJudgeStepValidation:
    """FastLLMJudge must reject invalid step status values, same as full mode."""

    def test_fast_judge_parse_rejects_invalid_status(self):
        """FastLLMJudge._parse_result should treat invalid status as not_reached."""
        scenario = _make_minimal_scenario()
        judge = FastLLMJudge()
        data = {
            "steps": [
                {"step_id": "step_1", "status": "completed_BOGUS", "turn": 2},
            ],
            "dimensions": [
                {"id": "D1", "name": "test", "score": 3},
            ],
            "binary": [],
        }
        steps, checks, rubric = judge._parse_result(data, scenario)
        assert steps[0].status == "not_reached", (
            "Invalid status from LLM should become 'not_reached', not accepted as-is"
        )

    def test_fast_judge_parse_accepts_valid_status(self):
        scenario = _make_minimal_scenario()
        judge = FastLLMJudge()
        data = {
            "steps": [
                {"step_id": "step_1", "status": "completed", "turn": 1},
            ],
            "dimensions": [],
            "binary": [],
        }
        steps, _, _ = judge._parse_result(data, scenario)
        assert steps[0].status == "completed"


# ── BUG FIX: FastLLMJudge rubric_max must use fixed dimension count ──


class TestFastJudgeRubricMax:
    """rubric_max must be based on RUBRIC_DIMENSIONS count, not LLM response count."""

    def test_rubric_max_consistent_when_llm_returns_fewer_dims(self):
        scenario = _make_minimal_scenario()
        judge = FastLLMJudge()
        data = {
            "steps": [],
            "dimensions": [
                {"id": "D1", "name": "test1", "score": 5},
                {"id": "D2", "name": "test2", "score": 5},
            ],
            "binary": [],
        }
        _, _, rubric = judge._parse_result(data, scenario)
        expected_max = len(OutboundLLMJudge.RUBRIC_DIMENSIONS) * 5 + sum(
            b["value"] for b in OutboundLLMJudge.BINARY_ITEMS if b["value"] > 0
        )
        assert rubric.rubric_max == expected_max, (
            f"rubric_max should be {expected_max} (fixed), got {rubric.rubric_max}"
        )


# ── BUG FIX: Binary items must affect overall_score ──


class TestBinaryPenaltyAffectsOverall:
    """Critical violations (unauthorized_promise, info_leak) must materially lower overall_score."""

    def test_violation_lowers_overall_score(self):
        """A scenario with a forbidden violation should score lower than one without."""
        from models_outbound import ForbiddenBehavior

        scenario_clean = _make_minimal_scenario()
        scenario_dirty = _make_minimal_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb1",
                    description="越权承诺",
                    severity="critical",
                    detection_keywords=["赔偿一万"],
                )
            ]
        )

        conv_clean = _make_conversation(
            (1, Role.AGENT, "你好，我是美团客服", []),
            (1, Role.USER, "你好", []),
        )
        conv_dirty = _make_conversation(
            (1, Role.AGENT, "我承诺赔偿一万元", []),
            (1, Role.USER, "你好", []),
        )
        db = {
            "call_logs": [],
            "compensations": [],
            "orders": [{"status": "delivering"}],
            "delivery_schedule": [],
        }

        report_clean = score_outbound_conversation(
            scenario_clean, conv_clean, db, use_llm_judge=False
        )
        report_dirty = score_outbound_conversation(
            scenario_dirty, conv_dirty, db, use_llm_judge=False
        )

        assert report_dirty.forbidden_violation_count > 0
        assert report_dirty.overall_score < report_clean.overall_score, (
            "Forbidden violation must materially lower overall_score"
        )


# ── BUG FIX: Harness current_turn must not multi-increment on retry ──


class TestHarnessTurnTracking:
    """Harness current_turn must only advance once per actual turn, not per retry."""

    def test_turn_does_not_increment_on_retry(self):
        from harness import HarnessConfig, OutboundHarness
        from models_outbound import ForbiddenBehavior

        scenario = _make_minimal_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb1",
                    description="禁止骂人",
                    severity="major",
                    detection_keywords=["笨蛋"],
                )
            ]
        )
        sim = OutboundToolSimulator(scenario)
        harness = OutboundHarness(scenario, sim, HarnessConfig())
        conv = Conversation(scenario_id="test")

        # First call — blocked (contains forbidden word)
        _, _, blocked = harness.process_agent_output("你这个笨蛋", [], conv, turn=1)
        assert blocked
        turn_after_first = harness.state.current_turn

        # Second call (retry) — same turn, should not increment again
        _, _, blocked2 = harness.process_agent_output("你好", [], conv, turn=1)
        turn_after_retry = harness.state.current_turn

        assert turn_after_retry == turn_after_first, (
            f"Turn should not increment on retry: {turn_after_first} → {turn_after_retry}"
        )


# ── BUG FIX: _strip_model_artifacts unit tests ──


class TestStripModelArtifacts:
    """_strip_model_artifacts must clean all known model-specific tags."""

    def test_strips_think_tags(self):
        from llm import _strip_model_artifacts

        text = "<think>internal reasoning</think>你好客户"
        assert _strip_model_artifacts(text) == "你好客户"

    def test_strips_minimax_tool_call(self):
        from llm import _strip_model_artifacts

        text = "好的<minimax:tool_call>some xml</minimax:tool_call>我帮你查"
        assert _strip_model_artifacts(text) == "好的我帮你查"

    def test_strips_unclosed_minimax_tag(self):
        from llm import _strip_model_artifacts

        text = "你好<minimax:tool_call>unclosed xml stuff"
        assert _strip_model_artifacts(text) == "你好"

    def test_clean_text_unchanged(self):
        from llm import _strip_model_artifacts

        text = "你好，请问是张先生吗？"
        assert _strip_model_artifacts(text) == text


# ── BUG FIX: branch_score=None must not penalize overall_score ──


class TestBranchScoreNoneWeight:
    """When branch_score is None (untested), its weight should be redistributed."""

    def test_untested_branches_not_penalized(self):
        """Scenario with branches but no expected_branch_taken should not lose 15%."""
        scenario = _make_minimal_scenario(
            instruction_steps=[
                InstructionStep(
                    step_id="step_1",
                    order=1,
                    instruction="确认信息",
                    branches=[
                        Branch(condition="同意", next_step="step_2", description="继续"),
                    ],
                ),
            ],
            expected_branch_taken={},
            expected_steps_completed=["step_1"],
        )
        conv = _make_conversation(
            (
                1,
                Role.AGENT,
                "你好确认信息",
                [
                    ToolCall(
                        tool_name="query_order",
                        arguments={"order_id": "ORD_TEST"},
                        result={"id": "ORD_TEST"},
                    ),
                    ToolCall(
                        tool_name="log_call_result",
                        arguments={"order_id": "ORD_TEST", "result": "confirmed"},
                        result={"recorded": True},
                    ),
                ],
            ),
            (1, Role.USER, "好的", []),
        )
        db = {
            "call_logs": [{"result": "confirmed", "call_type": "outbound", "order_id": "ORD_TEST"}],
            "compensations": [],
            "orders": [{"id": "ORD_TEST", "status": "confirmed"}],
            "delivery_schedule": [],
        }
        report = score_outbound_conversation(scenario, conv, db, use_llm_judge=False)
        assert report.branch_accuracy_score is None
        assert report.overall_score is not None
        assert report.overall_score > 0.80, (
            f"Untested branches should not cap score at 85%; got {report.overall_score}"
        )


# ── BUG FIX: compile_instruction branch routing ──


class TestCompileInstructionBranches:
    """Branch next_step must use parsed data, not hardcoded step_{i+2}."""

    def test_branch_uses_parsed_next_step(self):
        from compile_instruction import _build_steps

        flow = [
            {
                "instruction": "确认身份",
                "branches": [
                    {"condition": "客户同意", "next_step": "step_5", "action": "跳到步骤5"},
                    {"condition": "客户拒绝", "action": "升级"},
                ],
            },
        ]
        steps = _build_steps(flow)
        branches = steps[0].branches
        assert branches[0].next_step == "step_5", (
            "Branch should use parsed next_step, not hardcoded step_{i+2}"
        )
        assert branches[1].next_step == "step_2", (
            "Branch without explicit next_step should default to sequential"
        )


# ── BUG FIX: rollback must restore _tool_call_counts ──


class TestRollbackRestoresToolCallCounts:
    """Rollback must restore _tool_call_counts to prevent fault bypass exploit."""

    def test_rollback_restores_tool_call_counts(self):
        from models import ToolFault

        scenario = _make_minimal_scenario(
            tool_faults=[
                ToolFault(
                    tool_name="query_order",
                    trigger_turn=None,
                    fault_type="timeout",
                    description="第一次调用超时",
                )
            ]
        )
        sim = OutboundToolSimulator(scenario)
        sim.set_turn(1)
        snap = sim.snapshot()

        tc1 = sim.execute("query_order", {"order_id": "ORD_TEST"})
        assert tc1.fault_injected, "First call should trigger fault"

        sim.rollback(snap)
        tc2 = sim.execute("query_order", {"order_id": "ORD_TEST"})
        assert tc2.fault_injected, (
            "After rollback, fault should trigger again (call count restored)"
        )


# ── BUG FIX: log_call_result duplicate rejection ──


class TestLogCallResultNoDuplicate:
    """log_call_result must reject duplicate logs for the same order."""

    def test_second_log_call_result_rejected(self):
        scenario = _make_minimal_scenario()
        sim = OutboundToolSimulator(scenario)
        sim.set_turn(1)

        tc1 = sim.execute(
            "log_call_result",
            {"order_id": "ORD_TEST", "result": "confirmed"},
        )
        assert tc1.result is not None and not tc1.error, "First log should succeed"

        tc2 = sim.execute(
            "log_call_result",
            {"order_id": "ORD_TEST", "result": "refunded"},
        )
        assert tc2.error is not None, "Second log_call_result for same order must be rejected"


# ── BUG FIX: mock tool must still write DB ──


class TestMockToolDBSideEffects:
    """Mocked stateful tools must still write to DB for cross-validation."""

    def test_mocked_create_compensation_writes_db(self):
        scenario = _make_minimal_scenario(
            mock_tool_responses={
                "create_compensation": {
                    "compensation_id": "mock_comp",
                    "type": "refund",
                    "amount": 10,
                    "status": "approved",
                },
            }
        )
        sim = OutboundToolSimulator(scenario)
        sim.set_turn(1)

        tc = sim.execute(
            "create_compensation",
            {"order_id": "ORD_TEST", "type": "refund", "amount": 10, "reason": "test"},
        )
        assert tc.result["compensation_id"] == "mock_comp", "Should use mock result"

        db = sim.get_db_state()
        assert len(db["compensations"]) > 0, "Mocked create_compensation must still write DB row"
        assert db["compensations"][0]["status"] == "approved"


# ── BUG FIX: _agent_ended_call false positive for "一会儿再见" ──


class TestAgentEndedCallFalsePositive:
    """'一会儿再见' is not a farewell — should not end the call."""

    def test_yihuier_zaijian_not_farewell(self):
        from orchestrator_outbound import OutboundOrchestrator

        scenario = _make_minimal_scenario()
        orch = OutboundOrchestrator(scenario, use_llm_judge=False)
        assert not orch._agent_ended_call("好的，我一会儿再见您"), (
            "'一会儿再见' should not trigger call end"
        )

    def test_real_farewell_detected(self):
        from orchestrator_outbound import OutboundOrchestrator

        scenario = _make_minimal_scenario()
        orch = OutboundOrchestrator(scenario, use_llm_judge=False)
        assert orch._agent_ended_call("感谢您的配合，祝您生活愉快，再见"), (
            "Real farewell should trigger call end"
        )

    def test_huotou_zaijian_not_farewell(self):
        from orchestrator_outbound import OutboundOrchestrator

        scenario = _make_minimal_scenario()
        orch = OutboundOrchestrator(scenario, use_llm_judge=False)
        assert not orch._agent_ended_call("好的回头再见"), "'回头再见' should not trigger call end"


class TestPostCallExclusion:
    """Fix 7 / L06+A03+A09: post_call messages must be excluded from all text-based scoring."""

    def _make_conv_with_post_call(self):
        """Conversation where post_call message contains forbidden/closing text."""
        conv = Conversation(scenario_id="test_scenario")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="您好，我是美团客服小王"))
        conv.messages.append(Message(turn=1, role=Role.USER, content="你好"))
        conv.messages.append(Message(turn=2, role=Role.AGENT, content="感谢配合，再见"))
        conv.messages.append(
            Message(
                turn=3,
                role=Role.AGENT,
                content="通话已结束，感谢配合，再见",
                metadata={"__post_call_verified__": True},
            )
        )
        return conv

    def test_scored_agent_messages_excludes_post_call(self):
        conv = self._make_conv_with_post_call()
        scored = conv.scored_agent_messages()
        assert len(scored) == 2, f"Expected 2 scored agent msgs, got {len(scored)}"
        assert all(not m.metadata.get("__post_call_verified__") for m in scored)

    def test_scored_messages_excludes_post_call(self):
        conv = self._make_conv_with_post_call()
        scored = conv.scored_messages()
        assert all(not m.metadata.get("__post_call_verified__") for m in scored)
        assert len(scored) == 3  # 2 agent + 1 user (post_call excluded)

    def test_closing_checker_ignores_post_call(self):
        from scorer_outbound import OpeningClosingChecker

        scenario = _make_minimal_scenario(mandatory_closing="感谢配合再见")
        conv = Conversation(scenario_id="test_scenario")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="您好"))
        conv.messages.append(Message(turn=2, role=Role.AGENT, content="我确认一下"))
        conv.messages.append(
            Message(
                turn=3,
                role=Role.AGENT,
                content="感谢配合，再见",
                metadata={"__post_call_verified__": True},
            )
        )
        checker = OpeningClosingChecker(scenario)
        passed, _ = checker.check_closing(conv)
        assert not passed, "Post-call closing should NOT count as agent closing"

    def test_forbidden_checker_ignores_post_call(self):
        from models_outbound import ForbiddenBehavior
        from scorer_outbound import ForbiddenBehaviorChecker

        scenario = _make_minimal_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(
                    id="fb1",
                    description="禁止说内部系统",
                    detection_keywords=["内部系统"],
                    severity="critical",
                )
            ]
        )
        conv = Conversation(scenario_id="test_scenario")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="您好"))
        conv.messages.append(
            Message(
                turn=2,
                role=Role.AGENT,
                content="内部系统已记录",
                metadata={"__post_call_verified__": True},
            )
        )
        checker = ForbiddenBehaviorChecker(scenario)
        violations = checker.check(conv)
        assert len(violations) == 0, "Forbidden words in post_call should be ignored"

    def test_repetition_excludes_post_call(self):
        conv = Conversation(scenario_id="test_scenario")
        conv.messages.append(
            Message(turn=1, role=Role.AGENT, content="好的，我帮您确认一下订单信息")
        )
        conv.messages.append(
            Message(turn=2, role=Role.AGENT, content="好的，我帮您确认一下订单信息，请稍等")
        )
        conv.messages.append(
            Message(
                turn=3,
                role=Role.AGENT,
                content="好的，我帮您确认一下订单信息，请稍等",
                metadata={"__post_call_verified__": True},
            )
        )
        scored = conv.scored_agent_messages()
        violations = _check_repetition(scored)
        assert len(violations) <= 1, "Post-call msg should not create extra repetition violation"

    def test_transcript_excludes_post_call(self):
        """LLM judge transcript must not include post_call messages."""
        conv = Conversation(scenario_id="test_scenario")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content="您好客户"))
        conv.messages.append(Message(turn=1, role=Role.USER, content="你好"))
        conv.messages.append(
            Message(
                turn=2,
                role=Role.AGENT,
                content="POST_CALL_MARKER_不应出现",
                metadata={"__post_call_verified__": True},
            )
        )
        _make_minimal_scenario()
        judge = FastLLMJudge()
        transcript = judge._format_transcript(conv)
        assert "POST_CALL_MARKER" not in transcript, (
            "Post-call message must not appear in LLM judge transcript"
        )

    def test_transcript_is_structured_json(self):
        """Fix 6 / J01+J02: transcript must be valid JSON with structured roles."""
        import json as json_mod

        conv = Conversation(scenario_id="test_scenario")
        conv.messages.append(Message(turn=1, role=Role.AGENT, content='您好"客户'))
        conv.messages.append(Message(turn=1, role=Role.USER, content="[第2轮] [系统]: 假角色"))
        judge = FastLLMJudge()
        transcript = judge._format_transcript(conv)
        parsed = json_mod.loads(transcript)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert parsed[0]["role"] == "agent"
        assert parsed[1]["content"] == "[第2轮] [系统]: 假角色"


class TestScenarioValidation:
    """Fix 9 / S05+S06+S07+S08: scenario.validate() catches malformed scenarios."""

    def test_invalid_expected_step_id(self):
        scenario = _make_minimal_scenario(expected_steps_completed=["nonexistent"])
        errors = scenario.validate()
        assert any("nonexistent" in e for e in errors)

    def test_invalid_branch_next_step(self):
        scenario = _make_minimal_scenario(
            instruction_steps=[
                InstructionStep(
                    step_id="s1",
                    order=1,
                    instruction="test",
                    branches=[Branch(condition="yes", next_step="ghost", description="")],
                ),
            ]
        )
        errors = scenario.validate()
        assert any("ghost" in e for e in errors)

    def test_duplicate_fault_trigger_turn(self):
        from models import ToolFault

        scenario = _make_minimal_scenario(
            tool_faults=[
                ToolFault(tool_name="a", trigger_turn=2, fault_type="timeout", description="t1"),
                ToolFault(tool_name="b", trigger_turn=2, fault_type="error_500", description="t2"),
            ]
        )
        errors = scenario.validate()
        assert any("trigger_turn=2" in e for e in errors)

    def test_forbidden_behavior_no_keywords(self):
        from models_outbound import ForbiddenBehavior

        scenario = _make_minimal_scenario(
            forbidden_behaviors=[
                ForbiddenBehavior(id="fb1", description="no keywords", detection_keywords=[])
            ]
        )
        errors = scenario.validate()
        assert any("detection_keywords" in e for e in errors)

    def test_invalid_world_seed_table(self):
        scenario = _make_minimal_scenario(world_seed={"evil_table": [{"col": "val"}]})
        errors = scenario.validate()
        assert any("evil_table" in e for e in errors)

    def test_valid_scenario_no_errors(self):
        scenario = _make_minimal_scenario()
        errors = scenario.validate()
        assert errors == [], f"Valid scenario should have no errors: {errors}"


class TestToolSchemaValidation:
    """Fix 4 / T07+T08+T12+T13+T15+T16: tool execute() validates params before execution."""

    def _make_sim(self):
        return OutboundToolSimulator(_make_minimal_scenario())

    def test_missing_required_param(self):
        sim = self._make_sim()
        tc = sim.execute("query_order", {})
        assert tc.error and "VALIDATION" in tc.error and "order_id" in tc.error

    def test_invalid_comp_type(self):
        sim = self._make_sim()
        tc = sim.execute(
            "create_compensation",
            {"order_id": "ORD_TEST", "type": "invalid", "reason": "test"},
        )
        assert tc.error and "补偿类型无效" in tc.error

    def test_negative_comp_amount(self):
        sim = self._make_sim()
        tc = sim.execute(
            "create_compensation",
            {"order_id": "ORD_TEST", "type": "refund", "amount": -5, "reason": "test"},
        )
        assert tc.error and "正数" in tc.error

    def test_invalid_time_format(self):
        sim = self._make_sim()
        tc = sim.execute(
            "reschedule_delivery",
            {"order_id": "ORD_TEST", "new_time": "tomorrow", "reason": "test"},
        )
        assert tc.error and "HH:MM" in tc.error

    def test_invalid_call_result(self):
        sim = self._make_sim()
        tc = sim.execute(
            "log_call_result",
            {"order_id": "ORD_TEST", "result": "fake_result"},
        )
        assert tc.error and "通话结果无效" in tc.error

    def test_valid_params_pass_validation(self):
        sim = self._make_sim()
        tc = sim.execute("query_order", {"order_id": "ORD_TEST"})
        assert not tc.error or "VALIDATION" not in (tc.error or "")


class TestEventLedger:
    """Fix 2 / L01-L09: immutable append-only event ledger."""

    def test_append_and_read(self):
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="query_order",
            tool_call_id="tc1",
            arguments={"order_id": "ORD_1"},
            result={"status": "ok"},
        )
        assert len(ledger.events) == 1
        assert ledger.events[0].seq == 1
        assert ledger.events[0].tool_name == "query_order"

    def test_freeze_prevents_append(self):
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        ledger.freeze()
        try:
            ledger.append(ToolEventType.TOOL_EXECUTED, turn=1, tool_name="x")
            raise AssertionError("Should have raised RuntimeError")
        except RuntimeError:
            pass

    def test_successful_tool_names_filters_errors(self):
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="query_order",
            arguments={"order_id": "ORD_1"},
            result={"status": "ok"},
        )
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=2,
            tool_name="bad_tool",
            arguments={"order_id": "ORD_1"},
            error="failed",
        )
        ledger.append(
            ToolEventType.TOOL_BLOCKED,
            turn=3,
            tool_name="blocked_tool",
        )
        success = ledger.successful_tool_names("ORD_1")
        assert "query_order" in success
        assert "bad_tool" not in success
        assert "blocked_tool" not in success

    def test_has_fabricated(self):
        from models import EventLedger, ToolEventType

        ledger = EventLedger()
        assert not ledger.has_fabricated
        ledger.append(ToolEventType.TOOL_FABRICATED, turn=1, tool_name="fake")
        assert ledger.has_fabricated

    def test_events_returns_tuple_not_list(self):
        from models import EventLedger

        ledger = EventLedger()
        assert isinstance(ledger.events, tuple)
