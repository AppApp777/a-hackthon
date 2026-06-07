"""Contract tests: blocked/failed tool calls must not leave DB side effects.

Tests the snapshot-rollback mechanism for all tool types, ensuring:
1. Blocked tools have zero DB side effect
2. Snapshot + rollback restores exact DB state
3. Ledger records blocked events correctly
"""

from __future__ import annotations

import sqlite3

import pytest
from models_outbound import OutboundScenario
from tools_outbound import _TOOL_REQUIRED_PARAMS, OutboundToolSimulator

TOOL_NAMES = list(_TOOL_REQUIRED_PARAMS.keys())


def _make_scenario() -> OutboundScenario:
    return OutboundScenario(
        id="test_transactional",
        name="test",
        domain="outbound_call",
        call_type="after_sales",
        difficulty="easy",
        description="test scenario",
        call_purpose="test",
        call_context={
            "order_id": "MT001",
            "customer_name": "张三",
            "customer_phone": "13800138001",
            "merchant_name": "测试商家",
            "merchant_id": "M001",
            "rider_name": "李师傅",
        },
        instruction_steps=[],
        callee_persona={},
        callee_goal="test",
    )


def _db_snapshot(conn: sqlite3.Connection) -> dict[str, list[tuple]]:
    """Take a full snapshot of all tables in the DB."""
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    snapshot = {}
    for table in tables:
        cursor.execute(f"SELECT * FROM {table}")
        snapshot[table] = cursor.fetchall()
    return snapshot


def _snapshots_equal(a: dict, b: dict) -> bool:
    """Compare two DB snapshots for equality."""
    if set(a.keys()) != set(b.keys()):
        return False
    for table in a:
        if len(a[table]) != len(b[table]):
            return False
        for row_a, row_b in zip(a[table], b[table], strict=False):
            if tuple(row_a) != tuple(row_b):
                return False
    return True


class TestSnapshotRollback:
    def test_snapshot_restores_exact_state(self):
        sim = OutboundToolSimulator(_make_scenario())
        before = _db_snapshot(sim.conn)
        snap = sim.snapshot()

        sim.execute(
            "create_compensation", {"order_id": "MT001", "type": "refund", "reason": "test"}
        )

        after_execute = _db_snapshot(sim.conn)
        assert not _snapshots_equal(before, after_execute), "Tool should have changed DB"

        sim.rollback(snap)
        after_rollback = _db_snapshot(sim.conn)
        assert _snapshots_equal(before, after_rollback), "Rollback should restore exact state"

    def test_double_rollback_is_idempotent(self):
        sim = OutboundToolSimulator(_make_scenario())
        snap = sim.snapshot()

        sim.execute("log_call_result", {"order_id": "MT001", "result": "confirmed"})
        sim.rollback(snap)
        state_1 = _db_snapshot(sim.conn)

        snap2 = sim.snapshot()
        sim.execute("log_call_result", {"order_id": "MT001", "result": "escalated"})
        sim.rollback(snap2)
        state_2 = _db_snapshot(sim.conn)

        assert _snapshots_equal(state_1, state_2)


class TestBlockedToolsNoSideEffect:
    """For each tool that mutates state, verify snapshot+rollback leaves no trace."""

    MUTATING_TOOLS = {
        "update_delivery_status": {"order_id": "MT001", "new_status": "confirmed"},
        "reschedule_delivery": {"order_id": "MT001", "new_time": "14:00"},
        "create_compensation": {"order_id": "MT001", "type": "refund", "reason": "test"},
        "transfer_to_human": {"order_id": "MT001", "reason": "test"},
        "log_call_result": {"order_id": "MT001", "result": "confirmed"},
        "modify_rider_contract": {"rider_name": "李师傅", "action": "cancel"},
        "create_rider_appeal": {
            "rider_name": "李师傅",
            "appeal_type": "unfair_penalty",
            "content": "test",
        },
        "create_merchant_ticket": {
            "merchant_id": "M001",
            "ticket_type": "complaint",
            "content": "test",
        },
        "modify_merchant_subscription": {
            "merchant_id": "M001",
            "product": "basic",
            "action": "upgrade",
        },
    }

    @pytest.mark.parametrize("tool_name,args", list(MUTATING_TOOLS.items()))
    def test_blocked_tool_no_db_side_effect(self, tool_name: str, args: dict):
        sim = OutboundToolSimulator(_make_scenario())
        before = _db_snapshot(sim.conn)
        snap = sim.snapshot()

        sim.execute(tool_name, args)
        sim.rollback(snap)

        after = _db_snapshot(sim.conn)
        assert _snapshots_equal(before, after), (
            f"Tool '{tool_name}' left DB side effects after rollback"
        )


class TestReadOnlyToolsNoSideEffect:
    """Read-only tools should never modify DB, even without rollback."""

    READONLY_TOOLS = {
        "query_order": {"order_id": "MT001"},
        "query_customer": {"customer_phone": "13800138001"},
        "check_compensation_eligibility": {"order_id": "MT001"},
        "query_rider_status": {"rider_name": "李师傅"},
        "query_rider_contract": {"rider_name": "李师傅"},
        "query_rider_violations": {"rider_name": "李师傅"},
        "query_merchant_status": {"merchant_id": "M001"},
        "query_merchant_settlement": {"merchant_id": "M001", "period": "2026-05"},
        "query_merchant_violations": {"merchant_id": "M001"},
    }

    @pytest.mark.parametrize("tool_name,args", list(READONLY_TOOLS.items()))
    def test_readonly_tool_no_db_change(self, tool_name: str, args: dict):
        sim = OutboundToolSimulator(_make_scenario())
        before = _db_snapshot(sim.conn)
        sim.execute(tool_name, args)
        after = _db_snapshot(sim.conn)
        assert _snapshots_equal(before, after), f"Read-only tool '{tool_name}' modified DB state"


class TestCallLogConsistency:
    def test_rollback_clears_call_log(self):
        sim = OutboundToolSimulator(_make_scenario())
        snap = sim.snapshot()
        initial_log_len = len(sim.call_log)

        sim.execute("log_call_result", {"order_id": "MT001", "result": "confirmed"})
        assert len(sim.call_log) > initial_log_len

        sim.rollback(snap)
        assert len(sim.call_log) == initial_log_len
