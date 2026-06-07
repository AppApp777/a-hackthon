"""Tests for batch-1 maturity fixes (2026-05-22).

Covers:
- Fix 1: source_token not leaked to agent-visible ToolCalls
- Fix 2: EventLedger events stored in trace metadata
- Fix 3: pre-first-turn tool injections written to EventLedger
- Fix 4: raw_eval mode respects tool_call_gating=False
"""

from harness import HarnessConfig, OutboundHarness
from models import (
    EventLedger,
    ToolEventType,
)
from models_outbound import (
    CallContext,
    InstructionStep,
    OutboundScenario,
)
from tools_outbound import OutboundToolSimulator


def _make_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "id": "test_batch1",
        "name": "批次1测试场景",
        "domain": "outbound_call",
        "description": "测试安全/功能修复",
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
            order_id="ORD_BATCH1",
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


# ── Fix 1: source_token not leaked ──


class TestSourceTokenNotLeaked:
    """Harness-injected tool calls must NOT carry the ledger's secret source_token."""

    def test_pre_first_turn_tools_have_harness_source(self):
        """pre_first_turn() sets tc.source='harness', not the UUID token."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(force_query_order=True)
        harness = OutboundHarness(scenario, tool_sim, config)
        ledger = EventLedger()

        injected = harness.pre_first_turn()
        assert len(injected) > 0
        for tc in injected:
            assert tc.source == "harness", f"ToolCall.source should be 'harness', got '{tc.source}'"
            assert tc.source != ledger.source_token, (
                "ToolCall.source must NOT be the secret source_token"
            )

    def test_source_token_is_uuid_not_harness(self):
        """The ledger's internal source_token is a UUID, distinct from 'harness'."""
        ledger = EventLedger()
        assert ledger.source_token != "harness"
        assert len(ledger.source_token) == 36  # UUID format


# ── Fix 2: EventLedger in trace ──


class TestLedgerInTrace:
    """Trace metadata must include ledger_events for auditability."""

    def test_ledger_events_serializable(self):
        """EventLedger events can be serialized via model_dump."""
        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="query_order",
            tool_call_id="tc_001",
            arguments={"order_id": "ORD_001"},
            result={"status": "found"},
        )
        ledger.freeze()
        serialized = [e.model_dump(mode="json") for e in ledger.events]
        assert len(serialized) == 1
        assert serialized[0]["tool_name"] == "query_order"
        assert serialized[0]["event_type"] == "tool_executed"
        assert serialized[0]["source"] == "agent"


# ── Fix 3: pre-first-turn injections in ledger ──


class TestPreFirstTurnInLedger:
    """Harness pre-first-turn tool executions must appear in EventLedger."""

    def test_injected_tool_logged_with_source_token(self):
        """After pre_first_turn, orchestrator must log to ledger with source=source_token."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(force_query_order=True)
        harness = OutboundHarness(scenario, tool_sim, config)
        ledger = EventLedger()

        injected = harness.pre_first_turn()
        assert len(injected) > 0

        for tc in injected:
            ledger.append(
                ToolEventType.TOOL_EXECUTED,
                turn=0,
                tool_name=tc.tool_name,
                tool_call_id=tc.id,
                arguments=tc.arguments,
                result=tc.result,
                error=tc.error,
                source=ledger.source_token,
            )

        harness_events = [e for e in ledger.events if e.source == ledger.source_token]
        assert len(harness_events) == len(injected)
        assert harness_events[0].tool_name == "query_order"
        assert harness_events[0].turn == 0

    def test_harness_events_excluded_from_successful_tools(self):
        """Ledger.successful_tool_names() must skip harness-sourced events."""
        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=0,
            tool_name="query_order",
            tool_call_id="tc_harness",
            arguments={"order_id": "ORD_001"},
            result={"status": "found"},
            source=ledger.source_token,
        )
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="log_call_result",
            tool_call_id="tc_agent",
            arguments={"result": "confirmed"},
            result={"logged": True},
        )
        names = ledger.successful_tool_names()
        assert "query_order" not in names, "harness-injected query_order should be excluded"
        assert "log_call_result" in names


# ── Fix 4: raw_eval tool gating disabled ──


class TestRawEvalNoToolGating:
    """In raw_eval mode, check_tool_request must always return None."""

    def test_raw_eval_allows_premature_escalation(self):
        """transfer_to_human before create_compensation: blocked in guarded, allowed in raw_eval."""
        scenario = _make_scenario(
            must_call_tools=["query_order", "create_compensation", "log_call_result"]
        )
        tool_sim = OutboundToolSimulator(scenario)

        guarded_config = HarnessConfig.from_mode("guarded_eval")
        guarded_harness = OutboundHarness(scenario, tool_sim, guarded_config)
        blocked = guarded_harness.check_tool_request("transfer_to_human", {})
        assert blocked is not None, "guarded_eval should block premature transfer_to_human"

        raw_config = HarnessConfig.from_mode("raw_eval")
        raw_harness = OutboundHarness(scenario, tool_sim, raw_config)
        allowed = raw_harness.check_tool_request("transfer_to_human", {})
        assert allowed is None, "raw_eval must NOT block any tool calls"

    def test_raw_eval_allows_premature_log_call_result(self):
        """log_call_result before must-call tools: blocked in guarded, allowed in raw_eval."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)

        guarded_config = HarnessConfig.from_mode("guarded_eval")
        guarded_harness = OutboundHarness(scenario, tool_sim, guarded_config)
        blocked = guarded_harness.check_tool_request("log_call_result", {})
        assert blocked is not None, "guarded_eval should block premature log_call_result"

        raw_config = HarnessConfig.from_mode("raw_eval")
        raw_harness = OutboundHarness(scenario, tool_sim, raw_config)
        allowed = raw_harness.check_tool_request("log_call_result", {})
        assert allowed is None, "raw_eval must NOT block any tool calls"

    def test_config_tool_call_gating_false(self):
        """Explicit tool_call_gating=False disables check_tool_request."""
        scenario = _make_scenario()
        tool_sim = OutboundToolSimulator(scenario)
        config = HarnessConfig(tool_call_gating=False)
        harness = OutboundHarness(scenario, tool_sim, config)
        result = harness.check_tool_request("transfer_to_human", {})
        assert result is None
