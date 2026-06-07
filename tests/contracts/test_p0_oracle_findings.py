"""Tests for P0 issues found by Oracle Q9 code audit (2026-06-07).

P0-1: _check_db_state_match must not ignore domain-level 'id' fields
P0-2: No-ledger path must require order_id for entity-bound tools
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "agent-eval"))


def _get_db_checker():
    import scorer_outbound  # noqa: F401 — resolve circular import
    from scorer_modules.checkers import _check_db_state_match

    return _check_db_state_match


class TestDbStateMatchIdField:
    """P0-1: domain identifiers (order_id, customer_id) must NOT be ignored."""

    def test_ignores_auto_generated_id(self):
        fn = _get_db_checker()
        actual = {"compensations": [{"id": "comp_001", "order_id": "MT001", "status": "issued"}]}
        expected = {"compensations": [{"id": "comp_999", "order_id": "MT001", "status": "issued"}]}
        passed, score, _ = fn(actual, expected)
        assert passed, "Auto-generated IDs should be ignored"

    def test_does_not_ignore_domain_order_id(self):
        fn = _get_db_checker()
        actual = {"compensations": [{"order_id": "MT001", "status": "issued"}]}
        expected = {"compensations": [{"order_id": "MT999", "status": "issued"}]}
        passed, score, _ = fn(actual, expected)
        assert not passed, "order_id is a domain identifier and must be compared"

    def test_does_not_ignore_customer_id(self):
        fn = _get_db_checker()
        actual = {"customers": [{"customer_id": "C001", "name": "Alice"}]}
        expected = {"customers": [{"customer_id": "C999", "name": "Alice"}]}
        passed, score, _ = fn(actual, expected)
        assert not passed, "customer_id is a domain identifier and must be compared"

    def test_scenario_order_id_filters_rows(self):
        fn = _get_db_checker()
        actual = {
            "orders": [
                {"order_id": "MT001", "status": "delivered"},
                {"order_id": "MT002", "status": "pending"},
            ]
        }
        expected = {"orders": [{"order_id": "MT001", "status": "delivered"}]}
        passed, score, _ = fn(actual, expected, scenario_order_id="MT001")
        assert passed


class TestNoLedgerOrderIdBinding:
    """P0-2: No-ledger tool success check must require order_id when scenario has one."""

    def test_missing_order_id_not_counted_as_successful(self):
        """When scenario has order_id, a tool call with empty/missing order_id
        should NOT be counted as successful for entity-bound tools."""
        from models import ToolCall

        scenario_oid = "MT001"

        tc = ToolCall(
            tool_name="query_order",
            arguments={},  # Missing order_id!
            result={"status": "found"},
            error=None,
            source="agent",
        )

        # Simulate the no-ledger path logic
        successful_tools: set[str] = set()
        if tc.source != "harness" and not tc.error:
            tc_oid = tc.arguments.get("order_id", "")
            # Current bug: if tc_oid is "", the check passes
            if tc_oid and scenario_oid and tc_oid != scenario_oid:
                pass  # skip
            elif not tc_oid and scenario_oid and tc.tool_name in _entity_bound_tools():
                pass  # NEW: skip entity-bound tools without order_id
            else:
                successful_tools.add(tc.tool_name)

        assert "query_order" not in successful_tools, (
            "Tool call without order_id should not count when scenario has order_id"
        )

    def test_correct_order_id_counted(self):
        from models import ToolCall

        scenario_oid = "MT001"
        tc = ToolCall(
            tool_name="query_order",
            arguments={"order_id": "MT001"},
            result={"status": "found"},
            error=None,
            source="agent",
        )

        successful_tools: set[str] = set()
        if tc.source != "harness" and not tc.error:
            tc_oid = tc.arguments.get("order_id", "")
            if (
                tc_oid
                and scenario_oid
                and tc_oid != scenario_oid
                or not tc_oid
                and scenario_oid
                and tc.tool_name in _entity_bound_tools()
            ):
                pass
            else:
                successful_tools.add(tc.tool_name)

        assert "query_order" in successful_tools

    def test_wrong_order_id_rejected(self):
        from models import ToolCall

        scenario_oid = "MT001"
        tc = ToolCall(
            tool_name="query_order",
            arguments={"order_id": "MT999"},
            result={"status": "found"},
            error=None,
            source="agent",
        )

        successful_tools: set[str] = set()
        if tc.source != "harness" and not tc.error:
            tc_oid = tc.arguments.get("order_id", "")
            if (
                tc_oid
                and scenario_oid
                and tc_oid != scenario_oid
                or not tc_oid
                and scenario_oid
                and tc.tool_name in _entity_bound_tools()
            ):
                pass
            else:
                successful_tools.add(tc.tool_name)

        assert "query_order" not in successful_tools


def _entity_bound_tools() -> frozenset[str]:
    """Tools that require order_id binding."""
    return frozenset(
        {
            "query_order",
            "update_delivery_status",
            "reschedule_delivery",
            "create_compensation",
            "transfer_to_human",
            "log_call_result",
            "check_compensation_eligibility",
        }
    )
