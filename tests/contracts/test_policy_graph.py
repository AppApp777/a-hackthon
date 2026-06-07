"""Contract tests for PolicyGraph compilation and TraceVerifier alignment.

These tests verify:
1. Policy graph correctly compiles from scenario JSON
2. Edit distance DP produces correct alignments
3. Temporal constraints catch ordering violations
4. Branch verification works with observed steps
5. Scoring atoms correctly link to evidence
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "agent-eval"))

from models import Conversation, EventLedger, Message, Role, ToolCall, ToolEventType
from models_outbound import (
    Branch,
    CallContext,
    InstructionStep,
    OutboundScenario,
)
from policy_graph import (
    EdgeType,
    ToolPredicate,
    compile_policy_graph,
)
from trace_verifier import (
    EventKind,
    ObservedStep,
    TraceEvent,
    align_sequences,
    check_temporal_constraints,
    normalize_trace,
    verify_branches,
    verify_trace,
)

# ── Fixtures ──


def _make_scenario(**overrides) -> OutboundScenario:
    defaults = {
        "id": "test_pg",
        "name": "策略图测试场景",
        "domain": "outbound_call",
        "call_type": "delivery_confirm",
        "description": "测试策略图编译",
        "call_purpose": "确认配送",
        "call_context": CallContext(order_id="ORD_001", customer_name="张先生"),
        "instruction_steps": [
            InstructionStep(
                step_id="open",
                order=1,
                instruction="自报身份",
                required_actions=["self_identify"],
            ),
            InstructionStep(
                step_id="confirm",
                order=2,
                instruction="确认订单",
                required_actions=["query_order"],
            ),
            InstructionStep(
                step_id="deliver",
                order=3,
                instruction="确认配送",
                required_actions=["update_delivery_status"],
                branches=[
                    Branch(condition="客户在家", next_step="wrap_up", description="正常结束"),
                    Branch(condition="客户不在", next_step="alt", description="替代方案"),
                ],
            ),
            InstructionStep(
                step_id="alt",
                order=4,
                instruction="替代方案",
                required_actions=["reschedule_delivery"],
                is_optional=True,
            ),
            InstructionStep(
                step_id="wrap_up",
                order=5,
                instruction="结束通话",
                required_actions=["log_call_result"],
            ),
        ],
        "must_call_tools": ["query_order", "update_delivery_status", "log_call_result"],
        "expected_call_result": "confirmed",
        "expected_steps_completed": ["open", "confirm", "deliver", "wrap_up"],
        "expected_branch_taken": {"deliver": "客户在家"},
        "max_turns": 10,
    }
    defaults.update(overrides)
    return OutboundScenario(**defaults)


def _make_conversation(*msgs) -> Conversation:
    """msgs: list of (turn, role, content, tool_calls)"""
    conv = Conversation(scenario_id="test_pg")
    for turn, role, content, tcs in msgs:
        conv.messages.append(Message(turn=turn, role=role, content=content, tool_calls=tcs or []))
    return conv


# ── §1: Policy Graph Compilation ──


class TestPolicyGraphCompilation:
    """Verify compile_policy_graph produces correct graph structure."""

    def test_all_steps_become_nodes(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        assert len(graph.nodes) == 5
        assert "open" in graph.nodes
        assert "confirm" in graph.nodes
        assert "deliver" in graph.nodes
        assert "alt" in graph.nodes
        assert "wrap_up" in graph.nodes

    def test_sequential_edges(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        seq_edges = [e for e in graph.edges if e.edge_type == EdgeType.SEQUENTIAL]
        assert any(e.source == "open" and e.target == "confirm" for e in seq_edges)

    def test_branch_edges(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        branch_edges = [e for e in graph.edges if e.edge_type == EdgeType.BRANCH]
        assert any(
            e.source == "deliver" and e.target == "wrap_up" and e.condition == "客户在家"
            for e in branch_edges
        )
        assert any(
            e.source == "deliver" and e.target == "alt" and e.condition == "客户不在"
            for e in branch_edges
        )

    def test_optional_branch_target_flagged(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        alt_node = graph.nodes["alt"]
        assert alt_node.is_optional
        assert alt_node.is_branch_target

    def test_entry_and_exit_nodes(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        assert graph.entry_node == "open"
        assert "wrap_up" in graph.exit_nodes

    def test_topological_order(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        order = graph.topological_order()
        assert order.index("open") < order.index("confirm")
        assert order.index("confirm") < order.index("deliver")

    def test_tool_predicates_inferred(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        confirm_node = graph.nodes["confirm"]
        tool_preds = [p for p in confirm_node.predicates if isinstance(p, ToolPredicate)]
        assert any(p.tool_name == "query_order" for p in tool_preds)

    def test_expected_path_includes_branch_target(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        # Branch "客户在家" → wrap_up should already be in expected path
        assert "wrap_up" in graph.expected_path

    def test_temporal_constraints_generated(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        assert len(graph.constraints) > 0
        # Tool ordering constraint
        tool_constraints = [
            c
            for c in graph.constraints
            if c.source == "query_order" and c.target == "update_delivery_status"
        ]
        assert len(tool_constraints) >= 1

    def test_scoring_atoms_generated(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        step_atoms = [a for a in graph.atoms if a.dimension == "step_compliance"]
        tool_atoms = [a for a in graph.atoms if a.dimension == "tool_usage"]
        branch_atoms = [a for a in graph.atoms if a.dimension == "branch_accuracy"]
        assert len(step_atoms) == 4  # 4 expected steps
        assert len(tool_atoms) == 3  # 3 must_call_tools
        assert len(branch_atoms) == 1  # 1 expected branch


# ── §2: Edit Distance Alignment ──


class TestEditDistanceAlignment:
    """Verify DP alignment produces correct results."""

    def test_perfect_alignment_zero_cost(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        expected = ["open", "confirm", "deliver", "wrap_up"]
        observed = [
            ObservedStep(step_id="open", first_turn=1, last_turn=1),
            ObservedStep(step_id="confirm", first_turn=2, last_turn=2),
            ObservedStep(step_id="deliver", first_turn=3, last_turn=3),
            ObservedStep(step_id="wrap_up", first_turn=4, last_turn=4),
        ]
        cost, ops = align_sequences(expected, observed, graph)
        assert cost == 0.0
        assert all(op.op_type == "match" for op in ops)

    def test_missing_required_step_high_cost(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        expected = ["open", "confirm", "deliver", "wrap_up"]
        observed = [
            ObservedStep(step_id="open", first_turn=1, last_turn=1),
            # confirm is missing!
            ObservedStep(step_id="deliver", first_turn=3, last_turn=3),
            ObservedStep(step_id="wrap_up", first_turn=4, last_turn=4),
        ]
        cost, ops = align_sequences(expected, observed, graph)
        assert cost > 0
        deleted = [op for op in ops if op.op_type == "delete_expected"]
        assert any(op.expected_step == "confirm" for op in deleted)

    def test_extra_step_low_cost(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        expected = ["open", "confirm"]
        observed = [
            ObservedStep(step_id="open", first_turn=1, last_turn=1),
            ObservedStep(step_id="alt", first_turn=2, last_turn=2),  # extra
            ObservedStep(step_id="confirm", first_turn=3, last_turn=3),
        ]
        cost, ops = align_sequences(expected, observed, graph)
        inserted = [op for op in ops if op.op_type == "insert_observed"]
        assert len(inserted) >= 1

    def test_out_of_order_detected(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        expected = ["open", "confirm", "deliver"]
        observed = [
            ObservedStep(step_id="deliver", first_turn=1, last_turn=1),
            ObservedStep(step_id="open", first_turn=2, last_turn=2),
            ObservedStep(step_id="confirm", first_turn=3, last_turn=3),
        ]
        cost, ops = align_sequences(expected, observed, graph)
        assert cost > 0  # out-of-order has nonzero cost

    def test_empty_observed_all_deleted(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        expected = ["open", "confirm"]
        observed: list[ObservedStep] = []
        cost, ops = align_sequences(expected, observed, graph)
        assert cost > 0
        assert all(op.op_type == "delete_expected" for op in ops)


# ── §3: Temporal Constraint Checking ──


class TestTemporalConstraints:
    """Verify temporal constraints detect ordering violations."""

    def test_correct_order_no_violations(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        observed = [
            ObservedStep(step_id="open", first_turn=1, last_turn=1),
            ObservedStep(step_id="confirm", first_turn=2, last_turn=2),
            ObservedStep(step_id="deliver", first_turn=3, last_turn=3),
        ]
        events = [
            TraceEvent(
                seq=1,
                kind=EventKind.TOOL_EXECUTED,
                turn=2,
                tool_name="query_order",
                source="agent",
                event_id="e1",
            ),
            TraceEvent(
                seq=2,
                kind=EventKind.TOOL_EXECUTED,
                turn=3,
                tool_name="update_delivery_status",
                source="agent",
                event_id="e2",
            ),
            TraceEvent(
                seq=3,
                kind=EventKind.TOOL_EXECUTED,
                turn=4,
                tool_name="log_call_result",
                source="agent",
                event_id="e3",
            ),
        ]
        violations = check_temporal_constraints(graph, observed, events)
        # All tools in correct order → no violations
        tool_violations = [v for v in violations if "工具" in v.constraint_description]
        assert len(tool_violations) == 0

    def test_reversed_tool_order_detected(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        observed = [
            ObservedStep(step_id="open", first_turn=1, last_turn=1),
            ObservedStep(step_id="confirm", first_turn=3, last_turn=3),
        ]
        events = [
            TraceEvent(
                seq=1,
                kind=EventKind.TOOL_EXECUTED,
                turn=1,
                tool_name="update_delivery_status",
                source="agent",
                event_id="e1",
            ),
            TraceEvent(
                seq=2,
                kind=EventKind.TOOL_EXECUTED,
                turn=3,
                tool_name="query_order",
                source="agent",
                event_id="e2",
            ),
        ]
        violations = check_temporal_constraints(graph, observed, events)
        assert len(violations) > 0


# ── §4: Branch Verification ──


class TestBranchVerification:
    """Verify branch decisions are correctly identified."""

    def test_correct_branch_taken(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        observed = [
            ObservedStep(step_id="deliver", first_turn=3, last_turn=3),
            ObservedStep(step_id="wrap_up", first_turn=4, last_turn=4),
        ]
        results = verify_branches(graph, observed, scenario)
        assert results["deliver"][0] is True  # correct branch

    def test_wrong_branch_taken(self):
        scenario = _make_scenario()
        graph = compile_policy_graph(scenario)
        observed = [
            ObservedStep(step_id="deliver", first_turn=3, last_turn=3),
            ObservedStep(step_id="alt", first_turn=4, last_turn=4),  # wrong branch
        ]
        results = verify_branches(graph, observed, scenario)
        assert results["deliver"][0] is False


# ── §5: Full Verification Pipeline ──


class TestFullVerification:
    """End-to-end tests of verify_trace()."""

    def test_perfect_execution_high_score(self):
        scenario = _make_scenario()
        conv = _make_conversation(
            (1, Role.AGENT, "您好，我是美团配送智能助手", []),
            (1, Role.USER, "嗯你好", []),
            (
                2,
                Role.AGENT,
                "我来确认您的订单",
                [
                    ToolCall(
                        tool_name="query_order",
                        arguments={"order_id": "ORD_001"},
                        result={"status": "delivering"},
                    )
                ],
            ),
            (2, Role.USER, "好的", []),
            (
                3,
                Role.AGENT,
                "骑手即将到达，请问您在家吗",
                [
                    ToolCall(
                        tool_name="update_delivery_status",
                        arguments={"order_id": "ORD_001", "status": "confirmed"},
                        result={"success": True},
                    )
                ],
            ),
            (3, Role.USER, "在家，可以收", []),
            (
                4,
                Role.AGENT,
                "好的，感谢配合，再见",
                [
                    ToolCall(
                        tool_name="log_call_result",
                        arguments={"order_id": "ORD_001", "result": "confirmed"},
                        result={"logged": True},
                    )
                ],
            ),
        )
        result = verify_trace(scenario, conv)
        assert result.step_compliance_score >= 0.5
        assert result.alignment_score >= 0.5
        assert isinstance(result.overall_verification_score, float)

    def test_missing_steps_low_score(self):
        scenario = _make_scenario()
        # Agent skips directly to wrap_up
        conv = _make_conversation(
            (
                1,
                Role.AGENT,
                "好的再见",
                [
                    ToolCall(
                        tool_name="log_call_result",
                        arguments={"order_id": "ORD_001", "result": "confirmed"},
                        result={"logged": True},
                    )
                ],
            ),
        )
        result = verify_trace(scenario, conv)
        assert result.step_compliance_score < 0.8
        assert len(result.unsatisfied_atoms) > 0

    def test_with_event_ledger(self):
        scenario = _make_scenario()
        conv = _make_conversation(
            (1, Role.AGENT, "您好我是美团助手", []),
            (2, Role.AGENT, "确认订单", []),
        )
        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=2,
            tool_name="query_order",
            tool_call_id="tc1",
            arguments={"order_id": "ORD_001"},
            result={"status": "delivering"},
            source="agent",
        )
        result = verify_trace(scenario, conv, ledger=ledger)
        # Should recognize query_order was called
        tool_atoms = [a for a in result.satisfied_atoms if a.dimension == "tool_usage"]
        assert any(a.atom_id == "tool_query_order" for a in tool_atoms)

    def test_harness_tool_excluded_from_scoring(self):
        scenario = _make_scenario()
        conv = _make_conversation(
            (1, Role.AGENT, "您好", []),
        )
        ledger = EventLedger()
        harness_token = ledger.source_token
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=0,
            tool_name="query_order",
            tool_call_id="tc_h",
            arguments={"order_id": "ORD_001"},
            result={"status": "delivering"},
            source=harness_token,
        )
        result = verify_trace(scenario, conv, ledger=ledger)
        # Harness-initiated query_order should NOT count as agent's tool usage
        tool_atoms = [a for a in result.satisfied_atoms if a.atom_id == "tool_query_order"]
        assert len(tool_atoms) == 0


# ── §6: Normalize Trace ──


class TestNormalizeTrace:
    """Verify trace normalization handles all message types."""

    def test_system_messages_tagged(self):
        conv = _make_conversation(
            (1, Role.SYSTEM, "[系统提醒] 步骤1", []),
            (1, Role.AGENT, "您好", []),
        )
        events = normalize_trace(conv)
        system_events = [e for e in events if e.kind == EventKind.SYSTEM_INJECTION]
        assert len(system_events) == 1

    def test_tool_calls_extracted_from_conversation(self):
        conv = _make_conversation(
            (
                1,
                Role.AGENT,
                "查询中",
                [
                    ToolCall(
                        tool_name="query_order",
                        arguments={"order_id": "ORD_001"},
                        result={"status": "ok"},
                    )
                ],
            ),
        )
        events = normalize_trace(conv)
        tool_events = [e for e in events if e.kind == EventKind.TOOL_EXECUTED]
        assert len(tool_events) == 1
        assert tool_events[0].tool_name == "query_order"

    def test_ledger_overrides_conversation_tools(self):
        conv = _make_conversation(
            (
                1,
                Role.AGENT,
                "查询中",
                [
                    ToolCall(
                        tool_name="query_order",
                        arguments={"order_id": "ORD_001"},
                        result={"status": "ok"},
                    )
                ],
            ),
        )
        ledger = EventLedger()
        ledger.append(
            ToolEventType.TOOL_EXECUTED,
            turn=1,
            tool_name="query_order",
            arguments={"order_id": "ORD_001"},
            result={"status": "ok"},
            source="agent",
        )
        events = normalize_trace(conv, ledger)
        # Should only have ledger tool events, not conversation inline ones
        tool_events = [e for e in events if e.kind == EventKind.TOOL_EXECUTED]
        assert all(e.event_id.startswith("ledger_") for e in tool_events)
